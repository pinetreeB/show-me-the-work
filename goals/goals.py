from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import TypeAlias

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.contract import (  # noqa: E402 - direct-script import needs repo bootstrap
    _identity_agent_key,
    _looks_exact_identity_key,
    _single_active_exact_identity,
    namespaced_contract_path,
)
from core.ledger import load_ledger  # noqa: E402 - direct-script bootstrap
from core.state_layout import (  # noqa: E402 - direct-script bootstrap
    state_dir,
    state_write_scope,
)

JsonScalar: TypeAlias = str | bool | int | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
Identity: TypeAlias = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class GoalsCliError(ValueError):
    code: str
    message: str
    candidates: tuple[str, ...] = ()

    def payload(self) -> JsonObject:
        result: JsonObject = {"error": self.code, "message": self.message}
        if self.candidates:
            result["candidates"] = list(self.candidates)
        return result


def _legacy_goals_path(root: str) -> Path:
    return state_dir(root) / "goals.json"


def namespaced_goals_path(root: str, agent_key: str) -> Path:
    # Keep the contracts/<identity> safe-key + hash convention so Windows-invalid
    # identity characters and safe-key collisions cannot merge two sessions.
    filename = namespaced_contract_path(root, agent_key).name
    return state_dir(root) / "goals" / filename


def _goals_path(root: str, identity: Identity | None = None) -> Path:
    if identity is None:
        return _legacy_goals_path(root)
    return namespaced_goals_path(root, _identity_agent_key(identity))


def _identity_parts(agent_key: str) -> tuple[str, str, str] | None:
    if agent_key.count(":") != 2:
        return None
    host, session_id, agent = agent_key.split(":")
    if (
        not host
        or not session_id
        or session_id == "default"
        or not agent
        or not _looks_exact_identity_key(agent_key)
    ):
        return None
    return host, session_id, agent


def _identity_mapping(agent_key: str) -> Identity:
    parts = _identity_parts(agent_key)
    if parts is None:
        raise GoalsCliError(
            "invalid_identity",
            "identity must be exactly <host>:<session-id>:<agent> with a "
            "non-default session id",
        )
    host, session_id, agent = parts
    return {
        "host": host,
        "session_id": session_id,
        "agent": agent,
        "attribution": "exact",
    }


