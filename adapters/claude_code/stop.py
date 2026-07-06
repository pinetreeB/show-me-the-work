from __future__ import annotations

import json
from pathlib import Path
import sys


def _fail_open(message: str) -> int:
    data = json.dumps({"systemMessage": f"fable-lite fail-open: {message}"}, ensure_ascii=False)
    _ = sys.stdout.buffer.write(data.encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def main() -> int:
    try:
        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from adapters.claude_code.common import emit, fail_open, project_root, read_payload, transcript_last_assistant_text
        payload = read_payload()
        from core.verify_state import evaluate_stop

        result = evaluate_stop(
            {
                "project_root": project_root(payload),
                "stop_hook_active": payload.get("stop_hook_active") is True,
                "assistant_text": transcript_last_assistant_text(payload),
            }
        )
        if result["decision"] == "block":
            return emit({"decision": "block", "reason": str(result["reason"])})
        message = str(result.get("message", "fable-lite Stop gate allow."))
        return emit(
            {
                "systemMessage": message,
                "hookSpecificOutput": {
                    "hookEventName": "Stop",
                    "additionalContext": message,
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
