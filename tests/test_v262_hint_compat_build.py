"""v2.6.2 HINT-03 + COMPAT-03 + BUILD-03 (RED-first).

HINT-03: `env -S/--split-string "..."` payload를 shell-like argv로 재토큰해
인라인 python 쓰기 힌트를 탐지한다(friction 전용 — R2 권한 경계 무변경).
COMPAT-03: `-m` 탐지는 interpreter invocation prefix로 한정 — 스크립트 이름 뒤
애플리케이션 argument를 interpreter option으로 오인하지 않는다.
BUILD-03: build backend(setuptools)는 upper bound가 있는 range pin이다.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tomllib

from core.shell_hints import shell_candidate_paths


ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# HINT-03 — env -S payload re-tokenize (friction only)
# ---------------------------------------------------------------------------

_ENV_S_WRITE = "env -S \"python -c 'open(\\\".smtw/x\\\", \\\"w\\\")'\""
_ENV_S_WRITE_LONG = "env --split-string \"python -c 'open(\\\".smtw/y\\\", \\\"w\\\")'\""
_ENV_S_WRITE_JOINED = "env -S\"python -c 'open(\\\".smtw/z\\\", \\\"w\\\")'\""
_ENV_S_WRITE_WITH_ASSIGNMENT = (
    "env -S \"FOO=1 python -c 'open(\\\".smtw/w\\\", \\\"w\\\")'\""
)


def test_hint_03_env_split_string_python_write_is_friction_candidate() -> None:
    paths = shell_candidate_paths(_ENV_S_WRITE)

    # 수정 전: -S payload가 단일 미토큰 토큰으로 남아 python -c 미탐지(RED).
    assert ".smtw/x" in paths


def test_hint_03_env_split_string_long_and_joined_forms() -> None:
    assert ".smtw/y" in shell_candidate_paths(_ENV_S_WRITE_LONG)
    assert ".smtw/z" in shell_candidate_paths(_ENV_S_WRITE_JOINED)


def test_hint_03_env_split_string_with_assignment_prefix() -> None:
    assert ".smtw/w" in shell_candidate_paths(_ENV_S_WRITE_WITH_ASSIGNMENT)


def test_hint_03_plain_python_inline_write_still_detected() -> None:
    paths = shell_candidate_paths("python -c 'open(\".smtw/p\", \"w\")'")

    assert ".smtw/p" in paths


def test_hint_03_env_split_string_without_writes_yields_nothing() -> None:
    assert shell_candidate_paths("env -S \"echo hello\"") == ()


# ---------------------------------------------------------------------------
# COMPAT-03 — interpreter-prefix-only -m detection
# ---------------------------------------------------------------------------

_APP_SOURCE = (
    "import fable_lite, smtw, warnings\n"
    "warnings.simplefilter('ignore')\n"
    "print('IDENTICAL' if fable_lite is smtw else 'SHIM')\n"
)


def _run_app(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "app.py").write_text(_APP_SOURCE, encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(ROOT), os.environ.get("PYTHONPATH", "")]
        ),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONWARNINGS": "ignore",
    }
    return subprocess.run(
        [sys.executable, "app.py", *args],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_compat_03_app_argument_dash_m_does_not_hijack_identity(
    tmp_path: Path,
) -> None:
    result = _run_app(tmp_path, "--example", "-m", "fable_lite.cli")

    assert result.returncode == 0, result.stderr
    # 수정 전: sys.orig_argv 전체에서 -m을 찾아 fable_lite.cli 실행으로 오인 →
    # sys.modules[fable_lite]가 smtw로 alias되지 않아 SHIM(RED).
    assert "IDENTICAL" in result.stdout


def test_compat_03_module_execution_still_works(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(ROOT), os.environ.get("PYTHONPATH", "")]
        ),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONWARNINGS": "ignore",
    }
    result = subprocess.run(
        [sys.executable, "-m", "fable_lite.cli", "--help"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert "usage" in result.stdout.lower()


def test_compat_03_interpreter_option_with_value_before_dash_m(
    tmp_path: Path,
) -> None:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(ROOT), os.environ.get("PYTHONPATH", "")]
        ),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONWARNINGS": "ignore",
    }
    result = subprocess.run(
        [sys.executable, "-W", "ignore", "-m", "fable_lite.cli", "--help"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# BUILD-03 — setuptools backend range pin
# ---------------------------------------------------------------------------


def test_build_03_setuptools_backend_has_upper_bound_pin() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requires = pyproject["build-system"]["requires"]
    assert isinstance(requires, list)
    entries = [entry for entry in requires if entry.startswith("setuptools")]

    assert len(entries) == 1
    entry = entries[0]
    # 수정 전: "setuptools>=69" — upper bound 없어 isolated build가 최신을 받아
    # 재현성이 깨진다(RED).
    assert ">=" in entry, f"lower bound missing: {entry}"
    assert "<" in entry, f"upper bound missing: {entry}"
