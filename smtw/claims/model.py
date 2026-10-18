from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter
import time
from typing import Callable


class DeadlineExceeded(Exception):
    pass


class Budget:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.started = clock()
        self.end = self.started + seconds

    def check(self) -> None:
        if self.clock() >= self.end:
            raise DeadlineExceeded

    def remaining(self) -> float:
        self.check()
        return max(0.001, self.end - self.clock())


@dataclass
class Claim:
    report_line: int
    text: str
    check_type: str
    path: str = ""
    value: str = ""
    unit: str = ""
    start: int = 0
    end: int = 0
    quote: str = ""
    quote_code: bool = False
    binding_candidates: tuple[str, ...] = ()
    candidate_ranges: tuple[tuple[str, int, int], ...] = ()
    candidate_quotes: tuple[tuple[str, bool], ...] = ()
    candidate_group_closed: bool = False
    repository_contexts: tuple[str, ...] = ()
    command_exit: int | None = None
    ordinal: bool = False
    context: str = ""
    entity: str = ""
    definition: bool = False
    negated_context: bool = False
    contextual_reason: str = ""
    inherited_binding: bool = False
    malformed_number: bool = False
    author_marked: bool = False
    current: bool = False
    revision: str = ""
    when: str = ""
    verdict: str = "unverifiable"
    reason: str = "근거 미확보"
    reason_code: str = "no_evidence"
    source: dict = field(default_factory=dict)

    @property
    def exempt(self) -> bool:
        return self.author_marked and self.check_type not in {"K1", "K5"}

    def as_dict(self) -> dict:
        return {"report_line": self.report_line, "text": self.text,
                "check_type": self.check_type, "verdict": self.verdict,
                "author_marked": self.author_marked, "exempt": self.exempt,
                "source": self.source, "reason": self.reason, "reason_code": self.reason_code}


@dataclass
class Evidence:
    path: str = ""
    url: str = ""
    command: str = ""
    cwd: str = ""
    tool: str = ""
    origin: str = ""
    order: int = 0
    timestamp: str = ""
    revision: str = ""
    lines: dict[int, str] = field(default_factory=dict)
    text: str = ""
    total_lines: int | None = None
    total_bytes: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    complete: bool = False
    problem: str = ""
    problem_code: str = ""
    aux_path: str = ""
    task_id: str = ""
    conditional_marker: str = ""
    success_marker: str = ""
    exit_code: int | None = None
    view_loaded: bool = False
    measure_loaded: bool = False
    control_only: bool = False
    after_report: bool = False
    unsupported: bool = False
    locations: dict[int, str] = field(default_factory=dict)

    def metadata(self) -> dict:
        return {"path": self.path, "url": self.url, "tool": self.tool,
                "location": self.origin, "command": self.command, "cwd": self.cwd,
                "timestamp": self.timestamp, "revision": self.revision,
                "complete": self.complete, "problem": self.problem,
                "problem_code": self.problem_code,
                "range": [min(self.lines), max(self.lines)] if self.lines else [],
                "total_lines": self.total_lines, "total_bytes": self.total_bytes,
                "aux_path": self.aux_path,
                "conditional_marker": self.conditional_marker, "success_marker": self.success_marker,
                "exit_code": self.exit_code,
                "control_only": self.control_only,
                "search_scope": self.path or self.url or self.command or self.origin}


@dataclass
class Transcript:
    evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    valid: bool = True
    version: str = ""


def line_counts(text: str) -> dict[str, int]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    nl = text.count("\n")
    lines = nl + int(bool(text) and not text.endswith("\n"))
    return {"L_nl": nl, "L_lines": lines,
            "L_view": lines + int(text.endswith("\n"))}


def summarize(claims: list[Claim], *, timed_out: bool, unchecked_count: int,
              warnings: list, input_incomplete: bool = False) -> dict:
    checked = [c for c in claims if c.check_type != "K10"]
    deciding = [c for c in checked if c.verdict != "range_only"]
    failed = any(c.verdict in {"mismatch", "absent"} and not c.exempt and c.check_type != "K8" for c in deciding)
    incomplete = (not deciding or timed_out or input_incomplete
                  or any(c.verdict != "match" for c in deciding))
    status = "FAIL" if failed else "INCOMPLETE" if incomplete else "PASS"
    return {"status": status, "claims": [c.as_dict() for c in checked],
            "timed_out": timed_out, "unchecked_count": unchecked_count,
            "author_marked_review": [c.as_dict() for c in checked
                                      if c.exempt and c.verdict not in {"match", "range_only"}],
            "warnings": [*warnings, *[c.as_dict() for c in checked
                                      if c.check_type == "K8" and c.verdict != "match"]],
            "verdict_reason_counts": {
                verdict: dict(sorted(Counter(c.reason_code for c in checked if c.verdict == verdict).items()))
                for verdict in ("match", "mismatch", "absent", "unverifiable", "range_only")},
            "summary": {v: sum(c.verdict == v for c in checked)
                        for v in ("match", "mismatch", "absent", "unverifiable", "range_only")}}
