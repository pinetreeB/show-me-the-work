from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapters.claude_code.activation_policy import (
        config_state as _config_state,
        environment_root as _environment_root,
        fallback_root as _fallback_root,
        is_exact_home as _is_exact_home,
        plugin_data_dir as _plugin_data_dir,
    )
    from adapters.claude_code.hook_io import (
        JsonObject,
        JsonValue,
        emit,
        read_payload as _read_payload,
        string_value as _string,
    )
    from adapters.claude_code.session_registry import (
        bind_session,
        gc_stale,
        load_session,
        load_turn,
        mark_context_emitted,
        promote_quick,
        registry_was_bound,
        save_turn,
        warn_once,
    )
else:
    _module_prefix = "adapters.claude_code." if __package__ else ""
    _activation = import_module(f"{_module_prefix}activation_policy")
    _hook_io = import_module(f"{_module_prefix}hook_io")
    _registry = import_module(f"{_module_prefix}session_registry")
    _config_state = _activation.config_state
    _environment_root = _activation.environment_root
    _fallback_root = _activation.fallback_root
    _is_exact_home = _activation.is_exact_home
    _plugin_data_dir = _activation.plugin_data_dir
    JsonObject = _hook_io.JsonObject
    JsonValue = _hook_io.JsonValue
    emit = _hook_io.emit
    _read_payload = _hook_io.read_payload
    _string = _hook_io.string_value
    bind_session = _registry.bind_session
    gc_stale = _registry.gc_stale
    load_session = _registry.load_session
    load_turn = _registry.load_turn
    mark_context_emitted = _registry.mark_context_emitted
    promote_quick = _registry.promote_quick
    registry_was_bound = _registry.registry_was_bound
    save_turn = _registry.save_turn
    warn_once = _registry.warn_once

__all__ = ["JsonObject", "emit", "fail_open"]


@dataclass(frozen=True, slots=True)
class HookContext:
    active: bool
    payload: JsonObject
    root: Path | None
    data_dir: Path
    session_id: str
    agent: str
    task_mode: str
    turn_prompt: str
    turn_prompt_id: str
    warning: str


def bootstrap(event_name: str) -> HookContext:
    payload = _read_payload()
    force = os.environ.get("SMTW_TEST_FORCE_ENABLE") == "1"
    session_id = _string(payload.get("session_id")) or "default"
    agent = (
        _string(payload.get("agent_id")) or _string(payload.get("agent")) or "claude"
    )
    data_dir = _plugin_data_dir(force)
    if event_name == "UserPromptSubmit":
        gc_stale(data_dir, session_id)
    record, registry_corrupt = load_session(data_dir, session_id)
    was_bound = registry_was_bound(data_dir, session_id)
    env_root = _environment_root(payload)
    if record is not None:
        fixed_root = Path(record.root)
        candidate = env_root or fixed_root
        if _is_exact_home(candidate):
            return _inactive(payload, data_dir, session_id, agent)
        if env_root is not None and not _same_root(env_root, fixed_root):
            enabled, _, config_corrupt = _config_state(env_root)
            if force:
                enabled = True
            if not enabled:
                warning = ""
                if config_corrupt and warn_once(
                    data_dir,
                    session_id,
                    "config_or_registry_corrupt",
                ):
                    warning = (
                        "activation config or session registry is corrupt; "
                        "supervision is inactive"
                    )
                return _inactive(payload, data_dir, session_id, agent, warning)

            # 정책 A: mismatch env root는 별도 프로젝트로 opt-in을 확인하되, 기존
            # write-once registry latch는 A에 유지한다. 이번 hook의 유효 root만 B다.
            _ = bind_session(
                data_dir,
                session_id,
                fixed_root,
                record.config_digest,
            )
            warning = ""
            if warn_once(data_dir, session_id, "root_mismatch"):
                warning = (
                    "session root mismatch; using CLAUDE_PROJECT_DIR for this hook "
                    "while keeping the registry latch unchanged"
                )
            return _active(
                payload,
                env_root,
                data_dir,
                session_id,
                agent,
                warning,
            )
        bound = bind_session(
            data_dir,
            session_id,
            candidate,
            record.config_digest,
        )
        warning = ""
        if bound.root_mismatch and warn_once(data_dir, session_id, "root_mismatch"):
            warning = (
                "session root mismatch; using CLAUDE_PROJECT_DIR for this hook "
                "while keeping the registry latch unchanged"
            )
        return _active(
            payload,
            env_root or Path(bound.record.root),
            data_dir,
            session_id,
            agent,
            warning,
        )

    candidate = (
        env_root
        if env_root is not None
        else None
        if was_bound
        else _fallback_root(payload, event_name, force)
    )
    if candidate is None:
        code = ""
        message = ""
        if registry_corrupt:
            code = "registry_corrupt"
            message = "session registry is corrupt and cannot be reconstructed"
        elif was_bound:
            code = "registry_missing"
            message = "session registry is missing and no authoritative project root is available"
        warning = message if code and warn_once(data_dir, session_id, code) else ""
        return _inactive(payload, data_dir, session_id, agent, warning)
    if _is_exact_home(candidate):
        return _inactive(payload, data_dir, session_id, agent)

    enabled, config_digest, config_corrupt = _config_state(candidate)
    if force:
        enabled = True
        if not config_digest:
            config_digest = sha256(b"SMTW_TEST_FORCE_ENABLE=1").hexdigest()
    if not enabled:
        warning = ""
        if (config_corrupt or registry_corrupt) and warn_once(
            data_dir,
            session_id,
            "config_or_registry_corrupt",
        ):
            warning = "activation config or session registry is corrupt; supervision is inactive"
        return _inactive(payload, data_dir, session_id, agent, warning)

    bound = bind_session(data_dir, session_id, candidate, config_digest)
    warnings: list[str] = []
    if (registry_corrupt or bound.replaced_corrupt) and warn_once(
        data_dir,
        session_id,
        "registry_corrupt",
    ):
        warnings.append("session registry was corrupt and has been reconstructed")
    if bound.root_mismatch and warn_once(data_dir, session_id, "root_mismatch"):
        warnings.append(
            (
                "session root mismatch; using CLAUDE_PROJECT_DIR for this hook "
                "while keeping the registry latch unchanged"
            )
            if env_root is not None
            else "session root mismatch; keeping the original project root"
        )
    return _active(
        payload,
        env_root or Path(bound.record.root),
        data_dir,
        session_id,
        agent,
        "; ".join(warnings),
    )


