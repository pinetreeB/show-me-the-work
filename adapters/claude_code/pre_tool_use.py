from __future__ import annotations

from importlib import import_module
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapters.claude_code.bootstrap import (
        HookContext,
        JsonObject,
        bootstrap,
        emit,
        fail_open,
        promote_quick_turn,
        response,
    )
    from adapters.claude_code.session_registry import QuickPromotionPersistenceError
else:
    _bootstrap_module = import_module(
        "adapters.claude_code.bootstrap" if __package__ else "bootstrap"
    )
    HookContext = _bootstrap_module.HookContext
    JsonObject = _bootstrap_module.JsonObject
    bootstrap = _bootstrap_module.bootstrap
    emit = _bootstrap_module.emit
    fail_open = _bootstrap_module.fail_open
    promote_quick_turn = _bootstrap_module.promote_quick_turn
    response = _bootstrap_module.response
    _registry_module = import_module(
        "adapters.claude_code.session_registry" if __package__ else "session_registry"
    )
    QuickPromotionPersistenceError = _registry_module.QuickPromotionPersistenceError


def _deny(reason: str) -> JsonObject:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _promote_project_turn(
    context: HookContext,
    payload: JsonObject,
) -> None:
    from adapters.claude_code.common import canonical_invocation
    from core.ambiguity import evaluate_ambiguity
    from core.adapter_observation import start_turn
    from core.classify import classify_prompt
    from core.intent import clear_intent
    from core.ledger import record_event

    if context.root is None:
        return
    turn_payload = dict(payload)
    if context.turn_prompt_id:
        turn_payload["prompt_id"] = context.turn_prompt_id
    invocation = canonical_invocation(
        turn_payload,
        "turn_start",
        "other",
        [],
        "",
        True,
        "",
    )
    root = str(context.root)
    observation = start_turn(context.root, invocation)
    _ = clear_intent(root)
    result = classify_prompt({"project_root": root, "prompt": context.turn_prompt})
    ambiguity = evaluate_ambiguity(
        {
            "project_root": root,
            "prompt": context.turn_prompt,
            "requested_paths": result.get("requested_paths", []),
        }
    )
    packs_value = result.get("packs")
    packs = (
        [item for item in packs_value if isinstance(item, str)]
        if isinstance(packs_value, list)
        else []
    )
    intent_required = ambiguity.get("ambiguous") is True
    if intent_required and "intent-interview" not in packs:
        packs.append("intent-interview")
    _ = record_event(
        {
            "project_root": root,
            "event": "prompt",
            "host": invocation.host,
            "agent": invocation.agent,
            "session_id": invocation.session_id,
            "turn_id": invocation.turn_id,
            "baseline_snapshot_id": observation.baseline_snapshot_id,
            "current_snapshot_id": observation.snapshot_id,
            "provenance_incomplete": observation.incomplete,
            "provenance_status": observation.status.value,
            "provenance_status_reason": observation.status_reason,
            "task_mode": "normal",
            "prompt": context.turn_prompt,
            "packs": packs,
            "needs_goals": result.get("needs_goals") is True,
            "intent_required": intent_required,
            "ambiguity_score": ambiguity.get("ambiguity_score", 0),
            "requires_investigation_compliance": "investigation" in packs,
        }
    )


def main() -> int:
    context: HookContext | None = None
    try:
        context = bootstrap("PreToolUse")
        if not context.active or context.root is None:
            return emit(response(context, {}))
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from adapters.claude_code.common import (
            canonical_invocation,
            tool_command,
            tool_file_paths,
            tool_input,
        )
        from adapters.intent_command import intent_set_command
        from core.contract import EDIT_TOOLS, SHELL_TOOLS, evaluate_pretool_contract
        from core.destructive_guard import evaluate_r2_destructive_gate
        from core.shell_command import ShellEffect, classify_shell_effect

        payload = context.payload
        tool_name = payload.get("tool_name")
        family = (
            "edit"
            if tool_name in EDIT_TOOLS
            else "shell"
            if tool_name in SHELL_TOOLS
            else "other"
        )
        root = str(context.root)
        invocation = canonical_invocation(
            payload,
            "pre_tool",
            family,
            tool_file_paths(payload),
            tool_command(payload),
            False,
            "",
        )
        # R2 first: run before any other ledger read such as resolve_active_invocation.
        # Any exception raised here is absorbed inside destructive_guard as "degraded"
        # so a later broad except fail-open cannot undo this decision.
        r2_result = evaluate_r2_destructive_gate(
            {
                "project_root": root,
                "tool_name": "Bash"
                if family == "shell"
                else str(payload.get("tool_name", "")),
                "command": tool_command(payload),
                "host": invocation.host,
                "agent": invocation.agent,
                "session_id": invocation.session_id,
            }
        )
        if r2_result["decision"] == "block":
            from core.adapter_observation import record_r2_deny_after_resolution

            _ = record_r2_deny_after_resolution(
                context.root,
                invocation,
                str(r2_result.get("coordination_reason_code", "")),
            )
            return emit(response(context, _deny(str(r2_result["reason"]))))

        if context.task_mode == "quick":
            command = tool_command(payload)
            read_only = (
                family == "shell"
                and classify_shell_effect(command).effect
                is ShellEffect.PROVEN_READ_ONLY
            )
            if read_only:
                return emit(response(context, {}))
            try:
                with promote_quick_turn(context) as claimed:
                    if claimed:
                        _promote_project_turn(context, payload)
            except QuickPromotionPersistenceError as exc:
                return emit(response(context, _deny(f"[smtw] health: {exc}")))

        from core.adapter_observation import begin_invocation, resolve_active_invocation

        invocation = resolve_active_invocation(context.root, invocation)
        attribution = invocation.scorecard_attribution
        if attribution == "legacy_default" and invocation.session_id != "default":
            attribution = "exact"
        input_text = json.dumps(tool_input(payload), ensure_ascii=False)
        result = evaluate_pretool_contract(
            {
                "project_root": root,
                "tool_name": str(payload.get("tool_name", "")),
                "file_paths": tool_file_paths(payload),
                "command": tool_command(payload),
                "prompt": input_text,
                "intent_set_command": intent_set_command(__file__),
                "host": invocation.host,
                "agent": invocation.agent,
                "session_id": invocation.session_id,
                "turn_id": invocation.turn_id,
                "attribution": attribution,
            }
        )
        if result["decision"] == "block":
            return emit(response(context, _deny(str(result["reason"]))))
        observation = begin_invocation(context.root, invocation)
        if (
            observation.error_kind == "StaleTurn"
            and invocation.identity_conflict
            and invocation.mutation_capable
        ):
            return emit(
                response(
                    context,
                    _deny("[smtw] stale turn identity; submit a current prompt before mutation."),
                )
            )
        return emit(response(context, {}))
    except Exception as exc:  # noqa: BLE001
        denied = _bootstrap_module.fail_closed_runtime_env(
            "PreToolUse", exc, context
        )
        if denied is not None:
            return denied
        return fail_open(str(exc), context)


if __name__ == "__main__":
    raise SystemExit(main())
