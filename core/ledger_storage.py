from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time

from .state_layout import state_dir as _state_dir


# Windows AV / indexer filter drivers can briefly hold a freshly written temp or destination
# file, so os.replace transiently raises PermissionError. A single 10ms retry rarely covers the
# scan latency; back off geometrically before surfacing the failure (v2.6.2 durability — H1: a
# dropped ledger write let a capped gate emit an unpersisted block, i.e. the 3==2 lost update).
REPLACE_RETRY_DELAYS_SECONDS: tuple[float, ...] = (0.01, 0.04, 0.12, 0.3)


def state_dir(project_root: str) -> Path:
    return _state_dir(project_root)


def ledger_path(project_root: str) -> Path:
    return state_dir(project_root) / "ledger.json"


def atomic_write_text(destination: Path, serialized: str, prefix: str = "ledger-") -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=destination.parent,
        prefix=prefix,
        suffix=".tmp",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            _ = handle.write(serialized)
        _replace_with_retries(temporary, destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_bytes(destination: Path, content: bytes, prefix: str = "ledger-") -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "wb",
        delete=False,
        dir=destination.parent,
        prefix=prefix,
        suffix=".tmp",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            _ = handle.write(content)
        _replace_with_retries(temporary, destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _replace_with_retries(source: Path, destination: Path) -> None:
    delays = REPLACE_RETRY_DELAYS_SECONDS
    for index in range(len(delays) + 1):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            # Only the transient AV/indexer lock is worth retrying; other OSErrors propagate.
            if index == len(delays):
                raise
            time.sleep(delays[index])
