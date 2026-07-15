from __future__ import annotations

import os
from pathlib import Path
from typing import Final


HOME_ROOT_ADVISORY: Final = (
    "[smtw] 홈 디렉토리는 provenance 스캔 범위를 초과해 미지원입니다. "
    "프로젝트 폴더에서 세션을 여세요."
)


def is_user_home_root(project_root: str | Path) -> bool:
    root = _normalized(project_root)
    if root is None:
        return False
    candidates = [os.path.expanduser("~")]
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(user_profile)
    return any(root == candidate for value in candidates if (candidate := _normalized(value)))


def _normalized(path: str | Path) -> str | None:
    try:
        resolved = Path(os.path.expanduser(os.fspath(path))).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    normalized = os.path.normcase(str(resolved))
    return normalized.casefold() if os.name == "nt" else normalized
