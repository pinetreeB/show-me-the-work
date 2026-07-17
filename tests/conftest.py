from __future__ import annotations

from collections.abc import Iterator
import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def force_claude_adapter_for_legacy_tests() -> Iterator[None]:
    previous = os.environ.get("SMTW_TEST_FORCE_ENABLE")
    os.environ["SMTW_TEST_FORCE_ENABLE"] = "1"
    yield
    if previous is None:
        os.environ.pop("SMTW_TEST_FORCE_ENABLE", None)
    else:
        os.environ["SMTW_TEST_FORCE_ENABLE"] = previous
