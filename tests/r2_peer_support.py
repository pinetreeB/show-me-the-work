"""Shared R2 fixture: a peer agent holds unsettled work on ``peer.py`` and ``src/peer.py``."""

from __future__ import annotations

from pathlib import Path

from core.destructive_guard import evaluate_r2_destructive_gate
from core.ledger import JsonObject, JsonValue, record_event


PEER_PATHS = frozenset({"peer.py", "src/peer.py"})


def healthy(_ledger: JsonObject) -> JsonObject:
    return {"degraded": False, "capacity_exceeded": False}


def peer_lookup(_ledger: JsonObject, canonical_path: str) -> JsonObject | None:
    if canonical_path not in PEER_PATHS:
        return None
    return {
        "generation": 1,
        "status": "exclusive",
        "owners": [{"agent_key": "codex_cli:peer-session:codex", "settled": False}],
    }


def seed_peer_invocation(root: Path) -> None:
    """Record the peer's open invocation so container checks see the peer paths."""
    if (root / "src").exists():
        return
    (root / "src").mkdir()
    peer: JsonObject = {
        "project_root": str(root),
        "host": "codex_cli",
        "session_id": "peer-session",
        "agent": "codex",
        "turn_id": "peer-turn",
        "attribution": "exact",
    }
    _ = record_event(
        peer
        | {
            "event": "prompt",
            "prompt": "peer work",
            "baseline_snapshot_id": "snapshot:base",
            "current_snapshot_id": "snapshot:base",
        }
    )
    _ = record_event(
        peer
        | {
            "event": "invocation",
            "invocation_id": "peer-edit",
            "candidate_paths": sorted(PEER_PATHS),
        }
    )


def r2_gate(root: Path, command: str, **extra: JsonValue) -> dict[str, JsonValue]:
    """Evaluate R2 for our agent (claude) against the seeded peer fixture."""
    seed_peer_invocation(root)
    return evaluate_r2_destructive_gate(
        {
            "project_root": str(root),
            "tool_name": "Bash",
            "command": command,
            "host": "claude_code",
            "session_id": "followup-session",
            "agent": "claude",
            **extra,
        },
        lookup_path_attribution=peer_lookup,
        attribution_health=healthy,
    )