def _active_identities(root: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    ledger = load_ledger({"project_root": root})
    turns = ledger.get("active_turns")
    if not isinstance(turns, dict):
        return (), ()
    exact: list[str] = []
    synthetic: list[str] = []
    for key, turn in turns.items():
        if not isinstance(key, str) or not isinstance(turn, dict):
            continue
        if (
            _identity_parts(key) is not None
            and turn.get("identity_synthetic") is not True
        ):
            exact.append(key)
        else:
            synthetic.append(key)
    return tuple(sorted(exact)), tuple(sorted(synthetic))


def _explicit_identity(args: argparse.Namespace) -> str | None:
    raw_identity = getattr(args, "identity", None)
    identity = (
        raw_identity.strip()
        if isinstance(raw_identity, str) and raw_identity.strip()
        else None
    )
    trio = tuple(getattr(args, field, None) for field in ("host", "session_id", "agent"))
    trio_present = tuple(isinstance(value, str) and bool(value.strip()) for value in trio)
    if any(trio_present) and not all(trio_present):
        raise GoalsCliError(
            "incomplete_identity",
            "--host, --session-id, and --agent must be provided together",
        )
    trio_identity = (
        ":".join(str(value).strip() for value in trio)
        if all(trio_present)
        else None
    )
    for candidate in (identity, trio_identity):
        if candidate is not None and _identity_parts(candidate) is None:
            raise GoalsCliError(
                "invalid_identity",
                "identity must be exactly <host>:<session-id>:<agent> with a "
                "non-default session id",
            )
    if identity is not None and trio_identity is not None and identity != trio_identity:
        raise GoalsCliError(
            "identity_mismatch",
            "--identity and --host/--session-id/--agent refer to different identities",
        )
    return identity or trio_identity


def _environment_matches(
    candidates: tuple[str, ...],
    environ: Mapping[str, str],
) -> tuple[str, ...]:
    hints: set[tuple[str, str, str | None]] = set()
    smtw_values = tuple(
        environ.get(name, "").strip()
        for name in ("SMTW_HOST", "SMTW_SESSION_ID", "SMTW_AGENT")
    )
    if all(smtw_values):
        hints.add((smtw_values[0], smtw_values[1], smtw_values[2]))
    for variable, host in (
        ("CODEX_THREAD_ID", "codex_cli"),
        ("CODEX_SESSION_ID", "codex_cli"),
        ("CLAUDE_CODE_SESSION_ID", "claude_code"),
        ("CLAUDE_SESSION_ID", "claude_code"),
        ("ANTIGRAVITY_CONVERSATION_ID", "antigravity"),
    ):
        session_id = environ.get(variable, "").strip()
        if session_id:
            hints.add((host, session_id, None))

    matches: set[str] = set()
    for candidate in candidates:
        parts = _identity_parts(candidate)
        if parts is None:
            continue
        host, session_id, agent = parts
        if any(
            host == hint_host
            and session_id == hint_session
            and (hint_agent is None or agent == hint_agent)
            for hint_host, hint_session, hint_agent in hints
        ):
            matches.add(candidate)
    return tuple(sorted(matches))


def _hook_receipt_matches(
    root: str,
    candidates: tuple[str, ...],
) -> tuple[str, ...]:
    ledger = load_ledger({"project_root": root})
    turns = ledger.get("active_turns")
    if not isinstance(turns, dict):
        return ()
    matches: list[str] = []
    for candidate in candidates:
        turn = turns.get(candidate)
        if not isinstance(turn, dict):
            continue
        invocations = turn.get("invocations")
        if not isinstance(invocations, dict):
            continue
        if any(
            isinstance(receipt, dict) and receipt.get("status") == "open"
            for receipt in invocations.values()
        ):
            matches.append(candidate)
    return tuple(sorted(matches))


def resolve_identity(
    root: str,
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] | None = None,
) -> Identity | None:
    explicit = _explicit_identity(args)
    exact, synthetic = _active_identities(root)
    if explicit is not None:
        if exact and explicit not in exact:
            raise GoalsCliError(
                "wrong_identity",
                f"identity {explicit!r} is not an active exact identity",
                exact,
            )
        if not exact and synthetic:
            raise GoalsCliError(
                "synthetic_identity",
                "active identity is synthetic; provide a real host session before "
                "writing goals",
                synthetic,
            )
        return _identity_mapping(explicit)

    if len(exact) == 1:
        return _identity_mapping(exact[0])
    if len(exact) > 1:
        source = os.environ if environ is None else environ
        environment_matches = _environment_matches(exact, source)
        receipt_matches = _hook_receipt_matches(root, exact)
        unique_hints = {
            matches[0]
            for matches in (environment_matches, receipt_matches)
            if len(matches) == 1
        }
        if len(unique_hints) == 1:
            return _identity_mapping(next(iter(unique_hints)))
        raise GoalsCliError(
            "ambiguous_identity",
            "multiple active exact identities; rerun with --identity or the "
            "matching --host/--session-id/--agent triplet",
            exact,
        )
    if synthetic:
        raise GoalsCliError(
            "synthetic_identity",
            "only synthetic active identities are available; refusing to report "
            "a goals checkpoint success",
            synthetic,
        )
    # No active turn is the legacy single-agent compatibility surface.
    return None


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
    with state_write_scope(root, wait_seconds=15) as authority:
        if identity is None:
            path = authority / "goals.json"
        else:
            filename = namespaced_contract_path(
                root, _identity_agent_key(identity)
            ).name
            path = authority / "goals" / filename
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


def add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    _ = parser.add_argument("--identity")
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
    add_identity_arguments(plan_cmd)

    verify_cmd = sub.add_parser("verify")
    _ = verify_cmd.add_argument("--root", required=True)
    _ = verify_cmd.add_argument("--story", required=True)
    _ = verify_cmd.add_argument("--evidence", required=True)
    add_identity_arguments(verify_cmd)

    status_cmd = sub.add_parser("status")
    _ = status_cmd.add_argument("--root", required=True)
    add_identity_arguments(status_cmd)
    return parser


def _emit(result: JsonObject) -> int:
    _ = sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def execute(args: argparse.Namespace, *, command_field: str = "command") -> JsonObject:
    command = getattr(args, command_field, "")
    root = str(getattr(args, "root", ""))
    identity = resolve_identity(root, args)
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


def _run() -> JsonObject:
    return execute(build_parser().parse_args())


def main() -> int:
    if len(sys.argv) == 1:
        _ = _emit({"error": "missing_command", "message": "goals command is required"})
        return 2
    try:
        return _emit(_run())
    except GoalsCliError as exc:
        _ = _emit(exc.payload())
        return 2
    except SystemExit as exc:
        _ = _emit(
            {
                "error": "argument_parse_failed",
                "message": f"argument parse failed: {exc.code}",
            }
        )
        return 2
    except Exception as exc:  # noqa: BLE001
        _ = _emit({"error": "goals_failed", "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
