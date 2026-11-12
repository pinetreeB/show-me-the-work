from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from adapters.codex_cli.common import PayloadError, emit, read_payload, supervision_enabled

    try:
        payload = read_payload()
        if not supervision_enabled(payload):
            return emit({})
        # Turn setup belongs to UserPromptSubmit, which has the actual prompt.
        # This optional entry point does not register or activate any hook.
        return emit({})
    except (PayloadError, OSError, ValueError):
        return emit({})


if __name__ == '__main__':
    raise SystemExit(main())
