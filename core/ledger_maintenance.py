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
    RETIRED_UNVERIFIED,
    TURN_STALE_AFTER,
    _remember_closed_turn,
    closed_turn_capacity_available,
    remember_retired_turn,
    retired_turn_capacity_available,
)
from .verification_covers import active_turn


MAINTENANCE_BACKUP_PREFIX: Final = "ledger.maintenance-"
STATUSLESS_ARCHIVE_PREFIX: Final = "ledger.legacy-statusless-"
SAFE_AUTO: Final = "safe_auto"
MANUAL_ONLY: Final = "manual_only"
LEGACY_STATUSLESS: Final = "legacy_statusless"


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
    statusless_invocations: int = 0
    exclusions: tuple[str, ...] = ()


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
    def legacy_statusless(self) -> tuple[MaintenanceCandidate, ...]:
        return tuple(c for c in self.candidates if c.category == LEGACY_STATUSLESS)

    @property
    def statusless_invocations(self) -> int:
        return sum(c.statusless_invocations for c in self.candidates)

    @property
    def estimated_bytes(self) -> int:
        return sum(c.estimated_bytes for c in self.candidates)


@dataclass(frozen=True, slots=True)
class MaintenanceApplyResult:
    rejected: bool
    reason: str = ""
    removed: int = 0
    backup_path: str | None = None
    retired: int = 0
    archive_path: str | None = None


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
    retire_legacy_statusless: bool = False,
    ack_unverified: int | None = None,
) -> MaintenanceApplyResult:
    """digest CAS 재확인 + 백업 후 exact 후보만 적용한다.

    거부 조건: digest 불일치, pending manifest tx, lease/open/unsettled
    재확인 실패, closed-turn replay guard 256 cap 초과.

    ``retire_legacy_statusless``는 status 없는 구 스키마 invocation을 가진 턴을
    **완료가 아니라 '증거 부족 퇴역'으로** 옮긴다. status를 임의로 closed로 채우지
    않고 원문 bytes를 archive에 보관한 뒤 ``retired_turns``에 archive digest와 함께
    기록한다. 운영자가 ``ack_unverified``로 영향 invocation 수를 정확히 대야만 열린다.
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
    retire_keys: list[str] = []
    archive: Path | None = None
    if retire_legacy_statusless:
        retire_keys = _reverify_statusless(raw, plan, now=now)
        observed_statusless = sum(
            _count_statusless_invocations(turn)
            for key in retire_keys
            if isinstance(turn := _turn_at(raw, key), dict)
        )
        if ack_unverified is None:
            return MaintenanceApplyResult(rejected=True, reason="ack_unverified_required")
        if ack_unverified != observed_statusless:
            return MaintenanceApplyResult(
                rejected=True,
                reason=f"ack_mismatch:expected={observed_statusless}",
            )
    if not apply_keys and not retire_keys:
        return MaintenanceApplyResult(rejected=False, reason="no_candidates", removed=0)
    if not closed_turn_capacity_available(raw, additional=len(apply_keys)):
        return MaintenanceApplyResult(rejected=True, reason="closed_turn_capacity_exceeded")
    if not retired_turn_capacity_available(raw, additional=len(retire_keys)):
        return MaintenanceApplyResult(rejected=True, reason="retired_turn_capacity_exceeded")

    backup = _preserve_backup(destination, original)
    if retire_keys:
        # 원문 archive를 live 교체보다 **먼저** 쓴다. 중간 크래시에도 증거가 남는다.
        try:
            archive = _preserve_statusless_archive(destination, original, plan.source_digest)
        except OSError as exc:
            return MaintenanceApplyResult(
                rejected=True,
                reason=f"archive:{type(exc).__name__}",
            )
    active = raw.get("active_turns")
    removed = 0
    retired = 0
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
        for key in retire_keys:
            turn = active.pop(key, None)
            if isinstance(turn, dict):
                remember_retired_turn(
                    raw,
                    key,
                    turn,
                    sequence_value(raw.get("event_seq")),
                    archive_digest=plan.source_digest,
                    resolution=RETIRED_UNVERIFIED,
                )
                retired += 1
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
        retired=retired,
        archive_path=str(archive) if archive is not None else None,
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
        if _has_open_lease(turn, now):
            # 30분 lease 안의 open invocation은 실제 진행 중일 수 있다. 어떤 경우에도 건드리지 않는다.
            continue
        statusless = _count_statusless_invocations(turn)
        if statusless:
            # W5: status 없는 invocation은 구 스키마 잔재다. 현행 ``_remember_invocation``은
            # 언제나 status="open"을 기록하므로(ledger_v2.py), status 부재는 시각이 아니라
            # **스키마 세대**로 판정되는 증거다. 따라서 current/unsettled로 제외되는 턴이라도
            # 별도 카테고리로 보고해 운영자 명시 승인 경로를 열어 준다. 이 분류가 없으면
            # 제외된 턴에 남은 status-less 때문에 apply가 통째로 롤백된다.
            candidates.append(
                MaintenanceCandidate(
                    agent_key,
                    _turn_id(turn),
                    LEGACY_STATUSLESS,
                    "statusless_legacy_schema",
                    _estimate_bytes(turn),
                    statusless,
                    _exclusion_reasons(raw, agent_key, current_key),
                )
            )
            continue
        if agent_key == current_key:
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
        if candidate.category == LEGACY_STATUSLESS:
            # W5: status-less 턴은 절대 closed(=완료)로 기록하지 않는다. 증거 없이 완료로
            # 표시하는 것이 이 하네스가 막으려는 바로 그 행위다. 퇴역 경로로만 처리한다.
            continue
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


def _reverify_statusless(raw: dict, plan: MaintenancePlan, *, now: datetime) -> list[str]:
    """W5: lock 안에서 status-less 후보를 재확인한다.

    plan 이후 상태가 바뀌었을 수 있으므로 ①턴이 아직 존재하고 ②open lease가 없고
    ③status-less가 여전히 남아 있는 경우에만 퇴역 대상으로 확정한다.
    """
    active = raw.get("active_turns")
    if not isinstance(active, dict):
        return []
    keys: list[str] = []
    for candidate in plan.legacy_statusless:
        turn = active.get(candidate.agent_key)
        if not isinstance(turn, dict):
            continue
        if _has_open_lease(turn, now):
            continue
        if not _count_statusless_invocations(turn):
            continue
        keys.append(candidate.agent_key)
    return keys


def _turn_at(raw: dict, key: str) -> object:
    active = raw.get("active_turns")
    return active.get(key) if isinstance(active, dict) else None


def _preserve_statusless_archive(destination: Path, original: bytes, digest: str) -> Path:
    """W5: 퇴역 전 원문 bytes를 그대로 보관한다. retired_turns가 이 digest를 참조한다."""
    archive = destination.with_name(f"{STATUSLESS_ARCHIVE_PREFIX}{digest}.bak")
    if not archive.exists():
        atomic_write_bytes(archive, original, prefix="ledger-statusless-archive-")
    return archive


def _count_statusless_invocations(turn: dict) -> int:
    """W5: status 필드가 없는 invocation 수 — 구 스키마 잔재의 지표."""
    invocations = turn.get("invocations")
    if not isinstance(invocations, dict):
        return 0
    return sum(
        1
        for entry in invocations.values()
        if isinstance(entry, dict) and "status" not in entry
    )


def _exclusion_reasons(raw: dict, agent_key: str, current_key: str | None) -> tuple[str, ...]:
    """W5: 통상 경로였다면 이 턴을 후보에서 뺐을 사유 — 운영자 보고용."""
    reasons: list[str] = []
    if agent_key == current_key:
        reasons.append("current_turn")
    if _has_unsettled_attribution(raw, agent_key):
        reasons.append("unsettled_attribution")
    return tuple(reasons)


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