def remember_turn(
    context: HookContext,
    prompt: str,
    prompt_id: str,
    mode: str,
) -> None:
    _ = save_turn(
        context.data_dir,
        context.session_id,
        context.agent,
        prompt,
        prompt_id,
        mode,
    )


def promote_quick_turn(context: HookContext) -> AbstractContextManager[bool]:
    return promote_quick(context.data_dir, context.session_id, context.agent)


def show_context_once(context: HookContext, prompt_id: str) -> bool:
    return mark_context_emitted(
        context.data_dir,
        context.session_id,
        context.agent,
        prompt_id,
    )


def show_scope_once(context: HookContext) -> bool:
    turn_id = context.turn_prompt_id or "turn"
    return warn_once(
        context.data_dir,
        context.session_id,
        f"scope:{context.agent}:{turn_id}",
    )


def response(context: HookContext, body: Mapping[str, JsonValue]) -> JsonObject:
    result = dict(body)
    if context.warning:
        warning = f"[smtw] health: {context.warning}"
        current = result.get("systemMessage")
        result["systemMessage"] = (
            f"{current}; {context.warning}"
            if isinstance(current, str) and current
            else warning
        )
    return result


def fail_open(message: str, context: HookContext | None = None) -> int:
    body: JsonObject = {
        "systemMessage": f"[smtw] health: fail-open: {message}",
    }
    try:
        data_dir = (
            context.data_dir
            if context is not None
            else _plugin_data_dir(os.environ.get("SMTW_TEST_FORCE_ENABLE") == "1")
        )
        session_id = context.session_id if context is not None else "default"
        if not warn_once(data_dir, session_id, "fail_open"):
            body = {}
    except Exception:  # noqa: BLE001
        pass
    return emit(body)


def fail_closed_runtime_env(
    event_name: str,
    error: BaseException,
    context: HookContext | None = None,
) -> int | None:
    error_type = type(error)
    if (
        error_type.__module__ != "core.runtime_env"
        or error_type.__name__ != "SmtwEnvConflictError"
    ):
        return None
    from core.runtime_env import SmtwEnvConflictError

    if not isinstance(error, SmtwEnvConflictError):
        return None
    reason = f"[smtw] runtime environment conflict; denied fail-closed: {error}"
    if event_name == "PreToolUse":
        body: JsonObject = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    else:
        body = {"decision": "block", "reason": reason}
    if context is not None:
        body = response(context, body)
    return emit(body)


def health_response(
    context: HookContext,
    code: str,
    message: str,
    body: Mapping[str, JsonValue] | None = None,
) -> JsonObject:
    result: JsonObject = dict(body) if body is not None else {}
    if warn_once(context.data_dir, context.session_id, code):
        result["systemMessage"] = f"[smtw] health: {message}"
    return response(context, result)


def _active(
    payload: JsonObject,
    root: Path,
    data_dir: Path,
    session_id: str,
    agent: str,
    warning: str,
) -> HookContext:
    turn = load_turn(data_dir, session_id, agent)
    return HookContext(
        True,
        payload,
        root,
        data_dir,
        session_id,
        agent,
        turn.mode if turn is not None else "normal",
        turn.prompt if turn is not None else "",
        turn.prompt_id if turn is not None else "",
        warning,
    )


def _inactive(
    payload: JsonObject,
    data_dir: Path,
    session_id: str,
    agent: str,
    warning: str = "",
) -> HookContext:
    return HookContext(
        False,
        payload,
        None,
        data_dir,
        session_id,
        agent,
        "quick",
        "",
        "",
        warning,
    )


def _same_root(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(
        str(right.resolve())
    )
