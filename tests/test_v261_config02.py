from __future__ import annotations

from pathlib import Path

import pytest

from adapters.claude_code.project_config import (
    ConfigLoadState,
    ConfigSource,
    load_project_config,
)
from claude_hook_support import write_config


def _load_malformed_pyproject(root: Path, declaration: str):
    write_config(root)
    (root / "pyproject.toml").write_text(
        f"{declaration}\nbroken =\n",
        encoding="utf-8",
    )
    return load_project_config(root)


@pytest.mark.parametrize(
    "declaration",
    (
        "[tool.smtw]",
        '[tool."smtw"]',
        '["tool".smtw]',
        '["tool"."smtw"]',
        "tool.smtw.supervision = true",
        'tool."smtw".supervision = true',
    ),
)
def test_malformed_equivalent_smtw_declarations_never_fall_back_to_legacy(
    tmp_path: Path,
    declaration: str,
) -> None:
    loaded = _load_malformed_pyproject(tmp_path, declaration)

    assert loaded.state is ConfigLoadState.DECLARED_INVALID
    assert loaded.source is ConfigSource.PYPROJECT
    assert loaded.values is None


@pytest.mark.parametrize(
    "declaration",
    (
        '[tool."smtw]',
        '["tool.smtw]',
        'tool."smtw.supervision = true',
        "tool.smtw.supervision",
    ),
)
def test_truncated_canonical_key_is_conservatively_declared_invalid(
    tmp_path: Path,
    declaration: str,
) -> None:
    loaded = _load_malformed_pyproject(tmp_path, declaration)

    assert loaded.state is ConfigLoadState.DECLARED_INVALID
    assert loaded.source is ConfigSource.PYPROJECT


@pytest.mark.parametrize(
    "content",
    (
        "# [tool.smtw]\nbroken =\n",
        'message = "[tool.smtw]"\nbroken =\n',
        "message = '[tool.smtw]'\nbroken =\n",
        'message = """\n[tool."smtw"]\n"""\nbroken =\n',
        "message = '''\ntool.smtw.supervision = true\n'''\nbroken =\n",
        'items = ["tool.smtw.supervision = true"]\nbroken =\n',
        "[tool.smtw.fake]\nbroken =\n",
        "[[tool.smtw]]\nbroken =\n",
        '[other."smtw"]\nbroken =\n',
    ),
)
def test_malformed_unrelated_toml_text_still_allows_legacy_fallback(
    tmp_path: Path,
    content: str,
) -> None:
    write_config(tmp_path)
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.VALID
    assert loaded.source is ConfigSource.LEGACY
    assert loaded.values is not None
    assert loaded.values["supervision"] is True


def test_malformed_pyproject_without_smtw_is_provably_absent(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nbroken =\n',
        encoding="utf-8",
    )

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.ABSENT
    assert loaded.source is None


def test_dedicated_config_still_has_precedence_over_malformed_pyproject(
    tmp_path: Path,
) -> None:
    (tmp_path / ".smtw.toml").write_text(
        "schema_version = 1\nsupervision = false\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool."smtw"]\nbroken =\n',
        encoding="utf-8",
    )

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.VALID
    assert loaded.source is ConfigSource.DEDICATED
    assert loaded.values is not None
    assert loaded.values["supervision"] is False
