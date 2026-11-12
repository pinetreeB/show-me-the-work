"""Run the sealed local smoke only after a separate operator start signal."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smtw.claims.local_smoke import main


if __name__ == '__main__':
    raise SystemExit(main())
