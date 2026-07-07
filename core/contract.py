from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import shlex
from typing import Final, TypeAlias

from .ledger import JsonObject, load_ledger, save_ledger, state_dir
from .risk_terms import risk_flags

JsonValue: TypeAlias = str | int | bool | list[str]
Decision: TypeAlias = dict[str, JsonValue]

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"}
SHELL_TOOLS = {"Bash", "PowerShell"}
GUARDED_TOOLS = EDIT_TOOLS | SHELL_TOOLS
FAKE_EVIDENCE = ("assumed", "would pass", "should pass", "not run", "미실행")
MAX_GOALS_BLOCKS = 2
COMMAND_SEPARATORS: Final[frozenset[str]] = frozenset({"&&", "||", ";", "|"})
RM_COMMANDS: Final[frozenset[str]] = frozenset({"rm", "remove-item"})
PATH_OPTIONS: Final[frozenset[str]] = frozenset({"path", "literalpath"})
DIRECTORY_NAMES: Final[frozenset[str]] = frozenset({"node_modules", ".git", ".venv", "venv", "__pycache__"})


@dataclass(frozen=True, slots=True)
class RmInvocation:
    command: str
    recursive: bool
    targets: tuple[str, ...]


def contract_path(project_root: str) -> Path:
    return state_dir(project_root) / "contract.json"


def goals_path(project_root: str) -> Path:
    return state_dir(project_root) / "goals.json"


def _project_root(payload: Mapping[str, object]) -> str:
    root = payload.get("project_root") or payload.get("cwd")
    return root if isinstance(root, str) and root else "."


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _tool_name(payload: Mapping[str, object]) -> str:
    value = payload.get("tool_name")
    return value if isinstance(value, str) else ""


def _command(payload: Mapping[str, object]) -> str:
    value = payload.get("command")
    return value if isinstance(value, str) else ""


