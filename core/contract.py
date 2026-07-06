from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import TypeAlias

from .ledger import JsonObject, load_ledger, save_ledger, state_dir
from .risk_terms import is_high_risk

JsonValue: TypeAlias = str | int | bool | list[str]
Decision: TypeAlias = dict[str, JsonValue]

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"}
SHELL_TOOLS = {"Bash", "PowerShell"}
GUARDED_TOOLS = EDIT_TOOLS | SHELL_TOOLS
FAKE_EVIDENCE = ("assumed", "would pass", "should pass", "not run", "미실행")
MAX_GOALS_BLOCKS = 2


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
    prompt_value = payload.get("prompt")
    prompt = prompt_value if isinstance(prompt_value, str) else ""
    paths = _string_list(payload.get("file_paths"))
    command = _command(payload)
    haystack = " ".join([prompt, *paths, command])
    return is_high_risk(haystack)


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
