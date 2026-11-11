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


@pytest.fixture(autouse=True)
def forbid_real_claims_judge_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    from smtw.claims import judge

    def forbidden(*args, **kwargs):
        pytest.fail("REAL MODEL LAUNCH FORBIDDEN: global test tripwire")

    monkeypatch.setattr(judge, "_launch", forbidden)


@pytest.fixture(autouse=True)
def forbid_test_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests use injected transports; even loopback and DNS are forbidden."""
    import re
    import socket

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def forbidden(*args, **kwargs):
        pytest.fail("NETWORK CALL FORBIDDEN: global test tripwire")

    def _is_pymp_unix_ipc(sock: object, address: object) -> bool:
        af_unix = getattr(socket, "AF_UNIX", None)
        if af_unix is None or getattr(sock, "family", None) != af_unix:
            return False
        if isinstance(address, bytes):
            addr = address.decode("utf-8", errors="surrogateescape")
        elif isinstance(address, str):
            addr = address
        else:
            return False
        norm = addr.replace("\\", "/")
        if "\x00" in norm or ".." in norm.split("/"):
            return False
        return bool(re.search(r"(?:^|/)pymp-[^/]+/", norm))

    def guarded_connect(sock, *args, **kwargs):
        if len(args) == 1 and not kwargs and _is_pymp_unix_ipc(sock, args[0]):
            return real_connect(sock, args[0])
        forbidden()

    def guarded_connect_ex(sock, *args, **kwargs):
        if len(args) == 1 and not kwargs and _is_pymp_unix_ipc(sock, args[0]):
            return real_connect_ex(sock, args[0])
        forbidden()

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
