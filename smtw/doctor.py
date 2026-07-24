from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any

from adapters.claude_code.project_config import (
    ConfigLoadState,
    load_project_config,
)
from core.ledger_schema import LedgerSchemaError, validate_ledger_object
from core.provenance_types import provenance_status_unsafe
from core.quarantine import list_entries
from core.runtime_env import (
    RUNTIME_ENV_SUFFIXES,
    SmtwEnvConflictError,
    resolve_smtw_env,
)
from core.state_layout import (
    StateLayout,
    StateLayoutError,
    inspect_state_layout_details,
    state_dir,
)
from core.state_migration import MigrationStatus, check_migration_state
from core.verification_covers import covers_verified

from .versioning import version_diagnostics


_HOSTS = ("auto", "claude_code", "codex_cli", "antigravity", "unknown")


def add_diagnostics_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    doctor = subparsers.add_parser(
        "doctor",
        help="report installation, activation, state, and runtime health",
    )
    _add_common_arguments(doctor)
    doctor.set_defaults(func=run_doctor)

    status = subparsers.add_parser(
        "status",
        help="report concise runtime supervision status",
    )
    _add_common_arguments(status)
    status.set_defaults(func=run_status)


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=".")
    parser.add_argument("--host", choices=_HOSTS, default="auto")
    parser.add_argument("--json", action="store_true")


def run_doctor(args: argparse.Namespace) -> int:
    payload = diagnostic_snapshot(args.root, host=args.host)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        _print_doctor(payload)
    return int(payload["exit_code"])


