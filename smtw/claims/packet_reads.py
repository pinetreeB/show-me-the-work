"""Assist-only shell provenance. Never imported by the checker."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
import json
import shlex

from .model import Budget, Evidence, line_counts
from .paths import normalize_path
from .transcripts import Call, FILE_TOKEN, call_paths, js_calls, observed_path, parse_result, unwrapped


def git_file_read(command: str, cwd: str = "") -> tuple[str, str, str, bool] | None:
    """One fixed Git blob, optionally followed only by display-only stages.

    Allowed tails: 2>&1, | Out-String, | Select-Object -First N.
    No other options, operands, expressions, or commands are accepted.
    """
    from .packets import hard_excluded
    if any(char in command for char in "\r\n\x00"):
        return None
    literal = r"""(?:"[^"\r\n]+"|'[^'\r\n]+'|[^\s"'|&<>;]+)"""
    match = re.fullmatch(
        rf"\s*git(?:\s+-C\s+(?P<root>{literal}))?\s+show\s+(?P<blob>{literal})"
        r"(?P<tail>(?:\s+2>&1|\s*\|\s*(?:Out-String|Select-Object\s+-First\s+[1-9]\d*))*)\s*",
        command, re.I)
    if not match:
        return None
    unquote = lambda value: value[1:-1] if value[:1] in {"'", '"'} else value
    blob = unquote(match["blob"])
    parsed = re.fullmatch(r"([0-9a-fA-F]{7,40}):(.+)", blob)
    if not parsed:
        return None
    revision, path = parsed.groups()
    root = unquote(match["root"]) if match["root"] else ""
    operands = [value for value in (root, path) if value]
    if any(any(char in value for char in ";&|\r\n$<>(){}#^*?[]%," + chr(96))
           or "'" in value or '"' in value for value in operands):
        return None
    if not FILE_TOKEN.fullmatch(path):
        return None
    effective_cwd = observed_path(root, cwd) if root else cwd
    tokens = ["git", "show", revision, *(["-C"] if root else []), *operands, *re.findall(r"[\w-]+", match["tail"])]
    if any(hard_excluded(token) for token in tokens) or hard_excluded(observed_path(path, effective_cwd)):
        return None
    return path, revision.lower(), effective_cwd, bool(re.search(r"\bSelect-Object\b", match["tail"], re.I))


def simple_read_path(command: str, cwd: str = "") -> str:
    """Recognize a closed single-file read grammar, never execute shell text."""
    from .packets import hard_excluded
    git_read = git_file_read(command, cwd)
    if git_read:
        return git_read[0]
    # A closed count-only wrapper still has exactly one literal read operand.
    measured = re.fullmatch(r"\s*@?\((Get-Content\s+[^()\r\n]+)\)\.(?:Count|Length)\s*", command, re.I)
    if measured:
        return simple_read_path(measured.group(1), cwd)
    # Shell syntax is rejected even inside quotes: no substitutions or wrappers.
    if not command or any(char in command for char in ";&|\r\n$<>(){}#^\x00" + chr(96)):
        return ""
    try:
        lexer = shlex.shlex(command, posix=False)
        lexer.whitespace_split = True
        lexer.commenters = ""
        raw = list(lexer)
    except ValueError:
        return ""
    tokens = []
    for token in raw:
        if token[:1] in {"'", '"'}:
            if len(token) < 2 or token[-1] != token[0]:
                return ""
            token = token[1:-1]
        if (not token or any(char in token for char in "'\"$*?[]," + chr(96))
                or "%" in token and token != "%s"):
            return ""
        tokens.append(token)
    if not tokens or any(hard_excluded(token) for token in tokens):
        return ""
    name, args = tokens[0].casefold(), tokens[1:]
    positional = []
    index = 0
    while index < len(args):
        token = args[index]
        lower = token.casefold()
        if token == "--" and name in {"cat", "wc", "head", "tail"}:
            positional.extend(args[index + 1:])
            break
        if name == "get-content" and lower in {"-literalpath", "-path"}:
            index += 1
            if index >= len(args) or args[index].startswith("-"):
                return ""
            positional.append(args[index])
        elif name == "get-content" and lower == "-raw":
            pass
        elif name == "stat" and lower == "-c":
            index += 1
            if index >= len(args) or args[index] != "%s":
                return ""
        elif name == "wc" and lower in {"-l", "-c"}:
            pass
        elif name in {"head", "tail"} and lower == "-n":
            index += 1
            if index >= len(args) or not args[index].isdigit():
                return ""
        elif token.startswith("-"):
            return ""
        else:
            positional.append(token)
        index += 1
    if name not in {"cat", "type", "get-content", "wc", "head", "tail", "stat"} or len(positional) != 1:
        return ""
    path = positional[0]
    if not FILE_TOKEN.fullmatch(path) or hard_excluded(observed_path(path, cwd)):
        return ""
    return path



