"""v2.7.1 어댑터 ledger 읽기 캐시 커버리지 — 런타임 계측 매트릭스.

High-02 수리로 재작성됨. 구판은 ``inspect.getsource(hook_module.main)``에 특정
문자열이 있는지 보는 **소스 텍스트 매칭**이었다. 그 방식은
1) 캐시가 실제로 원장 읽기를 줄이는지 재지 못해서(구문만 있고 동작하지 않아도
   통과) codex_cli 3개 훅의 캐시 누락을 통과시켰고,
2) claude 엔트리포인트만 봐서 커버리지가 구조적으로 불완전했다.

이 파일은 ``core.ledger._load_ledger_uncached`` 호출 횟수를 wraps patch로 계측하는
런타임 검증만 남긴다. 어댑터×엔트리포인트 조합은 ``_CACHE_MATRIX``에 등록하고
parametrize로 돌리며, 메타 테스트 2종이 "새 어댑터/엔트리포인트는 반드시 등록"을
강제한다(미등록 시 RED).

session_end 예외(v2.7.1 의도 유지): claude session_end는 실제 ledger load가 0회라
캐시 비대상이다. 누락으로 오인되지 않도록 0회를 별도 테스트로 고정한다.

쓰기가 포함된 훅(codex user_prompt_submit·post_tool_use)은 ``save_ledger``의
"쓰기 전 캐시 무효화" 정합 계약 때문에 1회가 불가능하다. 캐시 적용 하한값으로
검증하되, 임계값은 무캐시 기준선(각 6·12회 실측)에서 반드시 실패하도록 잡았다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

from adapters.antigravity import oma_hook
from adapters.claude_code import post_tool_use as claude_post_tool_use
from adapters.claude_code import pre_tool_use as claude_pre_tool_use
from adapters.claude_code import session_end as claude_session_end
from adapters.claude_code import stop as claude_stop
from adapters.claude_code import user_prompt_submit as claude_user_prompt_submit
from adapters.claude_code.bootstrap import HookContext
from adapters.codex_cli import post_tool_use as codex_post_tool_use
from adapters.codex_cli import pre_tool_use as codex_pre_tool_use
from adapters.codex_cli import stop as codex_stop
from adapters.codex_cli import user_prompt_submit as codex_user_prompt_submit
from adapters.qwen_code import common as qwen_common
from adapters.qwen_code import qwen_hook
from core import ledger as ledger_module
from core.ledger_schema import JsonObject
from core.ledger_storage import ledger_path
from core.ledger_v2 import apply_v2_event, default_v2_ledger
import pytest

_ADAPTERS_DIR = Path(__file__).resolve().parents[1] / "adapters"


# --- 공용 헬퍼 ----------------------------------------------------------------


def _claude_context(
    root: Path,
    data_dir: Path,
    prompt: str = "inspect repository",
) -> HookContext:
    return HookContext(
        active=True,
        payload={
            "cwd": str(root),
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
            "tool_use_id": "claude-cache-tool",
            "session_id": "claude-cache-session",
            "turn_id": "claude-cache-turn",
            "agent": "claude",
            "prompt": prompt,
        },
        root=root,
        data_dir=data_dir,
        session_id="claude-cache-session",
        agent="claude",
        task_mode="normal",
        turn_prompt=prompt,
        turn_prompt_id="claude-cache-turn",
        warning="",
    )


def _pretool_payload(root: Path, session_id: str, agent: str) -> JsonObject:
    return {
        "cwd": str(root),
        "project_root": str(root),
        "tool_name": "Bash",
        "tool_input": {"command": "git status --short"},
        "tool_use_id": f"{agent}-cache-tool",
        "session_id": session_id,
        "turn_id": f"{agent}-cache-turn",
        "agent": agent,
    }


def _seed_active_turn(root: Path, host: str, session_id: str, agent: str) -> None:
    ledger = default_v2_ledger()
    _ = apply_v2_event(
        ledger,
        {
            "event": "prompt",
            "seq": 1,
            "host": host,
            "session_id": session_id,
            "agent": agent,
            "turn_id": f"{agent}-cache-turn",
            "prompt": "inspect repository",
        },
    )
    turns = ledger["active_turns"]
    assert isinstance(turns, dict)
    turn = turns[f"{host}:{session_id}:{agent}"]
    assert isinstance(turn, dict)
    turn["invocations"] = {
        "tool:legacy": {
            "candidate_paths": ["core/ledger.py"],
            "started_seq": 1,
            "started_at": "2020-01-01T00:00:00+00:00",
        }
    }
    destination = ledger_path(str(root))
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps(ledger), encoding="utf-8")


def _run_claude_main(hook_module, context: HookContext) -> int:
    with (
        patch.object(hook_module, "bootstrap", return_value=context),
        patch.object(hook_module, "emit", return_value=0),
        patch.object(
            hook_module,
            "fail_open",
            side_effect=AssertionError("unexpected fail-open"),
        ),
    ):
        return hook_module.main()


class _BufferedStream:
    def __init__(self, data: bytes = b"") -> None:
        self.buffer = io.BytesIO(data)


def _run_codex_main(hook_module, payload: dict[str, object]) -> int:
    """codex 훅 메인 루틴을 실제 런타임 실행(stdin/stdout만 메모리 스트림)."""
    stdin = _BufferedStream(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    stdout = _BufferedStream()
    with (
        patch.object(sys, "stdin", stdin),
        patch.object(sys, "stdout", stdout),
        patch.object(
            hook_module,
            "_fail_open",
            side_effect=AssertionError("unexpected fail-open"),
        ),
    ):
        result = hook_module.main()
    output = stdout.buffer.getvalue().decode("utf-8", errors="replace")
    assert "fail-open" not in output, output
    return result


def _run_oma_hook(event: str, payload: dict[str, object]) -> int:
    with (
        patch.object(sys, "argv", [str(oma_hook.__file__), event]),
        patch.object(oma_hook, "read_payload", return_value=payload),
        patch.object(oma_hook, "emit", return_value=0),
        patch.object(
            oma_hook,
            "fail_open",
            side_effect=AssertionError("unexpected fail-open"),
        ),
    ):
        return oma_hook.main()


def _run_qwen_hook(event: str, payload: dict[str, object]) -> int:
    with (
        patch.object(sys, "argv", [str(qwen_hook.__file__), event]),
        patch.object(qwen_common, "read_payload", return_value=payload),
        patch.object(qwen_hook, "emit", return_value=0),
        patch.object(
            qwen_hook,
            "fail_open",
            side_effect=AssertionError("unexpected fail-open"),
        ),
    ):
        return qwen_hook.main()


# --- 엔트리포인트별 런타임 프로브 ---------------------------------------------


def _claude_pretool(root: Path) -> int:
    _seed_active_turn(root, "claude_code", "claude-cache-session", "claude")
    return _run_claude_main(
        claude_pre_tool_use, _claude_context(root, root / "claude-data")
    )


def _claude_posttool(root: Path) -> int:
    _seed_active_turn(root, "claude_code", "claude-cache-session", "claude")
    return _run_claude_main(
        claude_post_tool_use, _claude_context(root, root / "claude-data")
    )


def _claude_stop(root: Path) -> int:
    _seed_active_turn(root, "claude_code", "claude-cache-session", "claude")
    return _run_claude_main(claude_stop, _claude_context(root, root / "claude-data"))


def _claude_user_prompt(root: Path) -> int:
    _seed_active_turn(root, "claude_code", "claude-cache-session", "claude")
    return _run_claude_main(
        claude_user_prompt_submit,
        _claude_context(
            root, root / "claude-data", prompt="please fix the bug in app.py"
        ),
    )


def _codex_pretool(root: Path) -> int:
    _seed_active_turn(root, "codex_cli", "codex-cache-session", "codex")
    return _run_codex_main(
        codex_pre_tool_use,
        {
            "cwd": str(root),
            "session_id": "codex-cache-session",
            "turn_id": "codex-cache-turn",
            "agent": "codex",
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
        },
    )


def _codex_bootstrap_turn(root: Path) -> None:
    """실제 user_prompt_submit 훅으로 턴(스냅샷 포함)을 부트스트랩 — 계측 밖 준비."""
    result = _run_codex_main(
        codex_user_prompt_submit,
        {
            "cwd": str(root),
            "session_id": "codex-cache-session",
            "agent": "codex",
            "prompt": "cache probe bootstrap",
        },
    )
    assert result == 0


def _codex_posttool(root: Path) -> int:
    _codex_bootstrap_turn(root)
    return _run_codex_main(
        codex_post_tool_use,
        {
            "cwd": str(root),
            "session_id": "codex-cache-session",
            "agent": "codex",
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"},
            "tool_response": "Exit code: 0\nOutput:\nSuccess.\n",
        },
    )


def _codex_stop(root: Path) -> int:
    _seed_active_turn(root, "codex_cli", "codex-cache-session", "codex")
    return _run_codex_main(
        codex_stop,
        {
            "cwd": str(root),
            "session_id": "codex-cache-session",
            "turn_id": "codex-cache-turn",
            "agent": "codex",
        },
    )


def _codex_user_prompt(root: Path) -> int:
    return _run_codex_main(
        codex_user_prompt_submit,
        {
            "cwd": str(root),
            "session_id": "codex-cache-session",
            "agent": "codex",
            "prompt": "cache probe prompt",
        },
    )


def _antigravity_pretool(root: Path) -> int:
    _seed_active_turn(root, "antigravity", "agy-cache-session", "antigravity")
    return _run_oma_hook(
        "PreToolUse", _pretool_payload(root, "agy-cache-session", "antigravity")
    )


def _antigravity_stop(root: Path) -> int:
    _seed_active_turn(root, "antigravity", "agy-cache-session", "antigravity")
    return _run_oma_hook(
        "Stop",
        {
            "cwd": str(root),
            "project_root": str(root),
            "session_id": "agy-cache-session",
            "turn_id": "antigravity-cache-turn",
            "agent": "antigravity",
        },
    )


def _qwen_pretool(root: Path) -> int:
    _seed_active_turn(root, "qwen_code", "qwen-cache-session", "qwen")
    return _run_qwen_hook(
        "PreToolUse", _pretool_payload(root, "qwen-cache-session", "qwen")
    )


def _qwen_stop(root: Path) -> int:
    _seed_active_turn(root, "qwen_code", "qwen-cache-session", "qwen")
    return _run_qwen_hook(
        "Stop",
        {
            "cwd": str(root),
            "project_root": str(root),
            "session_id": "qwen-cache-session",
            "turn_id": "qwen-cache-turn",
            "agent": "qwen",
        },
    )


# --- 커버리지 매트릭스 ---------------------------------------------------------


@dataclass(frozen=True)
class _CacheCase:
    adapter: str
    entrypoint: str
    run: Callable[[Path], int]
    max_uncached: int
    # 프로브가 실제로 원장을 최소 1회 읽어야 "읽기 0회라 무의미한 통과"를 막는다.
    min_uncached: int = 1

    @property
    def case_id(self) -> str:
        return f"{self.adapter}/{self.entrypoint}"


_CACHE_MATRIX: tuple[_CacheCase, ...] = (
    _CacheCase("claude_code", "pre_tool_use", _claude_pretool, 1),
    _CacheCase("claude_code", "post_tool_use", _claude_posttool, 1),
    _CacheCase("claude_code", "stop", _claude_stop, 1),
    _CacheCase("claude_code", "user_prompt_submit", _claude_user_prompt, 1),
    # session_end는 원장 로드 0회라 캐시 비대상 — 아래 별도 테스트로 0회 고정.
    _CacheCase("codex_cli", "pre_tool_use", _codex_pretool, 1),
    # post_tool_use·user_prompt_submit는 기록 쓰기가 포함되어 save_ledger의 쓰기 전
    # 캐시 무효화 때문에 1회 불가. 무캐시 기준선(실측 12·6회)에서는 실패하는 하한.
    # post_tool_use는 coordination 활동에 따라 캐시 적용 시 2~5회가 실측돼,
    # 무캐시 기준선(12+)과 캐시 적용 최대값(5) 사이인 6을 임계로 잡았다.
    _CacheCase("codex_cli", "post_tool_use", _codex_posttool, 6),
    _CacheCase("codex_cli", "stop", _codex_stop, 1),
    _CacheCase("codex_cli", "user_prompt_submit", _codex_user_prompt, 3),
    _CacheCase("antigravity", "pre_tool_use", _antigravity_pretool, 1),
    _CacheCase("antigravity", "stop", _antigravity_stop, 1),
    _CacheCase("qwen_code", "pre_tool_use", _qwen_pretool, 1),
    _CacheCase("qwen_code", "stop", _qwen_stop, 1),
)


@pytest.mark.parametrize(
    "case", _CACHE_MATRIX, ids=lambda case: case.case_id
)
def test_adapter_entrypoint_serves_cached_ledger_reads(
    tmp_path: Path, case: _CacheCase
) -> None:
    real_uncached_load = ledger_module._load_ledger_uncached
    with patch.object(
        ledger_module,
        "_load_ledger_uncached",
        wraps=real_uncached_load,
    ) as uncached_load:
        result = case.run(tmp_path)

    assert result == 0
    assert case.min_uncached <= uncached_load.call_count <= case.max_uncached, (
        f"{case.case_id}: expected {case.min_uncached}..{case.max_uncached} uncached "
        f"ledger load(s) with ledger_read_cache, got {uncached_load.call_count}. "
        "캐시가 없거나 동작하지 않으면 이 값이 무캐시 기준선으로 올라간다."
    )


def test_claude_session_end_does_not_load_ledger(tmp_path: Path) -> None:
    """v2.7.1 의도 고정: session_end는 실제 ledger load가 0회라 캐시 비대상이다.

    매트릭스에 넣지 않는 대신 0회를 여기서 고정해, 훗날 "캐시 적용 누락"으로
    오인되거나 반대로 불필요한 캐시가 추가되는 것을 모두 감시한다.
    """
    context = _claude_context(tmp_path, tmp_path / "claude-data")
    real_uncached_load = ledger_module._load_ledger_uncached
    with (
        patch.object(claude_session_end, "bootstrap", return_value=context),
        patch.object(claude_session_end, "cleanup_session"),
        patch.object(claude_session_end, "emit", return_value=0),
        patch.object(
            ledger_module,
            "_load_ledger_uncached",
            wraps=real_uncached_load,
        ) as uncached_load,
    ):
        result = claude_session_end.main()

    assert result == 0
    assert uncached_load.call_count == 0


# --- 구조적 강제: 새 어댑터·엔트리포인트는 등록되어야 통과 -------------------

# ledger를 읽는 표준 훅 엔트리포인트. 새 종류의 훅이 생기면 이 집합과
# _CACHE_MATRIX를 함께 갱신해야 한다(메타 테스트가 미등록을 RED로 만든다).
_CANONICAL_HOOK_ENTRYPOINTS: frozenset[str] = frozenset(
    {"pre_tool_use", "post_tool_use", "stop", "user_prompt_submit"}
)


def test_every_shipped_adapter_is_registered_in_the_cache_matrix() -> None:
    shipped = {
        entry.name
        for entry in _ADAPTERS_DIR.iterdir()
        if entry.is_dir()
        and not entry.name.startswith("_")
        and (entry / "__init__.py").exists()
    }
    registered = {case.adapter for case in _CACHE_MATRIX}
    missing = shipped - registered
    assert not missing, (
        f"새 어댑터 {sorted(missing)}가 캐시 커버리지 매트릭스에 등록되지 않았다. "
        "tests/test_w4c_adapter_cache.py의 _CACHE_MATRIX에 런타임 프로브를 추가하라."
    )


def test_multi_hook_adapters_register_every_canonical_entrypoint() -> None:
    for adapter in ("claude_code", "codex_cli"):
        registered = {
            case.entrypoint for case in _CACHE_MATRIX if case.adapter == adapter
        }
        missing = _CANONICAL_HOOK_ENTRYPOINTS - registered
        assert not missing, (
            f"{adapter}의 표준 엔트리포인트 {sorted(missing)}가 캐시 매트릭스에 없다. "
            "누락되면 codex_cli 3개 훅 캐시 미적용 사고가 재발할 수 있다."
        )
