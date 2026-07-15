from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Final, cast

_RESULT_CONTAINER_KEYS: Final = ("tool_response", "tool_result", "result", "data", "metadata")
_EVIDENCE_KEYS: Final = (
    "llmContent",
    "returnDisplay",
    "stdout",
    "stderr",
    "output",
    "text",
    "result",
    "error",
)
_TEXT_CONTAINER_KEYS: Final = ("tool_response", "tool_result")
_EXIT_CODE_KEYS: Final = ("exit_code", "exitCode", "returncode")
_ANSI_ESCAPE_RE: Final = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_TOOL_NAME_MAP: Final = {
    "view_file": "Read",
    "write_to_file": "Edit",
    "replace_file_content": "Edit",
    "multi_replace_file_content": "Edit",
    "replace": "Edit",
    "run_command": "Bash",
    "run_shell_command": "Bash",
    "ShellTool": "Bash",
    "write_file": "Edit",
    "edit_file": "Edit",
}


def tool_family(tool_name: str) -> str:
    from core.contract import EDIT_TOOLS, SHELL_TOOLS

    if tool_name == "Read":
        return "read"
    if tool_name in EDIT_TOOLS:
        return "edit"
    if tool_name in SHELL_TOOLS:
        return "shell"
    return "other"


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _mapping_sequence(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [cast(Mapping[str, object], item) for item in value if isinstance(item, Mapping)]


def _decoded_input(value: object) -> dict[str, object]:
    decoded = value
    if isinstance(decoded, str):
        try:
            decoded = cast(object, json.loads(decoded))
        except json.JSONDecodeError:
            return {}
    if isinstance(decoded, Mapping):
        return dict(cast(Mapping[str, object], decoded))
    return {}


def _tool_pair(
    source: Mapping[str, object],
    name_key: str,
    input_key: str,
) -> tuple[str, dict[str, object]] | None:
    name_value = source.get(name_key)
    input_value = source.get(input_key)
    if input_key == "args" and input_value is None:
        input_value = source.get("input")
    if not isinstance(name_value, str) or not name_value:
        return None
    return name_value, _decoded_input(input_value)


def extract_tool_info(payload: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    candidates: list[tuple[Mapping[str, object], str, str]] = [
        (payload, "tool_name", "tool_input"),
        (_mapping(payload.get("toolCall")), "name", "args"),
        (_mapping(payload.get("metadata")), "tool_name", "tool_input"),
    ]
    request = _mapping(payload.get("llm_request"))
    tool_calls = _mapping_sequence(request.get("tool_calls"))
    if tool_calls:
        candidates.append((tool_calls[0], "name", "args"))

    for source, name_key, input_key in candidates:
        pair = _tool_pair(source, name_key, input_key)
        if pair is not None:
            name, tool_input = pair
            return _TOOL_NAME_MAP.get(name, name), tool_input
    return "", {}


def extract_paths_from_input(tool_input: Mapping[str, object]) -> list[str]:
    paths: list[str] = []
    file_paths = tool_input.get("file_paths")
    if isinstance(file_paths, Sequence) and not isinstance(file_paths, str | bytes):
        paths.extend(str(path) for path in file_paths)
    for key in ("file_path", "path", "notebook_path", "TargetPath", "AbsolutePath", "TargetFile"):
        value = tool_input.get(key)
        if isinstance(value, str):
            paths.append(value)
    return paths


def extract_command(tool_input: Mapping[str, object]) -> str:
    for key in ("command", "CommandLine"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def _result_sources(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    sources = [payload]
    seen = {id(payload)}
    for source in sources:
        for key in _RESULT_CONTAINER_KEYS:
            value = source.get(key)
            if not isinstance(value, Mapping):
                continue
            nested = cast(Mapping[str, object], value)
            if id(nested) in seen:
                continue
            seen.add(id(nested))
            sources.append(nested)
    return sources


def _evidence(sources: Sequence[Mapping[str, object]]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for key in (*_EVIDENCE_KEYS, *_TEXT_CONTAINER_KEYS):
            value = source.get(key)
            if isinstance(value, str) and value and value not in seen:
                seen.add(value)
                parts.append(value)
    return "\n".join(parts)


def _exit_code(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        return int(value)
    return None


def _boolean_signal(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def verification_result(payload: Mapping[str, object]) -> tuple[bool, str]:
    from core.verification import text_indicates_success

    sources = _result_sources(payload)
    evidence = _evidence(sources)
    explicit_outcomes: list[bool] = []
    for source in sources:
        if (success := _boolean_signal(source.get("success"))) is not None:
            explicit_outcomes.append(success)
        if (is_error := _boolean_signal(source.get("isError"))) is not None:
            explicit_outcomes.append(not is_error)
        for key in _EXIT_CODE_KEYS:
            if (code := _exit_code(source.get(key))) is not None:
                explicit_outcomes.append(code == 0)
        error_value = source.get("error")
        error_flag = _boolean_signal(error_value)
        if error_flag is True or (error_flag is None and error_value not in (None, False, "")):
            explicit_outcomes.append(False)
    if explicit_outcomes:
        return all(explicit_outcomes), evidence
    return text_indicates_success(_ANSI_ESCAPE_RE.sub("", evidence)), evidence
