"""v2.6.2 STATE-03 — contract authoring × migration barrier (RED-first).

INV-03: migration 중 성공한 state write는 최종 authority(.smtw)에 있어야 한다.
direct contract authoring(friction 예외)은 migration lock이 잡혀 있거나 staging이
존재할 때 block/defer돼야 하고, 허용된 write는 publish 후 .smtw에 존재해야 한다.
legacy contract.json과 identity namespaced contract 둘 다 검증한다.
"""
from __future__ import annotations

import json
from pathlib import Path
import threading

from core.contract import evaluate_state_file_friction, namespaced_contract_path
from core.file_lock import owner_lock
from core.state_layout import (
    MIGRATION_STAGING_PREFIX,
    StateLayout,
    inspect_state_layout,
    migration_lock_path,
)
from core.state_migration import MigrationStatus, migrate_state


AGENT_KEY = "codex_cli:state-session:codex"


def _legacy_root(tmp_path: Path) -> Path:
    legacy = tmp_path / ".fable-lite"
    legacy.mkdir()
    return legacy


def _edit_payload(tmp_path: Path, target: Path, *, exact: bool = True) -> dict:
    payload: dict = {
        "project_root": str(tmp_path),
        "tool_name": "Edit",
        "file_paths": [str(target)],
    }
    if exact:
        payload |= {
            "host": "codex_cli",
            "session_id": "state-session",
            "agent": "codex",
            "attribution": "exact",
        }
    return payload


