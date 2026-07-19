from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from adapters.claude_code import atomic_file, session_registry


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows path contract")


def _destination_for_temporary_length(
    root: Path, temporary_length: int
) -> Path:
    token = "a" * 16
    fixed = len(str(root.resolve())) + 1 + 1 + 1 + len(token) + len(".tmp")
    name_length = temporary_length - fixed
    assert 1 <= name_length <= 255
    return root / ("d" * name_length)


@pytest.mark.parametrize("temporary_length", [259, 260, 262])
def test_atomic_write_crosses_windows_max_path_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    temporary_length: int,
) -> None:
    root = tmp_path / "boundary"
    root.mkdir()
    monkeypatch.setattr(atomic_file.secrets, "token_hex", lambda _size: "a" * 16)
    destination = _destination_for_temporary_length(root, temporary_length)
    temporary = destination.with_name(f".{destination.name}.{'a' * 16}.tmp")
    assert len(str(temporary.resolve())) == temporary_length

    atomic_file.atomic_write(destination, {"length": temporary_length})

    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "length": temporary_length
    }
    assert temporary.exists() is False


def test_warn_once_persists_in_deep_plugin_data_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(atomic_file.secrets, "token_hex", lambda _size: "b" * 16)
    session_id = "deep-session"
    code = "scope_warning"
    prefix = session_registry.session_digest(session_id)
    code_digest = session_registry.sha256(code.encode("utf-8")).hexdigest()[:16]
    base = tmp_path / "plugin-data"
    destination = base / "warnings" / f"{prefix}-{code_digest}.json"
    temporary = destination.with_name(f".{destination.name}.{'b' * 16}.tmp")
    extension = 262 - len(str(temporary.resolve()))
    assert extension > 1
    data_dir = base / ("x" * (extension - 1))
    destination = data_dir / "warnings" / f"{prefix}-{code_digest}.json"
    temporary = destination.with_name(f".{destination.name}.{'b' * 16}.tmp")
    assert len(str(temporary.resolve())) == 262

    assert session_registry.warn_once(data_dir, session_id, code) is True
    assert session_registry.warn_once(data_dir, session_id, code) is False
    assert json.loads(destination.read_text(encoding="utf-8"))["code"] == code
