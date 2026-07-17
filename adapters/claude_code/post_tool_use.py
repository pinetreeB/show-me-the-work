from __future__ import annotations

from importlib import import_module
from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapters.claude_code.bootstrap import (
        HookContext,
        bootstrap,
        emit,
        fail_open,
        health_response,
        response,
        show_scope_once,
    )
else:
    _bootstrap_module = import_module(
        "adapters.claude_code.bootstrap" if __package__ else "bootstrap"
    )
    HookContext = _bootstrap_module.HookContext
    bootstrap = _bootstrap_module.bootstrap
    emit = _bootstrap_module.emit
    fail_open = _bootstrap_module.fail_open
    health_response = _bootstrap_module.health_response
    response = _bootstrap_module.response
    show_scope_once = _bootstrap_module.show_scope_once


def main() -> int:
    context: HookContext | None = None
    try:
        context = bootstrap("PostToolUse")
        if not context.active or context.root is None:
            return emit(response(context, {}))
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from adapters.claude_code.common import (
            canonical_invocation,
            tool_command,
            tool_file_paths,
            tool_output,
            tool_success,
        )

        payload = context.payload
        raw_hook_event = payload.get("hook_event_name")
        hook_event_name = (
            raw_hook_event
            if isinstance(raw_hook_event, str)
            and raw_hook_event in {"PostToolUse", "PostToolUseFailure"}
            else "PostToolUse"
        )
        from core.adapter_observation import (
            observe_post_tool,
            resolve_active_invocation,
            verification_covers,
        )
        from core.classify import classify_prompt
        from core.contract import (
            EDIT_TOOLS,
            SHELL_TOOLS,
            record_contract_authored_event,
        )
        from core.ledger import JsonObject, load_ledger, record_event
        from core.provenance_types import ProvenanceStatus
        from core.scope_guard import evaluate_scope
        from core.verification import is_verification_command

        root = str(context.root)
        tool = payload.get("tool_name")
        family = (
            "edit"
            if tool in EDIT_TOOLS
            else "shell"
            if tool in SHELL_TOOLS
            else "other"
        )
        if context.task_mode == "quick":
            return emit(response(context, {}))
        if family != "other":
            command = tool_command(payload)
            invocation = canonical_invocation(
                payload,
                "post_tool",
                family,
                tool_file_paths(payload),
                command,
                tool_success(payload),
                tool_output(payload),
            )
            invocation = resolve_active_invocation(context.root, invocation)
            attribution = invocation.scorecard_attribution
            if attribution == "legacy_default" and invocation.session_id != "default":
                attribution = "exact"
            if family == "edit":
                record_contract_authored_event(
                    {
                        "project_root": root,
                        "file_paths": tool_file_paths(payload),
                        "host": invocation.host,
                        "agent": invocation.agent,
                        "session_id": invocation.session_id,
                        "turn_id": invocation.turn_id,
                        "attribution": attribution,
                    }
                )
            observation = observe_post_tool(Path(root), invocation)
            verification_command = family == "shell" and is_verification_command(
                command
            )
            if verification_command:
                covers = verification_covers(Path(root), invocation)
                verification: JsonObject = {
                    "project_root": root,
                    "event": "verification",
                    "host": invocation.host,
                    "agent": invocation.agent,
                    "session_id": invocation.session_id,
                    "turn_id": invocation.turn_id,
                    "invocation_id": invocation.invocation_id,
                    "command": command,
                    "success": invocation.success,
                    "evidence": invocation.evidence,
                }
                if covers is not None:
                    verification["covers"] = covers
                _ = record_event(verification)
                return emit(response(context, {}))
            if observation.status is ProvenanceStatus.SCOPE_TOO_LARGE:
                return emit(
                    health_response(
                        context,
                        "provenance_scope_too_large",
                        "provenance scope is too large; continuing fail-open",
                    )
                )
            if observation.incomplete and not verification_command:
                return emit(response(context, {}))
            paths = list(observation.changed_paths)
            ledger = load_ledger({"project_root": root})
            prompt = ledger.get("prompt")
            prompt_text = prompt if isinstance(prompt, str) else ""
            requested = (
                classify_prompt({"prompt": prompt_text})["requested_paths"]
                if prompt_text
                else []
            )
            scope = evaluate_scope(
                {
                    "project_root": root,
                    "prompt": prompt_text,
                    "requested_paths": requested,
                    "changed_files": paths,
                }
            )
            if scope["decision"] == "warn":
                if not show_scope_once(context):
                    return emit(response(context, {}))
                _ = record_event(
                    {
                        "project_root": root,
                        "event": "scope_warning",
                        "host": invocation.host,
                        "agent": invocation.agent,
                        "session_id": invocation.session_id,
                        "turn_id": invocation.turn_id,
                        "message": scope["message"],
                    }
                )
                return emit(
                    response(
                        context,
                        {
                            "hookSpecificOutput": {
                                "hookEventName": hook_event_name,
                                "additionalContext": str(scope["message"]),
                            },
                        },
                    )
                )
            return emit(response(context, {}))
        return emit(response(context, {}))
    except Exception as exc:  # noqa: BLE001
        return fail_open(str(exc), context)


if __name__ == "__main__":
    raise SystemExit(main())
