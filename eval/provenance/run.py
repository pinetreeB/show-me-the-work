from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from typing import Final

from .models import CaseResult
from .suite import SuiteResult, run_suite


DEFAULT_OUTPUT: Final = Path(__file__).resolve().parents[1] / "results" / "provenance-latest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the v2 provenance accuracy corpus.")
    parser.add_argument("--randomized", type=int, default=0)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    options = parser.parse_args(argv)
    result = run_suite(options.randomized, options.seed)
    encoded = _receipt_json(result, options.randomized, options.seed)
    _atomic_write(options.output, encoded)
    print(_summary_line(result))
    return 0 if _passed(result) else 1


def _receipt_json(result: SuiteResult, randomized: int, seed: int) -> str:
    positive = sum(item.positive for item in result.golden)
    negative = len(result.golden) - positive
    source_correct = sum(
        _source_matches(item) for item in result.golden if item.positive and item.expected
    )
    source_total = sum(1 for item in result.golden if item.positive and item.expected)
    high_confidence_misattributions = sum(
        not _source_matches(item) and item.source_expected in {"edit", "shell", "external"}
        for item in result.golden
        if item.positive and item.expected
    )
    noncompetitive_external = sum(
        item.source_expected != "external" and "external" in item.sources
        for item in result.golden
        if item.positive and item.expected
    )
    parser_total = sum(1 for item in result.golden if item.positive)
    parser_recalled = sum(item.parser_recalled for item in result.golden if item.positive)
    payload = {
        "schema_version": 1,
        "platform": os.name,
        "elapsed_seconds": result.elapsed_seconds,
        "golden": {
            "cases": len(result.golden),
            "positive": positive,
            "negative": negative,
            "false_negatives": result.false_negatives,
            "false_positives": result.false_positives,
        },
        "git_non_git": {"mismatches": list(result.git_mismatches)},
        "canonical_replay": {
            "cases": len(result.replay),
            "failures": result.replay_failures,
            "antigravity": "payload_injection_not_live_hook",
        },
        "reference_metrics": {
            "source_accuracy": source_correct / source_total if source_total else 1.0,
            "parser_recall": parser_recalled / parser_total if parser_total else 1.0,
            "high_confidence_misattributions": high_confidence_misattributions,
            "noncompetitive_external_unknown_rate": noncompetitive_external / source_total if source_total else 0.0,
        },
        "randomized": {
            "seed": seed,
            "cases": randomized,
            "false_negatives": sum(
                item.expected != item.observed or item.incomplete
                for item in result.randomized
                if item.positive
            ),
            "false_positives": sum(item.false_positive for item in result.randomized if not item.positive),
        },
        "hard_gate": {"passed": _passed(result)},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _source_matches(result: CaseResult) -> bool:
    return result.source_expected in result.sources


def _passed(result: SuiteResult) -> bool:
    return (
        result.false_negatives == 0
        and result.false_positives == 0
        and not result.git_mismatches
        and result.replay_failures == 0
    )


def _summary_line(result: SuiteResult) -> str:
    return (
        "provenance golden="
        f"{len(result.golden)} fn={result.false_negatives} fp={result.false_positives} "
        f"git_mismatch={len(result.git_mismatches)} replay_failures={result.replay_failures} "
        f"elapsed={result.elapsed_seconds:.2f}s"
    )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False, dir=path.parent) as handle:
        _ = handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main())
