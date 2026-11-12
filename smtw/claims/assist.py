"""Deterministic candidate selection. No judge or model execution."""
from __future__ import annotations

from collections import Counter, defaultdict, deque
import hashlib
import re

from .extract import LOCATION, NUMBER, PATH, REFERENCE, extract
from .markup import QUOTE, TICK, cell_spans, masked
from .model import Budget

ELIGIBLE = {"observation_conflict", "paraphrased_quote", "range_only"}
FIXED_ONLY = {"pointer_missing", "no_read_of_file", "partial_range"}
EXCLUDED = {"command_result_missing", "binding_ambiguous", "malformed_number",
            "measurement_missing", "repo_scope_unknown", "commit_missing_file"}
LOCAL_ELIGIBLE = ELIGIBLE | {"binding_ambiguous"}
LOCAL_FIXED_ONLY = FIXED_ONLY | {"found_outside_scope"}
PRIORITY = {"observation_conflict": 0, "paraphrased_quote": 1,
            "binding_ambiguous": 2, "found_outside_scope": 2,
            "not_extracted_numeric_or_quote": 2, "range_only": 3}
SELECT_PATH = re.compile(PATH.pattern.removesuffix(r"\b") + r"(?![A-Za-z0-9_])", re.I)
ASSERTION = re.compile(
    r"\b([A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*(?:\(\))?)"
    r"(?:은|는|이|가|에서|으로|를|을)?\s+[^.!?\n]{0,80}?"
    r"(?:있다|없다|한다|반환한다|호출한다|\bexists?\b|\breturns?\b|\bcalls?\b|\bdoes\b)",
    re.I,
)
CODE_NAME = re.compile(r"\b[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*(?:\(\))?")


def sentences(report: str) -> list[dict]:
    """Keep code/quoted punctuation intact; use table cells as sentence scopes."""
    result, fence = [], ""
    for number, line in enumerate(report.splitlines(), 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)[0]
            fence = "" if fence == token else token if not fence else fence
            continue
        if fence or not line.strip() or re.match(r"^\s*#{1,6}\s", line):
            continue
        if re.fullmatch(r"[\s|:=-]+", line):
            continue
        if line.lstrip().startswith("|"):
            spans = cell_spans(line)
        else:
            boundaries = [0, *[m.end() for m in re.finditer(r"[.!?;](?:\s+|$)", masked(line))], len(line)]
            spans = zip(boundaries, boundaries[1:])
        for start, end in spans:
            value = line[start:end].strip()
            if value:
                result.append({"line": number, "start": start, "end": end, "text": value})
    return result


def identifiers(text: str) -> list[str]:
    found = [m.group(1).removesuffix("()") for m in ASSERTION.finditer(text)]
    for match in CODE_NAME.finditer(text):
        name = match.group().removesuffix("()")
        if "_" in name or "." in name or re.search(r"[a-z][A-Z]", name) or name.isupper():
            found.append(name)
    for match in TICK.finditer(text):
        value = match.group(1)
        if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*(?:\(\))?", value):
            found.append(value.removesuffix("()"))
    return list(dict.fromkeys(found))


