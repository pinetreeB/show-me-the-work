from __future__ import annotations

import hashlib
import io
import multiprocessing
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from contextlib import redirect_stdout

import pytest


_PROCESS_COUNT = 64
_FIXED_NOW = 1_700_000_000.0
_FIXED_AGENT = "codex_cli:quarantine-race:codex"


def _record_body(path: Path) -> bytes:
    _header, separator, body = path.read_bytes().partition(b"# ---\n")
    assert separator == b"# ---\n"
    return body


def _race_worker(
    authority_text: str,
    index: int,
    start_barrier: object,
    result_queue: object,
) -> None:
    import core.quarantine as quarantine

    legacy_filename = getattr(quarantine, "_unique_filename", None)
    if legacy_filename is not None:

        def _synchronized_filename(
            directory: Path, stamp: str, short_agent: str
        ) -> str:
            candidate = legacy_filename(directory, stamp, short_agent)
            start_barrier.wait(timeout=60)
            return candidate

        quarantine._unique_filename = _synchronized_filename

    command = f"rm peer-{index:02d}.txt"
    saved = quarantine._backup_blocked_command_unlocked(
        Path(authority_text),
        command=command,
        agent_key=_FIXED_AGENT,
        reason_code="peer_unsettled_revision",
        target=f"peer-{index:02d}.txt",
        now=_FIXED_NOW,
        max_entries=_PROCESS_COUNT,
        max_entry_bytes=quarantine.MAX_ENTRY_BYTES,
        max_total_bytes=quarantine.MAX_TOTAL_BYTES,
        ttl_seconds=quarantine.TTL_SECONDS,
    )
    result_queue.put((index, str(saved) if saved is not None else None))


def _run_race(authority: Path) -> list[tuple[int, str | None]]:
    context = multiprocessing.get_context("spawn")
    start_barrier = context.Barrier(_PROCESS_COUNT)
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_race_worker,
            args=(str(authority), index, start_barrier, result_queue),
        )
        for index in range(_PROCESS_COUNT)
    ]
    for process in processes:
        process.start()
    results = [result_queue.get(timeout=90) for _ in processes]
    for process in processes:
        process.join(timeout=30)
    exit_codes = [process.exitcode for process in processes]
    assert exit_codes == [0] * _PROCESS_COUNT
    return sorted(results)


def test_quarantine_reserves_64_unique_destinations_across_processes(
    tmp_path: Path,
) -> None:
    authority = tmp_path / ".smtw"

    results = _run_race(authority)

    saved_paths = [Path(path) for _index, path in results if path is not None]
    files = sorted((authority / "quarantine").glob("blocked-*.txt"))
    bodies = {_record_body(path) for path in files}
    observed = (
        len(saved_paths),
        len({path.name for path in saved_paths}),
        len(files),
        len(bodies),
    )
    assert observed == (
        _PROCESS_COUNT,
        _PROCESS_COUNT,
        _PROCESS_COUNT,
        _PROCESS_COUNT,
    )
    assert bodies == {
        f"rm peer-{index:02d}.txt".encode() for index in range(_PROCESS_COUNT)
    }


def test_quarantine_records_exact_and_over_limit_metadata(tmp_path: Path) -> None:
    from core.quarantine import MAX_ENTRY_BYTES, backup_blocked_command, list_entries

    root = tmp_path / "project"
    root.mkdir()
    exact_command = "A" * MAX_ENTRY_BYTES
    over_command = "B" * (MAX_ENTRY_BYTES + 1)

    exact = backup_blocked_command(
        str(root),
        command=exact_command,
        agent_key=_FIXED_AGENT,
        reason_code="state_dir_protected",
        target="exact.txt",
        now=_FIXED_NOW,
    )
    over = backup_blocked_command(
        str(root),
        command=over_command,
        agent_key=_FIXED_AGENT,
        reason_code="state_dir_protected",
        target="over.txt",
        now=_FIXED_NOW + 1,
    )

    assert exact is not None
    assert over is not None
    records = {record.target: record for record in list_entries(str(root))}
    exact_record = records["exact.txt"]
    over_record = records["over.txt"]
    assert exact_record.original_bytes == MAX_ENTRY_BYTES
    assert exact_record.stored_bytes == MAX_ENTRY_BYTES
    assert exact_record.original_sha256 == hashlib.sha256(
        exact_command.encode()
    ).hexdigest()
    assert exact_record.stored_sha256 == exact_record.original_sha256
    assert exact_record.truncated is False
    assert exact_record.encoding == "utf-8"
    assert exact_record.record_status == "complete"
    assert over_record.original_bytes == MAX_ENTRY_BYTES + 1
    assert over_record.stored_bytes == MAX_ENTRY_BYTES
    assert over_record.original_sha256 == hashlib.sha256(
        over_command.encode()
    ).hexdigest()
    assert over_record.stored_sha256 == hashlib.sha256(
        over_command[:MAX_ENTRY_BYTES].encode()
    ).hexdigest()
    assert over_record.truncated is True
    assert over_record.encoding == "utf-8"
    assert over_record.record_status == "incomplete"


def test_utf8_truncation_stops_before_partial_code_point(tmp_path: Path) -> None:
    from core.quarantine import backup_blocked_command, list_entries

    root = tmp_path / "project"
    root.mkdir()
    command = "abc" + ("가" * 3)

    saved = backup_blocked_command(
        str(root),
        command=command,
        agent_key=_FIXED_AGENT,
        reason_code="state_dir_protected",
        target="utf8.txt",
        max_entry_bytes=10,
    )

    assert saved is not None
    record = list_entries(str(root))[0]
    body = _record_body(saved)
    assert body.decode("utf-8") == "abc가가"
    assert record.original_bytes == 12
    assert record.stored_bytes == 9
    assert record.stored_sha256 == hashlib.sha256(body).hexdigest()
    assert record.truncated is True
    assert record.record_status == "incomplete"


