from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Final, override


DEFAULT_MAX_SCAN_ENTRIES: Final = 10_000
DEFAULT_MAX_SCAN_BYTES: Final = 256 * 1024 * 1024
DEFAULT_FULL_SCAN_SECONDS: Final = 8.0
DEFAULT_INCREMENTAL_SCAN_SECONDS: Final = 2.0


class EntryKind(StrEnum):
    REGULAR = "regular"
    SYMLINK = "symlink"


class ChangeOperation(StrEnum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    TYPE_CHANGE = "type_change"
    MODE_CHANGE = "mode_change"


class ProvenanceStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    SCOPE_TOO_LARGE = "scope_too_large"


class ProvenanceReason(StrEnum):
    NONE = ""
    ENTRY_LIMIT = "entry_limit"
    BYTE_LIMIT = "byte_limit"
    DEADLINE = "deadline"
    SNAPSHOT_UNAVAILABLE = "snapshot_unavailable"
    STORE_READ_ERROR = "store_read_error"
    STORE_WRITE_ERROR = "store_write_error"
    OBSERVATION_ERROR = "observation_error"


@dataclass(frozen=True, slots=True)
class ProvenanceConfigError(ValueError):
    field: str
    requirement: str

    @override
    def __str__(self) -> str:
        return f"invalid provenance config at {self.field}: {self.requirement}"


@dataclass(frozen=True, slots=True)
class ProvenancePathError(ValueError):
    path: str
    root: str

    @override
    def __str__(self) -> str:
        return f"path is outside provenance root: {self.path} (root: {self.root})"


@dataclass(frozen=True, slots=True)
class ProvenanceDeltaPolicyError(ValueError):
    canonical_key: str

    @override
    def __str__(self) -> str:
        return f"canonical key collision while reconciling delta: {self.canonical_key}"


@dataclass(frozen=True, slots=True)
class ProvenanceConfig:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    generated: tuple[str, ...] = ()

    def is_generated(self, path: str) -> bool:
        return any(fnmatchcase(path, pattern) for pattern in self.generated)


@dataclass(frozen=True, slots=True)
class SnapshotScanOptions:
    previous: Snapshot | None = None
    windows: bool | None = None
    force_paths: frozenset[str] = frozenset()
    budget: ScanBudget | None = None


@dataclass(frozen=True, slots=True)
class ScanBudget:
    max_entries: int = DEFAULT_MAX_SCAN_ENTRIES
    max_bytes: int = DEFAULT_MAX_SCAN_BYTES
    max_seconds: float = DEFAULT_FULL_SCAN_SECONDS


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    canonical_key: str
    file_type: EntryKind
    size: int
    mtime_ns: int
    mode: int
    digest: str


@dataclass(frozen=True, slots=True)
class ScanIssue:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    entries: tuple[ManifestEntry, ...]
    reparse_observations: tuple[ManifestEntry, ...]
    issues: tuple[ScanIssue, ...]
    status: ProvenanceStatus = ProvenanceStatus.COMPLETE
    status_reason: ProvenanceReason = ProvenanceReason.NONE


@dataclass(frozen=True, slots=True)
class Snapshot:
    root: Path
    entries: tuple[ManifestEntry, ...]
    reparse_observations: tuple[ManifestEntry, ...]
    issues: tuple[ScanIssue, ...]
    snapshot_id: str
    scope_policy_id: str
    generated_patterns: tuple[str, ...]
    is_casefolded: bool = False
    platform: str = ""
    full_reconciled_at: str | None = None
    status: ProvenanceStatus = ProvenanceStatus.COMPLETE
    status_reason: ProvenanceReason = ProvenanceReason.NONE

    @property
    def incomplete(self) -> bool:
        return self.status is ProvenanceStatus.INCOMPLETE or bool(self.issues)

    def is_generated(self, path: str) -> bool:
        return any(fnmatchcase(path, pattern) for pattern in self.generated_patterns)


@dataclass(frozen=True, slots=True)
class NetDelta:
    path: str
    canonical_key: str
    op: ChangeOperation
    before: ManifestEntry | None
    after: ManifestEntry | None
    mode_changed: bool = False
