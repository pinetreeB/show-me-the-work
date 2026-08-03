from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.contract import (
    evaluate_pretool_contract,
    namespaced_contract_path,
    record_contract_authored_event,
)
from core.gate_counters import needs_goals_block, recover_checkpoint_gates
from core.ledger import JsonObject, load_ledger, record_event
from core.scorecard import GateAction
from core.scorecard_store import load_scorecard_journal
from core.state_layout import state_dir
from core.verify_state import evaluate_stop


def _identity(root: Path, suffix: str) -> JsonObject:
    return {
        "project_root": str(root),
        "host": "codex_cli",
        "session_id": f"{suffix}-session",
        "agent": "codex",
        "turn_id": f"{suffix}-turn",
        "attribution": "exact",
        "identity_synthetic": False,
    }


def _seed_goals_turn(root: Path, suffix: str) -> JsonObject:
    identity = _identity(root, suffix)
    _ = record_event(
        identity
        | {
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "implement gate preflight stories",
            "needs_goals": True,
        }
    )
    return identity


def _seed_contract_turn(root: Path) -> JsonObject:
    identity = _identity(root, "contract")
    _ = record_event(
        identity
        | {
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "implement a high risk migration",
        }
    )
    return identity


def _shell_payload(identity: JsonObject, command: str) -> JsonObject:
    return identity | {"tool_name": "Bash", "command": command}


def _high_risk_payload(identity: JsonObject) -> JsonObject:
    return identity | {
        "tool_name": "Edit",
        "file_paths": ["migrations/001_init.sql"],
        "prompt": "DB migration change",
    }


def _identity_key(identity: JsonObject) -> str:
    return ":".join(
        str(identity[field]) for field in ("host", "session_id", "agent")
    )


def _turn_state(identity: JsonObject) -> JsonObject:
    ledger = load_ledger(identity)
    turns = ledger.get("active_turns")
    assert isinstance(turns, dict)
    turn = turns.get(_identity_key(identity))
    assert isinstance(turn, dict)
    return turn


def _write_goals_checkpoint(root: Path, identity: JsonObject) -> Path:
    filename = namespaced_contract_path(str(root), _identity_key(identity)).name
    path = state_dir(root) / "goals" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text("{}\n", encoding="utf-8")
    return path


def _valid_contract_body(evidence: str = "migration test passed") -> JsonObject:
    return {
        "restated_goal": "apply the migration safely",
        "acceptance": ["migration test passes"],
        "evidence": [evidence],
    }


def _write_contract_checkpoint(root: Path, identity: JsonObject) -> Path:
    path = namespaced_contract_path(str(root), _identity_key(identity))
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(_valid_contract_body()), encoding="utf-8")
    record_contract_authored_event(
        identity
        | {
            "tool_name": "Write",
            "file_paths": [str(path)],
        }
    )
    return path


def _gate_actions(root: Path) -> list[GateAction]:
    return [
        transition.action
        for transition in load_scorecard_journal(root).transitions
    ]


def _record_change(identity: JsonObject) -> None:
    _ = record_event(
        identity | {"event": "change", "path": "app.py", "kind": "code"}
    )


def test_goals_recovery_does_not_latch_after_checkpoint_deletion(
    tmp_path: Path,
) -> None:
    identity = _seed_goals_turn(tmp_path, "goals-delete")
    payload = identity | {"tool_name": "Edit", "file_paths": ["app.py"]}
    first = evaluate_pretool_contract(payload)
    checkpoint = _write_goals_checkpoint(tmp_path, identity)
    recovered = evaluate_pretool_contract(payload)
    checkpoint.unlink()

    blocked_again = evaluate_pretool_contract(payload)

    assert first["decision"] == "block"
    assert recovered["decision"] == "allow"
    assert blocked_again["decision"] == "block"
    assert _turn_state(identity)["goals_blocks"] == 2
    assert _gate_actions(tmp_path) == [
        GateAction.BLOCK,
        GateAction.RECOVER,
        GateAction.BLOCK,
    ]


def test_contract_recovery_does_not_latch_after_digest_change(
    tmp_path: Path,
) -> None:
    identity = _seed_contract_turn(tmp_path)
    payload = _high_risk_payload(identity)
    first = evaluate_pretool_contract(payload)
    checkpoint = _write_contract_checkpoint(tmp_path, identity)
    recovered = evaluate_pretool_contract(payload)
    _ = checkpoint.write_text(
        json.dumps(_valid_contract_body("different evidence")),
        encoding="utf-8",
    )

    blocked_again = evaluate_pretool_contract(payload)

    assert first["decision"] == "block"
    assert recovered["decision"] == "allow"
    assert blocked_again["decision"] == "block"
    assert _gate_actions(tmp_path) == [
        GateAction.BLOCK,
        GateAction.RECOVER,
        GateAction.BLOCK,
    ]


