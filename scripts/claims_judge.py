"""Visible-terminal entrypoint; no automatic calls on import."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smtw.claims.judge import main

if __name__ == "__main__":
    raise SystemExit(main())
