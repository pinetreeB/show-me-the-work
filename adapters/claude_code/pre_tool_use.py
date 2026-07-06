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
        from adapters.claude_code.common import emit, fail_open, project_root, read_payload, tool_command, tool_file_paths, tool_input
        payload = read_payload()
        from core.contract import evaluate_pretool_contract

        input_text = json.dumps(tool_input(payload), ensure_ascii=False)
        result = evaluate_pretool_contract(
            {
                "project_root": project_root(payload),
                "tool_name": str(payload.get("tool_name", "")),
                "file_paths": tool_file_paths(payload),
                "command": tool_command(payload),
                "prompt": input_text,
            }
        )
        if result["decision"] == "block":
            return emit({"decision": "block", "reason": str(result["reason"])})
        return emit({})
    except Exception as exc:  # noqa: BLE001
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