def test_recover_transition_never_substitutes_for_live_checkpoint_validation(
    tmp_path: Path,
) -> None:
    identity = _seed_goals_turn(tmp_path, "recover-live")
    payload = identity | {"tool_name": "Edit", "file_paths": ["app.py"]}
    assert evaluate_pretool_contract(payload)["decision"] == "block"
    checkpoint = _write_goals_checkpoint(tmp_path, identity)
    recover_checkpoint_gates(payload)
    assert _gate_actions(tmp_path) == [GateAction.BLOCK, GateAction.RECOVER]
    checkpoint.unlink()

    recover_checkpoint_gates(payload)

    assert needs_goals_block(payload) is True
    assert _gate_actions(tmp_path) == [GateAction.BLOCK, GateAction.RECOVER]


def test_proven_read_only_shell_skips_goals_without_consuming_block_counter(
    tmp_path: Path,
) -> None:
    identity = _seed_goals_turn(tmp_path, "read-only")

    decision = evaluate_pretool_contract(_shell_payload(identity, "cat README.md"))

    assert decision["decision"] == "allow"
    assert _turn_state(identity)["goals_blocks"] == 0
    assert needs_goals_block(identity) is True
    assert _gate_actions(tmp_path) == []


def test_read_only_exception_requires_every_segment_to_be_proven_read_only(
    tmp_path: Path,
) -> None:
    safe_identity = _seed_goals_turn(tmp_path / "safe", "safe-pipeline")
    mixed_identity = _seed_goals_turn(tmp_path / "mixed", "mixed-pipeline")

    safe = evaluate_pretool_contract(
        _shell_payload(
            safe_identity,
            "cat README.md | rg --no-config -n friction README.md",
        )
    )
    mixed = evaluate_pretool_contract(
        _shell_payload(
            mixed_identity,
            "cat README.md | python -m pytest",
        )
    )

    assert safe["decision"] == "allow"
    assert _turn_state(safe_identity)["goals_blocks"] == 0
    assert mixed["decision"] == "block"
    assert _turn_state(mixed_identity)["goals_blocks"] == 1


@pytest.mark.parametrize(
    "command",
    (
        'cmd.exe /c "echo hello > marker.txt"',
        'pwsh -NonInteractive -Command "Get-Content README.md"',
        r"pwsh -NoProfile -NonInteractive -File .\script.ps1",
        'pwsh -NoProfile -NonInteractive -Command "& .\\script.ps1"',
    ),
)
def test_read_only_exception_rejects_redirection_profile_script_and_dynamic_wrapper(
    tmp_path: Path,
    command: str,
) -> None:
    identity = _seed_goals_turn(tmp_path, "unsafe-wrapper")

    decision = evaluate_pretool_contract(_shell_payload(identity, command))

    assert decision["decision"] == "block"
    assert _turn_state(identity)["goals_blocks"] == 1


def test_pretool_hint_lists_future_gates_without_recording_recover(
    tmp_path: Path,
) -> None:
    identity = _seed_goals_turn(tmp_path, "preflight")

    decision = evaluate_pretool_contract(_high_risk_payload(identity))

    assert decision["decision"] == "block"
    assert decision.get("preflight_requirements") == [
        "goals",
        "contract",
        "verification",
        "stop",
    ]
    reason = str(decision.get("reason", ""))
    assert "turn preflight" in reason
    assert all(
        requirement in reason
        for requirement in ("goals", "contract", "verification", "Stop")
    )
    assert _turn_state(identity)["goals_blocks"] == 1
    assert _gate_actions(tmp_path) == [GateAction.BLOCK]


def test_stop_decision_is_unchanged_by_pretool_read_only_exception(
    tmp_path: Path,
) -> None:
    control = _seed_goals_turn(tmp_path / "control", "stop-control")
    subject = _seed_goals_turn(tmp_path / "subject", "stop-subject")
    _record_change(control)
    _record_change(subject)

    read_only = evaluate_pretool_contract(_shell_payload(subject, "cat README.md"))
    control_stop = evaluate_stop(control)
    subject_stop = evaluate_stop(subject)

    assert read_only["decision"] == "allow"
    assert subject_stop["decision"] == control_stop["decision"]
    assert subject_stop.get("reason") == control_stop.get("reason")
    assert _turn_state(subject)["goals_blocks"] == 0
