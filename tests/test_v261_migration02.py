from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import ANY, patch

import pytest

import core.ledger_migration as migration_module
from core.ledger_schema import JsonObject, serialize_v2_ledger
from core.ledger_storage import ledger_path
from core.ledger_v2 import apply_v2_event, default_v2_ledger


ARCHIVE_INDEX_NAME = "ledger.v2-invocation-status.index.json"
ARCHIVE_PREFIX = "ledger.v2-invocation-status."
ARCHIVE_REASON = "invocation_status_backfill"


def _statusless_ledger(*, marker: str = "first") -> JsonObject:
    ledger = default_v2_ledger()
    _ = apply_v2_event(
        ledger,
        {
            "event": "prompt",
            "seq": 1,
            "host": "codex_cli",
            "session_id": "migration-session",
            "agent": "codex",
            "turn_id": "migration-turn",
            "prompt": "mixed-version migration",
        },
    )
    turns = ledger["active_turns"]
    assert isinstance(turns, dict)
    turn = turns["codex_cli:migration-session:codex"]
    assert isinstance(turn, dict)
    turn["invocations"] = {
        f"tool:{marker}": {
            "candidate_paths": [f"{marker}.py"],
            "started_seq": 1,
            "started_at": "2020-01-01T00:00:00+00:00",
        }
    }
    return ledger


def _write_statusless(root: Path, *, marker: str = "first") -> tuple[Path, bytes]:
    destination = ledger_path(str(root))
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = json.dumps(
        _statusless_ledger(marker=marker),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    destination.write_bytes(source)
    return destination, source


def _archive_path(destination: Path, source: bytes) -> Path:
    digest = sha256(source).hexdigest()
    return destination.with_name(f"{ARCHIVE_PREFIX}{digest}.bak")


def _load_index(destination: Path) -> dict[str, object]:
    value = json.loads(
        destination.with_name(ARCHIVE_INDEX_NAME).read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def _index_archives(destination: Path) -> list[dict[str, object]]:
    index = _load_index(destination)
    assert index["schema_version"] == 1
    archives = index["archives"]
    assert isinstance(archives, list)
    assert all(isinstance(entry, dict) for entry in archives)
    return archives


def _old_writer_adds_statusless_invocation(destination: Path, marker: str) -> bytes:
    ledger = json.loads(destination.read_text(encoding="utf-8"))
    assert isinstance(ledger, dict)
    turns = ledger["active_turns"]
    turn = turns["codex_cli:migration-session:codex"]
    invocations = turn["invocations"]
    invocations[f"tool:{marker}"] = {
        "candidate_paths": [f"{marker}.py"],
        "started_seq": 2,
        "started_at": "2020-01-01T00:00:00+00:00",
    }
    source = json.dumps(
        ledger,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    destination.write_bytes(source)
    return source


def _run_status_migration(root: str) -> JsonObject:
    return migration_module.migrate_v2_invocation_statuses(root)


def test_first_backfill_creates_digest_archive_and_manifest(
    tmp_path: Path,
) -> None:
    destination, source = _write_statusless(tmp_path)
    digest = sha256(source).hexdigest()

    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    archive = _archive_path(destination, source)
    assert archive.read_bytes() == source
    assert _index_archives(destination) == [
        {
            "digest": digest,
            "path": archive.name,
            "created_at": ANY,
            "reason": ARCHIVE_REASON,
        }
    ]


def test_old_version_writer_creates_a_new_distinct_rollback_source(
    tmp_path: Path,
) -> None:
    destination, first_source = _write_statusless(tmp_path)
    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    second_source = _old_writer_adds_statusless_invocation(destination, "second")

    assert sha256(second_source).digest() != sha256(first_source).digest()
    assert _archive_path(destination, first_source).is_file()
    assert not _archive_path(destination, second_source).exists()


def test_legacy_fixed_archive_does_not_block_new_digest_archive(
    tmp_path: Path,
) -> None:
    destination, old_source = _write_statusless(tmp_path)
    old_ledger = json.loads(old_source.decode("utf-8"))
    assert isinstance(old_ledger, dict)
    converted, changed = migration_module.backfill_invocation_statuses(
        old_ledger
    )
    assert changed == 1
    destination.write_text(
        serialize_v2_ledger(converted),
        encoding="utf-8",
    )
    legacy_fixed = destination.with_name(
        "ledger.v2-invocation-status.json.bak"
    )
    legacy_fixed.write_bytes(old_source)
    new_source = _old_writer_adds_statusless_invocation(destination, "new")

    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    assert legacy_fixed.read_bytes() == old_source
    assert _archive_path(destination, new_source).read_bytes() == new_source
    assert len(_index_archives(destination)) == 1


def test_second_backfill_preserves_both_distinct_sources(
    tmp_path: Path,
) -> None:
    destination, first_source = _write_statusless(tmp_path)
    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))
    second_source = _old_writer_adds_statusless_invocation(destination, "second")

    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    assert _archive_path(destination, first_source).read_bytes() == first_source
    assert _archive_path(destination, second_source).read_bytes() == second_source
    assert {entry["digest"] for entry in _index_archives(destination)} == {
        sha256(first_source).hexdigest(),
        sha256(second_source).hexdigest(),
    }


def test_identical_source_replay_reuses_archive_and_manifest_entry(
    tmp_path: Path,
) -> None:
    destination, source = _write_statusless(tmp_path)
    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))
    first_index = destination.with_name(ARCHIVE_INDEX_NAME).read_bytes()
    destination.write_bytes(source)

    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    matching = list(destination.parent.glob(f"{ARCHIVE_PREFIX}*.bak"))
    assert matching == [_archive_path(destination, source)]
    assert destination.with_name(ARCHIVE_INDEX_NAME).read_bytes() == first_index
    assert len(_index_archives(destination)) == 1


