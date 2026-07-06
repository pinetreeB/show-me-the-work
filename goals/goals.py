from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import TypeAlias

JsonScalar: TypeAlias = str | bool
JsonValue: TypeAlias = JsonScalar | list[dict[str, JsonScalar]]
JsonObject: TypeAlias = dict[str, JsonValue]


def _state_dir(root: str) -> Path:
    return Path(root).resolve() / ".fable-lite"


def _goals_path(root: str) -> Path:
    return _state_dir(root) / "goals.json"


def _load(root: str) -> JsonObject:
    try:
        raw: object = json.loads(_goals_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"goal": "", "stories": []}
    if isinstance(raw, dict):
        goal = raw.get("goal")
        stories = raw.get("stories")
        return {
            "goal": goal if isinstance(goal, str) else "",
            "stories": stories if isinstance(stories, list) else [],
        }
    return {"goal": "", "stories": []}


def _save(root: str, data: JsonObject) -> None:
    directory = _state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    _ = _goals_path(root).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )


def _stories(data: JsonObject) -> list[dict[str, JsonScalar]]:
    value = data.get("stories")
    if not isinstance(value, list):
        return []
    return value


def plan(root: str, goal: str, story: str, verify_cmd: str) -> JsonObject:
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
    _save(root, data)
    return data


def verify(root: str, story: str, evidence: str) -> JsonObject:
    data = _load(root)
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
    _save(root, data)
    return data


def status(root: str) -> JsonObject:
    return _load(root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fable-lite-goals")
    sub = parser.add_subparsers(dest="command", required=True)

    plan_cmd = sub.add_parser("plan")
    _ = plan_cmd.add_argument("--root", required=True)
    _ = plan_cmd.add_argument("--goal", required=True)
    _ = plan_cmd.add_argument("--story", required=True)
    _ = plan_cmd.add_argument("--verify-cmd", required=True)

    verify_cmd = sub.add_parser("verify")
    _ = verify_cmd.add_argument("--root", required=True)
    _ = verify_cmd.add_argument("--story", required=True)
    _ = verify_cmd.add_argument("--evidence", required=True)

    status_cmd = sub.add_parser("status")
    _ = status_cmd.add_argument("--root", required=True)
    return parser


def _emit(result: JsonObject) -> int:
    _ = sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def _fail_open(message: str) -> int:
    return _emit({"fail_open": True, "message": message})


def _run() -> JsonObject:
    args = build_parser().parse_args()
    command = getattr(args, "command", "")
    root = str(getattr(args, "root", ""))
    if command == "plan":
        return plan(
            root,
            str(getattr(args, "goal", "")),
            str(getattr(args, "story", "")),
            str(getattr(args, "verify_cmd", "")),
        )
    if command == "verify":
        return verify(
            root,
            str(getattr(args, "story", "")),
            str(getattr(args, "evidence", "")),
        )
    return status(root)


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
