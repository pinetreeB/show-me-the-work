# /// script
# requires-python = ">=3.12"
# ///
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter
from typing import Final, TypeAlias


ROOT: Final = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ledger_v2 import apply_v2_event  # noqa: E402

DEFAULT_FIXTURE: Final = ROOT / "tmp" / "friction-fix" / "fixture-ledger-8mb.json"
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


def _prepare_project(root: Path) -> None:
    (root / ".git").mkdir(parents=True)
    state = root / ".fable-lite"
    state.mkdir()
    (root / ".smtw.toml").write_text(
        "schema_version = 1\nsupervision = true\n",
        encoding="utf-8",
    )
    (state / "config.json").write_text(
        json.dumps({"schema_version": 1, "supervision": True}),
        encoding="utf-8",
    )


def _write_fixture_with_turn(
    root: Path,
    fixture: Path,
    host: str,
    session_id: str,
    agent: str,
) -> None:
    ledger: JsonValue = json.loads(fixture.read_bytes())
    if not isinstance(ledger, dict):
        raise TypeError("fixture ledger must be an object")
    raw_sequence = ledger.get("event_seq")
    sequence = raw_sequence if isinstance(raw_sequence, int) else 0
    _ = apply_v2_event(
        ledger,
        {
            "event": "prompt",
            "seq": sequence + 1,
            "host": host,
            "session_id": session_id,
            "agent": agent,
            "turn_id": f"{agent}-cache-turn",
            "prompt": "inspect repository",
        },
    )
    (root / ".fable-lite" / "ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _run(
    command: list[str],
    payload: dict[str, JsonValue],
    env: dict[str, str],
    adapter_root: Path,
) -> float:
    started = perf_counter()
    process = subprocess.run(
        command,
        cwd=adapter_root,
        env=env,
        input=json.dumps(payload),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed_ms = (perf_counter() - started) * 1000
    if process.returncode != 0:
        raise RuntimeError(
            f"adapter exited {process.returncode}: {process.stderr or process.stdout}"
        )
    return elapsed_ms


def _claude_sample(
    base: Path,
    fixture: Path,
    index: int,
    adapter_root: Path,
) -> float:
    root = base / f"claude-{index}"
    data_dir = base / f"claude-data-{index}"
    _prepare_project(root)
    env = os.environ.copy()
    env.update(
        {
            "CLAUDE_PROJECT_DIR": str(root),
            "CLAUDE_PLUGIN_DATA": str(data_dir),
            "PYTHONUTF8": "1",
        }
    )
    session_id = f"w4c-claude-{index}"
    prompt_payload: dict[str, JsonValue] = {
        "cwd": str(root),
        "session_id": session_id,
        "prompt": "app.py를 수정해 주세요",
    }
    _ = _run(
        [
            sys.executable,
            str(adapter_root / "adapters/claude_code/user_prompt_submit.py"),
        ],
        prompt_payload,
        env,
        adapter_root,
    )
    _write_fixture_with_turn(
        root,
        fixture,
        "claude_code",
        session_id,
        "claude",
    )
    return _run(
        [sys.executable, str(adapter_root / "adapters/claude_code/pre_tool_use.py")],
        {
            "cwd": str(root),
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
            "tool_use_id": f"claude-tool-{index}",
        },
        env,
        adapter_root,
    )


def _dispatcher_sample(
    base: Path,
    fixture: Path,
    index: int,
    adapter: str,
    adapter_root: Path,
) -> float:
    root = base / f"{adapter}-{index}"
    _prepare_project(root)
    if adapter == "antigravity":
        host = "antigravity"
        agent = "antigravity"
        command = [
            sys.executable,
            str(adapter_root / "adapters/antigravity/oma_hook.py"),
            "PreToolUse",
        ]
    else:
        host = "qwen_code"
        agent = "qwen"
        command = [
            sys.executable,
            str(adapter_root / "adapters/qwen_code/qwen_hook.py"),
            "PreToolUse",
        ]
    session_id = f"w4c-{adapter}-{index}"
    _write_fixture_with_turn(root, fixture, host, session_id, agent)
    return _run(
        command,
        {
            "cwd": str(root),
            "session_id": session_id,
            "turn_id": f"{agent}-cache-turn",
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
            "tool_use_id": f"{adapter}-tool-{index}",
        },
        os.environ.copy(),
        adapter_root,
    )


def _summary(samples: list[float]) -> dict[str, JsonValue]:
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "samples_ms": [round(value, 3) for value in samples],
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
    }


def _statusless_count(value: JsonValue) -> int:
    if not isinstance(value, dict):
        return 0
    total = 0
    turns = value.get("active_turns")
    if isinstance(turns, dict):
        for turn in turns.values():
            if not isinstance(turn, dict):
                continue
            invocations = turn.get("invocations")
            if isinstance(invocations, dict):
                total += sum(
                    1
                    for invocation in invocations.values()
                    if isinstance(invocation, dict) and "status" not in invocation
                )
    return total


def _benchmark(
    fixture: Path,
    label: str,
    samples: int,
    adapter_root: Path,
) -> dict[str, JsonValue]:
    fixture_bytes = fixture.read_bytes()
    fixture_json: JsonValue = json.loads(fixture_bytes)
    with tempfile.TemporaryDirectory(prefix="w4c-adapter-cache-") as raw:
        base = Path(raw)
        results = {
            "claude_code": [
                _claude_sample(base, fixture, index, adapter_root)
                for index in range(samples)
            ],
            "antigravity": [
                _dispatcher_sample(
                    base,
                    fixture,
                    index,
                    "antigravity",
                    adapter_root,
                )
                for index in range(samples)
            ],
            "qwen_code": [
                _dispatcher_sample(
                    base,
                    fixture,
                    index,
                    "qwen_code",
                    adapter_root,
                )
                for index in range(samples)
            ],
        }
    return {
        "schema_version": 1,
        "label": label,
        "fixture": {
            "path": str(fixture),
            "bytes": len(fixture_bytes),
            "sha256": sha256(fixture_bytes).hexdigest(),
            "statusless_invocations": _statusless_count(fixture_json),
        },
        "settings": {
            "samples": samples,
            "concurrency": 1,
            "adapter_root": str(adapter_root),
        },
        "method": "fresh process and fresh statusless fixture with a matching active turn per timed PreToolUse",
        "adapters": {name: _summary(values) for name, values in results.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark non-Codex adapter cache paths.")
    _ = parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    _ = parser.add_argument("--label", required=True)
    _ = parser.add_argument("--samples", type=int, default=5)
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--adapter-root", type=Path, default=ROOT)
    options = parser.parse_args(argv)
    if options.samples < 1:
        parser.error("--samples must be positive")
    receipt = _benchmark(
        options.fixture.resolve(),
        options.label,
        options.samples,
        options.adapter_root.resolve(),
    )
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