def test_archive_write_failure_keeps_live_ledger_unchanged(
    tmp_path: Path,
) -> None:
    destination, source = _write_statusless(tmp_path)
    real_atomic_write_bytes = migration_module.atomic_write_bytes

    def fail_archive(path: Path, content: bytes, prefix: str = "ledger-") -> None:
        if path.name.startswith(ARCHIVE_PREFIX):
            raise OSError("injected archive crash")
        real_atomic_write_bytes(path, content, prefix)

    with patch.object(
        migration_module,
        "atomic_write_bytes",
        side_effect=fail_archive,
    ):
        with pytest.raises(migration_module.LedgerMigrationError, match="archive"):
            _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    assert destination.read_bytes() == source
    assert not destination.with_name(ARCHIVE_INDEX_NAME).exists()


def test_archive_index_crash_is_retryable_without_rewriting_source(
    tmp_path: Path,
) -> None:
    destination, source = _write_statusless(tmp_path)
    real_atomic_write_text = migration_module.atomic_write_text

    def fail_index(path: Path, content: str, prefix: str = "ledger-") -> None:
        if path.name == ARCHIVE_INDEX_NAME:
            raise OSError("injected index crash")
        real_atomic_write_text(path, content, prefix)

    with patch.object(
        migration_module,
        "atomic_write_text",
        side_effect=fail_index,
    ):
        with pytest.raises(migration_module.LedgerMigrationError, match="archive"):
            _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    assert destination.read_bytes() == source
    assert _archive_path(destination, source).read_bytes() == source
    assert not destination.with_name(ARCHIVE_INDEX_NAME).exists()

    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    assert _archive_path(destination, source).read_bytes() == source
    assert len(_index_archives(destination)) == 1


def test_main_write_failure_restores_digest_archived_source(
    tmp_path: Path,
) -> None:
    destination, source = _write_statusless(tmp_path)
    real_atomic_write_text = migration_module.atomic_write_text

    def fail_main(path: Path, content: str, prefix: str = "ledger-") -> None:
        if path == destination:
            raise OSError("injected main crash")
        real_atomic_write_text(path, content, prefix)

    with patch.object(
        migration_module,
        "atomic_write_text",
        side_effect=fail_main,
    ):
        with pytest.raises(migration_module.LedgerMigrationError, match="write"):
            _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    assert destination.read_bytes() == source
    assert _archive_path(destination, source).read_bytes() == source
    assert len(_index_archives(destination)) == 1


def test_post_replace_failure_restores_original_bytes_exactly(
    tmp_path: Path,
) -> None:
    destination, source = _write_statusless(tmp_path)
    real_atomic_write_text = migration_module.atomic_write_text

    def write_then_fail(path: Path, content: str, prefix: str = "ledger-") -> None:
        real_atomic_write_text(path, content, prefix)
        if path == destination:
            raise OSError("crash after main replace")

    with patch.object(
        migration_module,
        "atomic_write_text",
        side_effect=write_then_fail,
    ):
        with pytest.raises(migration_module.LedgerMigrationError, match="write"):
            _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    assert destination.read_bytes() == source
    assert _archive_path(destination, source).read_bytes() == source


def test_concurrent_backfills_share_one_digest_archive(
    tmp_path: Path,
) -> None:
    destination, source = _write_statusless(tmp_path)

    with ProcessPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                _run_status_migration,
                [str(tmp_path)] * 8,
            )
        )

    assert len(results) == 8
    assert all(result == results[0] for result in results)
    assert _archive_path(destination, source).read_bytes() == source
    assert len(_index_archives(destination)) == 1


