from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.claude_code.activation_policy import config_state, fallback_root
from adapters.claude_code.project_config import (
    ConfigLoadState,
    ConfigSource,
    load_project_config,
)
from claude_hook_support import HookHarness, JsonObject, write_config
from core.ledger_storage import ledger_path


def _prompt(root: Path, session_id: str) -> JsonObject:
    return {
        "cwd": str(root),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "edit app.py",
        "prompt_id": f"prompt-{session_id}",
        "session_id": session_id,
    }


def _write_smtw_config(
    root: Path,
    *,
    schema_version: str = "1",
    supervision: str = "true",
) -> Path:
    path = root / ".smtw.toml"
    path.write_text(
        f"schema_version = {schema_version}\nsupervision = {supervision}\n",
        encoding="utf-8",
    )
    return path


def _write_pyproject_config(
    root: Path,
    *,
    schema_version: str = "1",
    supervision: str = "true",
) -> Path:
    path = root / "pyproject.toml"
    path.write_text(
        "[project]\n"
        'name = "example"\n\n'
        "[tool.smtw]\n"
        f"schema_version = {schema_version}\n"
        f"supervision = {supervision}\n",
        encoding="utf-8",
    )
    return path


def test_dedicated_config_is_canonical_and_shadows_lower_sources(
    tmp_path: Path,
) -> None:
    write_config(tmp_path)
    _write_pyproject_config(tmp_path)
    _write_smtw_config(tmp_path, supervision="false")

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.VALID
    assert loaded.source is ConfigSource.DEDICATED
    assert config_state(tmp_path) == (False, "", False)


@pytest.mark.parametrize(
    "content",
    (
        "supervision = [",
        "schema_version = true\nsupervision = true\n",
        "schema_version = 1\nsupervision = 1\n",
        "schema_version = 1\n",
    ),
)
def test_invalid_dedicated_config_never_falls_back(
    tmp_path: Path,
    content: str,
) -> None:
    write_config(tmp_path)
    (tmp_path / ".smtw.toml").write_text(content, encoding="utf-8")

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.DECLARED_INVALID
    assert loaded.source is ConfigSource.DEDICATED
    assert config_state(tmp_path) == (False, "", True)


def test_unreadable_dedicated_config_never_falls_back(tmp_path: Path) -> None:
    write_config(tmp_path)
    (tmp_path / ".smtw.toml").mkdir()

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.DECLARED_INVALID
    assert loaded.source is ConfigSource.DEDICATED
    assert "cannot read" in loaded.detail


def test_valid_pyproject_table_shadows_legacy(tmp_path: Path) -> None:
    write_config(tmp_path)
    _write_pyproject_config(tmp_path, supervision="false")

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.VALID
    assert loaded.source is ConfigSource.PYPROJECT
    assert config_state(tmp_path) == (False, "", False)


def test_valid_pyproject_without_smtw_table_falls_back(tmp_path: Path) -> None:
    write_config(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = \"example\"\n",
        encoding="utf-8",
    )

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.VALID
    assert loaded.source is ConfigSource.LEGACY
    assert config_state(tmp_path)[0] is True


def test_unreadable_pyproject_never_falls_back(tmp_path: Path) -> None:
    write_config(tmp_path)
    (tmp_path / "pyproject.toml").mkdir()

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.DECLARED_INVALID
    assert loaded.source is ConfigSource.PYPROJECT
    assert "cannot read" in loaded.detail


@pytest.mark.parametrize(
    "content",
    (
        "[tool]\nsmtw = 1\n",
        "[tool.smtw]\nschema_version = true\nsupervision = true\n",
        "[tool.smtw]\nschema_version = 1\nsupervision = \"true\"\n",
        "[tool.smtw]\nschema_version = 1\n",
    ),
)
def test_declared_invalid_pyproject_never_falls_back(
    tmp_path: Path,
    content: str,
) -> None:
    write_config(tmp_path)
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.DECLARED_INVALID
    assert loaded.source is ConfigSource.PYPROJECT
    assert config_state(tmp_path) == (False, "", True)


@pytest.mark.parametrize(
    "content",
    (
        "[tool.smtw]\nschema_version = 1\nsupervision = true\nbroken = [\n",
        "broken = [\n[tool.smtw]\nschema_version = 1\nsupervision = true\n",
        "[tool.smtw] trailing-invalid-token\n",
    ),
)
def test_malformed_pyproject_with_real_smtw_header_is_invalid(
    tmp_path: Path,
    content: str,
) -> None:
    write_config(tmp_path)
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.DECLARED_INVALID
    assert loaded.source is ConfigSource.PYPROJECT


