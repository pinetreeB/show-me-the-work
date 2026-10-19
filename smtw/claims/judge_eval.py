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
from .check import run
from .judge import validate_scope
from .judge_contract import validate_contract
from .model import Budget
from .packets import (display_path, encoded, redact, sensitive_path,
                      transcript_sources, write_packets)
from .paths import matches_path

HERE = Path(__file__).resolve().parent
CASES = ("N-research-codex-today", "P-pilot-agy-research")
KINDS = ("quote_token", "line_move", "identifier", "number", "negation")


def allowed_source(case, path):
    scope = read_json(HERE / "judge-scope.json")["cases"]
    visible = display_path(path).replace("\\", "/").casefold()
    return (case in scope and not sensitive_path(visible) and ".." not in visible.split("/")
            and any(visible.startswith(p.casefold()) for p in scope[case]))


def collect_pool(cases_root: Path):
    """Use literal match quotes from the fixed public cases and their captured reads."""
    pool, receipts = {}, {}
    for case in CASES:
        directory = cases_root / "regression" / case
        if case == "P-pilot-agy-research":
            directory = cases_root / "pilot-agy-research-20261018"
            report, transcript, kind = directory/"research-agy.md", directory/"transcript_full.jsonl", "agy"
        else:
            report, transcript, kind = directory/"report.md", directory/"rollout.jsonl", "codex"
        text = report.read_text(encoding="utf-8")
        checked = run(report, **{kind:transcript if kind == "codex" else str(transcript)}, time_limit=120)
        sources = transcript_sources(transcript, kind, report, text, {}, Budget(120))
        receipts[case] = {"report_sha256":hashlib.sha256(report.read_bytes()).hexdigest(),
                          "transcript_sha256":hashlib.sha256(transcript.read_bytes()).hexdigest(),
                          "checker_status":checked["status"]}
        for claim in checked["claims"]:
            if claim["check_type"] not in {"K1","K5"} or claim["verdict"] != "match":
                continue
            matrix = claim["source"].get("quote_check",{}).get("matrix",[])
            for row in matrix:
                if row["verdict"] != "match" or not allowed_source(case,row["path"]):
                    continue
                quote = re.sub(r"(?m)^\s*(?:line\s+)?\d+: ?", "", row["quote"]).strip()
                if not 8 <= len(quote) <= 180 or "\n" in quote or redact(quote)[0] != quote:
                    continue
                for source in sources:
                    if not matches_path(row["path"],source.path) or not allowed_source(case,source.path):
                        continue
                    # Keep only this successful observation, not an unrelated version.
                    location = row.get("observation",{}).get("location","")
                    if location and source.observation != hashlib.sha256(location.encode()).hexdigest()[:16]:
                        continue
                    for number, content in source.lines.items():
                        if quote not in content:
                            continue
                        window = {n:source.lines[n] for n in range(max(1,number-1),number+2)
                                  if n in source.lines}
                        excerpt = "\n".join(f"{n}: {v}" for n,v in window.items())
                        safe, counts = redact(excerpt,preserve_source_lines=True)
                        if len(safe)>1500 or quote not in safe:
                            continue
                        key=(display_path(source.path),number,quote,safe)
                        pool[key]={"case":case,"path":display_path(source.path),"line":number,
                                   "quote":quote,"window":window,"text":safe,"redactions":counts,
                                   "report_line":claim["report_line"],"observation":source.observation}
    return list(pool.values()), receipts


def statement(row, quote=None, number=None, negated=False):
    return (f'At line {row["line"] if number is None else number}, the source '
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
    for kind in KINDS:
        eligible=[row for row in pool if mutate(row,kind,random.Random(seed)) is not None]
        if not eligible:raise ValueError("no valid paired mutation for "+kind)
        for _ in range(per_kind):
            row=rng.choice(eligible)
            altered=mutate(row,kind,rng)
            used.add((row["path"],row["line"],row["quote"]))
            for claim, verdict in [(statement(row),"supported"),(altered,"contradicted")]:
                key="q-"+f"{rng.getrandbits(64):016x}"
                if key in truth:raise ValueError("duplicate generated id")
                if redact(claim)[0]!=claim:
                    raise ValueError("mutation is not safe after redaction")
                item={"id":key,"branch":"evidence_review","report_line":row["report_line"],
                      "claim":claim,"before":"","after":"","redactions":row["redactions"],
                      "excerpts":[{"path":row["path"],"source":"transcript_read","revision":"",
                                   "ref":1,"version":"A","observation":row["observation"],
                                   "text":row["text"],"redactions":row["redactions"]}]}
                items.append(item);item_cases[key]=row["case"]
                truth[key]={"verdict":verdict,"mutation":kind if verdict=="contradicted" else None}
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
             "unique_pool_quotes":len({(r["path"],r["line"],r["quote"]) for r in pool}),
             "unique_used_quotes":len(used),"sampling":"with_replacement",
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