def test_retention_evicts_oldest_unprotected_archive_by_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migration_module,
        "INVOCATION_STATUS_ARCHIVE_MAX_COUNT",
        2,
    )
    destination, first = _write_statusless(tmp_path, marker="first")
    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))
    second = _old_writer_adds_statusless_invocation(destination, "second")
    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))
    index_path = destination.with_name(ARCHIVE_INDEX_NAME)
    index = _load_index(destination)
    archives = index["archives"]
    assert isinstance(archives, list)
    index["archives"] = list(reversed(archives))
    index_path.write_text(
        json.dumps(index),
        encoding="utf-8",
    )
    third = _old_writer_adds_statusless_invocation(destination, "third")

    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    assert not _archive_path(destination, first).exists()
    assert _archive_path(destination, second).is_file()
    assert _archive_path(destination, third).is_file()
    assert len(_index_archives(destination)) == 2


def test_retention_evicts_oldest_unprotected_archive_by_total_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination, first = _write_statusless(tmp_path, marker="first")
    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))
    second = _old_writer_adds_statusless_invocation(destination, "second")
    monkeypatch.setattr(
        migration_module,
        "INVOCATION_STATUS_ARCHIVE_MAX_BYTES",
        len(second),
    )

    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    assert not _archive_path(destination, first).exists()
    assert _archive_path(destination, second).read_bytes() == second
    assert len(_index_archives(destination)) == 1


def test_retention_keeps_current_rollback_source_even_above_byte_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migration_module,
        "INVOCATION_STATUS_ARCHIVE_MAX_BYTES",
        1,
    )
    destination, source = _write_statusless(tmp_path)

    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    archive = _archive_path(destination, source)
    assert archive.read_bytes() == source
    assert [entry["digest"] for entry in _index_archives(destination)] == [
        sha256(source).hexdigest()
    ]


def test_network_tripwire_allows_only_multiprocessing_pymp_unix_ipc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import socket
    import tempfile
    from types import SimpleNamespace

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_sock:
        with pytest.raises(pytest.fail.Exception, match="NETWORK CALL FORBIDDEN"):
            tcp_sock.connect(("127.0.0.1", 9))
        with pytest.raises(pytest.fail.Exception, match="NETWORK CALL FORBIDDEN"):
            tcp_sock.connect_ex(("127.0.0.1", 9))
    with pytest.raises(pytest.fail.Exception, match="NETWORK CALL FORBIDDEN"):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(pytest.fail.Exception, match="NETWORK CALL FORBIDDEN"):
        socket.getaddrinfo("localhost", 80)

    if os.name != "nt" and hasattr(socket, "AF_UNIX"):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as unix_sock:
            with pytest.raises(pytest.fail.Exception, match="NETWORK CALL FORBIDDEN"):
                unix_sock.connect("/var/run/docker.sock")
            with pytest.raises(pytest.fail.Exception, match="NETWORK CALL FORBIDDEN"):
                unix_sock.connect_ex(b"/var/run/docker.sock")
            with pytest.raises(pytest.fail.Exception, match="NETWORK CALL FORBIDDEN"):
                unix_sock.connect("/tmp/pymp-fake/../../var/run/docker.sock")
        ipc_parent = "/tmp" if Path("/tmp").is_dir() else None
        with tempfile.TemporaryDirectory(prefix="pymp-", dir=ipc_parent) as pymp_dir:
            sock_path = str(Path(pymp_dir) / "sock-ipc")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(sock_path)
                server.listen(2)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client_str:
                    client_str.connect(sock_path)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client_bytes:
                    assert client_bytes.connect_ex(sock_path.encode("utf-8")) == 0

    af_unix = getattr(socket, "AF_UNIX", 1)
    monkeypatch.setattr(socket, "AF_UNIX", af_unix, raising=False)
    fake_unix = SimpleNamespace(family=af_unix)
    fake_inet = SimpleNamespace(family=socket.AF_INET)
    for blocked_addr in (
        "/var/run/docker.sock",
        b"/var/run/docker.sock",
        "/tmp/pymp-fake/../../var/run/docker.sock",
        r"C:\Temp\pymp-fake\..\..\docker.sock",
        "\x00/tmp/pymp-fake/sock-0",
    ):
        with pytest.raises(pytest.fail.Exception, match="NETWORK CALL FORBIDDEN"):
            socket.socket.connect(fake_unix, blocked_addr)
        with pytest.raises(pytest.fail.Exception, match="NETWORK CALL FORBIDDEN"):
            socket.socket.connect_ex(fake_unix, blocked_addr)
    with pytest.raises(pytest.fail.Exception, match="NETWORK CALL FORBIDDEN"):
        socket.socket.connect(fake_inet, "/tmp/pymp-fake/sock-0")
    for allowed_str in (
        "/tmp/pymp-work01/listener-0",
        r"C:\Temp\pymp-work01\sock-0",
    ):
        with pytest.raises(TypeError):
            socket.socket.connect(fake_unix, allowed_str)
        with pytest.raises(TypeError):
            socket.socket.connect_ex(fake_unix, allowed_str.encode("utf-8"))