@pytest.mark.parametrize(
    "content",
    (
        "# [tool.smtw]\nbroken = [\n",
        'message = "[tool.smtw]"\nbroken = [\n',
        "message = '[tool.smtw]'\nbroken = [\n",
        'message = """\n[tool.smtw]\n"""\nbroken = [\n',
        "message = '''\n[tool.smtw]\n'''\nbroken = [\n",
        "[tool.smtw.fake]\nbroken = [\n",
        "[[tool.smtw]]\nbroken = [\n",
    ),
)
def test_malformed_unrelated_pyproject_markers_do_not_block_legacy_fallback(
    tmp_path: Path,
    content: str,
) -> None:
    write_config(tmp_path)
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.VALID
    assert loaded.source is ConfigSource.LEGACY
    assert config_state(tmp_path)[0] is True


def test_malformed_pyproject_without_any_config_is_absent(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("broken = [\n", encoding="utf-8")

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.ABSENT
    assert loaded.present is False
    assert config_state(tmp_path) == (False, "", False)


def test_legacy_json_remains_exact_fallback(tmp_path: Path) -> None:
    write_config(tmp_path)

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.VALID
    assert loaded.source is ConfigSource.LEGACY
    assert loaded.digest
    assert config_state(tmp_path)[0] is True


@pytest.mark.parametrize(
    "value",
    (
        b"{not-json",
        b"[]",
        b'{"schema_version":true,"supervision":true}',
        b'{"schema_version":1,"supervision":1}',
    ),
)
def test_invalid_legacy_json_is_declared_invalid(
    tmp_path: Path,
    value: bytes,
) -> None:
    state = tmp_path / ".fable-lite"
    state.mkdir()
    (state / "config.json").write_bytes(value)

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.DECLARED_INVALID
    assert loaded.source is ConfigSource.LEGACY
    assert config_state(tmp_path) == (False, "", True)


def test_same_config_has_one_normalized_schema_and_digest_across_sources(
    tmp_path: Path,
) -> None:
    dedicated = tmp_path / "dedicated"
    pyproject = tmp_path / "pyproject"
    legacy = tmp_path / "legacy"
    for root in (dedicated, pyproject, legacy):
        root.mkdir()

    (dedicated / ".smtw.toml").write_text(
        "schema_version = 1\n"
        "supervision = true\n"
        'label = "한글"\n'
        'tags = ["a", "b"]\n'
        "[limits]\ncount = 2\nratio = 1.5\n",
        encoding="utf-8",
    )
    pyproject_path = pyproject / "pyproject.toml"
    pyproject_path.write_text(
        "[project]\nname = \"example\"\nversion = \"1\"\n\n"
        "[tool.smtw]\n"
        "schema_version = 1\n"
        "supervision = true\n"
        'label = "한글"\n'
        'tags = ["a", "b"]\n'
        "[tool.smtw.limits]\ncount = 2\nratio = 1.5\n",
        encoding="utf-8",
    )
    legacy_state = legacy / ".fable-lite"
    legacy_state.mkdir()
    (legacy_state / "config.json").write_text(
        json.dumps(
            {
                "limits": {"ratio": 1.5, "count": 2},
                "tags": ["a", "b"],
                "label": "한글",
                "supervision": True,
                "schema_version": 1,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loads = tuple(
        load_project_config(root) for root in (dedicated, pyproject, legacy)
    )

    assert all(load.state is ConfigLoadState.VALID for load in loads)
    assert loads[0].values == loads[1].values == loads[2].values
    assert loads[0].canonical_bytes == loads[1].canonical_bytes
    assert loads[0].digest == loads[1].digest == loads[2].digest

    digest_before = loads[1].digest
    pyproject_path.write_text(
        pyproject_path.read_text(encoding="utf-8").replace(
            'version = "1"',
            'version = "2"',
        ),
        encoding="utf-8",
    )
    assert load_project_config(pyproject).digest == digest_before


def test_initial_cwd_fallback_discovers_ancestor_smtw_config(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    nested = root / "src" / "feature"
    nested.mkdir(parents=True)
    _write_smtw_config(root)

    selected = fallback_root(
        {"cwd": str(nested)},
        event_name="UserPromptSubmit",
        force=False,
    )

    assert selected == root.resolve()


def test_smtw_toml_activates_real_hook_without_legacy_config(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _write_smtw_config(root)
    harness = HookHarness(root, root, tmp_path / "plugin-data")

    result = harness.run(
        "user_prompt_submit.py",
        _prompt(root, "shared-toml"),
    )

    assert "hookSpecificOutput" in result.output
    assert ledger_path(str(root)).exists()
    assert (root / ".fable-lite" / "config.json").exists() is False


def test_disabled_dedicated_config_keeps_hook_off_despite_enabled_legacy(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    write_config(root)
    _write_smtw_config(root, supervision="false")
    harness = HookHarness(root, root, tmp_path / "plugin-data")

    result = harness.run(
        "user_prompt_submit.py",
        _prompt(root, "root-disabled"),
    )

    assert result.output == {}
    assert ledger_path(str(root)).exists() is False
