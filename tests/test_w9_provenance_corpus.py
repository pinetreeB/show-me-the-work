from __future__ import annotations

from pathlib import Path

from eval.provenance.corpus import golden_cases, randomized_cases
from eval.provenance.fs_runner import run_case
from eval.provenance.replay import replay_case


def test_golden_corpus_has_fixed_positive_negative_and_required_families() -> None:
    cases = golden_cases()

    assert len(cases) == 200
    assert sum(case.positive for case in cases) == 120
    assert sum(not case.positive for case in cases) == 80
    assert {"redirect", "heredoc", "powershell", "python_node", "generator"} <= {
        case.family for case in cases
    }


def test_soft_exclude_candidate_is_confirmed_by_the_actual_filesystem_runner(tmp_path: Path) -> None:
    case = next(case for case in golden_cases() if case.force_candidate and case.positive)

    result = run_case(tmp_path, case)

    assert result.expected == result.observed
    assert result.false_positive is False


def test_soft_exclude_candidate_revert_does_not_leave_a_pending_create(tmp_path: Path) -> None:
    case = next(case for case in golden_cases() if case.case_id == "negative-077")

    result = run_case(tmp_path, case)

    assert result.expected == result.observed == ()
    assert result.pending == ()


def test_adapter_replay_and_randomized_cases_are_deterministic(tmp_path: Path) -> None:
    case = golden_cases()[0]

    replay = replay_case(tmp_path, case)

    assert replay.matched is True
    assert randomized_cases(8, 20260712) == randomized_cases(8, 20260712)


def test_adapter_replay_preserves_an_absolute_move_command_semantics(tmp_path: Path) -> None:
    case = next(case for case in golden_cases() if case.case_id == "positive-008")

    replay = replay_case(tmp_path, case)

    assert replay.matched is True
