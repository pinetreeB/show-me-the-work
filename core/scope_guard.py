from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePath
import re
from typing import TypeAlias

JsonValue: TypeAlias = str | bool | list[str]
Decision: TypeAlias = dict[str, JsonValue]

_PATH_PATTERN = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]+")


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.replace("\\", "/") for item in value if isinstance(item, str)]


def _project_root(payload: Mapping[str, object]) -> str:
    value = payload.get("project_root")
    return value if isinstance(value, str) and value else ""


def _canonical(path: str, project_root: str) -> str:
    raw = Path(path)
    if not raw.is_absolute() and project_root:
        raw = Path(project_root) / raw
    try:
        normalized = raw.resolve()
    except OSError:
        normalized = raw.absolute()
    return normalized.as_posix().rstrip("/").casefold()


def _under(path: str, root: str, project_root: str) -> bool:
    normalized = _canonical(path, project_root)
    base = _canonical(root, project_root)
    return normalized == base or normalized.startswith(f"{base}/")


def _prompt_mentions(prompt: str, path: str) -> bool:
    parts = [PurePath(path).name, path.replace("\\", "/")]
    lowered = prompt.lower()
    return any(part.lower() in lowered for part in parts if part)


def _prompt_has_no_path_pattern(prompt: str) -> bool:
    return not _PATH_PATTERN.search(prompt)


def evaluate_scope(payload: Mapping[str, object]) -> Decision:
    prompt_value = payload.get("prompt")
    prompt = prompt_value if isinstance(prompt_value, str) else ""
    project_root = _project_root(payload)
    changed_files = _string_list(payload.get("changed_files"))
    requested_paths = _string_list(payload.get("requested_paths"))

    if not requested_paths and _prompt_has_no_path_pattern(prompt):
        # 요청 범위가 애초에 특정되지 않은 프롬프트(대명사·심볼명 지칭 등)에서는
        # "범위 밖" 판정 자체가 무근거하다 — p5b·E1에서 반복 확인된 허위 경고.
        return {"decision": "allow", "out_of_scope": [], "message": "scope ok (요청 범위 미특정)"}

    out_of_scope: list[str] = []
    for changed in changed_files:
        if requested_paths:
            if not any(_under(changed, requested, project_root) for requested in requested_paths):
                out_of_scope.append(changed)
        elif prompt and not _prompt_mentions(prompt, changed):
            out_of_scope.append(changed)

    if out_of_scope:
        return {
            "decision": "warn",
            "out_of_scope": out_of_scope,
            "message": (
                "범위 이탈 가능성: 요청 범위 밖 파일 수정이 감지되었습니다. "
                "근본원인 수정을 위한 사이드파일이면 무시 가능하지만 근거를 남기세요. "
                "/ Potential scope drift detected; root-cause side-file edits may be valid with evidence."
            ),
        }
    return {"decision": "allow", "out_of_scope": [], "message": "scope ok"}
