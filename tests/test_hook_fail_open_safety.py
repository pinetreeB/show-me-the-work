from __future__ import annotations

import ast
from pathlib import Path

ADAPTERS_DIR = Path(__file__).resolve().parents[1] / "adapters"

# common.py/__init__.py는 훅 진입점이 아니라 진입점 스크립트가(자신의 try 블록 안에서)
# 지연 로드하는 공유 헬퍼 모듈이라 대상에서 제외한다 — 그 파일들의 최상단 import는
# 호출하는 쪽의 try 블록 실행 시점에 비로소 평가되므로 fail-open을 깨지 않는다.
_EXCLUDED_NAMES = {"__init__.py", "common.py"}


def _hook_entrypoint_files() -> list[Path]:
    return sorted(
        path
        for path in ADAPTERS_DIR.rglob("*.py")
        if path.name not in _EXCLUDED_NAMES
    )


def _top_level_risky_imports(path: Path) -> list[str]:
    """모듈 최상단(함수/클래스 바깥)에서 core.*나 adapters.*를 import하는 문장을 찾는다.
    이런 import가 main()의 try 블록 밖에 있으면, core/adapters에 문제가 생겼을 때
    fail-open 없이 훅 전체가 죽는다(v1 릴리스 심사 B1 — antigravity/oma_hook.py에서 발견)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in tree.body:  # 모듈 최상단 문장만(함수 안은 순회하지 않음)
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "core" or node.module.startswith("core.") or node.module == "adapters" or node.module.startswith("adapters."):
                offenders.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "core" or alias.name.startswith("core.") or alias.name == "adapters" or alias.name.startswith("adapters."):
                    offenders.append(f"import {alias.name}")
    return offenders


def test_hook_entrypoints_have_no_module_level_core_or_adapters_imports() -> None:
    files = _hook_entrypoint_files()
    assert files, "adapters/ 아래 훅 진입점 파일을 찾지 못했다 — 테스트 대상 경로를 확인하라"

    violations: dict[str, list[str]] = {}
    for path in files:
        offenders = _top_level_risky_imports(path)
        if offenders:
            violations[str(path.relative_to(ADAPTERS_DIR.parent))] = offenders

    assert not violations, (
        "다음 훅 진입점이 core/adapters를 모듈 최상단(try 블록 밖)에서 import한다 — "
        "core/adapters 쪽 오류 시 fail-open 없이 그대로 죽는다: "
        f"{violations}"
    )


def test_antigravity_handlers_each_locally_import_what_they_use() -> None:
    # B1이 정확히 고쳐졌는지 대상 파일 하나를 직접 짚어 확인 — 실물 이벤트 handler 4개
    # 전부 자신이 쓰는 core 심볼을 함수 본문 안에서 import해야 한다.
    path = ADAPTERS_DIR / "antigravity" / "oma_hook.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    handler_names = {
        "handle_pre_tool_use",
        "handle_post_tool_use",
        "handle_stop",
        "handle_pre_invocation",
    }
    found: dict[str, bool] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in handler_names:
            has_core_import = any(
                isinstance(child, ast.ImportFrom) and child.module and child.module.startswith("core")
                for child in ast.walk(node)
            )
            found[node.name] = has_core_import

    assert found == {name: True for name in handler_names}, (
        f"antigravity의 4개 handle_* 함수는 전부 자기 본문 안에서 core.* import를 해야 한다: {found}"
    )
