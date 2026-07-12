from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
import time

from .corpus import golden_cases, randomized_cases
from .fs_runner import run_case
from .models import CaseResult, CorpusCase, ReplayResult
from .replay import replay_case


@dataclass(frozen=True, slots=True)
class SuiteResult:
    golden: tuple[CaseResult, ...]
    git: tuple[CaseResult, ...]
    replay: tuple[ReplayResult, ...]
    randomized: tuple[CaseResult, ...]
    git_mismatches: tuple[str, ...]
    elapsed_seconds: float

    @property
    def false_negatives(self) -> int:
        return sum(result.expected != result.observed or result.incomplete for result in self.golden if result.positive)

    @property
    def false_positives(self) -> int:
        return sum(result.false_positive for result in self.golden if not result.positive)

    @property
    def replay_failures(self) -> int:
        return sum(not result.matched for result in self.replay)


def run_suite(randomized: int, seed: int) -> SuiteResult:
    started = time.perf_counter()
    cases = golden_cases()
    with tempfile.TemporaryDirectory(prefix="fable-provenance-") as temporary:
        root = Path(temporary)
        non_git = _run_cases(root / "non-git", cases, False)
        git = _run_cases(root / "git", cases, True)
        replay = tuple(replay_case(root / "replay" / case.case_id, case) for case in cases)
        random_cases = randomized_cases(randomized, seed)
        random_results = _run_cases(root / "randomized", random_cases, False)
    return SuiteResult(non_git, git, replay, random_results, _mismatches(non_git, git), time.perf_counter() - started)


def _run_cases(root: Path, cases: tuple[CorpusCase, ...], with_git: bool) -> tuple[CaseResult, ...]:
    root.mkdir(parents=True, exist_ok=True)
    if with_git:
        subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True, text=True)
    results: list[CaseResult] = []
    for case in cases:
        case_root = root / case.case_id
        case_root.mkdir(parents=True, exist_ok=True)
        results.append(run_case(case_root, case))
    return tuple(results)


def _mismatches(non_git: tuple[CaseResult, ...], git: tuple[CaseResult, ...]) -> tuple[str, ...]:
    compared = zip(non_git, git, strict=True)
    return tuple(
        plain.case_id
        for plain, repository in compared
        if (plain.expected, plain.observed, plain.pending) != (repository.expected, repository.observed, repository.pending)
    )
