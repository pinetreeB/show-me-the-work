from __future__ import annotations

from pathlib import Path

import pytest

from core.destructive_guard import evaluate_r2_destructive_gate
from core.ledger import JsonObject
from core.state_layout import state_dir


def _payload(root: Path, command: str) -> JsonObject:
    return {
        "project_root": str(root),
        "tool_name": "Bash",
        "command": command,
        "host": "codex_cli",
        "agent": "codex",
        "session_id": "b1-quarantine-session",
    }


def _evaluate(root: Path, command: str) -> JsonObject:
    return evaluate_r2_destructive_gate(
        _payload(root, command),
        lookup_path_attribution=lambda _ledger, _canonical: None,
        attribution_health=lambda _ledger: {
            "degraded": False,
            "capacity_exceeded": False,
        },
    )


def _quarantine_files(root: Path) -> list[Path]:
    directory = state_dir(root) / "quarantine"
    if not directory.exists():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file())


# ---------------------------------------------------------------------------
# Q-1: deny 시 백업
# ---------------------------------------------------------------------------


def test_deny_creates_quarantine_backup_with_full_command_and_header(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    command = "rm .fable-lite/ledger.json"

    result = _evaluate(root, command)

    assert result["decision"] == "block"
    files = _quarantine_files(root)
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert command in text
    assert "state_dir_protected" in text
    assert "codex_cli:b1-quarantine-session:codex" in text


def test_deny_decision_unaffected_by_backup_presence(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    command = "rm .fable-lite/ledger.json"

    result = _evaluate(root, command)

    assert result["decision"] == "block"
    assert result["coordination_reason_code"] == "state_dir_protected"


# ---------------------------------------------------------------------------
# Q-2: 자기참조 예외 — quarantine 쓰기 자체가 R2에 재차단되지 않음
# ---------------------------------------------------------------------------


def test_quarantine_write_is_not_recursively_blocked_by_state_dir_protection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    # 대상 자체가 .fable-lite 하위이므로 state_dir_protected 로 차단되는 명령.
    result = _evaluate(root, "rm .fable-lite/ledger.json")

    assert result["decision"] == "block"
    # quarantine 파일도 .fable-lite 하위에 실제로 생성됐어야 한다 — 만약 백업 쓰기가
    # 게이트를 다시 타서 재귀 차단됐다면 이 파일은 존재하지 않을 것이다.
    files = _quarantine_files(root)
    assert len(files) == 1


# ---------------------------------------------------------------------------
# Q-3: 게이트 불변 — 백업이 실패해도 deny/reason_code는 그대로(best-effort)
# ---------------------------------------------------------------------------


def test_deny_decision_and_reason_code_identical_when_backup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    command = "rm .fable-lite/ledger.json"

    baseline = _evaluate(root, command)

    import core.quarantine as quarantine_module

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated backup sink failure")

    monkeypatch.setattr(quarantine_module, "backup_blocked_command", _boom)

    root2 = tmp_path / "project2"
    root2.mkdir()
    failing = _evaluate(root2, command)

    assert failing["decision"] == baseline["decision"] == "block"
    assert (
        failing["coordination_reason_code"]
        == baseline["coordination_reason_code"]
        == "state_dir_protected"
    )
    # 실패 시엔 quarantine 파일이 없어야 하며 실패 안내가 사실대로 남아야 한다.
    assert _quarantine_files(root2) == []
    assert "could not be preserved in quarantine" in str(failing["reason"])


def test_backup_module_never_raises_on_unwritable_directory(tmp_path: Path) -> None:
    from core.quarantine import backup_blocked_command

    # NUL 바이트가 섞인 프로젝트 루트는 어떤 플랫폼에서도 유효한 경로로 만들 수
    # 없다 -- mkdir/replace가 확실히 실패하는 조건에서 예외가 새지 않는지 확인한다.
    result = backup_blocked_command(
        str(tmp_path / "does-not-exist" / "\x00bad"),
        command="rm something",
        agent_key="host:sess:agent",
        reason_code="state_dir_protected",
        target="something",
    )
    assert result is None


# ---------------------------------------------------------------------------
# Q-4: UX 메시지 — 백업 성공 시에만 안내문이 reason에 추가됨
# ---------------------------------------------------------------------------


def test_deny_reason_includes_recovery_note_on_successful_backup(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    result = _evaluate(root, "rm .fable-lite/ledger.json")

    reason = str(result["reason"])
    assert "quarantine" in reason
    assert "state_dir_protected" in reason
    assert "Blocked content preserved completely" in reason


def test_deny_reason_reports_preservation_failure_without_weakening_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.quarantine as quarantine_module

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated backup sink failure")

    monkeypatch.setattr(quarantine_module, "backup_blocked_command", _boom)

    root = tmp_path / "project"
    root.mkdir()
    result = _evaluate(root, "rm .fable-lite/ledger.json")

    assert result["decision"] == "block"
    assert result["coordination_reason_code"] == "state_dir_protected"
    assert "could not be preserved in quarantine" in str(result["reason"])


# ---------------------------------------------------------------------------
# Q-3: bounded + GC
# ---------------------------------------------------------------------------


def test_gc_evicts_oldest_first_when_entry_count_exceeds_cap(tmp_path: Path) -> None:
    from core.quarantine import backup_blocked_command, list_entries

    root = tmp_path / "project"
    root.mkdir()

    for index in range(5):
        saved = backup_blocked_command(
            str(root),
            command=f"rm target-{index}.txt",
            agent_key="host:sess:agent",
            reason_code="state_dir_protected",
            target=f"target-{index}.txt",
            now=1_700_000_000.0 + index,
            max_entries=3,
        )
        assert saved is not None

    entries = list_entries(str(root))
    assert len(entries) == 3
    remaining_commands = {entry.path.read_text(encoding="utf-8") for entry in entries}
    assert not any("target-0.txt" in text for text in remaining_commands)
    assert not any("target-1.txt" in text for text in remaining_commands)
    assert any("target-4.txt" in text for text in remaining_commands)


def test_gc_evicts_oldest_first_when_total_bytes_exceed_cap(tmp_path: Path) -> None:
    from core.quarantine import backup_blocked_command, list_entries

    root = tmp_path / "project"
    root.mkdir()

    for index in range(4):
        saved = backup_blocked_command(
            str(root),
            command="x" * 200,
            agent_key="host:sess:agent",
            reason_code="state_dir_protected",
            target="big.txt",
            now=1_700_000_000.0 + index,
            max_entries=64,
            max_total_bytes=1600,
        )
        assert saved is not None

    entries = list_entries(str(root))
    total = sum(entry.size_bytes for entry in entries)
    assert total <= 1600
    assert len(entries) < 4


def test_gc_evicts_entries_older_than_ttl(tmp_path: Path) -> None:
    from core.quarantine import backup_blocked_command, list_entries

    root = tmp_path / "project"
    root.mkdir()

    old = backup_blocked_command(
        str(root),
        command="rm old.txt",
        agent_key="host:sess:agent",
        reason_code="state_dir_protected",
        target="old.txt",
        now=1_700_000_000.0,
    )
    assert old is not None
    import os

    old_time = 1_700_000_000.0 - 1000
    os.utime(old, (old_time, old_time))

    fresh = backup_blocked_command(
        str(root),
        command="rm fresh.txt",
        agent_key="host:sess:agent",
        reason_code="state_dir_protected",
        target="fresh.txt",
        now=1_700_000_000.0 + 10,
        ttl_seconds=500,
    )
    assert fresh is not None

    entries = list_entries(str(root))
    names = {entry.id for entry in entries}
    assert old.name not in names
    assert fresh.name in names


def test_single_backup_never_exceeds_stored_command_byte_cap(tmp_path: Path) -> None:
    from core.quarantine import backup_blocked_command, list_entries

    root = tmp_path / "project"
    root.mkdir()
    huge_command = "cat <<'EOF' > out.txt\n" + ("A" * 5000) + "\nEOF\n"

    saved = backup_blocked_command(
        str(root),
        command=huge_command,
        agent_key="host:sess:agent",
        reason_code="state_dir_protected",
        target="out.txt",
        max_entry_bytes=1024,
    )

    assert saved is not None
    record = list_entries(str(root))[0]
    assert record.original_bytes == len(huge_command.encode("utf-8"))
    assert record.stored_bytes == 1024
    assert record.truncated is True
    assert record.record_status == "incomplete"
