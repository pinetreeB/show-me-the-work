"""Read-only activation check shared by non-Claude hook entry points."""
from __future__ import annotations

import os
from pathlib import Path

def supervision_enabled(root: str | Path) -> bool:
    candidate = Path(root).expanduser().resolve()
    # Match Claude's home safeguard and existing isolated-test override.
    if candidate == Path.home().resolve():
        return False
    if os.environ.get('SMTW_TEST_FORCE_ENABLE') == '1':
        return True
    from adapters.claude_code.project_config import config_state

    enabled, _digest, _corrupt = config_state(candidate)
    return enabled