def _is_contract_authoring(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.endswith(".fable-lite/contract.json")


def _is_goals_authoring(paths: list[str], command: str) -> bool:
    normalized_paths = [path.replace("\\", "/") for path in paths]
    if any(path.endswith(".fable-lite/goals.json") for path in normalized_paths):
        return True
    lowered = command.lower()
    return "goals.py" in lowered and " plan" in lowered


def _high_risk(payload: Mapping[str, object]) -> bool:
    root = _project_root(payload)
    prompt_value = payload.get("prompt")
    prompt = prompt_value if isinstance(prompt_value, str) else ""
    paths = _string_list(payload.get("file_paths"))
    command = _command(payload)
    haystack = " ".join([prompt, *paths, command])
    flags = risk_flags(haystack)
    if command and _rm_family_high_risk(command, root):
        return True
    if any(not _is_rm_risk_flag(flag) for flag in flags):
        return True
    if any(_is_rm_risk_flag(flag) for flag in flags):
        return _rm_family_high_risk(command or haystack, root, fail_closed=True)
    return False


def _is_rm_risk_flag(flag: str) -> bool:
    lowered = flag.casefold()
    return "rm -rf" in lowered or "remove-item" in lowered


def _rm_family_high_risk(text: str, root: str, *, fail_closed: bool = False) -> bool:
    invocations = _rm_invocations(text)
    if not invocations:
        return fail_closed
    return any(_rm_invocation_high_risk(invocation, root) for invocation in invocations)


def _rm_invocations(text: str) -> list[RmInvocation]:
    try:
        tokens = [_clean_token(token) for token in shlex.split(text, posix=False)]
    except ValueError:
        lowered = text.casefold()
        if "rm" in lowered or "remove-item" in lowered:
            return [RmInvocation(command="rm", recursive=True, targets=())]
        return []
    invocations: list[RmInvocation] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _is_rm_command(token):
            invocation, index = _parse_rm_invocation(tokens, index)
            invocations.append(invocation)
        else:
            index += 1
    return invocations


def _parse_rm_invocation(tokens: list[str], start: int) -> tuple[RmInvocation, int]:
    command = _command_name(tokens[start])
    recursive = False
    targets: list[str] = []
    index = start + 1
    while index < len(tokens) and tokens[index] not in COMMAND_SEPARATORS:
        token = tokens[index]
        if token == "--":
            index += 1
            while index < len(tokens) and tokens[index] not in COMMAND_SEPARATORS:
                targets.append(tokens[index])
                index += 1
            return RmInvocation(command=command, recursive=recursive, targets=tuple(targets)), index
        if token.startswith("-"):
            recursive = recursive or _option_is_recursive(command, token)
            option = _option_name(token)
            inline_target = _inline_path_target(token)
            if inline_target:
                targets.append(inline_target)
                index += 1
                continue
            if option in PATH_OPTIONS and index + 1 < len(tokens):
                targets.append(tokens[index + 1])
                index += 2
                continue
        else:
            targets.append(token)
        index += 1
    return RmInvocation(command=command, recursive=recursive, targets=tuple(targets)), index


def _rm_invocation_high_risk(invocation: RmInvocation, root: str) -> bool:
    if not invocation.targets:
        return True
    return any(_rm_target_high_risk(target, root, invocation.recursive) for target in _split_targets(invocation.targets))


def _split_targets(targets: tuple[str, ...]) -> list[str]:
    return [part.strip() for target in targets for part in target.split(",")]


def _rm_target_high_risk(target: str, root: str, recursive: bool) -> bool:
    normalized = _clean_token(target).replace("\\", "/").strip()
    if not normalized or _has_glob(normalized) or _is_root_or_home(normalized) or _has_environment_reference(normalized):
        return True
    if Path(normalized).is_absolute() or _is_drive_qualified(normalized) or _contains_parent_traversal(normalized):
        return True
    target_path = Path(root) / normalized
    try:
        target_path.resolve().relative_to(Path(root).resolve())
    except ValueError:
        return True
    if recursive and _looks_like_directory_target(normalized, target_path):
        return True
    return False


def _looks_like_directory_target(target: str, path: Path) -> bool:
    if path.exists():
        return path.is_dir()
    clean = target.rstrip("/")
    if target.endswith("/"):
        return True
    name = Path(clean).name.casefold()
    return name in DIRECTORY_NAMES or not Path(clean).suffix


def _option_is_recursive(command: str, token: str) -> bool:
    option = _option_name(token)
    if command == "remove-item":
        return option in {"r", "recurse"} or (len(option) >= 2 and "recurse".startswith(option))
    return option == "recursive" or ("r" in option and not token.startswith("--"))


def _option_name(token: str) -> str:
    return token.lstrip("-").split(":", 1)[0].casefold()


def _inline_path_target(token: str) -> str:
    option, separator, value = token.lstrip("-").partition(":")
    if separator and option.casefold() in PATH_OPTIONS:
        return value
    return ""


def _has_glob(target: str) -> bool:
    return any(marker in target for marker in ("*", "?", "[", "]", "{", "}"))


def _is_root_or_home(target: str) -> bool:
    clean = target.strip()
    return clean in {"/", "\\", "~", ".", "./"} or clean.startswith("~")


def _has_environment_reference(target: str) -> bool:
    lowered = target.casefold()
    return "$" in target or "%" in target or "$env:" in lowered


def _contains_parent_traversal(target: str) -> bool:
    return ".." in Path(target.replace("\\", "/")).parts


def _is_drive_qualified(target: str) -> bool:
    return len(target) >= 2 and target[0].isalpha() and target[1] == ":"


def _is_rm_command(token: str) -> bool:
    return _command_name(token) in RM_COMMANDS


def _command_name(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return name.removesuffix(".exe")


def _clean_token(token: str) -> str:
    return token.strip("\"'")


def evaluate_r1_contract(payload: Mapping[str, object]) -> Decision:
    root = _project_root(payload)
    if not _high_risk(payload):
        return {"decision": "allow", "message": "not high-risk"}
    if _valid_contract(root):
        return {"decision": "allow", "message": "valid high-risk contract found"}
    return {
        "decision": "block",
        "reason": (
            "fable-lite R1: high-risk 수정은 `.fable-lite/contract.json` 계약이 먼저 필요합니다. "
            "restated_goal, acceptance, evidence를 기록한 뒤 다시 시도하세요. "
            "/ High-risk edits require a valid task contract first."
        ),
    }


def _valid_contract(root: str) -> bool:
    try:
        raw: object = json.loads(contract_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False

    restated = raw.get("restated_goal")
    acceptance = raw.get("acceptance")
    evidence = raw.get("evidence", [])
    if not isinstance(restated, str) or not restated.strip():
        return False
    if not isinstance(acceptance, list) or not any(isinstance(item, str) and item.strip() for item in acceptance):
        return False
    if isinstance(evidence, list):
        text = "\n".join(item for item in evidence if isinstance(item, str)).lower()
        return not any(marker in text for marker in FAKE_EVIDENCE)
    return True


def _needs_goals_block(root: str) -> bool:
    ledger = load_ledger({"project_root": root})
    return ledger.get("needs_goals") is True and not goals_path(root).exists()


def _goals_blocks(ledger: Mapping[str, object]) -> int:
    value = ledger.get("goals_blocks")
    return value if isinstance(value, int) else 0


def _block_goals_once(root: str) -> Decision:
    ledger: JsonObject = load_ledger({"project_root": root})
    blocks = _goals_blocks(ledger)
    if blocks >= MAX_GOALS_BLOCKS:
        return {
            "decision": "allow",
            "message": "goals gate max 2 blocks reached; fail-open allow",
        }
    ledger["goals_blocks"] = blocks + 1
    save_ledger({"project_root": root}, ledger)
    return {
        "decision": "block",
        "reason": (
            "fable-lite N2: 2+ 스토리 작업은 `.fable-lite/goals.json` 체크포인트가 먼저 필요합니다. "
            "goals plan을 작성하거나 명시 확인 후 다시 시도하세요. "
            "/ Multi-story work requires a goals checkpoint first."
        ),
    }


def evaluate_pretool_contract(payload: Mapping[str, object]) -> Decision:
    tool = _tool_name(payload)
    if tool not in GUARDED_TOOLS:
        return {"decision": "allow", "message": "not a guarded tool"}

    paths = _string_list(payload.get("file_paths"))
    command = _command(payload)
    root = _project_root(payload)
    if _needs_goals_block(root) and not _is_goals_authoring(paths, command):
        return _block_goals_once(root)

    if paths and all(_is_contract_authoring(path) for path in paths):
        return {"decision": "allow", "message": "contract authoring allowed"}
    return evaluate_r1_contract(payload)
