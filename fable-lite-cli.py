from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parent
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from smtw.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
