"""Offline Phase 2 corpus, packet summary and scoring. Never executes a judge."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re

from .advisory import load_sent_items, read_json, validate_judgments, verify_judgments
from .judge import validate_scope
from .judge_contract import validate_contract
from .model import Budget
from .packets import (display_path, encoded, redact, sensitive_path,
                      sensitive_reference, write_packets)
from .sources import Repositories

HERE = Path(__file__).resolve().parent
CASES = ("N-research-codex-today", "P-pilot-agy-research")
KINDS = ("quote_token", "line_move", "identifier", "number", "negation")


def allowed_source(case, path):
    scope = read_json(HERE / "judge-scope.json")["cases"]
    visible = display_path(path).replace("\\", "/").casefold()
    return (case in scope and not sensitive_path(visible) and ".." not in visible.split("/")
            and any(visible.startswith(p.casefold()) for p in scope[case]))


# The same public-case repository snapshots used by the deterministic evaluation.
FIXED_SOURCES = (
    ("N-research-codex-today", "show-me-the-work", "95d18194dee601151b4181ae328b58d876f5f493"),
    ("P-pilot-agy-research", ".claude/harness/agent-gate", "3a58908572afe8dd1575e31ff5be4f22cea3ac0b"),
)
CODE_SUFFIXES = {".py", ".mjs", ".js", ".ts"}
DOCUMENT_SUFFIXES = {".md", ".rst", ".txt"}


def corpus_file(name: str) -> bool:
    """Filter metadata before any blob read; never inspect settings or eval trees."""
    path = Path(name)
    return (bool(name) and not sensitive_path(name)
            and not any(part.startswith(".") or part in {"tests", "eval", "research", "reviews", "pilot"}
                        for part in path.parts)
            and (len(path.parts) == 1 or path.parts[0] in {"smtw", "core", "adapters", "docs"})
            and path.suffix.lower() in CODE_SUFFIXES | DOCUMENT_SUFFIXES)


def collect_pool(cases_root: Path):
    """Build synthetic citations from safe file lines at the pinned public revisions.

    cases_root remains accepted for CLI compatibility; reports/transcripts and the
    working tree are not corpus inputs.
    """
    pool, receipts, seen = [], {}, set()
    for case, relative_root, revision in FIXED_SOURCES:
        root = Path.home() / relative_root
        repo = Repositories([str(root)], {str(root): revision}, {}, Budget(120))
        names = repo.files(root, revision)
        if names is None:
            raise ValueError("fixed evaluation repository unavailable")
        receipt = {"revision": revision, "source": "git_show", "files": {}}
        counts = Counter()
        for name in sorted(names):
            if not corpus_file(name):
                continue
            category = "code" if Path(name).suffix.lower() in CODE_SUFFIXES else "document"
            if counts[category] >= 200:
                continue
            path = "~/" + relative_root + "/" + name
            if not allowed_source(case, path):
                continue
            result = repo.git(root, "show", revision + ":" + name)
            if result.returncode:
                raise ValueError("fixed evaluation file unavailable")
            try:
                text = result.stdout.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue
            if "\x00" in text:
                continue
            lines = dict(enumerate(text.splitlines(), 1))
            taken = 0
            for number, content in lines.items():
                quote = content.strip()
                if (not 20 <= len(quote) <= 180 or quote in seen
                        or sensitive_reference(quote) or redact(quote)[0] != quote):
                    continue
                window = {n: lines[n] for n in range(max(1, number-1), number+2) if n in lines}
                excerpt = "\n".join(f"{n}: {v}" for n, v in window.items())
                if any(sensitive_reference(v) for v in window.values()):
                    continue
                safe, redactions = redact(excerpt, preserve_source_lines=True)
                if redactions or safe != excerpt or len(safe) > 1500:
                    continue
                row = {"case": case, "path": path, "line": number, "quote": quote,
                       "window": window, "text": safe, "redactions": {}, "report_line": 1,
                       "citation_path": name,
                       "source": "git_show", "revision": revision, "category": category,
                       "observation": hashlib.sha256((revision + ":" + name).encode()).hexdigest()[:16]}
                if len(statement(row)) > 300 or redact(statement(row))[0] != statement(row):
                    continue
                pool.append(row); seen.add(quote); counts[category] += 1; taken += 1
                if taken >= 30 or counts[category] >= 200:
                    break
            if taken:
                receipt["files"][name] = hashlib.sha256(result.stdout).hexdigest()
        receipt["pool_by_category"] = dict(counts)
        receipts[relative_root] = receipt
    return pool, receipts


def statement(row, quote=None, number=None, negated=False):
    return (f'{row.get("citation_path", row["path"].rsplit("/", 1)[-1])}:{row["line"] if number is None else number} '
            + ("does not contain" if negated else "contains") + " the quotation "
            + json.dumps(row["quote"] if quote is None else quote, ensure_ascii=False) + ".")


def mutate(row, kind, rng):
    quote = row["quote"]
    if kind == "negation":
        return statement(row,negated=True)
    if kind == "line_move":
        targets=[n for n,text in row["window"].items()
                 if n!=row["line"] and quote not in text and len(text.strip())>=8
                 and redact(text)[0]==text]
        return statement(row,number=rng.choice(targets)) if targets else None
    if kind == "number":
        match=re.search(r"\d+",quote)
        if not match:return None
        changed=str(int(match[0]) + rng.choice([-1,1]))
    else:
        matches=list(re.finditer(r"[A-Za-z_][A-Za-z_0-9]*",quote))
        if not matches:return None
        match=matches[-1] if kind=="quote_token" else matches[0]
        changed=("missing_" if kind=="identifier" else "altered_")+str(rng.randrange(100000,999999))
    return statement(row,quote=quote[:match.start()]+changed+quote[match.end():])


def generate(pool: list[dict], output: Path, *, seed: int = 2, per_kind: int = 20, receipts=None):
    if not pool or type(per_kind) is not int or per_kind < 1:
        raise ValueError("no usable matched quotes or invalid sample count")
    if output.exists() and any(output.iterdir()):
        raise ValueError("evaluation output must be empty")
    # Validate before creating any packet, including caller-provided pool fixtures.
    if any(not allowed_source(row["case"],row["path"]) for row in pool):
        raise ValueError("corpus source outside the fixed allowlist")
    rng=random.Random(seed)
    items, truth, item_cases, used = [], {}, {}, set()
    categories = Counter()
    for kind in KINDS:
        eligible = []
        for row in pool:
            altered = mutate(row, kind, rng)
            if (altered and len(altered) <= 300 and len(statement(row)) <= 300
                    and redact(altered)[0] == altered
                    and redact(statement(row))[0] == statement(row)):
                eligible.append((row, altered))
        if not eligible:raise ValueError("no valid paired mutation for "+kind)
        for _ in range(per_kind):
            available = [pair for pair in eligible if pair[0]["quote"] not in used] or eligible
            # Mix code/document and both approved roots without answer-dependent fields.
            groups = sorted({(r["case"], r.get("category", "code")) for r, _altered in available})
            group = groups[_ % len(groups)]
            row, altered = rng.choice([pair for pair in available
                                       if (pair[0]["case"], pair[0].get("category", "code")) == group])
            used.add(row["quote"])
            categories[row.get("category", "code")] += 1
            for claim, verdict in [(statement(row),"supported"),(altered,"contradicted")]:
                key="q-"+f"{rng.getrandbits(64):016x}"
                if key in truth:raise ValueError("duplicate generated id")
                if redact(claim)[0]!=claim:
                    raise ValueError("mutation is not safe after redaction")
                item={"id":key,"branch":"evidence_review","report_line":row["report_line"],
                      "claim":claim,"before":"","after":"","redactions":row["redactions"],
                      "excerpts":[{"path":row["path"],"source":row.get("source", "git_show"),"revision":row.get("revision", ""),
                                   "ref":1,"version":"A","observation":row["observation"],
                                   "text":row["text"],"redactions":row["redactions"]}]}
                items.append(item);item_cases[key]=row["case"]
                truth[key]={"verdict":verdict,"mutation":kind if verdict=="contradicted" else None}
    if len(used) < min(60, per_kind * len(KINDS)):
        raise ValueError("L4 requires at least 60 distinct original quotations (or all small-sample pairs)")
    rng.shuffle(items)
    packets=[]
    for item in items:
        if not packets or len(encoded({**packets[-1],"data_block":[*packets[-1]["data_block"],item]}))>30000:
            packets.append({"version":1,"instructions":"Evidence review data.","data_block":[]})
        packets[-1]["data_block"].append(item)
    manifest=write_packets(output/"packets",packets,{"item_cases":item_cases,
        "selected_count":len(items),"sent_count":len(items),"deferred_count":0})
    truth_dir=output/"truth";truth_dir.mkdir(parents=True)
    answers={"version":1,"seed":seed,"items":truth,"source_receipts":receipts or {},
             "unique_pool_quotes":len({r["quote"] for r in pool}),
             "unique_used_quotes":len(used),"sampling":"prefer_distinct_quote_text",
              "originals_by_category":dict(categories),
             "original_count":len(items)//2,"mutation_count":len(items)//2}
    (truth_dir/"answers.json").write_text(json.dumps(answers,ensure_ascii=False,indent=2),encoding="utf-8")
    validate_scope(manifest,load_sent_items(output/"packets"))
    return {"items":len(items),"packets":len(packets),"unique_originals":len(used),
            "model_calls":0,"answers":str(truth_dir/"answers.json")}


def summarize(folder: Path) -> str:
    sent=load_sent_items(folder);manifest=read_json(folder/"manifest.json")
    validate_contract(manifest);validate_scope(manifest,sent)
    lines=["| Packet | Items | Excerpt source paths | Redactions |",
           "|---|---:|---|---:|"]
    for entry in manifest["packets"]:
        items=[sent[key] for key in entry["item_ids"]]
        paths=sorted({e["path"].replace("|","\\|").replace("\n"," ") for i in items for e in i["excerpts"]})
        counts=sum(sum(i.get("redactions",{}).values()) for i in items)
        lines.append(f'| {entry["file"]} | {len(items)} | '+ "<br>".join(paths)+f" | {counts} |")
    return "\n".join(lines)


def read_run(folder: Path, sent: dict):
    rows, displayed, not_in = {}, {}, 0
    files=sorted(folder.glob("judgments-*.json"))
    if not files:raise ValueError("no judge outputs")
    for path in files:
        judgments=validate_judgments(read_json(path))
        if any(row["id"] in rows for row in judgments):raise ValueError("duplicate run id")
        verified=read_json(path.with_name(path.name.replace("judgments-","verified-")))
        # Recompute the displayed subset, independently of a supplied verdict file.
        actual=verify_judgments(judgments,sent)
        if verified["items"]!=actual["items"] or verified["counts"]!=actual["counts"]:
            raise ValueError("supplied quote verification differs from recomputation")
        rows.update((row["id"],row) for row in judgments)
        displayed.update((row["id"],row) for row in actual["items"])
        not_in+=actual["counts"]["quote_not_in_excerpt"]
    return rows,displayed,not_in


def score(folder: Path, truth_file: Path, runs: list[Path]):
    if len(runs) not in {1,3}:raise ValueError("supply one run or exactly three reproducibility runs")
    sent=load_sent_items(folder);truth=read_json(truth_file)["items"]
    if set(truth)!=set(sent):raise ValueError("truth ids differ from sent ids")
    if any(row["verdict"] not in {"supported","contradicted"} for row in truth.values()):
        raise ValueError("invalid truth label")
    evaluated=[read_run(run,sent) for run in runs]
    positives={key for key,row in truth.items() if row["verdict"]=="contradicted"}
    negatives=set(truth)-positives
    measures=[]
    for raw,shown,bad in evaluated:
        flags={key for key,row in shown.items() if row["verdict"]=="contradicted"}
        tp,fp=len(flags&positives),len(flags&negatives)
        ratio=lambda a,b: a/b if b else None
        measures.append({"recall":ratio(tp,len(positives)),"precision":ratio(tp,tp+fp),
            "false_display_rate":ratio(fp,len(negatives)),
            "quote_not_in_excerpt_rate":ratio(bad,len(raw)),
            "true_positive":tp,"false_positive":fp,"judged":len(raw),
            "unjudged":len(sent)-len(raw)})
    verdict_agreement=quote_agreement=None
    if len(evaluated)==3:
        verdict_agreement=sum(all(key in raw for raw,_,_ in evaluated) and
            len({raw[key]["verdict"] for raw,_,_ in evaluated})==1 for key in truth)/len(truth)
        union=set().union(*(set(shown) for _,shown,_ in evaluated))
        quote_agreement=(sum(all(key in shown for _,shown,_ in evaluated) and
            len({(shown[key]["evidence_ref"],shown[key]["evidence_quote"]) for _,shown,_ in evaluated})==1
            for key in union)/len(union)) if union else None
    return {"runs":measures,"verdict_agreement_3":verdict_agreement,
            "verified_quote_agreement_3":quote_agreement,"model_calls":0,
            "interpretation":"Scores reflect supplied outputs; synthetic fake outputs are pipeline tests only."}


def score_table(result):
    lines=["| Run | Recall | Precision | False display | Quote not in excerpt | Unjudged |",
           "|---|---:|---:|---:|---:|---:|"]
    def fmt(value):return "n/a" if value is None else f"{value:.4f}"
    for index,row in enumerate(result["runs"],1):
        lines.append(f"| {index} | "+" | ".join(fmt(row[key]) for key in
            ("recall","precision","false_display_rate","quote_not_in_excerpt_rate"))+f' | {row["unjudged"]} |')
    lines.extend(["",f'Verdict agreement (3): {fmt(result["verdict_agreement_3"])}',
                  f'Verified quote agreement (3): {fmt(result["verified_quote_agreement_3"])}'])
    return "\n".join(lines)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="action",required=True)
    g=sub.add_parser("generate");g.add_argument("--cases-root",type=Path,required=True);g.add_argument("--out",type=Path,required=True)
    h=sub.add_parser("summary");h.add_argument("--packets",type=Path,required=True)
    s=sub.add_parser("score");s.add_argument("--packets",type=Path,required=True);s.add_argument("--truth",type=Path,required=True);s.add_argument("--runs",type=Path,nargs="+",required=True);s.add_argument("--out",type=Path,required=True)
    args=parser.parse_args(argv)
    if args.action=="generate":
        pool,receipts=collect_pool(args.cases_root)
        print(json.dumps(generate(pool,args.out,receipts=receipts),ensure_ascii=False,indent=2))
    elif args.action=="summary":print(summarize(args.packets))
    else:
        result=score(args.packets,args.truth,args.runs)
        args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(score_table(result))
    return 0
