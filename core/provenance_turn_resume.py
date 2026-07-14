from __future__ import annotations

from pathlib import Path

from .provenance_lifecycle_types import TurnState
from .provenance_store import load_turn_baseline
from .provenance_types import ProvenanceReason, ProvenanceStatus


class MissingTurnBaselineError(KeyError):
    agent: str
    turn_id: str

    def __init__(self, agent: str, turn_id: str) -> None:
        super().__init__((agent, turn_id))
        self.agent = agent
        self.turn_id = turn_id


class TurnBootstrapError(RuntimeError):
    status: ProvenanceStatus
    reason: ProvenanceReason
    incomplete: bool

    def __init__(
        self,
        status: ProvenanceStatus,
        reason: ProvenanceReason,
        incomplete: bool,
    ) -> None:
        super().__init__((status.value, reason.value))
        self.status = status
        self.reason = reason
        self.incomplete = incomplete


def load_resumed_turn(
    root: Path,
    agent: str,
    turn_id: str,
    event_seq: int,
    mutation_capable: bool,
) -> TurnState:
    baseline = load_turn_baseline(root, agent, turn_id)
    if baseline is None:
        raise MissingTurnBaselineError(agent, turn_id)
    return TurnState(agent, turn_id, baseline, event_seq, mutation_capable)
