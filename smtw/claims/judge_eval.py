"""Offline Phase 2 corpus, packet summary and scoring. Never executes a judge."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import re

from .advisory import load_sent_items, read_json, validate_judgments, verify_judgments
from .judge import validate_scope
from .judge_scope import JudgeScope, load_scope
from .judge_contract import validate_contract
from .model import Budget
from .packets import (display_path, encoded, redact, sensitive_path,
                      sensitive_reference, write_packets)
from .sources import Repositories

HERE = Path(__file__).resolve().parent
KINDS = ("quote_token", "line_move", "identifier", "number", "negation")

MARKER = re.compile(r"altered|tamper|fake|missing_\d+", re.I)
TOKEN = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
COMMON_WORDS = set("""a an the this that it its to by on at be are was were have has
and as assert async await break case class const continue def default delete do else
except export false finally for from function if import in instanceof is let match
new none not null of or pass raise return switch throw true try typeof var void when
while with yield""".split())


def source_has_marker(row):
    return any(MARKER.search(str(row.get(key, "")))
               for key in ("case", "path", "citation_path", "quote", "text"))


def source_tokens(row):
    return {m.group() for text in row["window"].values() for m in TOKEN.finditer(text)
            if len(m.group()) >= 2 and m.group().casefold() not in COMMON_WORDS
            and not MARKER.search(m.group())}


def token_source_key(row, scope):
    path = display_path(row["path"]).replace(chr(92), "/").casefold()
    prefix = max((p.casefold() for p in scope.cases[row["case"]]
                  if path.startswith(p.casefold())), key=len)
    repository = (row["case"], prefix, row.get("revision", ""))
    return repository, path


def token_inventory(pool, scope):
    files, repositories = defaultdict(set), defaultdict(set)
    for row in pool:
        if source_has_marker(row):
            continue
        repository, path = token_source_key(row, scope)
        tokens = source_tokens(row)
        files[(repository, path)].update(tokens)
        repositories[repository].update(tokens)
    return {key: (tuple(sorted(tokens)), tuple(sorted(repositories[key[0]])))
            for key, tokens in files.items()}


def token_style(word):
    if word.isupper():
        return "upper"
    if "_" in word:
        return "snake"
    if re.search(r"[a-z][A-Z]", word):
        return "camel"
    return "word"


def allowed_source(case, path, scope: JudgeScope):
    visible = display_path(path).replace(chr(92), "/").casefold()
    return (case in scope.cases and not sensitive_path(visible) and ".." not in visible.split("/")
            and any(visible.startswith(p.casefold()) for p in scope.cases[case]))


CODE_SUFFIXES = {".py", ".mjs", ".js", ".ts"}
DOCUMENT_SUFFIXES = {".md", ".rst", ".txt"}


def corpus_file(name: str) -> bool:
    """Filter metadata before any blob read; never inspect settings or eval trees."""
    path = Path(name)
    return (bool(name) and not sensitive_path(name) and not MARKER.search(name)
            and not any(part.startswith(".") or part in {"tests", "eval", "research", "reviews", "pilot"}
                        for part in path.parts)
            and (len(path.parts) == 1 or path.parts[0] in {"smtw", "core", "adapters", "docs"})
            and path.suffix.lower() in CODE_SUFFIXES | DOCUMENT_SUFFIXES)


def collect_pool(scope_file: Path):
    """Build safe file-line citations only from explicitly scoped fixed revisions."""
    scope = load_scope(scope_file)
    if not scope.repositories:
        raise ValueError("scope needs fixed evaluation repositories")
    for source in scope.repositories:
        if (not isinstance(source, dict)
                or not isinstance(source.get("root"), str) or not source["root"]
                or not isinstance(source.get("revision"), str)
                or not re.fullmatch(r"[0-9a-fA-F]{40}", source["revision"])
                or not isinstance(source.get("prefix"), str)
                or source["prefix"] not in scope.cases.get(source.get("case"), [])):
            raise ValueError("invalid fixed evaluation repository scope")
    pool, receipts, seen = [], {}, set()
    for source in scope.repositories:
        case, revision, prefix = source["case"], source["revision"], source["prefix"]
        root = Path(source["root"]).expanduser().resolve()
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
            path = prefix + name
            if not allowed_source(case, path, scope):
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
                        or MARKER.search(quote) or sensitive_reference(quote) or redact(quote)[0] != quote):
                    continue
                window = {n: lines[n] for n in range(max(1, number-1), number+2) if n in lines}
                excerpt = "\n".join(f"{n}: {v}" for n, v in window.items())
                if any(sensitive_reference(v) or MARKER.search(v) for v in window.values()):
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
        receipts[prefix] = receipt
    return pool, receipts


def statement(row, quote=None, number=None, negated=False):
    return (f'{row.get("citation_path", row["path"].rsplit("/", 1)[-1])}:{row["line"] if number is None else number} '
            + ("does not contain" if negated else "contains") + " the quotation "
            + json.dumps(row["quote"] if quote is None else quote, ensure_ascii=False) + ".")


def mutate(row, kind, rng, token_groups=None):
    quote = row["quote"]
    if source_has_marker(row) or quote not in row["window"].get(row["line"], ""):
        return None
    if kind == "negation":
        return statement(row, negated=True)
    if kind == "line_move":
        targets = [n for n, text in row["window"].items()
                   if n != row["line"] and quote not in text and len(text.strip()) >= 8
                   and redact(text)[0] == text]
        return statement(row, number=rng.choice(targets)) if targets else None

    def replacement(match, value):
        changed = quote[:match.start()] + value + quote[match.end():]
        # A neighboring captured line may already contain the apparent change.
        # Such an example cannot be used as a fabricated-quotation negative.
        if changed == quote or any(changed in text for text in row["window"].values()):
            return None
        return statement(row, quote=changed)

    if kind == "number":
        # Integers only: do not change an identifier suffix or a decimal digit
        # by 0.1 while labeling it as an adjacent integer mutation.
        matches = list(re.finditer(r"(?<![\w.])\d+(?![\w.])", quote))
        rng.shuffle(matches)
        for match in matches:
            digits = match.group()
            deltas = [-1, 1]
            rng.shuffle(deltas)
            for delta in deltas:
                value = int(digits) + delta
                if value < 0:
                    continue
                changed = str(value)
                if len(digits) > 1 and digits.startswith("0"):
                    changed = changed.zfill(len(digits))
                if len(changed) != len(digits):
                    continue
                result = replacement(match, changed)
                if result:
                    return result
        return None

    groups = token_groups or (tuple(sorted(source_tokens(row))),)
    matches = [m for m in TOKEN.finditer(quote)
               if len(m.group()) >= 2 and m.group().casefold() not in COMMON_WORDS]
    if kind == "quote_token":
        matches.reverse()
    for match in matches:
        # Prefer a similarly shaped token from the file, then the repository.
        # Every fallback still comes from captured, approved source text.
        same_style = [tuple(word for word in group if token_style(word) == token_style(match.group()))
                      for group in groups]
        seen = set()
        for group in [*same_style, *groups]:
            candidates = [word for word in group
                          if word.casefold() != match.group().casefold() and word not in seen]
            rng.shuffle(candidates)
            for word in candidates:
                seen.add(word)
                result = replacement(match, word)
                if result:
                    return result
    return None


def generation_bounds(batch_size, limit_items):
    if batch_size is not None and (type(batch_size) is not int or batch_size < 1):
        raise ValueError("--batch-size must be a positive packet count")
    if limit_items is not None and (type(limit_items) is not int or limit_items < 2 or limit_items % 2):
        raise ValueError("--limit-items must be an even item count of at least 2 (complete original/mutation pairs)")


def generate(pool: list[dict], output: Path, *, scope_file: Path, seed: int = 2,
             per_kind: int = 20, receipts=None, batch_size: int | None = None,
             limit_items: int | None = None):
    generation_bounds(batch_size, limit_items)
    if not pool or type(per_kind) is not int or per_kind < 1:
        raise ValueError("no usable matched quotes or invalid sample count")
    if output.exists() and any(output.iterdir()):
        raise ValueError("evaluation output must be empty")
    # Validate before creating any packet, including caller-provided pool fixtures.
    scope = load_scope(scope_file)
    if any(not allowed_source(row["case"],row["path"],scope) for row in pool):
        raise ValueError("corpus source outside the fixed allowlist")
    inventory = token_inventory(pool, scope)
    rng=random.Random(seed)
    items, truth, item_cases, used = [], {}, {}, set()
    item_sources = {}
    for kind in KINDS:
        eligible = []
        for row in pool:
            if source_has_marker(row):
                continue
            altered = mutate(row, kind, rng, inventory.get(token_source_key(row, scope)))
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
                item_sources[key] = (row["quote"], row.get("category", "code"))
                truth[key]={"verdict":verdict,"mutation":kind if verdict=="contradicted" else None}
    if len(used) < min(60, per_kind * len(KINDS)):
        raise ValueError("L4 requires at least 60 distinct original quotations (or all small-sample pairs)")
    # The generation order contains adjacent original/mutation pairs. Limit
    # before shuffling so a small first run never selects an unpaired prefix.
    if limit_items is not None:
        items = items[:limit_items]
    selected_ids = {item["id"] for item in items}
    truth = {key: value for key, value in truth.items() if key in selected_ids}
    item_cases = {key: value for key, value in item_cases.items() if key in selected_ids}
    used = {item_sources[key][0] for key in selected_ids}
    rng.shuffle(items)
    packets=[]
    for item in items:
        if not packets or len(encoded({**packets[-1],"data_block":[*packets[-1]["data_block"],item]}))>30000:
            packets.append({"version":1,"instructions":"Evidence review data.","data_block":[]})
        packets[-1]["data_block"].append(item)
    groups = []
    width = batch_size or len(packets)
    for start in range(0, len(packets), width):
        group = packets[start:start + width]
        name = f"batch-{start // width + 1:03d}" if batch_size is not None else ""
        folder, truth_dir = output / "packets", output / "truth"
        if name:
            folder, truth_dir = folder / name, truth_dir / name
        ids = {item["id"] for packet in group for item in packet["data_block"]}
        group_truth = {key: value for key, value in truth.items() if key in ids}
        manifest = write_packets(folder, group, {
            "item_cases": {key: value for key, value in item_cases.items() if key in ids},
            "scope_sha256": scope.sha256, "selected_count": len(ids),
            "sent_count": len(ids), "deferred_count": 0})
        originals = [key for key in group_truth if group_truth[key]["verdict"] == "supported"]
        answers = {"version": 1, "seed": seed, "items": group_truth, "source_receipts": receipts or {},
                   "unique_pool_quotes": len({r["quote"] for r in pool}),
                   "unique_used_quotes": len({item_sources[key][0] for key in ids}),
                   "sampling": "prefer_distinct_quote_text",
                   "originals_by_category": dict(Counter(item_sources[key][1] for key in originals)),
                   "original_count": len(originals), "mutation_count": len(ids) - len(originals)}
        truth_dir.mkdir(parents=True)
        answer_file = truth_dir / "answers.json"
        answer_file.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
        validate_scope(manifest, load_sent_items(folder), scope)
        groups.append({"packets_dir": str(folder), "answers": str(answer_file),
                       "items": len(ids), "packets": len(group)})
    result = {"items": len(items), "packets": len(packets), "unique_originals": len(used),
              "model_calls": 0}
    if batch_size is None:
        result["answers"] = groups[0]["answers"]
    else:
        result["batches"] = groups
    return result


def summarize(folder: Path, *, scope_file: Path) -> str:
    sent=load_sent_items(folder);manifest=read_json(folder/"manifest.json")
    validate_contract(manifest);validate_scope(manifest,sent,load_scope(scope_file))
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


def score(folder: Path, truth_file: Path, runs: list[Path], *, scope_file: Path):
    summarize(folder, scope_file=scope_file)
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
    g=sub.add_parser("generate")
    g.add_argument("--out-root",type=Path,required=True)
    g.add_argument("--batch-size", type=int, help="packets per batch folder; default: no split")
    g.add_argument("--limit-items", type=int, help="first K items before shuffle; K must preserve complete pairs")
    h=sub.add_parser("summary");h.add_argument("--packets",type=Path,required=True)
    s=sub.add_parser("score")
    s.add_argument("--packets",type=Path,required=True)
    s.add_argument("--truth",type=Path,required=True)
    s.add_argument("--runs",type=Path,nargs="+",required=True)
    s.add_argument("--out-root",type=Path,required=True)
    for command in (g,h,s):
        command.add_argument("--scope-file",type=Path,required=True)
    args=parser.parse_args(argv)
    if args.action=="generate":
        try:
            generation_bounds(args.batch_size, args.limit_items)
        except ValueError as exc:
            parser.error(str(exc))
        pool,receipts=collect_pool(args.scope_file)
        print(json.dumps(generate(pool,args.out_root,scope_file=args.scope_file,receipts=receipts,
                                  batch_size=args.batch_size,limit_items=args.limit_items),ensure_ascii=False,indent=2))
    elif args.action=="summary":
        print(summarize(args.packets,scope_file=args.scope_file))
    else:
        result=score(args.packets,args.truth,args.runs,scope_file=args.scope_file)
        args.out_root.mkdir(parents=True,exist_ok=True)
        (args.out_root/"score.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(score_table(result))
    return 0
