"""hard-review 수리 RED/GREEN 테스트: H3 antigravity 신원 승격 + High-01 codex_cli 캐시.

H3: antigravity의 pre/post 경로가 복원된 신원(legacy_default + 실제 session_id)을
다른 3개 어댑터와 동일하게 "exact"로 승격하는지 **런타임** 검증.

High-01: codex_cli의 post_tool_use/stop/user_prompt_submit 훅을 실제 런타임 실행해
uncached ledger 로드 횟수가 1회인지 검증. 소스코드 문자열 매칭禁止
(test_w4c_adapter_cache.py:114-122 방식이 이 누락을 못 잡았음).
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

from adapters.antigravity import oma_hook
from adapters.codex_cli import stop as codex_stop
from adapters.codex_cli import user_prompt_submit as codex_user_prompt_submit
from adapters.codex_cli import post_tool_use as codex_post_tool_use
from core import ledger as ledger_module
from core.adapter_observation import CanonicalInvocation
from core.ledger_storage import ledger_path
from core.ledger_v2 import apply_v2_event, default_v2_ledger


def _recovered_invocation(host: str, agent: str, phase: str, family: str) -> CanonicalInvocation:
    """복원된 신원: 실제 session_id를 갖지만 identity_synthetic이 남아
    scorecard_attribution이 'legacy_default'인 상태(어댑터 승격이 필요한 정확히 그 입력)."""
    return CanonicalInvocation(
        host,
        agent,
        "recovered-session-1",
        "turn:recovered-session-1",
        f"{phase}:recovered",
        phase,
        family,
        (),
        "",
        True,
        "",
        True,
        False,
    )


def test_antigravity_pretool_promotes_recovered_legacy_default_to_exact(tmp_path: Path) -> None:
    recovered = _recovered_invocation("antigravity", "antigravity", "pre_tool", "shell")
    captured: dict[str, object] = {}

    def _capture_contract(payload):
        captured.update(payload)
        return {"decision": "block", "reason": "probe-end"}

    payload = {
        "cwd": str(tmp_path),
        "project_root": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "git status --short"},
    }
    with (
        patch(
            "core.destructive_guard.evaluate_r2_destructive_gate",
            return_value={"decision": "allow", "message": "probe"},
        ),
        patch(
            "core.adapter_observation.resolve_active_invocation",
            return_value=recovered,
        ),
        patch(
            "core.contract.evaluate_pretool_contract",
            side_effect=_capture_contract,
        ),
    ):
        result = oma_hook.handle_pre_tool_use(payload)

    assert result == 0
    assert captured.get("attribution") == "exact", (
        "antigravity pre_tool must promote recovered legacy_default to exact, "
        f"got {captured.get('attribution')!r}"
    )


def test_antigravity_posttool_promotes_recovered_legacy_default_to_exact(tmp_path: Path) -> None:
    recovered = _recovered_invocation("antigravity", "antigravity", "post_tool", "edit")
    captured: dict[str, object] = {}

    def _capture_authored(payload):
        captured.update(payload)

    payload = {
        "cwd": str(tmp_path),
        "project_root": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {"file_paths": [str(tmp_path / "CONTRACT.md")]},
        "tool_response": "ok",
    }
    with (
        patch(
            "core.adapter_observation.resolve_active_invocation",
            return_value=recovered,
        ),
        patch(
            "core.contract.record_contract_authored_event",
            side_effect=_capture_authored,
        ),
    ):
        result = oma_hook.handle_post_tool_use(payload)

    assert result == 0
    assert captured.get("attribution") == "exact", (
        "antigravity post_tool must promote recovered legacy_default to exact before "
        f"recording contract authorship, got {captured.get('attribution')!r}"
    )


# --- High-01: codex_cli 3개 훅 런타임 캐시 검증 -------------------------------
#
# 검증 방식: 훅 메인 루틴을 실제 런타임 실행하고(core 모듈 임포트·게이트 전부 실동작,
# stdin/stdout만 메모리 스트림) `_load_ledger_uncached` 호출 횟수를 잰다. 소스코드
# 문자열 매칭은 쓰지 않는다(test_w4c_adapter_cache.py:114-122 방식이 이 누락을 못 잡음).
#
# 기대 횟수 산정 근거: `save_ledger`는 쓰기 직전 캐시를 무효화하므로(core/ledger.py),
# 쓰기가 없는 훅 구간은 캐시로 합산되고 쓰기 뒤 첫 읽기는 새로 로드된다.
# - stop(W4c 방식 시드, 쓰기 없음): 정확히 1회. 수리 전 실측 9회.
# - user_prompt_submit(실제 턴 부트스트랩, 기록 쓰기 포함): 3회. 수리 전 실측 6회.
# - post_tool_use(실제 턴 부트스트랩 뒤 관측): 2회. 수리 전 실측 12회.
# 수리 전 값은 전부 기대값보다 커서 RED가 재현된다.


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
            "turn_id": f"turn:{session_id}",
            "prompt": "cache probe",
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
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(ledger), encoding="utf-8")


class _BufferedStream:
    def __init__(self, data: bytes = b"") -> None:
        self.buffer = io.BytesIO(data)


def _run_codex_main(hook_module, payload: dict[str, object]) -> tuple[int, str]:
    """훅 메인 루틴을 실제 런타임 실행(stdin/stdout만 메모리 스트림으로 교체)."""
    stdin = _BufferedStream(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    stdout = _BufferedStream()
    with (
        patch.object(sys, "stdin", stdin),
        patch.object(sys, "stdout", stdout),
        patch.object(
            hook_module,
            "_fail_open",
            side_effect=AssertionError("hook must not fail-open in cache probe"),
        ),
    ):
        result = hook_module.main()
    return result, stdout.buffer.getvalue().decode("utf-8", errors="replace")


def _bootstrap_turn(root: Path) -> None:
    """실제 user_prompt_submit 훅으로 턴(스냅샷 포함)을 부트스트랩한다."""
    result, output = _run_codex_main(
        codex_user_prompt_submit,
        {
            "cwd": str(root),
            "session_id": "codex-cache-s1",
            "agent": "codex",
            "prompt": "cache probe bootstrap",
        },
    )
    assert result == 0, output


def test_codex_stop_uses_one_uncached_ledger_load(tmp_path: Path) -> None:
    _seed_active_turn(tmp_path, "codex_cli", "codex-cache-s1", "codex")
    real_uncached_load = ledger_module._load_ledger_uncached
    with patch.object(
        ledger_module,
        "_load_ledger_uncached",
        wraps=real_uncached_load,
    ) as uncached_load:
        result, output = _run_codex_main(
            codex_stop,
            {
                "cwd": str(tmp_path),
                "session_id": "codex-cache-s1",
                "turn_id": "turn:codex-cache-s1",
                "agent": "codex",
            },
        )

    assert result == 0, output
    assert "fail-open" not in output, output
    assert uncached_load.call_count == 1, (
        "codex_cli/stop.py must serve the whole hook from one uncached ledger "
        f"load (ledger_read_cache), got {uncached_load.call_count}"
    )


def test_codex_user_prompt_submit_collapses_ledger_loads_with_cache(
    tmp_path: Path,
) -> None:
    real_uncached_load = ledger_module._load_ledger_uncached
    with patch.object(
        ledger_module,
        "_load_ledger_uncached",
        wraps=real_uncached_load,
    ) as uncached_load:
        result, output = _run_codex_main(
            codex_user_prompt_submit,
            {
                "cwd": str(tmp_path),
                "session_id": "codex-cache-s1",
                "agent": "codex",
                "prompt": "cache probe prompt",
            },
        )

    assert result == 0, output
    assert "fail-open" not in output, output
    assert uncached_load.call_count <= 3, (
        "codex_cli/user_prompt_submit.py must serve reads between writes from the "
        f"ledger_read_cache, got {uncached_load.call_count} uncached loads "
        "(no-cache baseline was 6)"
    )


def test_codex_post_tool_use_collapses_ledger_loads_with_cache(tmp_path: Path) -> None:
    _bootstrap_turn(tmp_path)
    real_uncached_load = ledger_module._load_ledger_uncached
    with patch.object(
        ledger_module,
        "_load_ledger_uncached",
        wraps=real_uncached_load,
    ) as uncached_load:
        result, output = _run_codex_main(
            codex_post_tool_use,
            {
                "cwd": str(tmp_path),
                "session_id": "codex-cache-s1",
                "agent": "codex",
                "tool_name": "apply_patch",
                "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"},
                "tool_response": "Exit code: 0\nOutput:\nSuccess.\n",
            },
        )

    assert result == 0, output
    assert "fail-open" not in output, output
    assert uncached_load.call_count <= 2, (
        "codex_cli/post_tool_use.py must serve reads between writes from the "
        f"ledger_read_cache, got {uncached_load.call_count} uncached loads "
        "(no-cache baseline was 12)"
    )
