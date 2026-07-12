from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Polarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class Mutation(StrEnum):
    CREATE = "create"
    APPEND = "append"
    TRUNCATE = "truncate"
    SAME_SIZE_MODIFY = "same_size_modify"
    DELETE = "delete"
    RENAME = "rename"
    COPY = "copy"
    MOVE = "move"
    MULTI_FILE = "multi_file"
    SYMLINK = "symlink"
    NONE = "none"


class Origin(StrEnum):
    EDIT = "edit"
    SHELL = "shell"
    GENERATED = "generated"
    EXTERNAL = "external"
    OVERLAP = "overlap"


class Family(StrEnum):
    REDIRECT = "redirect"
    HEREDOC = "heredoc"
    TEE = "tee"
    SED_IN_PLACE = "sed_i"
    CP_MV = "cp_mv"
    REMOVE = "rm"
    POWERSHELL = "powershell"
    PYTHON_NODE = "python_node"
    USER_SCRIPT = "user_script"
    GENERATOR = "generator"
    EXTERNAL_PROCESS = "external_process"
    STRUCTURED_EDIT = "structured_edit"
    CAT = "cat"
    RIPGREP = "rg"
    LIST = "ls"
    GIT_STATUS = "git_status"
    FAILED = "failed"
    PATH_MENTION = "path_mention"
    SAME_BYTES = "same_bytes"
    OUTSIDE = "outside"
    HARD_EXCLUDE = "hard_exclude"
    REVERT = "revert"


@dataclass(frozen=True, slots=True)
class CorpusCase:
    case_id: str
    polarity: Polarity
    mutation: Mutation
    family: Family
    target: str
    origin: Origin
    force_candidate: bool
    absolute_hint: bool
    glob_hint: bool

    @property
    def positive(self) -> bool:
        return self.polarity is Polarity.POSITIVE


@dataclass(frozen=True, slots=True, order=True)
class Signature:
    path: str
    op: str
    after: str | None


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    positive: bool
    expected: tuple[Signature, ...]
    observed: tuple[Signature, ...]
    pending: tuple[str, ...]
    source_expected: str
    sources: tuple[str, ...]
    parser_recalled: bool
    incomplete: bool

    @property
    def false_positive(self) -> bool:
        return not self.expected and bool(self.pending)


@dataclass(frozen=True, slots=True)
class ReplayResult:
    case_id: str
    canonical_match: bool
    ledger_match: bool
    antigravity_mode: str

    @property
    def matched(self) -> bool:
        return self.canonical_match and self.ledger_match
