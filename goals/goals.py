from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
import sys
from typing import TypeAlias

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.contract import (  # noqa: E402 - direct-script import needs repo bootstrap
    _identity_agent_key,
    _single_active_exact_identity,
    namespaced_contract_path,
)

JsonScalar: TypeAlias = str | bool
JsonValue: TypeAlias = JsonScalar | list[dict[str, JsonScalar]]
JsonObject: TypeAlias = dict[str, JsonValue]
Identity: TypeAlias = Mapping[str, object]


def _state_dir(root: str) -> Path:
    return Path(root).resolve() / ".fable-lite"


def _legacy_goals_path(root: str) -> Path:
    return _state_dir(root) / "goals.json"


def namespaced_goals_path(root: str, agent_key: str) -> Path:
    # Keep the contracts/<identity> safe-key + hash convention so Windows-invalid
    # identity characters and safe-key collisions cannot merge two sessions.
    filename = namespaced_contract_path(root, agent_key).name
    return _state_dir(root) / "goals" / filename


def _goals_path(root: str, identity: Identity | None = None) -> Path:
    if identity is None:
        return _legacy_goals_path(root)
    return namespaced_goals_path(root, _identity_agent_key(identity))


def _load_json(path: Path) -> JsonObject | None:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(raw, dict):
        goal = raw.get("goal")
        stories = raw.get("stories")
        return {
            "goal": goal if isinstance(goal, str) else "",
            "stories": stories if isinstance(stories, list) else [],
        }
    return None


def _load(root: str, identity: Identity | None = None) -> JsonObject:
    primary = _goals_path(root, identity)
    data = _load_json(primary)
    if data is not None:
        return data
    if identity is not None:
        agent_key = _identity_agent_key(identity)
        if not primary.exists() and _single_active_exact_identity(root, agent_key):
            legacy = _load_json(_legacy_goals_path(root))
            if legacy is not None:
                return legacy
    return {"goal": "", "stories": []}


def _save(root: str, data: JsonObject, identity: Identity | None = None) -> None:
    path = _goals_path(root, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )


def _stories(data: JsonObject) -> list[dict[str, JsonScalar]]:
    value = data.get("stories")
    if not isinstance(value, list):
        return []
    return value


def plan(
    root: str,
    goal: str,
    story: str,
    verify_cmd: str,
    identity: Identity | None = None,
) -> JsonObject:
    data: JsonObject = {
        "goal": goal,
        "stories": [
            {
                "name": story,
                "verify_cmd": verify_cmd,
                "verified": False,
                "evidence": "",
            }
        ],
    }
    _save(root, data, identity)
    return data


def verify(
    root: str,
    story: str,
    evidence: str,
    identity: Identity | None = None,
) -> JsonObject:
    data = _load(root, identity)
    updated: list[dict[str, JsonScalar]] = []
    matched = False
    for item in _stories(data):
        if item.get("name") == story:
            item = {**item, "verified": True, "evidence": evidence}
            matched = True
        updated.append(item)
    if not matched:
        updated.append(
            {
                "name": story,
                "verify_cmd": "",
                "verified": True,
                "evidence": evidence,
            }
        )
    data["stories"] = updated
    _save(root, data, identity)
    return data


def status(root: str, identity: Identity | None = None) -> JsonObject:
    return _load(root, identity)


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    _ = parser.add_argument("--host")
    _ = parser.add_argument("--session-id")
    _ = parser.add_argument("--agent")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fable-lite-goals")
    sub = parser.add_subparsers(dest="command", required=True)

    plan_cmd = sub.add_parser("plan")
    _ = plan_cmd.add_argument("--root", required=True)
    _ = plan_cmd.add_argument("--goal", required=True)
    _ = plan_cmd.add_argument("--story", required=True)
    _ = plan_cmd.add_argument("--verify-cmd", required=True)
    _add_identity_arguments(plan_cmd)

    verify_cmd = sub.add_parser("verify")
    _ = verify_cmd.add_argument("--root", required=True)
    _ = verify_cmd.add_argument("--story", required=True)
    _ = verify_cmd.add_argument("--evidence", required=True)
    _add_identity_arguments(verify_cmd)

    status_cmd = sub.add_parser("status")
    _ = status_cmd.add_argument("--root", required=True)
    _add_identity_arguments(status_cmd)
    return parser


def _emit(result: JsonObject) -> int:
    _ = sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def _fail_open(message: str) -> int:
    return _emit({"fail_open": True, "message": message})


def _identity(args: argparse.Namespace) -> Identity | None:
    values = {
        field: value
        for field in ("host", "session_id", "agent")
        if isinstance(value := getattr(args, field, None), str) and value
    }
    return values or None


def _run() -> JsonObject:
    args = build_parser().parse_args()
    command = getattr(args, "command", "")
    root = str(getattr(args, "root", ""))
    identity = _identity(args)
    if command == "plan":
        return plan(
            root,
            str(getattr(args, "goal", "")),
            str(getattr(args, "story", "")),
            str(getattr(args, "verify_cmd", "")),
            identity,
        )
    if command == "verify":
        return verify(
            root,
            str(getattr(args, "story", "")),
            str(getattr(args, "evidence", "")),
            identity,
        )
    return status(root, identity)


def main() -> int:
    if len(sys.argv) == 1:
        return _fail_open("missing command")
    try:
        return _emit(_run())
    except SystemExit as exc:
        return _fail_open(f"argument parse failed: {exc.code}")
    except Exception as exc:  # noqa: BLE001
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
