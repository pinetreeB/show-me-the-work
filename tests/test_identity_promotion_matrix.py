"""어댑터 신원 승격(legacy_default → exact) 커버리지 매트릭스 — 런타임 검증.

이번 세션에서 같은 계열 사고가 두 번 났다: 캐시 누락(codex 3개 훅)과 신원 승격
누락(antigravity). 캐시는 test_w4c_adapter_cache.py의 런타임 매트릭스로 막았고,
이 파일은 **신원 승격**에 같은 구조적 강제를 만든다.

승격이란: ``resolve_active_invocation``으로 복원된 신원(session_id는 생략됐다가
유일한 활성 턴으로 해석됨)이 ``scorecard_attribution == "legacy_default"``이면서도
실제 session_id를 가진 경우, 하류 게이트에 넘기기 전에 ``"exact"``로 승격하는 것
(CODEX-01/02, QWEN-01/02, AGY-01/02). 승격이 빠지면 ①R1이 유효한 namespaced 계약을
미귀속 편집으로 오인해 잘못 차단하고 ②``record_contract_authored_event``가 조기
반환해 계약 감사 증거가 영구 미기록된다.

검증 방식(소스 문자열 매칭 금지 — High-02의 교훈): 복원된 신원
(``identity_synthetic=True`` + 실제 session_id)을 ``resolve_active_invocation``의
결과로 주입하고, 하류 호출(``evaluate_pretool_contract`` /
``record_contract_authored_event``)이 **실제로 받은** attribution 값을 캡처해
``"exact"``인지 확인한다. 하류는 probe-end 응답으로 조기에 끝내 승격 지점 이후의
경로는 건드리지 않는다.
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
from adapters.claude_code.bootstrap import HookContext
from adapters.codex_cli import post_tool_use as codex_post_tool_use
from adapters.codex_cli import pre_tool_use as codex_pre_tool_use
from adapters.qwen_code import common as qwen_common
from adapters.qwen_code import qwen_hook
from core.adapter_observation import CanonicalInvocation
import pytest

_ADAPTERS_DIR = Path(__file__).resolve().parents[1] / "adapters"


def _recovered_invocation(host: str, agent: str, phase: str, family: str) -> CanonicalInvocation:
    """복원된 신원: 실제 session_id를 갖지만 identity_synthetic이 남아
    scorecard_attribution이 'legacy_default'인 상태(승격이 필요한 정확히 그 입력)."""
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


class _PromotionCapture:
    """하류 게이트 호출이 실제로 받은 payload를 캡처하고 probe-end 응답으로 끝낸다."""

    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    def for_contract(self, payload):
        self.payload = dict(payload)
        return {"decision": "block", "reason": "probe-end"}

    def for_authorship(self, payload) -> None:
        self.payload = dict(payload)

    @property
    def attribution(self) -> object:
        assert self.payload is not None, "downstream gate was never invoked"
        return self.payload.get("attribution")


# --- 어댑터별 프로브 -----------------------------------------------------------
# 각 프로브는 (1) 복원된 신원을 resolve_active_invocation 결과로 주입하고,
# (2) 하류 게이트를 캡처 스파이로 대체한 뒤, (3) 훅 진입점을 실제 실행한다.


def _run_claude(hook_module, context: HookContext) -> int:
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


def _claude_context(root: Path, tool_name: str, tool_input: dict) -> HookContext:
    return HookContext(
        active=True,
        payload={
            "cwd": str(root),
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": "identity-probe-tool",
            "session_id": "recovered-session-1",
            "turn_id": "turn:recovered-session-1",
            "agent": "claude",
        },
        root=root,
        data_dir=root / "claude-data",
        session_id="recovered-session-1",
        agent="claude",
        task_mode="normal",
        turn_prompt="identity probe",
        turn_prompt_id="identity-probe-turn",
        warning="",
    )


def _claude_pretool(tmp_path: Path) -> object:
    capture = _PromotionCapture()
    context = _claude_context(
        tmp_path, "Bash", {"command": "git status --short"}
    )
    with (
        patch(
            "core.destructive_guard.evaluate_r2_destructive_gate",
            return_value={"decision": "allow", "message": "probe"},
        ),
        patch(
            "core.adapter_observation.resolve_active_invocation",
            return_value=_recovered_invocation(
                "claude_code", "claude", "pre_tool", "shell"
            ),
        ),
        patch(
            "core.contract.evaluate_pretool_contract",
            side_effect=capture.for_contract,
        ),
    ):
        result = _run_claude(claude_pre_tool_use, context)
    assert result == 0
    return capture.attribution


def _claude_posttool(tmp_path: Path) -> object:
    capture = _PromotionCapture()
    context = _claude_context(
        tmp_path, "Write", {"file_paths": [str(tmp_path / "CONTRACT.md")]}
    )
    with (
        patch(
            "core.adapter_observation.resolve_active_invocation",
            return_value=_recovered_invocation(
                "claude_code", "claude", "post_tool", "edit"
            ),
        ),
        patch(
            "core.contract.record_contract_authored_event",
            side_effect=capture.for_authorship,
        ),
    ):
        result = _run_claude(claude_post_tool_use, context)
    assert result == 0
    return capture.attribution


class _BufferedStream:
    def __init__(self, data: bytes = b"") -> None:
        self.buffer = io.BytesIO(data)


def _run_codex(hook_module, payload: dict[str, object]) -> int:
    stdin = _BufferedStream(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    stdout = _BufferedStream()
    with (
        patch.object(sys, "stdin", stdin),
        patch.object(sys, "stdout", stdout),
        patch.object(
            hook_module,
            "_fail_open",
            side_effect=AssertionError("hook must not fail-open in identity probe"),
        ),
    ):
        # 실제 fail-open 경로는 위에서 _fail_open을 AssertionError로 바꿔 막는다.
        # 출력 문자열 검사는 하지 않는다 — codex post의 정상 StaleTurn 메시지
        # ("fail-open observation.")에 오작동하기 때문이다.
        return hook_module.main()


def _codex_pretool(tmp_path: Path) -> object:
    capture = _PromotionCapture()
    with (
        patch(
            "core.destructive_guard.evaluate_r2_destructive_gate",
            return_value={"decision": "allow", "message": "probe"},
        ),
        patch(
            "core.adapter_observation.resolve_active_invocation",
            return_value=_recovered_invocation(
                "codex_cli", "codex", "pre_tool", "shell"
            ),
        ),
        patch(
            "core.contract.evaluate_pretool_contract",
            side_effect=capture.for_contract,
        ),
    ):
        result = _run_codex(
            codex_pre_tool_use,
            {
                "cwd": str(tmp_path),
                "tool_name": "Bash",
                "tool_input": {"command": "git status --short"},
            },
        )
    assert result == 0
    return capture.attribution


def _codex_posttool(tmp_path: Path) -> object:
    capture = _PromotionCapture()
    with (
        patch(
            "core.adapter_observation.resolve_active_invocation",
            return_value=_recovered_invocation(
                "codex_cli", "codex", "post_tool", "edit"
            ),
        ),
        patch(
            "core.contract.record_contract_authored_event",
            side_effect=capture.for_authorship,
        ),
    ):
        result = _run_codex(
            codex_post_tool_use,
            {
                "cwd": str(tmp_path),
                "tool_name": "Write",
                "tool_input": {"file_paths": [str(tmp_path / "CONTRACT.md")]},
                "tool_response": "Exit code: 0\nOutput:\nSuccess.\n",
            },
        )
    assert result == 0
    return capture.attribution


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


def _antigravity_pretool(tmp_path: Path) -> object:
    capture = _PromotionCapture()
    with (
        patch(
            "core.destructive_guard.evaluate_r2_destructive_gate",
            return_value={"decision": "allow", "message": "probe"},
        ),
        patch(
            "core.adapter_observation.resolve_active_invocation",
            return_value=_recovered_invocation(
                "antigravity", "antigravity", "pre_tool", "shell"
            ),
        ),
        patch(
            "core.contract.evaluate_pretool_contract",
            side_effect=capture.for_contract,
        ),
    ):
        result = _run_oma_hook(
            "PreToolUse",
            {
                "cwd": str(tmp_path),
                "project_root": str(tmp_path),
                "tool_name": "Bash",
                "tool_input": {"command": "git status --short"},
            },
        )
    assert result == 0
    return capture.attribution


def _antigravity_posttool(tmp_path: Path) -> object:
    capture = _PromotionCapture()
    with (
        patch(
            "core.adapter_observation.resolve_active_invocation",
            return_value=_recovered_invocation(
                "antigravity", "antigravity", "post_tool", "edit"
            ),
        ),
        patch(
            "core.contract.record_contract_authored_event",
            side_effect=capture.for_authorship,
        ),
    ):
        result = _run_oma_hook(
            "PostToolUse",
            {
                "cwd": str(tmp_path),
                "project_root": str(tmp_path),
                "tool_name": "Write",
                "tool_input": {"file_paths": [str(tmp_path / "CONTRACT.md")]},
                "tool_response": "ok",
            },
        )
    assert result == 0
    return capture.attribution


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


def _qwen_pretool(tmp_path: Path) -> object:
    capture = _PromotionCapture()
    with (
        patch(
            "core.destructive_guard.evaluate_r2_destructive_gate",
            return_value={"decision": "allow", "message": "probe"},
        ),
        patch(
            "core.adapter_observation.resolve_active_invocation",
            return_value=_recovered_invocation(
                "qwen_code", "qwen", "pre_tool", "shell"
            ),
        ),
        patch(
            "core.contract.evaluate_pretool_contract",
            side_effect=capture.for_contract,
        ),
    ):
        result = _run_qwen_hook(
            "PreToolUse",
            {
                "cwd": str(tmp_path),
                "project_root": str(tmp_path),
                "tool_name": "Bash",
                "tool_input": {"command": "git status --short"},
            },
        )
    # qwen의 PreToolUse 차단 규약은 exit 2 + stderr 사유(qwen_hook.py emit_deny).
    # probe-end block이 그 경로를 타므로 rc는 2가 정상이다.
    assert result == 2
    return capture.attribution


def _qwen_posttool(tmp_path: Path) -> object:
    capture = _PromotionCapture()
    with (
        patch(
            "core.adapter_observation.resolve_active_invocation",
            return_value=_recovered_invocation(
                "qwen_code", "qwen", "post_tool", "edit"
            ),
        ),
        patch(
            "core.contract.record_contract_authored_event",
            side_effect=capture.for_authorship,
        ),
    ):
        result = _run_qwen_hook(
            "PostToolUse",
            {
                "cwd": str(tmp_path),
                "project_root": str(tmp_path),
                "tool_name": "Write",
                "tool_input": {"file_paths": [str(tmp_path / "CONTRACT.md")]},
                "tool_response": "ok",
            },
        )
    assert result == 0
    return capture.attribution


# --- 커버리지 매트릭스 ---------------------------------------------------------


@dataclass(frozen=True)
class _PromotionCase:
    adapter: str
    site: str  # "pre_tool" | "post_tool"
    run: Callable[[Path], object]  # returns the attribution the downstream received

    @property
    def case_id(self) -> str:
        return f"{self.adapter}/{self.site}"


_PROMOTION_MATRIX: tuple[_PromotionCase, ...] = (
    _PromotionCase("claude_code", "pre_tool", _claude_pretool),
    _PromotionCase("claude_code", "post_tool", _claude_posttool),
    _PromotionCase("codex_cli", "pre_tool", _codex_pretool),
    _PromotionCase("codex_cli", "post_tool", _codex_posttool),
    _PromotionCase("antigravity", "pre_tool", _antigravity_pretool),
    _PromotionCase("antigravity", "post_tool", _antigravity_posttool),
    _PromotionCase("qwen_code", "pre_tool", _qwen_pretool),
    _PromotionCase("qwen_code", "post_tool", _qwen_posttool),
)


@pytest.mark.parametrize(
    "case", _PROMOTION_MATRIX, ids=lambda case: case.case_id
)
def test_recovered_identity_is_promoted_to_exact_before_downstream_gate(
    tmp_path: Path, case: _PromotionCase
) -> None:
    attribution = case.run(tmp_path)
    assert attribution == "exact", (
        f"{case.case_id}: 복원된 신원(legacy_default + 실제 session_id)이 하류 게이트에 "
        f"{attribution!r}로 전달됐다. 다른 어댑터들과 동일하게 resolve_active_invocation "
        "직후 'exact'로 승격해야 한다(CODEX-01/02 · QWEN-01/02 · AGY-01/02 계열)."
    )


# --- 구조적 강제: 새 어댑터는 승격 매트릭스에도 등록되어야 통과 ----------------


def test_every_shipped_adapter_registers_both_promotion_sites() -> None:
    shipped = {
        entry.name
        for entry in _ADAPTERS_DIR.iterdir()
        if entry.is_dir()
        and not entry.name.startswith("_")
        and (entry / "__init__.py").exists()
    }
    for adapter in sorted(shipped):
        registered = {
            case.site for case in _PROMOTION_MATRIX if case.adapter == adapter
        }
        missing = {"pre_tool", "post_tool"} - registered
        assert not missing, (
            f"어댑터 {adapter}의 신원 승격 지점 {sorted(missing)}가 매트릭스에 없다. "
            "tests/test_identity_promotion_matrix.py의 _PROMOTION_MATRIX에 런타임 "
            "프로브를 추가하라. (antigravity 승격 누락 사고의 재발 방지 장치)"
        )
