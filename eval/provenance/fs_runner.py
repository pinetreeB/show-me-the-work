from __future__ import annotations

from pathlib import Path
from typing import assert_never

from core.provenance_lifecycle import ProvenanceLifecycle
from core.provenance_lifecycle_types import ObservationResult, ObservedChange
from core.shell_hints import shell_candidate_paths

from .actions import ActionLayout, cleanup, command_for, execute, prepare
from .models import CaseResult, CorpusCase, Origin, Signature
from .oracle import delta, snapshot


def run_case(root: Path, case: CorpusCase) -> CaseResult:
    root.mkdir(parents=True, exist_ok=True)
    layout = prepare(root, case)
    command = command_for(case, layout)
    candidates = _candidates(case, command)
    before = snapshot(root, frozenset(candidates))
    lifecycle = ProvenanceLifecycle(root)
    try:
        results = _observe(lifecycle, case, layout, tuple(candidates))
        after = snapshot(root, frozenset(candidates))
        expected = delta(before, after)
        observed = _observed(lifecycle.changes)
        pending = tuple(sorted({item for result in results for item in result.pending_change_ids}))
        recalled = _parser_recalled(case, candidates, expected)
        return CaseResult(
            case.case_id,
            case.positive,
            expected,
            observed,
            pending,
            _expected_source(case.origin),
            tuple(sorted({change.source for change in lifecycle.changes})),
            recalled,
            any(result.incomplete for result in results),
        )
    finally:
        cleanup(layout)


def _observe(
    lifecycle: ProvenanceLifecycle,
    case: CorpusCase,
    layout: ActionLayout,
    candidates: tuple[str, ...],
) -> tuple[ObservationResult, ...]:
    match case.origin:
        case Origin.OVERLAP:
            return _observe_overlap(lifecycle, case, layout, candidates)
        case Origin.EDIT | Origin.SHELL | Origin.GENERATED | Origin.EXTERNAL:
            return _observe_one(lifecycle, case, layout, candidates)
        case unreachable:
            assert_never(unreachable)


def _observe_one(
    lifecycle: ProvenanceLifecycle,
    case: CorpusCase,
    layout: ActionLayout,
    candidates: tuple[str, ...],
) -> tuple[ObservationResult, ...]:
    agent = "corpus-agent"
    turn = case.case_id
    _ = lifecycle.start_turn(agent, turn, True)
    invocation = lifecycle.begin_invocation(agent, turn, f"{case.case_id}:one", candidates)
    execute(layout, case)
    post = lifecycle.post_tool(invocation, _source(case.origin))
    stop = lifecycle.finish_turn(agent, turn)
    return post, stop


def _observe_overlap(
    lifecycle: ProvenanceLifecycle,
    case: CorpusCase,
    layout: ActionLayout,
    candidates: tuple[str, ...],
) -> tuple[ObservationResult, ...]:
    first_agent = "corpus-agent-a"
    second_agent = "corpus-agent-b"
    first_turn = f"{case.case_id}:a"
    second_turn = f"{case.case_id}:b"
    _ = lifecycle.start_turn(first_agent, first_turn, True)
    _ = lifecycle.start_turn(second_agent, second_turn, True)
    first = lifecycle.begin_invocation(first_agent, first_turn, f"{case.case_id}:a", candidates)
    second = lifecycle.begin_invocation(second_agent, second_turn, f"{case.case_id}:b", candidates)
    execute(layout, case)
    first_post = lifecycle.post_tool(first, "shell")
    second_post = lifecycle.post_tool(second, "shell")
    first_stop = lifecycle.finish_turn(first_agent, first_turn)
    second_stop = lifecycle.finish_turn(second_agent, second_turn)
    return first_post, second_post, first_stop, second_stop


def _candidates(case: CorpusCase, command: str) -> tuple[str, ...]:
    match case.origin:
        case Origin.EDIT:
            return (case.target,)
        case Origin.SHELL | Origin.GENERATED | Origin.OVERLAP:
            return shell_candidate_paths(command)
        case Origin.EXTERNAL:
            return ()
        case unreachable:
            assert_never(unreachable)


def _source(origin: Origin) -> str:
    match origin:
        case Origin.EDIT:
            return "edit"
        case Origin.SHELL | Origin.GENERATED | Origin.OVERLAP:
            return "shell"
        case Origin.EXTERNAL:
            return "external"
        case unreachable:
            assert_never(unreachable)


def _expected_source(origin: Origin) -> str:
    match origin:
        case Origin.EDIT:
            return "edit"
        case Origin.SHELL:
            return "shell"
        case Origin.GENERATED:
            return "generated"
        case Origin.EXTERNAL | Origin.OVERLAP:
            return "external"
        case unreachable:
            assert_never(unreachable)


def _observed(changes: tuple[ObservedChange, ...]) -> tuple[Signature, ...]:
    return tuple(sorted(Signature(change.path, change.op.value, change.after_digest) for change in changes))


def _parser_recalled(case: CorpusCase, candidates: tuple[str, ...], expected: tuple[Signature, ...]) -> bool:
    if case.origin is Origin.EDIT:
        return True
    if case.origin is Origin.EXTERNAL:
        return False
    paths = {signature.path for signature in expected}
    return bool(paths & set(candidates))
