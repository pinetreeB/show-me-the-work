from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
from typing import Protocol, cast

from core.design_gate_state import design_lint_scopes, record_design_result
from core.design_lint import DesignLintResult, DesignViolation, run_design_lint


class DesignCheckArgs(Protocol):
    root: str | None
    agent: str | None


def run_design_check(args: Namespace) -> int:
    values = cast(DesignCheckArgs, cast(object, args))
    root = Path(values.root or Path.cwd()).resolve()
    agent = values.agent or ""
    scopes = design_lint_scopes(root, agent)
    if not scopes:
        result = run_design_lint(root)
    else:
        results = tuple(
            run_design_lint(root, list(scope.paths), scope.baseline)
            for scope in scopes
        )
        for scope, scoped_result in zip(scopes, results, strict=True):
            _ = record_design_result(
                root,
                agent,
                scoped_result,
                expected_change_seq=scope.change_seq,
                turn_key=scope.turn_key,
            )
        result = _merge_results(results)
    print(json.dumps(result.to_json(), ensure_ascii=False, sort_keys=True))
    return 0 if result.passed else 1


def _merge_results(results: tuple[DesignLintResult, ...]) -> DesignLintResult:
    violations: dict[tuple[str, int, str], DesignViolation] = {}
    checked: set[str] = set()
    for result in results:
        checked.update(result.checked_files)
        for violation in result.violations:
            violations[(violation.file, violation.line, violation.rule_id)] = violation
    return DesignLintResult(tuple(violations.values()), tuple(sorted(checked)))