def _write_contract_json(tmp_path: Path, goal: str) -> Path:
    legacy = _legacy_root(tmp_path)
    target = legacy / "contract.json"
    target.write_text(json.dumps({"goal": goal}), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# ① migration lock / staging 중 contract authoring block (RED)
# ---------------------------------------------------------------------------


def test_state_03_contract_edit_blocked_while_migration_lock_held(
    tmp_path: Path,
) -> None:
    target = _write_contract_json(tmp_path, "v1")
    payload = _edit_payload(tmp_path, target)
    # lock 밖에서는 허용(하위호환).
    assert evaluate_state_file_friction(payload)["decision"] == "allow"

    with owner_lock(migration_lock_path(str(tmp_path)), wait_seconds=0):
        decision = evaluate_state_file_friction(payload)

    assert decision["decision"] == "block"
    assert "STATE-03" in str(decision["reason"])


def test_state_03_contract_edit_blocked_while_staging_exists(tmp_path: Path) -> None:
    target = _write_contract_json(tmp_path, "v1")
    (tmp_path / f"{MIGRATION_STAGING_PREFIX}99999-abc").mkdir()
    assert inspect_state_layout(str(tmp_path)) is StateLayout.MIGRATING

    decision = evaluate_state_file_friction(_edit_payload(tmp_path, target))

    assert decision["decision"] == "block"
    assert "STATE-03" in str(decision["reason"])


def test_state_03_namespaced_contract_edit_blocked_while_lock_held(
    tmp_path: Path,
) -> None:
    legacy = _legacy_root(tmp_path)
    namespaced = namespaced_contract_path(str(tmp_path), AGENT_KEY)
    assert namespaced.parent == legacy / "contracts"
    namespaced.parent.mkdir(parents=True, exist_ok=True)
    namespaced.write_text(json.dumps({"goal": "v1"}), encoding="utf-8")
    payload = _edit_payload(tmp_path, namespaced)
    assert evaluate_state_file_friction(payload)["decision"] == "allow"

    with owner_lock(migration_lock_path(str(tmp_path)), wait_seconds=0):
        decision = evaluate_state_file_friction(payload)

    assert decision["decision"] == "block"
    assert "STATE-03" in str(decision["reason"])


# ---------------------------------------------------------------------------
# ④ fault test: publish boundary에서 허용된 write는 최종 authority에 존재
# ---------------------------------------------------------------------------


def test_state_03_publish_boundary_blocks_contract_edit_and_preserves_authority(
    tmp_path: Path,
) -> None:
    target = _write_contract_json(tmp_path, "v1")
    payload = _edit_payload(tmp_path, target)

    paused = threading.Event()
    release = threading.Event()
    stages: list[str] = []

    def fault_injector(stage: str, _path: object) -> None:
        stages.append(stage)
        if stage == "before_publish":
            paused.set()
            if not release.wait(timeout=30):
                raise RuntimeError("fault release timed out")

    worker = threading.Thread(
        target=lambda: results.append(
            migrate_state(str(tmp_path), fault_injector=fault_injector)
        ),
        daemon=True,
    )
    results: list = []
    worker.start()
    assert paused.wait(timeout=30), f"migration never reached publish: {stages}"
    try:
        # migration publish 직전(staging 존재·layout lock 보유): authoring은 defer.
        # 수정 전: legacy가 authority로 보여 allow → publish 후 write 유실(RED).
        decision = evaluate_state_file_friction(payload)
        assert decision["decision"] == "block"
        assert "STATE-03" in str(decision["reason"])
    finally:
        release.set()
        worker.join(timeout=60)

    assert results and results[0].status is MigrationStatus.MIGRATED
    assert inspect_state_layout(str(tmp_path)) is StateLayout.MIGRATED
    # 차단된 write는 일어나지 않았고, publish는 v1을 최종 authority로 옮겼다.
    published = tmp_path / ".smtw" / "contract.json"
    assert json.loads(published.read_text(encoding="utf-8"))["goal"] == "v1"
    assert json.loads(target.read_text(encoding="utf-8"))["goal"] == "v1"
    # publish 이후 새 authority 경로 authoring은 허용된다.
    assert (
        evaluate_state_file_friction(_edit_payload(tmp_path, published))["decision"]
        == "allow"
    )


def test_state_03_allowed_write_before_migration_lands_in_target(
    tmp_path: Path,
) -> None:
    # INV-03 baseline: lock 없이 허용된 write가 publish 전에 들어가면 .smtw에 존재.
    target = _write_contract_json(tmp_path, "v1")
    payload = _edit_payload(tmp_path, target)
    assert evaluate_state_file_friction(payload)["decision"] == "allow"
    target.write_text(json.dumps({"goal": "v2-updated"}), encoding="utf-8")

    result = migrate_state(str(tmp_path))

    assert result.status is MigrationStatus.MIGRATED
    published = tmp_path / ".smtw" / "contract.json"
    assert json.loads(published.read_text(encoding="utf-8"))["goal"] == "v2-updated"


# ---------------------------------------------------------------------------
# 회귀 가드: 정상 authoring은 계속 허용 (하위호환)
# ---------------------------------------------------------------------------


def test_state_03_idle_legacy_contract_edit_still_allowed(tmp_path: Path) -> None:
    target = _write_contract_json(tmp_path, "v1")

    decision = evaluate_state_file_friction(_edit_payload(tmp_path, target))

    assert decision["decision"] == "allow"


def test_state_03_idle_legacy_namespaced_contract_edit_still_allowed(
    tmp_path: Path,
) -> None:
    _ = _legacy_root(tmp_path)
    namespaced = namespaced_contract_path(str(tmp_path), AGENT_KEY)
    namespaced.parent.mkdir(parents=True, exist_ok=True)
    namespaced.write_text(json.dumps({"goal": "v1"}), encoding="utf-8")

    decision = evaluate_state_file_friction(_edit_payload(tmp_path, namespaced))

    assert decision["decision"] == "allow"


def test_state_03_native_contract_edit_still_allowed(tmp_path: Path) -> None:
    native = tmp_path / ".smtw"
    native.mkdir()
    target = native / "contract.json"
    target.write_text(json.dumps({"goal": "v1"}), encoding="utf-8")
    assert inspect_state_layout(str(tmp_path)) is StateLayout.NATIVE

    decision = evaluate_state_file_friction(_edit_payload(tmp_path, target))

    assert decision["decision"] == "allow"


def test_state_03_non_contract_state_edit_still_blocked(tmp_path: Path) -> None:
    legacy = _legacy_root(tmp_path)
    ledger = legacy / "ledger.json"
    ledger.write_text("{}", encoding="utf-8")

    decision = evaluate_state_file_friction(_edit_payload(tmp_path, ledger))

    assert decision["decision"] == "block"
    assert "STATE-03" not in str(decision.get("reason", ""))
