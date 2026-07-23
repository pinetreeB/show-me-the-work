from __future__ import annotations

import sys


def run(command: str | None = None) -> int:
    from smtw.cli import main

    if command is not None:
        sys.argv.insert(1, command)
    return main()
