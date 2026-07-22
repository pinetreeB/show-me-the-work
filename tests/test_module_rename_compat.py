from __future__ import annotations

import ast
import json
import multiprocessing
import os
from pathlib import Path
import pickle
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
WARNING_MESSAGE = "fable_lite is deprecated; import smtw instead"
SUBMODULE_EXCLUSIONS = {"__init__", "__main__"}


def _python(
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(ROOT),
    }
    if extra_env is not None:
        environment.update(extra_env)
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _declared_submodules() -> tuple[str, ...]:
    tree = ast.parse((ROOT / "fable_lite" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "_SUBMODULES" for target in node.targets):
                value = ast.literal_eval(node.value)
                assert isinstance(value, tuple)
                assert all(isinstance(item, str) for item in value)
                return value
    raise AssertionError("fable_lite shim does not declare _SUBMODULES")


def _spawn_identity(queue: multiprocessing.Queue[tuple[bool, bool]]) -> None:
    import fable_lite
    import fable_lite.cli
    import smtw
    import smtw.cli

    queue.put((fable_lite is smtw, fable_lite.cli is smtw.cli))


def test_shim_inventory_matches_canonical_modules() -> None:
    actual = {
        path.stem
        for path in (ROOT / "smtw").glob("*.py")
        if path.stem not in SUBMODULE_EXCLUSIONS
    }
    declared = _declared_submodules()

    assert len(declared) == len(set(declared))
    assert set(declared) == actual


def test_shim_aliases_every_module_and_warns_once_per_process() -> None:
    code = f"""
import importlib
import json
import warnings

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always", DeprecationWarning)
    import fable_lite
    import smtw
    identities = [fable_lite is smtw]
    for name in {list(_declared_submodules())!r}:
        legacy = importlib.import_module(f"fable_lite.{{name}}")
        canonical = importlib.import_module(f"smtw.{{name}}")
        identities.append(legacy is canonical)
    import fable_lite.cli
    import smtw.cli
    reloaded = importlib.reload(fable_lite.cli)
    identities.append(reloaded is fable_lite.cli is smtw.cli)

matching = [item for item in caught if str(item.message) == {WARNING_MESSAGE!r}]
print(json.dumps({{"identities": all(identities), "warnings": len(matching)}}))
"""
    result = _python("-c", code)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"identities": True, "warnings": 1}


def test_legacy_module_json_stdout_is_not_polluted(tmp_path: Path) -> None:
    result = _python(
        "-W",
        "always::DeprecationWarning",
        "-m",
        "fable_lite",
        "intent",
        "show",
        "--root",
        str(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    assert result.stderr.count(WARNING_MESSAGE) == 1


def test_strict_warning_policy_uses_child_python_process() -> None:
    default = _python("-c", "import fable_lite")
    strict = _python(
        "-c",
        "import fable_lite",
        extra_env={"PYTHONWARNINGS": "error"},
    )

    assert default.returncode == 0, default.stderr
    assert strict.returncode != 0
    assert "DeprecationWarning" in strict.stderr
    assert WARNING_MESSAGE in strict.stderr


def test_legacy_pickle_fixture_loads_as_canonical_class(tmp_path: Path) -> None:
    from smtw.card import TaskCard

    expected = TaskCard(
        path=tmp_path / "card.json",
        slug="legacy-card",
        owner="codex",
        allowed_paths=["smtw/**"],
        forbidden_paths=[],
        verify="python -m pytest",
        done_artifact="",
        sentinel="tmp/done",
    )
    canonical_payload = pickle.dumps(expected, protocol=0)
    assert b"csmtw.card\nTaskCard\n" in canonical_payload
    legacy_payload = canonical_payload.replace(
        b"csmtw.card\nTaskCard\n",
        b"cfable_lite.card\nTaskCard\n",
    )

    restored = pickle.loads(legacy_payload)

    assert restored == expected
    assert type(restored) is TaskCard


def test_windows_spawn_child_preserves_alias_identity() -> None:
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_spawn_identity, args=(queue,))

    process.start()
    process.join(timeout=30)

    assert process.exitcode == 0
    assert queue.get(timeout=5) == (True, True)


def test_module_entry_points_work_with_safe_path_flag() -> None:
    canonical = _python("-P", "-m", "smtw", "version")
    legacy = _python("-P", "-m", "fable_lite", "version")

    assert canonical.returncode == 0, canonical.stderr
    assert legacy.returncode == 0, legacy.stderr
    assert canonical.stdout == legacy.stdout
