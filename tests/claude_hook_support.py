from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import TypeAlias


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_HOOKS = ROOT / "adapters" / "claude_code"

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class HookRun:
    output: JsonObject
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class HookHarness:
    cwd: Path
    project_dir: Path | None
    data_dir: Path
    home: Path | None = None
    profile_imports: bool = False

    def run(self, name: str, payload: JsonObject) -> HookRun:
        environment = os.environ.copy()
        environment.pop("SMTW_TEST_FORCE_ENABLE", None)
        if self.project_dir is None:
            environment.pop("CLAUDE_PROJECT_DIR", None)
        else:
            environment["CLAUDE_PROJECT_DIR"] = str(self.project_dir)
        environment["CLAUDE_PLUGIN_DATA"] = str(self.data_dir)
        environment["PYTHONUTF8"] = "1"
        if self.home is not None:
            environment["HOME"] = str(self.home)
            environment["USERPROFILE"] = str(self.home)
        if self.profile_imports:
            environment["PYTHONPROFILEIMPORTTIME"] = "1"
        process = subprocess.run(
            [sys.executable, str(CLAUDE_HOOKS / name)],
            input=json.dumps(payload, ensure_ascii=False),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self.cwd,
            env=environment,
        )
        assert process.returncode == 0, process.stderr
        raw: JsonValue = json.loads(process.stdout or "{}")
        assert isinstance(raw, dict)
        return HookRun(raw, process.stdout, process.stderr)


def write_config(root: Path, supervision: JsonValue = True) -> Path:
    config = root / ".fable-lite" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps({"schema_version": 1, "supervision": supervision}),
        encoding="utf-8",
    )
    return config


def registry_path(data_dir: Path, session_id: str) -> Path:
    digest = sha256(session_id.encode("utf-8")).hexdigest()
    return data_dir / "sessions" / f"{digest}.json"


def read_json(path: Path) -> JsonObject:
    raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def ledger_path(root: Path) -> Path:
    return root / ".fable-lite" / "ledger.json"