def select_candidates(report: str, checked: dict, *, limit: int = 40,
                      fixed_commit: bool = False, local_candidates: bool = False) -> dict:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("선별 상한은 0 이상의 정수여야 합니다.")
    atoms, _ = extract(report, Budget(60))
    lookup = defaultdict(deque)
    for atom in atoms:
        lookup[(atom.report_line, atom.check_type, atom.text)].append(atom)
    parts = sentences(report)
    candidates, excluded = [], Counter()
    covered = set()
    report_id = hashlib.sha256(report.encode("utf-8")).hexdigest()

    def make_item(index: int, branch: str, claim: dict | None = None, atom=None,
                  atom_key: str = "") -> dict:
        part = parts[index]
        text = part["text"]
        paths = [m.group() for m in SELECT_PATH.finditer(text)]
        if atom is not None:
            paths += [atom.path, *atom.binding_candidates]
        ranges = [{"path": m.group("path"), "start": int(m.group("start")),
                   "end": int(m.group("end") or m.group("start"))} for m in REFERENCE.finditer(text)]
        if atom is not None and atom.path and atom.start:
            ranges.append({"path": atom.path, "start": atom.start, "end": atom.end or atom.start})
        # Keep the historical ID contract for the default/team path.  The local
        # path needs an atom-specific identity because several check atoms can
        # legitimately share a report row.
        identity = (f"{report_id}:{index}:{branch}:{atom_key}"
                    if local_candidates else f"{report_id}:{index}:{branch}:{len(candidates)}")
        item = {
            "id": "a-" + hashlib.sha256(identity.encode()).hexdigest()[:16],
            "branch": branch, "priority": PRIORITY.get(branch, 4),
            "report_line": part["line"], "claim": text,
            "before": parts[index - 1]["text"] if index else "",
            "after": parts[index + 1]["text"] if index + 1 < len(parts) else "",
            "paths": list(dict.fromkeys(p for p in paths if p)),
            "identifiers": identifiers(text), "ranges": ranges,
            "check_type": claim.get("check_type", "") if claim else "",
            "reason_code": claim.get("reason_code", "") if claim else "",
        }
        if local_candidates and claim:
            item["atom"] = {"check_type": item["check_type"], "sentence_start": part["start"],
                            "sentence_end": part["end"]}
        if local_candidates and claim and branch in LOCAL_FIXED_ONLY:
            # Keep the originally reported range separate from a repository hit
            # in another file.  A hit can add context, never replace this target.
            targets = []
            if atom is not None:
                for target in ranges:
                    copied = dict(target)
                    if copied["path"] == atom.path and copied not in targets:
                        targets.append(copied)
            if atom is not None and atom.path and atom.start:
                target = {"path": atom.path, "start": atom.start, "end": atom.end or atom.start}
                if target not in targets:
                    targets.append(target)
            if branch == "found_outside_scope":
                other_targets = []
                for hit in claim.get("source", {}).get("repository_hits", []):
                    path, line, revision = hit.get("path"), hit.get("line"), hit.get("revision")
                    if (isinstance(path, str) and isinstance(line, int) and line > 0
                            and isinstance(revision, str) and re.fullmatch(r"[0-9a-fA-F]{40}", revision)):
                        target = {"path": path, "start": line, "end": line,
                                  "revision": revision.casefold()}
                        if target not in other_targets:
                            other_targets.append(target)
                            paths.append(path)
                            ranges.append({"path": path, "start": line, "end": line})
                item["other_file_evidence"] = other_targets
            item["paths"] = list(dict.fromkeys(p for p in paths if p))
            item["ranges"] = ranges
            item["target_evidence"] = targets
        return item

    for claim_index, claim in enumerate(checked.get("claims", [])):
        key = (claim["report_line"], claim["check_type"], claim["text"])
        atom = lookup[key].popleft() if lookup[key] else None
        indexes = [i for i, p in enumerate(parts) if p["line"] == claim["report_line"] and
                   (p["text"] == atom.context.strip() if atom and atom.context else
                    claim["text"] in p["text"])]
        # Multi-line/structured claims still cover their original reported row.
        if not indexes:
            indexes = [i for i, p in enumerate(parts) if p["line"] == claim["report_line"]
                       and claim["text"] in p["text"]]
        covered.update(indexes)
        verdict, reason = claim.get("verdict"), claim.get("reason_code", "")
        if verdict in {"absent", "mismatch", "match"}:
            excluded[verdict] += 1
            continue
        branch = "range_only" if verdict == "range_only" else reason
        eligible = LOCAL_ELIGIBLE if local_candidates else ELIGIBLE
        fixed_only = LOCAL_FIXED_ONLY if local_candidates else FIXED_ONLY
        if (reason in EXCLUDED and not local_candidates or branch not in eligible | fixed_only):
            excluded[reason or "ineligible"] += 1
            continue
        if branch in fixed_only and not fixed_commit:
            excluded["fixed_commit_required"] += 1
            continue
        if local_candidates and branch == "found_outside_scope" and not any(
                isinstance(hit, dict) and isinstance(hit.get("path"), str)
                and isinstance(hit.get("line"), int) and hit["line"] > 0
                and re.fullmatch(r"[0-9a-fA-F]{40}", str(hit.get("revision", "")))
                for hit in claim.get("source", {}).get("repository_hits", [])):
            excluded["fixed_target_missing"] += 1
            continue
        if indexes:
            candidate = make_item(indexes[0], branch, claim, atom, str(claim_index))
            if local_candidates:
                candidate["atom"]["ordinal"] = claim_index
            candidates.append(candidate)
        else:
            excluded["no_sentence"] += 1

    for warning in checked.get("warnings", []):
        if isinstance(warning, dict) and warning.get("check_type") == "K10":
            covered.update(i for i, p in enumerate(parts) if p["line"] == warning.get("report_line")
                           and warning.get("text", "") in p["text"])
    for i, part in enumerate(parts):
        if i in covered:
            continue
        text = part["text"]
        numeric_or_quote = bool(NUMBER.search(text) or QUOTE.search(text) or TICK.search(text))
        other = bool(SELECT_PATH.search(text) or LOCATION.search(text) or ASSERTION.search(text))
        if numeric_or_quote or other:
            branch = "not_extracted_numeric_or_quote" if numeric_or_quote else "not_extracted_other"
            candidates.append(make_item(i, branch))
    candidates.sort(key=lambda item: (item["priority"], item["report_line"], item["id"]))
    # One report row consumes one slot, retaining all eligible evidence pointers.
    def conflict_key(item):
        return (item["report_line"], item["claim"], tuple(item["paths"]),
                tuple((r["path"], r["start"], r["end"]) for r in item["ranges"]))
    conflict_keys = {conflict_key(item) for item in candidates
                     if item["branch"] == "observation_conflict"}
    grouped = {}
    for candidate in candidates:
        line = candidate["report_line"]
        identity = conflict_key(candidate)
        group = identity if identity in conflict_keys else (
            candidate["id"] if local_candidates else (line, "ordinary"))
        if group not in grouped:
            grouped[group] = {**candidate, "branches": [candidate["branch"]],
                             "paths": list(candidate["paths"]), "ranges": list(candidate["ranges"]),
                             "identifiers": list(candidate["identifiers"])}
            continue
        item = grouped[group]
        if candidate["branch"] not in item["branches"]:
            item["branches"].append(candidate["branch"])
        for field in ("paths", "ranges", "identifiers"):
            item[field].extend(value for value in candidate[field] if value not in item[field])
        if item["branch"] != "observation_conflict":
            item["claim"] = report.splitlines()[line - 1].strip()
    candidates = list(grouped.values())
    # Conflicting observations retain strict precedence. Then round-robin by branch
    # so a large numeric/quote family cannot starve range and missing-pointer rows.
    conflicts = [item for item in candidates if item["branch"] == "observation_conflict"]
    queues = {}
    for item in candidates:
        if item["branch"] != "observation_conflict":
            queues.setdefault(item["branch"], deque()).append(item)
    ranked = list(conflicts)
    while any(queues.values()):
        for queue in queues.values():
            if queue:
                ranked.append(queue.popleft())
    selected = ranked[:limit]
    return {"version": 1, "report_sha256": report_id, "limit": limit,
            "candidate_count": len(candidates), "selected_count": len(selected),
            "deferred_count": max(0, len(candidates) - limit),
            "branch_counts": dict(sorted(Counter(x["branch"] for x in candidates).items())),
            "selected_branch_counts": dict(sorted(Counter(x["branch"] for x in selected).items())),
            "excluded_reason_counts": dict(sorted(excluded.items())), "items": selected}
