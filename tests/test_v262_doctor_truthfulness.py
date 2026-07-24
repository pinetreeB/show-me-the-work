"""v2.6.2 DOCTOR-03A/B — doctor health truthfulness (RED-first).

INV-06: doctor가 healthy라고 보고하면 runtime도 해당 state를 읽을 수 있어야 한다.

DOCTOR-03A: doctor의 지원 schema 집합은 runtime ledger loader와 공유된다.
missing/1 → legacy, 2 → validate_v2, 그외(0·3·99·비정수) → error.

DOCTOR-03B: provenance_status 판정은 ProvenanceStatus enum 기준 단일 함수를
쓴다(문자열 하드코딩 금지). runtime Stop safety ↔ doctor provenance_health ↔
doctor exit_code는 같은 fixture에서 모순되면 안 된다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.ledger import default_v2_ledger, load_ledger, record_event
from core.ledger_schema import LedgerSchemaError
from core.state_layout import state_dir
from core.verify_state import evaluate_stop
from smtw.doctor import diagnostic_snapshot


IDENTITY = {
    "host": "codex_cli",
    "session_id": "doctor-session",
    "agent": "codex",
    "turn_id": "turn:doctor-session:codex",
    "attribution": "exact",
    "identity_synthetic": False,
}
TURN_KEY = "codex_cli:doctor-session:codex"


def _activate(root: Path) -> None:
    (root / ".smtw.toml").write_text(
        "schema_version = 1\nsupervision = true\n", encoding="utf-8"
    )


def _write_raw_ledger(root: Path, raw: str) -> None:
    authority = state_dir(str(root))
    authority.mkdir(parents=True, exist_ok=True)
    (authority / "ledger.json").write_text(raw, encoding="utf-8")


def _write_json_ledger(root: Path, payload: object) -> None:
    _write_raw_ledger(root, json.dumps(payload, ensure_ascii=False))


def _seed_v2_turn(root: Path) -> None:
    _ = record_event(
        IDENTITY
        | {
            "project_root": str(root),
            "event": "prompt",
            "prompt": "implement and verify two stories",
            "task_mode": "normal",
            "needs_goals": True,
        }
    )


def _set_turn_provenance(root: Path, status: str, *, mutation_capable: bool) -> None:
    path = state_dir(str(root)) / "ledger.json"
    ledger = json.loads(path.read_text(encoding="utf-8"))
    turn = ledger["active_turns"][TURN_KEY]
    turn["provenance_status"] = status
    turn["provenance_mutation_capable"] = mutation_capable
    turn["changed_files_seen"] = ["app.py"]
    path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")


def _snapshot(root: Path) -> dict:
    _activate(root)
    payload = diagnostic_snapshot(str(root))
    assert isinstance(payload, dict)
    return payload


# ---------------------------------------------------------------------------
# DOCTOR-03A — unsupported schema는 error (runtime loader와 공유)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema_version", [0, 3, 99, "2", True])
def test_doctor_03a_unsupported_schema_is_error(
    tmp_path: Path, schema_version: object
) -> None:
    _write_json_ledger(tmp_path, {"schema_version": schema_version, "active_turns": {}})

    snapshot = _snapshot(tmp_path)

    # 수정 전: schema 0/3/99가 healthy로 fall-through(INV-06 위반, RED).
    assert snapshot["ledger_health"] == "error"
    assert snapshot["exit_code"] == 1
    assert snapshot["status"] == "unsafe"
    assert "ledger is unreadable or invalid" in snapshot["errors"]


def test_doctor_03a_non_object_ledger_is_error(tmp_path: Path) -> None:
    _write_raw_ledger(tmp_path, "[1, 2, 3]")

    snapshot = _snapshot(tmp_path)

    assert snapshot["ledger_health"] == "error"
    assert snapshot["exit_code"] == 1


def test_doctor_03a_malformed_json_ledger_is_error(tmp_path: Path) -> None:
    _write_raw_ledger(tmp_path, "{not-json")

    snapshot = _snapshot(tmp_path)

    assert snapshot["ledger_health"] == "error"
    assert snapshot["exit_code"] == 1


def test_doctor_03a_valid_v2_ledger_is_healthy(tmp_path: Path) -> None:
    _write_json_ledger(tmp_path, default_v2_ledger())

    snapshot = _snapshot(tmp_path)

    assert snapshot["ledger_health"] == "healthy"
    assert snapshot["exit_code"] == 0


def test_doctor_03a_valid_legacy_ledger_is_healthy(tmp_path: Path) -> None:
    _write_json_ledger(tmp_path, {"schema_version": 1, "seq": 0})

    snapshot = _snapshot(tmp_path)

    assert snapshot["ledger_health"] == "healthy"


def test_doctor_03a_missing_schema_legacy_shape_is_healthy(tmp_path: Path) -> None:
    _write_json_ledger(tmp_path, {"seq": 0, "prompt": "legacy"})

    snapshot = _snapshot(tmp_path)

    assert snapshot["ledger_health"] == "healthy"


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 0},
        {"schema_version": 3},
        {"schema_version": 99},
        {"schema_version": "2"},
        {"schema_version": True},
    ],
)
def test_doctor_03a_agrees_with_runtime_loader_rejection(
    tmp_path: Path, payload: dict
) -> None:
    # 무모순: runtime loader가 LedgerSchemaError로 거절하면 doctor도 error여야 한다.
    _write_json_ledger(tmp_path, payload)
    with pytest.raises(LedgerSchemaError):
        _ = load_ledger({"project_root": str(tmp_path)})

    snapshot = _snapshot(tmp_path)

    assert snapshot["ledger_health"] == "error"


# ---------------------------------------------------------------------------
# DOCTOR-03B — provenance status enum 단일 판정
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status", ["incomplete", "scope_too_large", "unsupported"]
)
def test_doctor_03b_unsafe_provenance_statuses_are_unsafe(
    tmp_path: Path, status: str
) -> None:
    _seed_v2_turn(tmp_path)
    _set_turn_provenance(tmp_path, status, mutation_capable=True)

    snapshot = _snapshot(tmp_path)

    # 수정 전: doctor가 {"Incomplete","OverBudget","Error"} 문자열만 봐서
    # 실제 enum 값(소문자)을 놓치고 healthy 보고(RED).
    assert snapshot["provenance_health"] == "unsafe"
    assert snapshot["exit_code"] == 1
    assert "active provenance is incomplete" in snapshot["errors"]


@pytest.mark.parametrize(
    "status", ["complete", "complete_with_exclusions"]
)
def test_doctor_03b_safe_provenance_statuses_are_healthy(
    tmp_path: Path, status: str
) -> None:
    _seed_v2_turn(tmp_path)
    _set_turn_provenance(tmp_path, status, mutation_capable=True)

    snapshot = _snapshot(tmp_path)

    assert snapshot["provenance_health"] == "healthy"


def test_doctor_03b_unknown_provenance_status_is_unsafe_fail_closed(
    tmp_path: Path,
) -> None:
    _seed_v2_turn(tmp_path)
    _set_turn_provenance(tmp_path, "some_future_status", mutation_capable=True)

    snapshot = _snapshot(tmp_path)

    assert snapshot["provenance_health"] == "unsafe"


def test_doctor_03b_runtime_stop_safety_matches_doctor(tmp_path: Path) -> None:
    # 불변식: 같은 fixture에서 runtime Stop block ↔ doctor unsafe ↔ exit 1.
    _seed_v2_turn(tmp_path)
    _set_turn_provenance(tmp_path, "scope_too_large", mutation_capable=True)

    runtime_decision = evaluate_stop(
        IDENTITY
        | {
            "project_root": str(tmp_path),
            "stop_hook_active": False,
            "assistant_text": "변경을 완료했습니다.",
        }
    )
    snapshot = _snapshot(tmp_path)

    assert runtime_decision["decision"] == "block"
    assert "provenance" in str(runtime_decision.get("reason", ""))
    assert snapshot["provenance_health"] == "unsafe"
    assert snapshot["exit_code"] == 1


def test_doctor_03b_runtime_stop_allow_matches_doctor_healthy(tmp_path: Path) -> None:
    _seed_v2_turn(tmp_path)
    _set_turn_provenance(tmp_path, "complete", mutation_capable=True)

    snapshot = _snapshot(tmp_path)

    assert snapshot["provenance_health"] == "healthy"
    assert snapshot["exit_code"] == 0