def run_status(args: argparse.Namespace) -> int:
    snapshot = diagnostic_snapshot(args.root, host=args.host)
    payload = {
        "active": snapshot["activation_status"] == "active",
        "layout": snapshot["state_layout"],
        "authority": snapshot["authoritative_state_dir"],
        "current_turn": snapshot["current_turn"],
        "block_counters": snapshot["block_counters"],
        "verification_freshness": snapshot["verification_freshness"],
        "coordination_degraded": snapshot["coordination_degraded"],
        "exit_code": snapshot["exit_code"],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Active: {'yes' if payload['active'] else 'no'}")
        print(f"Layout: {payload['layout']}")
        print(f"Authority: {payload['authority']}")
        print(f"Current turn: {payload['current_turn']}")
        print(f"Block counters: {json.dumps(payload['block_counters'], sort_keys=True)}")
        print(f"Verification freshness: {payload['verification_freshness']}")
        print(f"Coordination degraded: {str(payload['coordination_degraded']).lower()}")
    return int(payload["exit_code"])


def diagnostic_snapshot(project_root: str | Path, *, host: str = "auto") -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    versions = version_diagnostics()
    warnings: list[str] = []
    errors: list[str] = []
    if versions.mismatch:
        warnings.append(
            "module/distribution version mismatch: source checkout version is authoritative"
        )

    config = load_project_config(root)
    active = (
        config.state is ConfigLoadState.VALID
        and isinstance(config.values, Mapping)
        and config.values.get("supervision") is True
    )
    activation = "active" if active else "inactive"
    if config.state is ConfigLoadState.DECLARED_INVALID:
        activation = "unsafe"
        errors.append("project configuration is invalid")

    env_sources: set[str] = set()
    env_conflicts: list[str] = []
    for suffix in sorted(RUNTIME_ENV_SUFFIXES):
        try:
            resolved = resolve_smtw_env(suffix)
        except SmtwEnvConflictError:
            env_conflicts.append(suffix)
        else:
            if resolved.source != "absent":
                env_sources.add(resolved.source)
    if env_conflicts:
        errors.append("conflicting canonical and legacy runtime environment controls")

    inspection = inspect_state_layout_details(root)
    try:
        authority = state_dir(root)
    except StateLayoutError:
        authority = inspection.target
    if inspection.layout is StateLayout.CONFLICT:
        errors.append("state layout has no single authoritative tree")

    readiness = check_migration_state(root, activation=active)
    ledger = _ledger_snapshot(authority)
    if ledger["ledger_health"] == "error":
        errors.append("ledger is unreadable or invalid")
    if ledger["provenance_health"] == "unsafe":
        errors.append("active provenance is incomplete")
    if ledger["coordination_degraded"]:
        errors.append("coordination is degraded")

    try:
        quarantine = list_entries(str(root))
    except (OSError, StateLayoutError):
        quarantine = []
        if inspection.layout is StateLayout.CONFLICT:
            warnings.append("quarantine inventory unavailable for conflicting layout")

    selected_host = _detect_host() if host == "auto" else host
    plugin_registration = _plugin_registration(root, selected_host)
    probe = _last_probe_receipt(root)

    exit_code = 0
    status = "healthy"
    if errors:
        exit_code = 1
        status = "unsafe"
    elif not active:
        exit_code = 2
        status = "inactive"
    elif readiness.status in {
        MigrationStatus.READY,
        MigrationStatus.DEFERRED,
        MigrationStatus.HOME_REFUSED,
    }:
        exit_code = 2
        status = "action_required"

    runtime_source = (
        "absent"
        if not env_sources
        else next(iter(env_sources))
        if len(env_sources) == 1
        else "mixed"
    )
    return {
        "tool_version": versions.tool_version,
        "module_version": versions.module_version,
        "distribution_version": versions.distribution_version,
        "module_path": versions.module_path,
        "distribution_path": versions.distribution_path,
        "version_mismatch": versions.mismatch,
        "python_version": platform.python_version(),
        "python_path": sys.executable,
        "project_root": str(root),
        "host": selected_host,
        "plugin_registration": plugin_registration,
        "activation_status": activation,
        "config_source": config.source.value if config.source else "absent",
        "config_digest": config.digest,
        "runtime_env_source": runtime_source,
        "env_conflict": bool(env_conflicts),
        "env_conflict_keys": env_conflicts,
        "state_layout": inspection.layout.value,
        "authoritative_state_dir": str(authority),
        "migration_readiness": readiness.status.value,
        "active_turns": ledger["active_turns"],
        "open_invocations": ledger["open_invocations"],
        "ledger_health": ledger["ledger_health"],
        "provenance_health": ledger["provenance_health"],
        "quarantine_count": len(quarantine),
        "quarantine_bytes": sum(record.size_bytes for record in quarantine),
        "last_probe_receipt": probe,
        "host_support_status": _host_support(selected_host),
        "current_turn": ledger["current_turn"],
        "block_counters": ledger["block_counters"],
        "verification_freshness": ledger["verification_freshness"],
        "coordination_degraded": ledger["coordination_degraded"],
        "warnings": warnings,
        "errors": errors,
        "status": status,
        "exit_code": exit_code,
    }


def _ledger_snapshot(authority: Path) -> dict[str, Any]:
    empty = {
        "active_turns": 0,
        "open_invocations": 0,
        "ledger_health": "absent",
        "provenance_health": "not_applicable",
        "current_turn": "none",
        "block_counters": {"stop": 0, "goals": 0, "intent": 0, "design": 0},
        "verification_freshness": "not_applicable",
        "coordination_degraded": False,
    }
    path = authority / "ledger.json"
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {**empty, "ledger_health": "error"}
    if not isinstance(raw, dict):
        return {**empty, "ledger_health": "error"}
    # DOCTOR-03A (INV-06): runtime ledger loader와 공유하는 schema 게이트.
    # unsupported schema(0·3·99·비정수)가 healthy로 fall-through하지 않는다.
    try:
        _ = validate_ledger_object(raw)
    except (LedgerSchemaError, TypeError, ValueError):
        return {**empty, "ledger_health": "error"}

    turns = raw.get("active_turns")
    active = turns if isinstance(turns, dict) else {}
    open_count = 0
    counters = {"stop": 0, "goals": 0, "intent": 0, "design": 0}
    incomplete = False
    freshness: list[bool | None] = []
    for turn in active.values():
        if not isinstance(turn, dict):
            continue
        invocations = turn.get("invocations")
        if isinstance(invocations, dict):
            open_count += sum(
                1
                for invocation in invocations.values()
                if isinstance(invocation, dict) and invocation.get("status") == "open"
            )
        blocks = turn.get("blocks")
        if isinstance(blocks, dict):
            for name in counters:
                value = blocks.get(name)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    counters[name] += value
        for name in counters:
            legacy = turn.get(f"{name}_blocks")
            if isinstance(legacy, int) and not isinstance(legacy, bool) and legacy > 0:
                counters[name] += legacy
        incomplete = incomplete or turn.get("provenance_incomplete") is True
        # DOCTOR-03B (INV-06): ProvenanceStatus enum 기준 단일 판정 — 문자열
        # 하드코딩 금지. runtime Stop safety와 같은 enum 값을 본다.
        incomplete = incomplete or provenance_status_unsafe(
            turn.get("provenance_status")
        )
        try:
            freshness.append(covers_verified(turn))
        except (TypeError, ValueError):
            freshness.append(False)
    if not active:
        freshness_value = "not_applicable"
    elif all(value is True for value in freshness):
        freshness_value = "fresh"
    elif any(value is False for value in freshness):
        freshness_value = "stale"
    else:
        freshness_value = "unknown"
    current = "none"
    if len(active) == 1:
        current = next(iter(active))
    elif len(active) > 1:
        current = f"{len(active)} active"
    return {
        "active_turns": len(active),
        "open_invocations": open_count,
        "ledger_health": "healthy",
        "provenance_health": "unsafe" if incomplete else "healthy",
        "current_turn": current,
        "block_counters": counters,
        "verification_freshness": freshness_value,
        "coordination_degraded": raw.get("coordination_degraded") is True,
    }


def _detect_host() -> str:
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return "claude_code"
    if os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID"):
        return "codex_cli"
    return "unknown"


def _plugin_registration(root: Path, host: str) -> str:
    candidates = {
        "claude_code": (
            root / ".claude-plugin" / "plugin.json",
            root / ".claude" / "settings.json",
        ),
        "codex_cli": (root / ".codex" / "hooks.json",),
        "antigravity": (root / ".antigravity" / "hooks.json",),
    }.get(host, ())
    return "registered" if any(path.is_file() for path in candidates) else "not_detected"


def _last_probe_receipt(root: Path) -> str:
    candidates = (
        root / "eval" / "results" / "probes-latest.json",
        Path(__file__).resolve().parents[1] / "eval" / "results" / "probes-latest.json",
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return f"invalid:{path}"
        if isinstance(raw, dict):
            result = raw.get("result", raw.get("status", "available"))
            return f"{result}:{path}"
        return f"available:{path}"
    return "not_found"


def _host_support(host: str) -> str:
    if host in {"claude_code", "codex_cli"}:
        return "supported"
    if host == "antigravity":
        return "payload_and_load_confirmed_live_execution_unconfirmed"
    return "unknown"


def _print_doctor(payload: Mapping[str, Any]) -> None:
    rows = (
        ("Tool version", payload["tool_version"]),
        ("Distribution version", payload["distribution_version"]),
        ("Module path", payload["module_path"]),
        ("Python", f"{payload['python_version']} ({payload['python_path']})"),
        ("Project root", payload["project_root"]),
        ("Host", payload["host"]),
        ("Plugin registration", payload["plugin_registration"]),
        ("Activation", payload["activation_status"]),
        ("Config source", payload["config_source"]),
        ("Config digest", payload["config_digest"] or "none"),
        ("Runtime env source", payload["runtime_env_source"]),
        ("Env conflict", str(payload["env_conflict"]).lower()),
        ("State layout", payload["state_layout"]),
        ("Authority", payload["authoritative_state_dir"]),
        ("Migration readiness", payload["migration_readiness"]),
        ("Active turns", payload["active_turns"]),
        ("Open invocations", payload["open_invocations"]),
        ("Ledger health", payload["ledger_health"]),
        ("Provenance health", payload["provenance_health"]),
        (
            "Quarantine",
            f"{payload['quarantine_count']} files / {payload['quarantine_bytes']} bytes",
        ),
        ("Last probe receipt", payload["last_probe_receipt"]),
        ("Host support", payload["host_support_status"]),
    )
    for label, value in rows:
        print(f"{label}: {value}")
    for warning in payload["warnings"]:
        print(f"Warning: {warning}")
    for error in payload["errors"]:
        print(f"Error: {error}")