def test_r2_partial_preservation_message_is_truthful_and_stays_blocked(
    tmp_path: Path,
) -> None:
    from core.destructive_guard import evaluate_r2_destructive_gate

    root = tmp_path / "project"
    root.mkdir()
    command = "rm .fable-lite/ledger.json #" + ("x" * (1024 * 1024))
    payload = {
        "project_root": str(root),
        "tool_name": "Bash",
        "command": command,
        "host": "codex_cli",
        "agent": "codex",
        "session_id": "quarantine-truncation",
    }

    result = evaluate_r2_destructive_gate(payload)

    assert result["decision"] == "block"
    assert result["coordination_reason_code"] == "state_dir_protected"
    reason = str(result["reason"])
    assert "Blocked content was only partially preserved" in reason
    assert "do not apply as a complete command" in reason
    assert "Blocked content preserved completely" not in reason


def test_r2_complete_preservation_message_is_truthful(tmp_path: Path) -> None:
    from core.destructive_guard import evaluate_r2_destructive_gate

    root = tmp_path / "project"
    root.mkdir()
    payload = {
        "project_root": str(root),
        "tool_name": "Bash",
        "command": "rm .fable-lite/ledger.json",
        "host": "codex_cli",
        "agent": "codex",
        "session_id": "quarantine-complete",
    }

    result = evaluate_r2_destructive_gate(payload)

    assert result["decision"] == "block"
    assert result["coordination_reason_code"] == "state_dir_protected"
    reason = str(result["reason"])
    assert "Blocked content preserved completely" in reason
    assert "only partially preserved" not in reason


def test_cli_show_prints_truncation_warning_before_record(tmp_path: Path) -> None:
    from core.quarantine import backup_blocked_command
    from smtw.cli import build_parser

    root = tmp_path / "project"
    root.mkdir()
    saved = backup_blocked_command(
        str(root),
        command="abc" + ("가" * 3),
        agent_key=_FIXED_AGENT,
        reason_code="state_dir_protected",
        target="utf8.txt",
        max_entry_bytes=10,
    )
    assert saved is not None
    parser = build_parser()
    args = parser.parse_args(
        ["quarantine", "show", saved.name, "--root", str(root)]
    )
    output = io.StringIO()

    with redirect_stdout(output):
        exit_code = int(args.func(args))

    assert exit_code == 0
    lines = output.getvalue().splitlines()
    assert lines[0].startswith("WARNING:")
    assert "only partially preserved" in lines[0]
    assert lines[1].startswith("# blocked_at:")


def test_uuid_collision_retries_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.quarantine as quarantine

    root = tmp_path / "project"
    root.mkdir()
    collision = "0" * 32
    replacement = "1" * 32
    tokens = iter((collision, replacement))
    monkeypatch.setattr(
        quarantine.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex=next(tokens)),
    )
    directory = root / ".smtw" / "quarantine"
    directory.mkdir(parents=True)
    stamp = quarantine._timestamp_compact(_FIXED_NOW)
    short_agent = quarantine._safe_agent_key(_FIXED_AGENT)
    occupied = directory / f"blocked-{stamp}-{short_agent}-{collision}.txt"
    occupied.write_text("do-not-overwrite", encoding="utf-8")

    saved = quarantine.backup_blocked_command(
        str(root),
        command="rm collision.txt",
        agent_key=_FIXED_AGENT,
        reason_code="state_dir_protected",
        target="collision.txt",
        now=_FIXED_NOW,
    )

    assert saved is not None
    assert saved.name.endswith(f"-{replacement}.txt")
    assert occupied.read_text(encoding="utf-8") == "do-not-overwrite"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_quarantine_file_is_owner_only_on_posix(tmp_path: Path) -> None:
    from core.quarantine import backup_blocked_command

    root = tmp_path / "project"
    root.mkdir()

    saved = backup_blocked_command(
        str(root),
        command="rm private.txt",
        agent_key=_FIXED_AGENT,
        reason_code="state_dir_protected",
        target="private.txt",
    )

    assert saved is not None
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


@pytest.mark.parametrize("tamper", ["delete", "size", "digest"])
def test_success_requires_post_gc_existence_size_and_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    import core.quarantine as quarantine

    root = tmp_path / "project"
    root.mkdir()
    real_gc = quarantine._gc

    def _tampering_gc(directory: Path, **kwargs: object) -> None:
        real_gc(directory, **kwargs)
        created = next(directory.glob("blocked-*.txt"))
        if tamper == "delete":
            created.unlink()
        elif tamper == "size":
            created.write_bytes(created.read_bytes() + b"x")
        else:
            payload = bytearray(created.read_bytes())
            payload[-1] ^= 1
            created.write_bytes(payload)

    monkeypatch.setattr(quarantine, "_gc", _tampering_gc)

    saved = quarantine.backup_blocked_command(
        str(root),
        command="rm verify-me.txt",
        agent_key=_FIXED_AGENT,
        reason_code="state_dir_protected",
        target="verify-me.txt",
    )

    assert saved is None


def test_gc_eviction_of_just_created_entry_is_not_reported_as_success(
    tmp_path: Path,
) -> None:
    from core.quarantine import backup_blocked_command

    root = tmp_path / "project"
    root.mkdir()

    saved = backup_blocked_command(
        str(root),
        command="rm immediately-evicted.txt",
        agent_key=_FIXED_AGENT,
        reason_code="state_dir_protected",
        target="immediately-evicted.txt",
        max_entries=0,
    )

    assert saved is None
