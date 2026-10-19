"""Assist-only packet provenance. Never imported by the checker.

Packet excerpts come only from results that identify their own source:
an AGY file-read result whose leading ``File Path:`` header names the same
file as the call argument, with line numbers consistent with the requested
range. Shell and code-execution output is never packet evidence, whatever
its command grammar or output count; attributing such output to a call is
not decidable from the transcript. Fixed-commit blobs are read directly by
``packets.fixed_sources`` and never taken from transcript output.
"""
from __future__ import annotations

from pathlib import Path
import re

from .model import Budget, Evidence
from .paths import normalize_path
from .transcripts import NUMBERED, Call, call_paths, observed_path, parse_result

# AGY file-read tools whose runtime result starts with a ``File Path:`` header.
FILE_READ_TOOLS = frozenset({"view_file", "read_file", "view_file_outline"})


def _control(call: Call, origin: str, problem: str, code: str, path: str = "") -> Evidence:
    """Metadata only: no output text, lines, counts, or pointers."""
    return Evidence(path=path, command=call.command, tool=call.name, origin=origin,
                    order=call.order, timestamp=call.timestamp, control_only=True,
                    problem=problem, problem_code=code)


def _excluded_literal(call: Call) -> str:
    """Keep the hard-exclusion category for a single excluded literal path."""
    from .packets import hard_excluded
    candidates = call_paths(call)
    return (candidates[0] if len(candidates) == 1
            and hard_excluded(observed_path(candidates[0], call.cwd)) else "")


def _line_number(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\s*\d+\s*", value):
        return int(value)
    return None


def self_identified_read(call: Call, body: str, e: Evidence) -> bool:
    """True only when the result itself names the requested file and range."""
    expected = normalize_path(observed_path(call.path, call.cwd)) if call.path else ""
    if not expected or e.aux_path or e.problem_code == "binding_ambiguous" or not e.lines:
        return False
    rows = body.splitlines()
    first_numbered = next((i for i, row in enumerate(rows) if NUMBERED.match(row)), len(rows))
    headers = [i for i, row in enumerate(rows) if re.match(r"File Path:", row)]
    # Exactly one header, placed in the result preamble before any source line.
    if len(headers) != 1 or headers[0] >= first_numbered:
        return False
    actual = re.match(r"File Path:\s*`?([^\r\n`]+)", rows[headers[0]])
    if not actual or normalize_path(actual.group(1).strip()) != expected:
        return False
    if normalize_path(observed_path(e.path, call.cwd)) != expected:
        return False
    keys = sorted(e.lines)
    if keys != list(range(keys[0], keys[0] + len(keys))):
        return False
    shown = [re.match(r"Showing lines (\d+) to (\d+)\s*$", row) for row in rows[:first_numbered]]
    shown = [m for m in shown if m]
    if len(shown) > 1:
        return False
    start = end = None
    if shown:
        start, end = int(shown[0].group(1)), int(shown[0].group(2))
        if keys[0] != start or keys[-1] > end:
            return False
    requested_start = requested_end = None
    if "StartLine" in call.args:
        requested_start = _line_number(call.args["StartLine"])
        if requested_start is None:
            return False
    if "EndLine" in call.args:
        requested_end = _line_number(call.args["EndLine"])
        if requested_end is None:
            return False
    # An unranged read starts at the first line; a ranged read at its start.
    if keys[0] != (requested_start if requested_start is not None else 1):
        return False
    if requested_end is not None and (keys[-1] > requested_end or end is not None and end > requested_end):
        return False
    return True


def parse_packet_result(call: Call, body: str, origin: str, root: Path,
                        budget: Budget, exit_code: int | None = None, *, kind: str = "agy") -> list[Evidence]:
    """Packet-only selection over the shared parser; the checker never calls this."""
    if call.command:
        return [_control(call, origin, "셸·코드 실행 출력은 패킷 근거 아님", "shell_output_excluded",
                         _excluded_literal(call))]
    if kind != "agy":
        # Codex results (including code-mode tool calls) carry no runtime
        # self-identification; a script may print any header it likes.
        return [_control(call, origin, "자기 식별 없는 도구 결과는 패킷 근거 아님", "unidentified_result")]
    result = []
    for e in parse_result(call, body, origin, root, budget, exit_code):
        if e.url or e.url_content_path:
            # Web visits stay with the existing pointer rule and are never
            # excerpt sources; content.md enters only through a matching view.
            result.append(e)
        elif call.name in FILE_READ_TOOLS and self_identified_read(call, body, e):
            result.append(e)
        else:
            result.append(_control(call, origin, "결과 머리의 파일 경로·범위가 호출과 불일치", "unidentified_result"))
    return result


class PacketResultParser:
    """Packet result hook for ``read_transcript``; the checker passes none."""

    def __init__(self, path: Path, kind: str, budget: Budget):
        self.kind = kind

    def allow_codex_output(self, calls, row):
        # Codex output never becomes packet evidence, so it is not parsed at
        # all here (the shared adapter's output-index fallback stays unused).
        return False

    def __call__(self, call, body, origin, root, budget, exit_code=None):
        return parse_packet_result(call, body, origin, root, budget, exit_code, kind=self.kind)
