from __future__ import annotations

import json
from multiprocessing.connection import Connection, Listener, wait
import os
from pathlib import Path
import subprocess
import sys
from unittest import skipIf

from core.contract import evaluate_pretool_contract
from core.ledger import JsonObject, JsonValue, load_ledger, record_event
from core.ledger_v1 import default_ledger
from core.ledger_v2 import default_v2_ledger, refresh_v1_projection


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_ONLY = skipIf(os.name != "nt", "Windows canonical-path contract")
MIGRATION_WORKER = "\n".join(
    (
        "from multiprocessing.connection import Client",
        "import sys",
        "import core.ledger_migration as migration",
        "from core.ledger import record_event",
        "channel = Client((sys.argv[1], int(sys.argv[2])), authkey=bytes.fromhex(sys.argv[3]))",
        "role, root, message = sys.argv[4:]",
        "if role == 'stalled':",
        "    convert = migration._convert_legacy",
        "    def pause_before_convert(legacy):",
        "        channel.send_bytes(b'paused')",
        "        channel.recv_bytes()",
        "        return convert(legacy)",
        "    migration._convert_legacy = pause_before_convert",
        "    _ = migration.migrate_v1_ledger(root)",
        "else:",
        "    channel.send_bytes(b'ready')",
        "    channel.recv_bytes()",
        "    _ = migration.migrate_v1_ledger(root)",
        "_ = record_event({'project_root': root, 'event': 'scope_warning', 'message': message})",
        "channel.send_bytes(b'done')",
        "channel.close()",
    )
)


def _prompt(root: Path, agent: str, session: str, **extra: JsonValue) -> JsonObject:
    payload: JsonObject = {
        "project_root": str(root),
        "event": "prompt",
        "agent": agent,
        "host": "host",
        "session_id": session,
        "turn_id": f"{agent}-{session}",
        "prompt": agent,
    }
    payload.update(extra)
    return record_event(payload)


def test_agent_scoped_goals_gate_ignores_another_prompt_projection(tmp_path: Path) -> None:
    _ = _prompt(tmp_path, "alpha", "one", needs_goals=True)
    _ = _prompt(tmp_path, "beta", "two", needs_goals=False)

    result = evaluate_pretool_contract(
        {
            "project_root": str(tmp_path),
            "tool_name": "Edit",
            "file_paths": ["app.py"],
            "agent": "alpha",
            "host": "host",
            "session_id": "one",
        }
    )

    assert result["decision"] == "block"


def _change(
    root: Path,
    event_id: str,
    change_id: str,
    path: str,
    before: str,
    after: str,
) -> JsonObject:
    return record_event(
        {
            "project_root": str(root),
            "event": "change",
            "agent": "alpha",
            "host": "host",
            "session_id": "one",
            "event_id": event_id,
            "paths": [
                {
                    "change_id": change_id,
                    "path": path,
                    "kind": "code",
                    "before": before,
                    "after": after,
                    "requires_verification": True,
                }
            ],
        }
    )


@WINDOWS_ONLY
def test_case_variants_share_one_pending_revision_and_revert(tmp_path: Path) -> None:
    _ = _prompt(tmp_path, "alpha", "one")
    _ = _change(tmp_path, "change-1", "change:1", "core/ledger.py", "base", "one")
    changed = _change(tmp_path, "change-2", "change:2", "CORE\\Ledger.py", "one", "two")

    active = changed["active_turns"]
    assert isinstance(active, dict)
    turn = active["host:one:alpha"]
    assert isinstance(turn, dict)
    revisions = turn["path_revisions"]
    assert isinstance(revisions, dict)
    assert list(revisions) == ["core/ledger.py"]
    revision = revisions["core/ledger.py"]
    assert isinstance(revision, dict)
    assert revision["path"] == "CORE\\Ledger.py"

    reverted = _change(tmp_path, "change-3", "change:3", "core/ledger.py", "two", "base")
    active = reverted["active_turns"]
    assert isinstance(active, dict)
    turn = active["host:one:alpha"]
    assert isinstance(turn, dict)
    assert turn["path_revisions"] == {}
    assert turn["pending_change_ids"] == []


def test_projection_merges_active_turn_counters_conservatively() -> None:
    ledger = default_v2_ledger()
    alpha = default_ledger()
    beta = default_ledger()
    alpha["last_change_seq"] = 9
    alpha["blocks"] = {"stop": 1}
    beta["last_change_seq"] = 4
    beta["blocks"] = {"stop": 0}
    ledger["active_turns"] = {"host:one:alpha": alpha, "host:two:beta": beta}
    ledger["event_seq"] = 12

    result = refresh_v1_projection(ledger, beta)

    assert result["last_change_seq"] == 9
    assert result["stop_blocks"] == 1


def _migration_process(
    host: str, port: int, authkey: bytes, role: str, root: Path, message: str
) -> subprocess.Popen[str]:
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            MIGRATION_WORKER,
            host,
            str(port),
            authkey.hex(),
            role,
            str(root),
            message,
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": python_path},
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_concurrent_migration_serializes_archive_and_events(tmp_path: Path) -> None:
    state = tmp_path / ".fable-lite"
    state.mkdir()
    legacy = default_ledger()
    legacy["prompt"] = "legacy"
    original = json.dumps(legacy, ensure_ascii=False).encode("utf-8")
    _ = (state / "ledger.json").write_bytes(original)
    authkey = os.urandom(16)
    listener = Listener(("127.0.0.1", 0), authkey=authkey)
    address = listener.address
    assert isinstance(address, tuple)
    host, port = address
    assert isinstance(host, str)
    assert isinstance(port, int)
    stalled = _migration_process(host, port, authkey, "stalled", tmp_path, "stalled")
    regular: subprocess.Popen[str] | None = None
    channels: list[Connection] = []
    try:
        stalled_channel = listener.accept()
        channels.append(stalled_channel)
        assert stalled_channel.recv_bytes() == b"paused"
        regular = _migration_process(host, port, authkey, "regular", tmp_path, "regular")
        regular_channel = listener.accept()
        channels.append(regular_channel)
        assert regular_channel.recv_bytes() == b"ready"
        regular_channel.send_bytes(b"go")
        assert wait([regular_channel], timeout=0.5) == []
        stalled_channel.send_bytes(b"go")
        assert stalled_channel.recv_bytes() == b"done"
        assert regular_channel.recv_bytes() == b"done"
        archive = state / "ledger.v1.json.bak"
        assert archive.exists()
        assert len(list(state.glob("ledger.v1.json.bak"))) == 1
        assert archive.read_bytes() == original
        ledger = load_ledger({"project_root": str(tmp_path)})
        warnings = ledger["scope_warnings"]
        assert isinstance(warnings, list)
        assert set(warnings) == {"stalled", "regular"}
    finally:
        for channel in channels:
            channel.close()
        listener.close()
        for process in (stalled, regular):
            if process is not None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                if process.poll() is None:
                    _ = process.kill()
                assert process.wait(timeout=10) == 0, stderr
