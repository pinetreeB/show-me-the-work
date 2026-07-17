from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def claude_plugin_data_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("claude-plugin-data")


@pytest.fixture(autouse=True)
def force_claude_adapter_for_legacy_tests(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    claude_plugin_data_root: Path,
) -> Iterator[None]:
    digest = sha256(request.node.nodeid.encode("utf-8")).hexdigest()
    monkeypatch.setenv(
        "CLAUDE_PLUGIN_DATA",
        str(claude_plugin_data_root / digest),
    )
    monkeypatch.setenv("SMTW_TEST_FORCE_ENABLE", "1")
    yield
