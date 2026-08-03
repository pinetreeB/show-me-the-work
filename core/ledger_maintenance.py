"""W4a — ledger lifecycle maintenance (plan/apply).

plan_maintenance는 safe-auto(타임스탬프 있는 stale turn)와 manual-only
(timestamp-less legacy turn) 후보를 source digest·identity·reason·예상 bytes와
함께 만든다. current identity·30분 lease 내 open invocation·unsettled
attribution·pending manifest tx는 후보에서 제외한다.

apply_maintenance_plan은 source digest CAS를 재확인하고, 백업 후 exact 후보만
적용한다. closed-turn replay guard 256 cap을 밀어내는 apply는 거부한다.
운영 ledger apply는 별도 승인과 백업 확인 후 수행한다(이 모듈은 코드 경로만).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Final

from .ledger_schema import LedgerSchemaError, serialize_v2_ledger
from .ledger_storage import atomic_write_bytes, atomic_write_text, ledger_path
from .ledger_v1 import sequence_value
from .ledger_v2 import (
    INVOCATION_LEASE,
    TURN_STALE_AFTER,
    _remember_closed_turn,
    closed_turn_capacity_available,
)
from .verification_covers import active_turn


MAINTENANCE_BACKUP_PREFIX: Final = "ledger.maintenance-"
SAFE_AUTO: Final = "safe_auto"
MANUAL_ONLY: Final = "manual_only"


class LedgerMaintenanceError(RuntimeError):
    def __init__(self, stage: str, detail: str) -> None:
        self.stage = stage
        self.detail = detail
        super().__init__(stage, detail)

    def __str__(self) -> str:
        return f"ledger maintenance failed during {self.stage}: {self.detail}"


@dataclass(frozen=True, slots=True)
class MaintenanceCandidate:
    agent_key: str
    turn_id: str
    category: str
    reason: str
    estimated_bytes: int


@dataclass(frozen=True, slots=True)
class MaintenancePlan:
    source_digest: str
    source_bytes: int
    candidates: tuple[MaintenanceCandidate, ...]
    blocked: tuple[str, ...] = ()

    @property
    def safe_auto(self) -> tuple[MaintenanceCandidate, ...]:
        return tuple(c for c in self.candidates if c.category == SAFE_AUTO)

    @property
    def manual_only(self) -> tuple[MaintenanceCandidate, ...]:
        return tuple(c for c in self.candidates if c.category == MANUAL_ONLY)

    @property
    def estimated_bytes(self) -> int:
        return sum(c.estimated_bytes for c in self.candidates)


@dataclass(frozen=True, slots=True)
class MaintenanceApplyResult:
    rejected: bool
    reason: str = ""
    removed: int = 0
    backup_path: str | None = None


def plan_maintenance(project_root: str) -> MaintenancePlan:
    """Write-free 분석: safe-auto/manual-only 후보와 source digest를 만든다."""
    root = Path(project_root).expanduser().resolve()
    destination = ledger_path(str(root))
    try:
        original = destination.read_bytes()
    except FileNotFoundError:
        return MaintenancePlan("", 0, (), blocked=("absent",))
    except OSError as exc:
        raise LedgerMaintenanceError("read", str(exc)) from exc
    try:
        raw: object = json.loads(original.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerMaintenanceError("parse", str(exc)) from exc
    if not isinstance(raw, dict):
        raise LedgerMaintenanceError("parse", "ledger root must be an object")
    digest = sha256(original).hexdigest()
    blocked: list[str] = []
    if raw.get("manifest_pending") is not None:
        blocked.append("pending_manifest_transaction")
    candidates = _plan_candidates(raw)
    return MaintenancePlan(
        source_digest=digest,
        source_bytes=len(original),
        candidates=candidates,
        blocked=tuple(blocked),
    )


def apply_maintenance_plan(
    project_root: str,
    plan: MaintenancePlan,
    *,
    include_manual_only: bool = False,
) -> MaintenanceApplyResult:
    """digest CAS 재확인 + 백업 후 exact 후보만 적용한다.

    거부 조건: digest 불일치, pending manifest tx, lease/open/unsettled
    재확인 실패, closed-turn replay guard 256 cap 초과.
    """
    root = Path(project_root).expanduser().resolve()
    destination = ledger_path(str(root))
    try:
        original = destination.read_bytes()
    except FileNotFoundError:
        return MaintenanceApplyResult(rejected=True, reason="absent")
    except OSError as exc:
        return MaintenanceApplyResult(
            rejected=True,
            reason=f"read:{type(exc).__name__}",
        )
    if sha256(original).hexdigest() != plan.source_digest:
        return MaintenanceApplyResult(rejected=True, reason="digest_mismatch")
    try:
        raw: object = json.loads(original.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return MaintenanceApplyResult(
            rejected=True,
            reason=f"parse:{type(exc).__name__}",
        )
    if not isinstance(raw, dict):
        return MaintenanceApplyResult(rejected=True, reason="parse:not_object")
    if raw.get("manifest_pending") is not None:
        return MaintenanceApplyResult(rejected=True, reason="pending_manifest_transaction")

    now = datetime.now(UTC)
    current = active_turn(raw)
    current_key = _find_key(raw.get("active_turns"), current) if current is not None else None
    apply_keys = _reverify_candidates(
        raw,
        plan,
        now=now,
        current_key=current_key,
        include_manual_only=include_manual_only,
    )
    if not apply_keys:
        return MaintenanceApplyResult(rejected=False, reason="no_candidates", removed=0)
    if not closed_turn_capacity_available(raw, additional=len(apply_keys)):
        return MaintenanceApplyResult(rejected=True, reason="closed_turn_capacity_exceeded")

    backup = _preserve_backup(destination, original)
    active = raw.get("active_turns")
    removed = 0
    if isinstance(active, dict):
        for key in apply_keys:
            turn = active.pop(key, None)
            if isinstance(turn, dict):
                _remember_closed_turn(
                    raw,
                    key,
                    turn,
                    sequence_value(raw.get("event_seq")),
                )
                removed += 1
        raw["active_turns"] = active
    try:
        serialized = serialize_v2_ledger(raw)
    except LedgerSchemaError as exc:
        _restore_backup(destination, backup)
        return MaintenanceApplyResult(
            rejected=True,
            reason=f"validate:{exc.field}",
        )
    try:
        atomic_write_text(destination, serialized, prefix="ledger-maintenance-")
    except OSError as exc:
        _restore_backup(destination, backup)
        return MaintenanceApplyResult(
            rejected=True,
            reason=f"write:{type(exc).__name__}",
        )
    return MaintenanceApplyResult(
        rejected=False,
        removed=removed,
        backup_path=str(backup),
    )


def _plan_candidates(raw: dict) -> tuple[MaintenanceCandidate, ...]:
    active = raw.get("active_turns")
    if not isinstance(active, dict):
        return ()
    current = active_turn(raw)
    current_key = _find_key(active, current) if current is not None else None
    now = datetime.now(UTC)
    candidates: list[MaintenanceCandidate] = []
    for agent_key, turn in active.items():
        if not isinstance(agent_key, str) or not isinstance(turn, dict):
            continue
        if agent_key == current_key:
            continue
        if _has_open_lease(turn, now):
            continue
        if _has_unsettled_attribution(raw, agent_key):
            continue
        last_event = _parse_timestamp(turn.get("last_event_at"))
        if last_event is None:
            candidates.append(
                MaintenanceCandidate(
                    agent_key,
                    _turn_id(turn),
                    MANUAL_ONLY,
                    "timestamp_less_legacy",
                    _estimate_bytes(turn),
                )
            )
        elif now - last_event > TURN_STALE_AFTER:
            candidates.append(
                MaintenanceCandidate(
                    agent_key,
                    _turn_id(turn),
                    SAFE_AUTO,
                    "stale_turn",
                    _estimate_bytes(turn),
                )
            )
    return tuple(candidates)


def _reverify_candidates(
    raw: dict,
    plan: MaintenancePlan,
    *,
    now: datetime,
    current_key: str | None,
    include_manual_only: bool,
) -> list[str]:
    active = raw.get("active_turns")
    if not isinstance(active, dict):
        return []
    apply_keys: list[str] = []
    for candidate in plan.candidates:
        if candidate.category == MANUAL_ONLY and not include_manual_only:
            continue
        if candidate.agent_key == current_key:
            continue
        turn = active.get(candidate.agent_key)
        if not isinstance(turn, dict):
            continue
        if _has_open_lease(turn, now):
            continue
        if _has_unsettled_attribution(raw, candidate.agent_key):
            continue
        apply_keys.append(candidate.agent_key)
    return apply_keys


def _has_open_lease(turn: dict, now: datetime) -> bool:
    invocations = turn.get("invocations")
    if not isinstance(invocations, dict):
        return False
    for entry in invocations.values():
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        started = _parse_timestamp(entry.get("started_at"))
        if started is None or now - started <= INVOCATION_LEASE:
            return True
    return False


def _has_unsettled_attribution(raw: dict, agent_key: str) -> bool:
    attribution = raw.get("path_attribution")
    if not isinstance(attribution, dict):
        return False
    for entry in attribution.values():
        if not isinstance(entry, dict):
            continue
        owners = entry.get("owners")
        if not isinstance(owners, list):
            continue
        for owner in owners:
            if (
                isinstance(owner, dict)
                and owner.get("agent_key") == agent_key
                and owner.get("settled") is False
            ):
                return True
    return False


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _turn_id(turn: dict) -> str:
    value = turn.get("turn_id")
    return value if isinstance(value, str) else ""


def _estimate_bytes(turn: dict) -> int:
    return len(json.dumps(turn, ensure_ascii=False, sort_keys=True))


def _find_key(active: object, target: dict) -> str | None:
    if not isinstance(active, dict):
        return None
    for key, value in active.items():
        if value is target:
            return key if isinstance(key, str) else None
    return None


def _preserve_backup(destination: Path, original: bytes) -> Path:
    digest = sha256(original).hexdigest()
    backup = destination.with_name(f"{MAINTENANCE_BACKUP_PREFIX}{digest}.bak")
    if not backup.exists():
        atomic_write_bytes(backup, original, prefix="ledger-maintenance-backup-")
    return backup


def _restore_backup(destination: Path, backup: Path) -> None:
    try:
        content = backup.read_bytes()
    except OSError as exc:
        raise LedgerMaintenanceError("restore", str(exc)) from exc
    atomic_write_bytes(destination, content, prefix="ledger-maintenance-restore-")