def parse_packet_result(call: Call, body: str, origin: str, root: Path,
                        budget: Budget, exit_code: int | None = None) -> list[Evidence]:
    """Reject ambiguous output before the shared parser can bind it to a file."""
    if not call.command:
        return parse_result(call, body, origin, root, budget, exit_code)
    path = simple_read_path(call.command, call.cwd)
    if not path:
        from .packets import hard_excluded
        # Preserve the rejection category for a single excluded literal path;
        # this control record contains no output and cannot become an excerpt.
        candidates = call_paths(call)
        excluded = (candidates[0] if len(candidates) == 1
                    and hard_excluded(observed_path(candidates[0], call.cwd)) else "")
        return [Evidence(path=excluded, command=call.command, tool=call.name, origin=origin,
                         order=call.order, timestamp=call.timestamp, control_only=True,
                         problem="패킷 단일 읽기 출처 미확정", problem_code="binding_ambiguous")]
    # Shell output notices cannot authorize reading another tool step's file.
    if re.search(r"(?:saved to:|Task logs are available at:|steps[/\\]\d+[/\\].*output\.txt)", body, re.I):
        return [Evidence(command=call.command, tool=call.name, origin=origin,
                         order=call.order, control_only=True,
                         problem="셸 보조 포인터는 패킷 출처 아님", problem_code="pointer_unbound")]
    observations = parse_result(call, body, origin, root, budget, exit_code)
    git_read = git_file_read(call.command, call.cwd)
    if git_read:
        path, revision, cwd, partial = git_read
        result = []
        for e in observations:
            complete = not partial and not e.problem and not e.aux_path
            result.append(replace(
                e, path=observed_path(path, cwd), cwd=cwd, revision=revision,
                text=body, lines=dict(enumerate(body.splitlines(), 1)),
                complete=complete, counts=line_counts(body) if complete else {},
                total_lines=line_counts(body)["L_view"] if complete else None,
                total_bytes=None))
        return result
    expected = normalize_path(observed_path(path, call.cwd))
    result = []
    for e in observations:
        if normalize_path(observed_path(e.path, call.cwd)) != expected:
            continue
        # The legacy check adapter intentionally retains its historical type
        # handling; only packet reads expose this safe single-file output.
        if re.match(r"(?i)^\s*type\s+", call.command) and not e.problem and not e.aux_path:
            e = replace(e, text=body, lines=dict(enumerate(body.splitlines(), 1)),
                        complete=True, counts=line_counts(body),
                        total_lines=line_counts(body)["L_view"])
        result.append(e)
    return result


class PacketResultParser:
    """Validate attribution groups before any output becomes packet evidence.

    The shared check adapter remains unchanged. Preflight sees the entire
    transcript, so an extra result or an unfinished final FIFO invalidates
    earlier shell observations in that group as well.
    """

    def __init__(self, path: Path, kind: str, budget: Budget):
        self.kind = kind
        self.allowed = set()
        groups = []
        current = None
        codex = {}
        malformed = False
        with path.open("rb") as stream:
            for number, raw in enumerate(stream, 1):
                budget.check()
                try:
                    row = json.loads(raw)
                    if not isinstance(row, dict):
                        raise ValueError("invalid row")
                except (ValueError, UnicodeError):
                    malformed = True
                    continue
                if kind == "agy":
                    typ = row.get("type")
                    if typ == "PLANNER_RESPONSE":
                        tools = row.get("tool_calls", [])
                        current = {"order": number, "count": len(tools) if isinstance(tools, list) else 0,
                                   "results": [], "bad": not isinstance(tools, list)}
                        groups.append(current)
                    elif current is not None:
                        if typ == "GENERIC":
                            current["results"].append(number)
                        elif len(current["results"]) < current["count"]:
                            current["bad"] = True
                    continue
                if row.get("type") != "response_item" or not isinstance(row.get("payload"), dict):
                    continue
                item = row["payload"]
                ident = str(item.get("call_id", ""))
                typ = item.get("type")
                if typ in {"function_call", "custom_tool_call"}:
                    args = item.get("arguments", item.get("input", {}))
                    code_mode = str(item.get("name", "")).rsplit(".", 1)[-1] == "exec"
                    count, bad = 1, False
                    if code_mode:
                        if not isinstance(args, str):
                            count, bad = 0, True
                        else:
                            count = len(js_calls(args, number, ""))
                            occurrences = len(re.findall(
                                r"\btools\s*(?:\.\s*[\w$]+|\[[^\]]+\])\s*\(", args))
                            bad = not count or occurrences != count
                    if ident in codex:
                        codex[ident]["bad"] = True
                        bad = True
                    current = {"order": number, "count": count, "results": [],
                               "bad": bad or not ident}
                    groups.append(current)
                    codex[ident] = current
                elif typ in {"function_call_output", "custom_tool_call_output"}:
                    if ident in codex:
                        codex[ident]["results"].append((number, unwrapped(item.get("output", ""))))
        if malformed:
            return
        for group in groups:
            budget.check()
            if group["bad"] or not group["count"]:
                continue
            if kind == "agy":
                if len(group["results"]) == group["count"]:
                    self.allowed.update((group["order"], row) for row in group["results"])
                continue
            # One terminal output row per call ID. Duplicate rows cannot safely
            # share the adapter's pop-on-first-result fallback.
            if len(group["results"]) != 1:
                continue
            row, outputs = group["results"][0]
            if row <= group["order"] or not outputs:
                continue
            indices = [index for _, _, index in outputs]
            if all(index is None for index in indices):
                aligned = len(outputs) == group["count"]
            else:
                aligned = (all(type(index) is int and 0 <= index < group["count"] for index in indices)
                           and len(set(indices)) == len(indices))
            if aligned:
                self.allowed.add((group["order"], row))

    def allow_codex_output(self, calls, row):
        return bool(calls and (calls[0].order, row) in self.allowed)

    def __call__(self, call, body, origin, root, budget, exit_code=None):
        row = int(origin.rsplit(":", 1)[1])
        if (self.kind != "agy" or call.command) and (call.order, row) not in self.allowed:
            return [Evidence(command=call.command, tool=call.name, origin=origin,
                             order=call.order, control_only=True,
                             problem="출력과 호출의 일대일 귀속 미확정", problem_code="output_binding")]
        return parse_packet_result(call, body, origin, root, budget, exit_code)
