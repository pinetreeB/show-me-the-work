from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapters.claude_code.bootstrap import (
        HookContext,
        bootstrap,
        emit,
        fail_open,
        response,
    )
    from adapters.claude_code.session_registry import cleanup_session
else:
    _module_prefix = "adapters.claude_code." if __package__ else ""
    _bootstrap_module = import_module(f"{_module_prefix}bootstrap")
    _registry_module = import_module(f"{_module_prefix}session_registry")
    HookContext = _bootstrap_module.HookContext
    bootstrap = _bootstrap_module.bootstrap
    emit = _bootstrap_module.emit
    fail_open = _bootstrap_module.fail_open
    response = _bootstrap_module.response
    cleanup_session = _registry_module.cleanup_session


def main() -> int:
    context: HookContext | None = None
    try:
        context = bootstrap("SessionEnd")
        cleanup_session(context.data_dir, context.session_id)
        return emit(response(context, {}))
    except Exception as exc:  # noqa: BLE001
        return fail_open(str(exc), context)


if __name__ == "__main__":
    raise SystemExit(main())
