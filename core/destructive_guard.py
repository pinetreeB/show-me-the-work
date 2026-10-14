from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum, auto
import os
from pathlib import Path
import re
from typing import Final, TypeAlias

from .ledger import JsonObject, JsonValue, load_ledger, state_dir
from .provenance_policy import (
    PROJECT_PATH_OUT_OF_ROOT as _CANON_OUT_OF_ROOT,
    PROJECT_PATH_UNRESOLVABLE as _CANON_UNRESOLVABLE,
    canonical_manifest_key,
    canonicalize_project_path,
)
from .scorecard_coordination import CoordinationReason
from .shell_command import is_proven_read_only_command
from .state_layout import is_protected_state_name

Decision: TypeAlias = dict[str, JsonValue]

SHELL_TOOLS: Final[frozenset[str]] = frozenset({"Bash", "PowerShell"})
COMMAND_SEPARATORS: Final[frozenset[str]] = frozenset({"&&", "||", ";", "|"})

GIT_SUBCOMMANDS_ALWAYS_IMPLICIT: Final[frozenset[str]] = frozenset(
    {"reset", "clean", "stash", "read-tree"}
)
GIT_SUBCOMMANDS_PATHSPEC: Final[frozenset[str]] = frozenset(
    {"checkout", "restore", "rm"}
)
GIT_SWITCH_DISCARD_FLAGS: Final[frozenset[str]] = frozenset(
    {"-f", "--force", "--discard-changes", "--orphan"}
)
GIT_SUBCOMMAND_NAMES: Final[frozenset[str]] = (
    GIT_SUBCOMMANDS_ALWAYS_IMPLICIT | GIT_SUBCOMMANDS_PATHSPEC | frozenset({"switch"})
)

REMOVE_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {"rm", "remove-item", "del", "rd", "rmdir", "shred", "unlink"}
)
TRUNCATE_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {"clear-content", "set-content", "out-file"}
)
TRANSFER_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {"copy-item", "cp", "move-item", "mv", "rename-item"}
)
MOVE_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {"move-item", "mv", "rename-item"}
)
SINK_PATHS: Final[frozenset[str]] = frozenset({"/dev/null", "nul"})
# PowerShell's `$null` discards output, but in bash `$null` is an ordinary variable
# (`null=peer.py; echo x >$null` writes peer.py).  Only a host that reports the
# PowerShell tool gets this exception; see parse_destructive_commands(powershell=).
POWERSHELL_DISCARD_TARGETS: Final[frozenset[str]] = frozenset({"$null"})
CHANGE_DIRECTORY_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {"cd", "chdir", "pushd", "push-location", "set-location", "sl"}
)
RETURN_DIRECTORY_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {"popd", "pop-location"}
)
# 한 명령 안의 cd 조합으로 생기는 기준 폴더 후보 상한. 넘으면 해석 불가로 본다.
MAX_WORKING_DIRECTORY_BASES: Final[int] = 16
SHELL_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {
        "ash",
        "bash",
        "csh",
        "dash",
        "fish",
        "ksh",
        "sh",
        "tcsh",
        "zsh",
    }
)
INVOKE_EXPRESSION_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {"iex", "invoke-expression"}
)
WRAPPER_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {
        *SHELL_COMMAND_NAMES,
        *INVOKE_EXPRESSION_COMMAND_NAMES,
        "busybox",
        "builtin",
        "cmd",
        "cmd.exe",
        "command",
        "doas",
        "env",
        "eval",
        "exec",
        "ionice",
        "nice",
        "nohup",
        "powershell",
        "powershell.exe",
        "pwsh",
        "setsid",
        "stdbuf",
        "sudo",
        "time",
        "timeout",
        "toybox",
        "xargs",
    }
)
OPAQUE_WRAPPER_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {
        *SHELL_COMMAND_NAMES,
        *INVOKE_EXPRESSION_COMMAND_NAMES,
        "cmd",
        "cmd.exe",
        "eval",
        "powershell",
        "powershell.exe",
        "pwsh",
    }
)
PATH_FLAGS: Final[frozenset[str]] = frozenset({"-path", "-literalpath", "-filepath"})
_POWERSHELL_COMMAND_ALIASES: Final[dict[str, str]] = {
    "ac": "add-content",
    "clc": "clear-content",
    "copy": "copy-item",
    "cpi": "copy-item",
    "erase": "remove-item",
    "mi": "move-item",
    "move": "move-item",
    "ni": "new-item",
    "ren": "rename-item",
    "ri": "remove-item",
    "rni": "rename-item",
    "sc": "set-content",
}
_POWERSHELL_COMMON_PARAMETERS: Final[frozenset[str]] = frozenset(
    {
        "confirm",
        "debug",
        "erroraction",
        "errorvariable",
        "informationaction",
        "informationvariable",
        "outbuffer",
        "outvariable",
        "pipelinevariable",
        "progressaction",
        "verbose",
        "warningaction",
        "warningvariable",
        "whatif",
    }
)
_POWERSHELL_COMMAND_PARAMETERS: Final[dict[str, frozenset[str]]] = {
    "clear-content": frozenset(
        {
            "credential",
            "exclude",
            "filter",
            "force",
            "include",
            "literalpath",
            "path",
            "stream",
        }
    ),
    "copy-item": frozenset(
        {
            "container",
            "credential",
            "destination",
            "exclude",
            "filter",
            "force",
            "fromsession",
            "include",
            "literalpath",
            "passthru",
            "path",
            "recurse",
            "tosession",
        }
    ),
    "invoke-expression": frozenset({"command"}),
    "move-item": frozenset(
        {
            "credential",
            "destination",
            "exclude",
            "filter",
            "force",
            "include",
            "literalpath",
            "passthru",
            "path",
        }
    ),
    "new-item": frozenset(
        {
            "credential",
            "force",
            "itemtype",
            "name",
            "options",
            "path",
            "target",
            "type",
            "value",
        }
    ),
    "out-file": frozenset(
        {
            "append",
            "encoding",
            "filepath",
            "force",
            "inputobject",
            "noclobber",
            "nonewline",
            "width",
        }
    ),
    "remove-item": frozenset(
        {
            "credential",
            "exclude",
            "filter",
            "force",
            "include",
            "literalpath",
            "path",
            "recurse",
            "stream",
        }
    ),
    "rename-item": frozenset(
        {"credential", "force", "literalpath", "newname", "passthru", "path"}
    ),
    "set-content": frozenset(
        {
            "asbytestream",
            "credential",
            "encoding",
            "exclude",
            "filter",
            "force",
            "include",
            "literalpath",
            "nonewline",
            "passthru",
            "path",
            "stream",
            "value",
        }
    ),
    "tee-object": frozenset(
        {"append", "filepath", "inputobject", "literalpath", "variable"}
    ),
}
_POWERSHELL_SWITCH_PARAMETERS: Final[frozenset[str]] = frozenset(
    {
        "append",
        "asbytestream",
        "confirm",
        "container",
        "debug",
        "force",
        "noclobber",
        "nonewline",
        "passthru",
        "recurse",
        "verbose",
        "whatif",
    }
)
_POWERSHELL_COMMON_VALUE_OPTIONS: Final[frozenset[str]] = frozenset(
    {
        "-erroraction",
        "-errorvariable",
        "-informationaction",
        "-informationvariable",
        "-outbuffer",
        "-outvariable",
        "-pipelinevariable",
        "-progressaction",
        "-warningaction",
        "-warningvariable",
    }
)
_SC_SERVICE_VERBS: Final[frozenset[str]] = frozenset(
    {
        "boot",
        "config",
        "continue",
        "control",
        "create",
        "delete",
        "description",
        "enumdepend",
        "failure",
        "failureflag",
        "getdisplayname",
        "getkeyname",
        "interrogate",
        "lock",
        "pause",
        "qc",
        "qdescription",
        "qfailure",
        "qfailureflag",
        "qprivs",
        "qprotected",
        "query",
        "queryex",
        "sdset",
        "sdshow",
        "start",
        "stop",
        "triggerinfo",
    }
)
FIND_TESTS_WITH_VALUE: Final[frozenset[str]] = frozenset(
    {
        "-name",
        "-iname",
        "-path",
        "-ipath",
        "-wholename",
        "-iwholename",
        "-lname",
        "-ilname",
        "-regex",
        "-iregex",
        "-type",
        "-xtype",
        "-user",
        "-group",
        "-uid",
        "-gid",
        "-size",
        "-mtime",
        "-mmin",
        "-atime",
        "-amin",
        "-ctime",
        "-cmin",
        "-newer",
    }
)

CATEGORY_GIT: Final[str] = "git_destructive"
CATEGORY_REMOVE: Final[str] = "os_remove"
CATEGORY_TRUNCATE: Final[str] = "truncate_redirect"

R2_COORDINATION_REASON_MAP: Final[dict[str, CoordinationReason]] = {
    "ledger_degraded": CoordinationReason.ATTRIBUTION_DEGRADED,
    "attribution_health_unavailable": CoordinationReason.ATTRIBUTION_DEGRADED,
    "attribution_degraded_or_capacity_exceeded": CoordinationReason.ATTRIBUTION_DEGRADED,
    "canonicalization_unavailable": CoordinationReason.UNRESOLVABLE_TARGET,
    "state_dir_protected": CoordinationReason.STATE_DIR_PROTECTED,
    "attribution_lookup_unavailable": CoordinationReason.ATTRIBUTION_DEGRADED,
    "peer_unsettled_revision": CoordinationReason.PEER_UNSETTLED,
    "peer_open_invocation_candidate": CoordinationReason.PEER_UNSETTLED,
    "parse_unable_dynamic_command": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_dynamic_expression": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_missing_path_flag": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_missing_target": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_missing_value": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_obfuscated": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_pathspec_from_file": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_pipeline": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_shell_syntax": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_subcommand": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_target": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_wrapped": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
}

_STRIP_RE = re.compile(r"[^A-Za-z0-9.-]+")
_NOISE_RE = re.compile(r"['\"\\()&$\[\]]")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_ATTACHED_HERE_STRING_RE = re.compile(
    rf"(?m)(?P<prefix>^|[;&|][ \t]*)(?P<shell>(?:[^\s;&|]+/)?"
    rf"(?:{'|'.join(sorted(SHELL_COMMAND_NAMES))}))<<<"
)
_FUNCTION_CALL_RE = re.compile(
    r"(?ms)(?:^|[;&]\s*)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\(\s*\)\s*\{(?P<body>.*?)\}\s*;\s*(?P=name)(?:\s|$)"
)
_CASE_BLOCK_RE = re.compile(r"(?ms)(?:^|[;&]\s*)case\b.*?\bin\b(?P<body>.*?)\besac\b")
_CASE_CLAUSE_RE = re.compile(r"(?ms)(?:^|;;)\s*[^)]*\)\s*(?P<body>.*?)(?=;;|$)")
_AMBIGUOUS_NESTED_BACKTICK_RE = re.compile(r"`[^`\r\n]*`[^`\r\n]+``")
_DOTNET_FILE_CALL_RE = re.compile(
    r"^\[(?:system\.)?io\.file\]::"
    r"(?P<method>delete|writeall(?:text|bytes|lines))\((?P<arguments>.*)\)$",
    re.IGNORECASE,
)
_DOTNET_FIRST_LITERAL_RE = re.compile(
    r"^\s*(?P<quote>['\"])(?P<path>.*?)(?P=quote)\s*(?:,|$)"
)


class _ShellScanState(Enum):
    EXPECT_COMMAND = auto()
    IN_ARGUMENTS = auto()
    AFTER_SEPARATOR = auto()
    AFTER_CONTROL_WORD = auto()
    IN_SINGLE_QUOTE = auto()
    IN_DOUBLE_QUOTE = auto()
    ESCAPED = auto()


_COMMAND_POSITION_CONTROL_WORDS: Final[frozenset[str]] = frozenset(
    {
        "if",
        "while",
        "until",
        "for",
        "select",
        "then",
        "do",
        "else",
        "elif",
        "coproc",
        "{",
        "(",
        "!",
        "&",
    }
)
_COMMAND_TERMINATORS: Final[frozenset[str]] = frozenset(
    {"fi", "done", "esac", "}", ")"}
)
# 07-16 사고 재발 방지 3절차(설계 §6-4) — 원복 자가발급 표면을 두지 않는 대신 안내만 한다.
RESOLUTION_PROCEDURES: Final[str] = (
    "파괴 조치는 오케스트레이터 전결입니다. 해소 절차: "
    "① 소유 에이전트 본인이 자기 세션에서 실행 "
    "② 소유 에이전트가 검증/정리로 settlement 후 실행 "
    "③ 사용자(인간)가 직접 실행(훅 밖)."
)


@dataclass(frozen=True, slots=True)
class ParsedDestructiveCommand:
    category: str
    resolved: bool
    targets: tuple[str, ...]
    reason: str
    container_targets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _HeredocDeclaration:
    delimiter: str
    strip_tabs: bool
    quoted: bool
    consumer: str


@dataclass(frozen=True, slots=True)
class _CommandSegment:
    text: str
    incoming_separator: str | None


def _tokenize(command: str) -> list[str]:
    import shlex

    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return command.split()


def _normalize_attached_here_strings(command: str) -> str:
    return _ATTACHED_HERE_STRING_RE.sub(
        lambda match: f"{match.group('prefix')}{match.group('shell')} <<<",
        command,
    )


def _command_name(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if name.endswith(".exe"):
        return name.removesuffix(".exe")
    return _POWERSHELL_COMMAND_ALIASES.get(name, name)


def _deobfuscated_name(token: str) -> str:
    stripped = _STRIP_RE.sub("", token.rsplit("/", 1)[-1])
    return stripped.casefold().removesuffix(".exe")


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[1:-1]
    return token


def _has_glob(target: str) -> bool:
    return any(ch in target for ch in ("*", "?", "[", "]", "{", "}"))


def _has_environment_reference(target: str) -> bool:
    return "$" in target or "%" in target


def _is_root_or_home(target: str) -> bool:
    clean = target.strip()
    return clean in {"/", "\\", "~", ".", "./", ".\\"} or clean.startswith("~")


def _validate_target(target: str) -> str:
    if not target:
        return "parse_unable"
    if target.startswith(":"):
        return "pathspec_magic"
    if _has_glob(target) or _has_environment_reference(target):
        return "parse_unable"
    if _is_root_or_home(target):
        return "implicit_scope"
    return "ok"


def _powershell_parameter_name(command: str, raw_name: str) -> str | None:
    parameters = _POWERSHELL_COMMAND_PARAMETERS.get(command)
    if parameters is None:
        return None
    name = raw_name.casefold()
    if name == "pspath":
        return "literalpath"
    available = parameters | _POWERSHELL_COMMON_PARAMETERS
    if name in available:
        return name
    matches = tuple(parameter for parameter in available if parameter.startswith(name))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return ""
    return None


def _normalize_powershell_parameters(
    tokens: list[str],
) -> tuple[list[str], str | None]:
    command = _command_name(tokens[0])
    if command not in _POWERSHELL_COMMAND_PARAMETERS:
        return tokens, None
    normalized = [tokens[0]]
    index = 1
    while index < len(tokens):
        raw_token = tokens[index]
        token = _unquote(raw_token)
        if not token.startswith("-") or token.startswith("--") or token == "-?":
            normalized.append(raw_token)
            index += 1
            continue
        option, separator, attached = token[1:].partition(":")
        parameter = _powershell_parameter_name(command, option)
        if parameter == "":
            return tokens, "parse_unable_target"
        if parameter is None:
            normalized.append(raw_token)
            index += 1
            continue
        canonical = f"-{parameter}"
        if not separator:
            normalized.append(canonical)
            index += 1
            continue
        if parameter in _POWERSHELL_SWITCH_PARAMETERS:
            enabled = attached.casefold()
            if enabled == "$true":
                normalized.append(canonical)
            elif enabled != "$false":
                return tokens, "parse_unable_target"
            index += 1
            continue
        if not attached:
            return tokens, "parse_unable_missing_value"
        attached, index = _joined_attached_option_value(tokens, index, ":")
        normalized.extend((canonical, attached))
    return normalized, None


def _is_powershell_dynamic_path(raw: str) -> bool:
    stripped = raw.lstrip()
    if stripped.startswith(("'", '"')):
        return False
    return stripped.startswith(("(", "@(", "+(", "&{", ".{"))


def _has_enabled_whatif(tokens: list[str]) -> bool:
    return any(_unquote(token).casefold() == "-whatif" for token in tokens[1:])


def _is_bare_sc_service_command(tokens: list[str]) -> bool:
    raw_name = _unquote(tokens[0]).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return (
        raw_name == "sc"
        and len(tokens) > 1
        and _unquote(tokens[1]).casefold() in _SC_SERVICE_VERBS
    )


def _blocked(category: str, reason: str) -> ParsedDestructiveCommand:
    return ParsedDestructiveCommand(category, False, (), reason)


_GIT_LONG_OPTIONS_WITH_VALUE: Final[dict[str, frozenset[str]]] = {
    "checkout": frozenset(
        {
            "--conflict",
            "--inter-hunk-context",
            "--orphan",
            "--pathspec-from-file",
            "--unified",
        }
    ),
    "restore": frozenset(
        {
            "--conflict",
            "--inter-hunk-context",
            "--no-source",
            "--pathspec-from-file",
            "--source",
            "--unified",
        }
    ),
}
_GIT_SHORT_OPTIONS_WITH_VALUE: Final[dict[str, frozenset[str]]] = {
    "checkout": frozenset({"B", "U", "b"}),
    "restore": frozenset({"U", "s"}),
}


def _git_option_flags(subcommand: str, token: str) -> tuple[frozenset[str], bool]:
    if token.startswith("--"):
        option, separator, _attached = token.partition("=")
        consumes_next = not separator and option in _GIT_LONG_OPTIONS_WITH_VALUE.get(
            subcommand, frozenset()
        )
        return frozenset(), consumes_next
    if not token.startswith("-") or token == "-":
        return frozenset(), False
    cluster = token[1:]
    flags: set[str] = set()
    value_flags = _GIT_SHORT_OPTIONS_WITH_VALUE.get(subcommand, frozenset())
    for index, flag in enumerate(cluster):
        flags.add(flag)
        if flag in value_flags:
            return frozenset(flags), index == len(cluster) - 1
    return frozenset(flags), False


def _parse_git_pathspec(
    subcommand: str, rest: list[str]
) -> ParsedDestructiveCommand | None:
    positionals: list[str] = []
    index = 0
    after_double_dash = False
    checkout_target_pending = False
    while index < len(rest):
        raw_token = rest[index]
        token = _unquote(raw_token)
        if token in COMMAND_SEPARATORS:
            break
        if not after_double_dash and token == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and token.startswith("--pathspec-from-file"):
            return _blocked(CATEGORY_GIT, "parse_unable_pathspec_from_file")
        if not after_double_dash and token.startswith("-"):
            flags, consumes_next = _git_option_flags(subcommand, token)
            if subcommand == "checkout" and (token == "--detach" or "d" in flags):
                checkout_target_pending = True
            index += 2 if consumes_next else 1
            continue
        if not after_double_dash and checkout_target_pending:
            checkout_target_pending = False
            index += 1
            continue
        positionals.append(raw_token)
        index += 1
    if not positionals:
        # `git checkout`/`git restore` 인자 없음: 대상 pathspec이 없어 특정 파일을 복원하지
        # 않는다(checkout 단독=상태 표시). R2 대상 아님.
        return None
    # `--` 유무와 무관하게 위치 인자를 pathspec 대상으로 attribution 판정에 위임한다.
    # 브랜치명(git checkout main·release/v2)은 루트에 매칭 파일이 없어 미추적으로 통과하고,
    # 실제 파일 경로(src/app.py)는 canonical 키로 조회돼 타 에이전트 미정산 소유면 차단된다.
    # 정적으로 브랜치명/파일명을 구분하려던 기존 로직은 release/v2 같은 슬래시 브랜치명을
    # 오탐했다 — 판정을 attribution 조회에 넘겨 오탐을 제거한다.
    targets: list[str] = []
    for raw in positionals:
        target = _unquote(raw)
        validity = _validate_target(target)
        if validity == "ok":
            targets.append(target)
        elif validity == "implicit_scope":
            return _blocked(CATEGORY_GIT, "implicit_scope")
        else:
            return _blocked(CATEGORY_GIT, "parse_unable_target")
    return ParsedDestructiveCommand(CATEGORY_GIT, True, tuple(targets), "")


_CHECKOUT_CLUSTER_FLAGS: Final[frozenset[str]] = frozenset(
    {"f", "q", "l", "m", "d", "t", "p"}
)


def _parse_checkout_options(rest: list[str]) -> tuple[bool, bool]:
    """Return effective force and branch-creation states before ``--``."""
    force = False
    branch_creation = False
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == "--":
            break
        if token == "--force":
            force = True
        elif token == "--no-force":
            force = False
        elif token in {"-b", "-B"}:
            branch_creation = True
            index += 1
        elif token.startswith(("-b", "-B")) and len(token) > 2:
            branch_creation = True
            suffix = token[2:]
            # Attached branch values remain data.  An all-flag suffix, such
            # as the contracted ``-Bf`` case, remains part of the cluster.
            if suffix and set(suffix) <= _CHECKOUT_CLUSTER_FLAGS:
                force = force or "f" in suffix
        elif token.startswith("-") and not token.startswith("--"):
            cluster = token[1:]
            cluster_index = 0
            while cluster_index < len(cluster):
                flag = cluster[cluster_index]
                if flag == "f":
                    force = True
                if flag in {"b", "B"}:
                    branch_creation = True
                    suffix = cluster[cluster_index + 1 :]
                    if suffix and set(suffix) <= _CHECKOUT_CLUSTER_FLAGS:
                        force = force or "f" in suffix
                    break
                cluster_index += 1
        index += 1
    return force, branch_creation


def _git_patch_mode(subcommand: str, rest: list[str]) -> bool:
    patch_mode = False
    index = 0
    while index < len(rest):
        token = _unquote(rest[index])
        if token == "--":
            break
        flags, consumes_next = _git_option_flags(subcommand, token)
        if token == "--patch":
            patch_mode = True
        elif token == "--no-patch":
            patch_mode = False
        elif "p" in flags:
            patch_mode = True
        index += 2 if consumes_next else 1
    return patch_mode


def _detect_git(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if _command_name(tokens[0]) != "git":
        return None
    if len(tokens) < 2:
        return None
    candidate = tokens[1]
    subcommand = candidate.casefold()
    if subcommand not in GIT_SUBCOMMAND_NAMES:
        if candidate.startswith("-"):
            # 서브커맨드 앞에 global 옵션(예: --git-dir=...)이 와서 어떤 서브커맨드인지
            # 확정할 수 없다 — 파괴 서브커맨드일 가능성을 배제할 수 없으므로 보수적으로
            # parse_unable 처리한다(§6-4). global 옵션 파싱은 구현하지 않는다(문서 허용).
            return _blocked(CATEGORY_GIT, "parse_unable_subcommand")
        # commit/add/push/log 등 무관한 서브커맨드 — R2 대상이 아니다.
        return None
    if subcommand in GIT_SUBCOMMANDS_ALWAYS_IMPLICIT:
        return _blocked(CATEGORY_GIT, "implicit_scope")
    if subcommand == "switch":
        flags = {t.casefold() for t in tokens[2:]}
        if flags & GIT_SWITCH_DISCARD_FLAGS:
            return _blocked(CATEGORY_GIT, "implicit_scope")
        return None
    rest = tokens[2:]
    if subcommand == "rm":
        for token in rest:
            option = _unquote(token).casefold()
            if option == "--":
                break
            if option in {"-n", "--dry-run", "--cached"}:
                return None
    if subcommand == "checkout":
        force, branch_creation = _parse_checkout_options(rest)
        if force:
            return _blocked(CATEGORY_GIT, "implicit_scope")
        if branch_creation:
            return None
    patch_mode = _git_patch_mode(subcommand, rest)
    parsed = _parse_git_pathspec(subcommand, rest)
    if parsed is None:
        return _blocked(CATEGORY_GIT, "implicit_scope") if patch_mode else None
    recursive_rm = subcommand == "rm" and any(
        option in {"-r", "--recursive"}
        or (
            option.startswith("-") and not option.startswith("--") and "r" in option[1:]
        )
        for option in (_unquote(token).casefold() for token in rest)
    )
    container_targets = (
        parsed.targets
        if (
            (subcommand == "checkout" and "--" in rest)
            or subcommand == "restore"
            or recursive_rm
        )
        else ()
    )
    return ParsedDestructiveCommand(
        parsed.category,
        parsed.resolved,
        parsed.targets,
        parsed.reason,
        container_targets,
    )


_REMOVE_OPTIONS_WITH_VALUE: Final[frozenset[str]] = frozenset(
    {
        "-credential",
        "-exclude",
        "-filter",
        "-include",
        "-iterations",
        "-random-source",
        "-size",
        "-stream",
        *_POWERSHELL_COMMON_VALUE_OPTIONS,
    }
)


def _detect_remove(tokens: list[str]) -> ParsedDestructiveCommand | None:
    command = _command_name(tokens[0])
    if command not in REMOVE_COMMAND_NAMES:
        return None
    if command in {"remove-item", "rm", "del", "rd", "rmdir"} and (
        _has_enabled_whatif(tokens)
        or any(
            _unquote(token).casefold() in {"-?", "-help", "--help"}
            for token in tokens[1:]
        )
    ):
        return None
    if (
        command in {"shred", "unlink"}
        and len(tokens) > 1
        and _unquote(tokens[1]).casefold() in {"--help", "--version"}
    ):
        return None
    explicit_targets: list[str] = []
    positionals: list[str] = []
    recursive = False
    index = 1
    while index < len(tokens):
        token = _unquote(tokens[index])
        option = token.casefold()
        if token in COMMAND_SEPARATORS:
            break
        if option in {"-path", "-literalpath"}:
            if index + 1 >= len(tokens):
                return _blocked(CATEGORY_REMOVE, "parse_unable_missing_value")
            explicit_targets.append(tokens[index + 1])
            index += 2
            continue
        if option in _REMOVE_OPTIONS_WITH_VALUE:
            if index + 1 >= len(tokens):
                return _blocked(CATEGORY_REMOVE, "parse_unable_missing_value")
            index += 2
            continue
        if (
            option in {"-r", "--recursive", "-recurse"}
            or (command in {"rd", "rmdir"} and option == "/s")
            or (
                command == "rm"
                and option.startswith("-")
                and not option.startswith("--")
                and "r" in option[1:]
            )
        ):
            recursive = True
            index += 1
            continue
        if token.startswith("-") or token.startswith("/"):
            index += 1
            continue
        positionals.append(tokens[index])
        index += 1
    raw_targets = explicit_targets or positionals
    if not raw_targets:
        return _blocked(CATEGORY_REMOVE, "implicit_scope")
    if command in {"remove-item", "rm", "del", "rd", "rmdir"}:
        raw_targets = _powershell_path_elements(raw_targets)
    return _resolved_static_targets(
        CATEGORY_REMOVE,
        raw_targets,
        container_targets=raw_targets if recursive else (),
    )


def _resolved_static_targets(
    category: str,
    raw_targets: list[str],
    *,
    container_targets: list[str] | tuple[str, ...] = (),
) -> ParsedDestructiveCommand:
    if not raw_targets:
        return _blocked(category, "parse_unable_missing_target")
    targets: list[str] = []
    for raw in raw_targets:
        if _is_powershell_dynamic_path(raw):
            return _blocked(category, "parse_unable_dynamic_expression")
        target = _unquote(raw)
        validity = _validate_target(target)
        if validity == "ok":
            if target not in targets:
                targets.append(target)
        elif validity == "implicit_scope":
            return _blocked(category, "implicit_scope")
        else:
            return _blocked(category, "parse_unable_target")
    containers: list[str] = []
    for raw in container_targets:
        if _is_powershell_dynamic_path(raw):
            return _blocked(category, "parse_unable_dynamic_expression")
        target = _unquote(raw)
        validity = _validate_target(target)
        if validity != "ok":
            return _blocked(
                category,
                "implicit_scope"
                if validity == "implicit_scope"
                else "parse_unable_target",
            )
        if target not in containers:
            containers.append(target)
    return ParsedDestructiveCommand(
        category,
        True,
        tuple(targets),
        "",
        tuple(containers),
    )


def _powershell_path_elements(raw_targets: list[str]) -> list[str]:
    elements: list[str] = []
    for raw in raw_targets:
        quoted = len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\""
        parts = (raw,) if quoted else raw.split(",")
        elements.extend(part.strip() for part in parts if part.strip())
    return elements


_CLEAR_CONTENT_OPTIONS_WITH_VALUE: Final[frozenset[str]] = frozenset(
    {
        "-credential",
        "-exclude",
        "-filter",
        "-include",
        "-stream",
        *_POWERSHELL_COMMON_VALUE_OPTIONS,
    }
)


def _detect_clear_content(tokens: list[str]) -> ParsedDestructiveCommand | None:
    explicit_targets: list[str] = []
    positionals: list[str] = []
    index = 1
    while index < len(tokens):
        token = _unquote(tokens[index])
        option = token.casefold()
        if option in {"-?", "-help", "--help"} or _has_enabled_whatif(tokens):
            return None
        if option in PATH_FLAGS:
            if index + 1 >= len(tokens):
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_value")
            explicit_targets.append(tokens[index + 1])
            index += 2
            continue
        if option in _CLEAR_CONTENT_OPTIONS_WITH_VALUE:
            if index + 1 >= len(tokens):
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_value")
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        positionals.append(tokens[index])
        index += 1
    return _resolved_static_targets(
        CATEGORY_TRUNCATE,
        _powershell_path_elements(explicit_targets or positionals),
    )


def _detect_truncate_cmdlet(tokens: list[str]) -> ParsedDestructiveCommand | None:
    command = _command_name(tokens[0])
    if command not in TRUNCATE_COMMAND_NAMES:
        return None
    if _has_enabled_whatif(tokens):
        return None
    if command == "clear-content":
        return _detect_clear_content(tokens)
    index = 1
    while index < len(tokens):
        if tokens[index].casefold() in PATH_FLAGS:
            if index + 1 >= len(tokens):
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_value")
            value_token = tokens[index + 1]
            if value_token.startswith("(") or "$" in value_token:
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_dynamic_expression")
            if index + 2 < len(tokens) and tokens[index + 2] == "+":
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_dynamic_expression")
            return _resolved_static_targets(
                CATEGORY_TRUNCATE,
                _powershell_path_elements([value_token]),
            )
        index += 1
    return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_path_flag")


def _detect_truncate_file(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if _command_name(tokens[0]) != "truncate":
        return None
    positionals: list[str] = []
    has_size_source = False
    after_double_dash = False
    index = 1
    while index < len(tokens):
        token = _unquote(tokens[index])
        option = token.casefold()
        if not after_double_dash and option in {"--help", "--version"}:
            return None
        if not after_double_dash and token == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and option in {"-s", "--size", "-r", "--reference"}:
            if index + 1 >= len(tokens):
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_value")
            has_size_source = True
            index += 2
            continue
        if not after_double_dash and (
            option.startswith(("--size=", "--reference="))
            or (len(option) > 2 and option.startswith(("-s", "-r")))
        ):
            has_size_source = True
            index += 1
            continue
        if not after_double_dash and token.startswith("-"):
            index += 1
            continue
        positionals.append(tokens[index])
        index += 1
    if not has_size_source:
        return None
    return _resolved_static_targets(CATEGORY_TRUNCATE, positionals)


def _detect_dd_output(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if _command_name(tokens[0]) != "dd":
        return None
    output: str | None = None
    append = False
    for raw_token in tokens[1:]:
        token = _unquote(raw_token)
        key, separator, value = token.partition("=")
        if not separator:
            continue
        option = key.casefold()
        if option == "of":
            output = value
        elif option == "oflag":
            append = "append" in {flag.casefold() for flag in value.split(",")}
    if output is None or append:
        return None
    return _resolved_static_targets(CATEGORY_TRUNCATE, [output])


_POWERSHELL_TRANSFER_VALUE_OPTIONS: Final[frozenset[str]] = frozenset(
    {
        "-credential",
        "-exclude",
        "-filter",
        "-include",
        *_POWERSHELL_COMMON_VALUE_OPTIONS,
    }
)
_POSIX_TRANSFER_VALUE_OPTIONS: Final[frozenset[str]] = frozenset({"-S", "--suffix"})


def _transfer_operands(
    tokens: list[str],
) -> tuple[list[str], str, bool, bool] | None:
    command = _command_name(tokens[0])
    powershell_syntax = command in {"copy-item", "move-item", "rename-item"} or any(
        _unquote(token).casefold()
        in {
            "-?",
            "-destination",
            "-help",
            "--help",
            "-literalpath",
            "-newname",
            "-path",
            "-whatif",
            *_POWERSHELL_TRANSFER_VALUE_OPTIONS,
        }
        for token in tokens[1:]
    )
    if powershell_syntax:
        sources: list[str] = []
        positionals: list[str] = []
        destination: str | None = None
        recursive = False
        index = 1
        while index < len(tokens):
            token = _unquote(tokens[index])
            option = token.casefold()
            if option in {"-?", "-help", "--help"} or _has_enabled_whatif(tokens):
                return None
            if option == "-recurse":
                recursive = True
                index += 1
                continue
            if option in {"-path", "-literalpath"}:
                if index + 1 >= len(tokens):
                    return None
                sources.append(tokens[index + 1])
                index += 2
                continue
            if option in {"-destination", "-newname"}:
                if index + 1 >= len(tokens):
                    return None
                destination = tokens[index + 1]
                index += 2
                continue
            if option in _POWERSHELL_TRANSFER_VALUE_OPTIONS:
                if index + 1 >= len(tokens):
                    return None
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            positionals.append(tokens[index])
            index += 1
        if destination is None:
            if len(positionals) < (1 if sources else 2):
                return None
            destination = positionals[-1]
            sources.extend(positionals[:-1])
        else:
            sources.extend(positionals)
        return (sources, destination, recursive, False) if sources else None

    positionals = []
    destination = None
    recursive = False
    preserve_parents = False
    after_double_dash = False
    index = 1
    while index < len(tokens):
        token = _unquote(tokens[index])
        if not after_double_dash and token in {"--help", "--version"}:
            return None
        if not after_double_dash and token == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and token in {"-t", "--target-directory"}:
            if index + 1 >= len(tokens):
                return None
            destination = tokens[index + 1]
            index += 2
            continue
        if not after_double_dash and token.startswith("--target-directory="):
            destination = token.split("=", 1)[1]
            index += 1
            continue
        if not after_double_dash and token in {"--context", "--parents"}:
            preserve_parents = preserve_parents or token == "--parents"
            index += 1
            continue
        if not after_double_dash and token in {"--archive", "--recursive"}:
            recursive = True
            index += 1
            continue
        if not after_double_dash and token.startswith("--context="):
            index += 1
            continue
        if not after_double_dash and token in _POSIX_TRANSFER_VALUE_OPTIONS:
            if index + 1 >= len(tokens):
                return None
            index += 2
            continue
        if (
            not after_double_dash
            and token.startswith("-")
            and not token.startswith("--")
            and token != "-"
        ):
            cluster = token[1:]
            recursive = recursive or any(flag in cluster for flag in ("a", "r", "R"))
            target_index = cluster.find("t")
            if target_index >= 0:
                attached = cluster[target_index + 1 :]
                if attached:
                    destination = attached
                    index += 1
                elif index + 1 < len(tokens):
                    destination = tokens[index + 1]
                    index += 2
                else:
                    return None
                continue
            index += 1
            continue
        positionals.append(tokens[index])
        index += 1
    if destination is None:
        if len(positionals) < 2:
            return None
        destination = positionals[-1]
        positionals = positionals[:-1]
    return (
        (positionals, destination, recursive, preserve_parents) if positionals else None
    )


def _transfer_destination_candidates(
    sources: list[str],
    destination: str,
    *,
    preserve_parents: bool,
) -> list[str]:
    candidates = [destination]
    separator = "\\" if "\\" in destination and "/" not in destination else "/"
    base = destination.rstrip("/\\")
    for raw_source in sources:
        source = _unquote(raw_source)
        if _validate_target(source) != "ok":
            continue
        normalized_source = source.replace("\\", "/")
        while normalized_source.startswith("./"):
            normalized_source = normalized_source[2:]
        relative = (
            normalized_source
            if preserve_parents
            else normalized_source.rsplit("/", 1)[-1]
        )
        candidate = f"{base}{separator}{relative}"
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _detect_transfer(tokens: list[str]) -> ParsedDestructiveCommand | None:
    command = _command_name(tokens[0])
    if command not in TRANSFER_COMMAND_NAMES:
        return None
    operands = _transfer_operands(tokens)
    if operands is None:
        return None
    sources, destination, recursive, preserve_parents = operands
    if command in {"copy-item", "move-item", "rename-item", "cp", "mv"}:
        sources = _powershell_path_elements(sources)
        destinations = _powershell_path_elements([destination])
    else:
        destinations = [destination]
    if command == "rename-item" and len(sources) == 1:
        source = _unquote(sources[0]).replace("\\", "/")
        if "/" in source:
            parent = source.rsplit("/", 1)[0]
            destinations = [
                destination
                if any(separator in destination for separator in ("/", "\\"))
                else f"{parent}/{destination}"
                for destination in destinations
            ]
    destination_targets: list[str] = []
    copy_container_targets: list[str] = []
    for target_destination in destinations:
        if _unquote(target_destination).replace("\\", "/").casefold() in SINK_PATHS:
            continue
        candidates = (
            [target_destination]
            if command == "rename-item"
            else _transfer_destination_candidates(
                sources,
                target_destination,
                preserve_parents=preserve_parents,
            )
        )
        destination_targets.extend(candidates)
        if recursive:
            copy_container_targets.extend(candidates[1:])
    if command in MOVE_COMMAND_NAMES:
        return _resolved_static_targets(
            CATEGORY_REMOVE,
            [*sources, *destination_targets],
            container_targets=sources,
        )
    return _resolved_static_targets(
        CATEGORY_TRUNCATE,
        destination_targets,
        container_targets=copy_container_targets,
    )


_RSYNC_SHORT_VALUE_LETTERS: Final[frozenset[str]] = frozenset("efBTM@")
# Every rsync long option that takes a value (rsync 3.4 option table).  popt
# takes the next word as the value even when it looks like an option, so
# `--out-format --dry-run` formats output as "--dry-run" and is no dry run.
_RSYNC_LONG_VALUE_OPTIONS: Final[frozenset[str]] = frozenset(
    {
        "--address",
        "--backup-dir",
        "--block-size",
        "--bwlimit",
        "--cc",
        "--checksum-choice",
        "--checksum-seed",
        "--chmod",
        "--chown",
        "--compare-dest",
        "--compress-choice",
        "--compress-level",
        "--config",
        "--contimeout",
        "--copy-as",
        "--copy-dest",
        "--debug",
        "--dparam",
        "--early-input",
        "--exclude",
        "--exclude-from",
        "--files-from",
        "--filter",
        "--groupmap",
        "--iconv",
        "--include",
        "--include-from",
        "--info",
        "--link-dest",
        "--log-file",
        "--log-file-format",
        "--log-format",
        "--max-alloc",
        "--max-delete",
        "--max-size",
        "--min-size",
        "--modify-window",
        "--only-write-batch",
        "--out-format",
        "--outbuf",
        "--partial-dir",
        "--password-file",
        "--port",
        "--protocol",
        "--read-batch",
        "--remote-option",
        "--rsh",
        "--rsync-path",
        "--skip-compress",
        "--sockopts",
        "--stderr",
        "--stop-after",
        "--stop-at",
        "--suffix",
        "--temp-dir",
        "--time-limit",
        "--timeout",
        "--usermap",
        "--write-batch",
        "--zc",
        "--zl",
    }
)


def _is_remote_transfer_operand(operand: str) -> bool:
    text = _unquote(operand)
    if text.casefold().startswith("rsync://"):
        return True
    head, separator, _rest = text.partition(":")
    if not separator or (len(head) == 1 and head.isalpha()):
        return False
    return "/" not in head and "\\" not in head


def _remote_transfer_path(operand: str) -> str:
    """Path part of ``host:path``, ``host::module/path`` or ``rsync://host/path``.

    A remote source still lands in the *local* destination, so its name (or
    its trailing ``/``) decides which local paths it overwrites.
    """
    text = _unquote(operand)
    if text.casefold().startswith("rsync://"):
        return text[len("rsync://") :].partition("/")[2]
    _host, _separator, path = text.partition(":")
    return path.removeprefix(":")


def _detect_rsync(tokens: list[str]) -> ParsedDestructiveCommand | None:
    """rsync overwrites like cp; ``--delete*`` also empties the destination tree."""
    if _command_name(tokens[0]) != "rsync":
        return None
    positionals: list[str] = []
    deletes = False
    remove_sources = False
    recursive = False
    # `--files-from` and `-R/--relative` decide the copied names outside the
    # source operands, so the whole destination tree may be written.
    unknown_layout = False
    after_double_dash = False
    index = 1
    while index < len(tokens):
        token = _unquote(tokens[index])
        if not after_double_dash and token == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and token.startswith("--"):
            name, separator, _value = token.partition("=")
            if name in {"--help", "--version", "--dry-run", "--list-only"}:
                return None
            if name == "--del" or name.startswith("--delete"):
                deletes = True
            elif name == "--remove-source-files":
                remove_sources = True
            elif name in {"--archive", "--recursive"}:
                recursive = True
            elif name in {"--files-from", "--relative"}:
                unknown_layout = True
            if not separator and name in _RSYNC_LONG_VALUE_OPTIONS:
                index += 1
            index += 1
            continue
        if not after_double_dash and token.startswith("-") and token != "-":
            # Only the letters before the first value-taking option are flags:
            # in `-e"nc host"` the `n` belongs to the remote-shell value.
            for position, letter in enumerate(token[1:]):
                if letter == "n":
                    return None  # -n is --dry-run
                if letter in "ar":
                    recursive = True
                if letter == "R":
                    unknown_layout = True
                if letter in _RSYNC_SHORT_VALUE_LETTERS:
                    if position == len(token) - 2:
                        index += 1
                    break
            index += 1
            continue
        positionals.append(tokens[index])
        index += 1
    if len(positionals) < 2:
        return None
    destination = positionals[-1]
    local_sources = [
        source for source in positionals[:-1] if not _is_remote_transfer_operand(source)
    ]
    # Remote sources (`host:src/`) write into the local destination exactly
    # like local ones, so they are read by their path part.
    source_paths = [
        _remote_transfer_path(source)
        if _is_remote_transfer_operand(source)
        else _unquote(source)
        for source in positionals[:-1]
    ]
    targets: list[str] = []
    containers: list[str] = []
    if not _is_remote_transfer_operand(destination):
        candidates = _transfer_destination_candidates(
            source_paths, destination, preserve_parents=False
        )
        targets.extend(candidates)
        # A source whose copied names are unknown (`host:`, `src/*`, `.`, `$DIR`)
        # may overwrite anything directly inside the destination.
        opaque_source = unknown_layout or any(
            _validate_target(path) != "ok" for path in source_paths
        )
        if (
            deletes
            or opaque_source
            or (
                recursive
                and any(path.endswith(("/", "\\")) for path in source_paths)
            )
        ):
            # `--delete*` empties the tree; `src/` copies *contents* into it.
            containers.append(destination)
        elif recursive:
            containers.extend(candidates[1:])
    if remove_sources:
        targets.extend(local_sources)
        if recursive:
            containers.extend(local_sources)
    if not targets:
        return None
    return _resolved_static_targets(
        CATEGORY_REMOVE if deletes or remove_sources else CATEGORY_TRUNCATE,
        targets,
        container_targets=containers,
    )


def _detect_robocopy(tokens: list[str]) -> ParsedDestructiveCommand | None:
    """robocopy overwrites the destination tree; /MIR and /PURGE delete in it."""
    if _command_name(tokens[0]) != "robocopy":
        return None
    positionals: list[str] = []
    options: set[str] = set()
    for raw in tokens[1:]:
        token = _unquote(raw)
        if token.startswith("/"):
            options.add(token.casefold().split(":", 1)[0])
        else:
            positionals.append(raw)
    if "/l" in options or "/?" in options or len(positionals) < 2:
        return None
    source, destination = positionals[0], positionals[1]
    containers = [destination]
    targets = [destination]
    if options & {"/mov", "/move"}:
        containers.append(source)
        targets.append(source)
    deletes = bool(options & {"/mir", "/purge", "/mov", "/move"})
    return _resolved_static_targets(
        CATEGORY_REMOVE if deletes else CATEGORY_TRUNCATE,
        targets,
        container_targets=containers,
    )


def _detect_forced_link(tokens: list[str]) -> ParsedDestructiveCommand | None:
    """``ln -f``/``ln -sf`` replaces an existing file at the link name."""
    if _command_name(tokens[0]) != "ln":
        return None
    force = False
    for raw in tokens[1:]:
        token = _unquote(raw)
        if token == "--":
            break
        if token in {"--help", "--version"}:
            return None
        if token == "--force" or (
            token.startswith("-") and not token.startswith("--") and "f" in token[1:]
        ):
            force = True
    if not force:
        return None
    operands = _transfer_operands(tokens)
    if operands is not None:
        sources, destination, _recursive, _parents = operands
        return _resolved_static_targets(
            CATEGORY_TRUNCATE,
            _transfer_destination_candidates(
                sources, destination, preserve_parents=False
            ),
        )
    positionals = [
        raw
        for raw in tokens[1:]
        if not _unquote(raw).startswith("-") or _unquote(raw) == "-"
    ]
    if len(positionals) != 1:
        return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_target")
    # `ln -sf ../x` creates ./x in the working directory.
    link_name = _unquote(positionals[0]).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return _resolved_static_targets(CATEGORY_TRUNCATE, [link_name])


def _detect_dotnet_file(tokens: list[str]) -> ParsedDestructiveCommand | None:
    match = _DOTNET_FILE_CALL_RE.match(" ".join(tokens))
    if match is None:
        return None
    method = match.group("method").casefold()
    literal = _DOTNET_FIRST_LITERAL_RE.match(match.group("arguments"))
    category = CATEGORY_REMOVE if method == "delete" else CATEGORY_TRUNCATE
    if literal is None:
        return _blocked(category, "parse_unable_dynamic_expression")
    return _resolved_static_targets(category, [literal.group("path")])


def _detect_sed_in_place(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if _command_name(tokens[0]) != "sed":
        return None
    in_place = False
    script_supplied = False
    positionals: list[str] = []
    after_double_dash = False
    index = 1
    while index < len(tokens):
        token = _unquote(tokens[index])
        if not after_double_dash and token == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and (
            token in {"-i", "--in-place"} or token.startswith(("-i", "--in-place="))
        ):
            in_place = True
            if (
                token == "-i"
                and index + 1 < len(tokens)
                and tokens[index + 1] in {"''", '""'}
            ):
                index += 2
            else:
                index += 1
            continue
        if not after_double_dash and token in {"-e", "--expression", "-f", "--file"}:
            if index + 1 >= len(tokens):
                return None
            script_supplied = True
            index += 2
            continue
        if (
            not after_double_dash
            and token.startswith("-")
            and not token.startswith("--")
            and token != "-"
        ):
            cluster = token[1:]
            cluster_index = 0
            consumed_next = False
            while cluster_index < len(cluster):
                option = cluster[cluster_index]
                if option == "i":
                    in_place = True
                    break
                if option in {"e", "f"}:
                    script_supplied = True
                    if cluster_index == len(cluster) - 1:
                        if index + 1 >= len(tokens):
                            return None
                        consumed_next = True
                    break
                cluster_index += 1
            index += 2 if consumed_next else 1
            continue
        if not after_double_dash and token.startswith("--"):
            index += 1
            continue
        positionals.append(tokens[index])
        index += 1
    if not in_place:
        return None
    files = positionals if script_supplied else positionals[1:]
    if not files:
        return None
    return _resolved_static_targets(CATEGORY_TRUNCATE, files)


def _detect_perl_in_place(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if _command_name(tokens[0]) != "perl":
        return None
    in_place = False
    script_supplied = False
    positionals: list[str] = []
    after_double_dash = False
    index = 1
    while index < len(tokens):
        token = _unquote(tokens[index])
        if not after_double_dash and token == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and token in {"--help", "--version"}:
            return None
        if not after_double_dash and token.startswith("-") and token != "-":
            short_options = token[1:]
            option_index = 0
            consumed_next = False
            while option_index < len(short_options):
                option = short_options[option_index]
                if option == "i":
                    in_place = True
                    break
                if option in {"I", "M", "m"}:
                    if option_index == len(short_options) - 1:
                        if index + 1 >= len(tokens):
                            return None
                        consumed_next = True
                    break
                if option in {"e", "E"}:
                    script_supplied = True
                    if option_index == len(short_options) - 1:
                        if index + 1 >= len(tokens):
                            return None
                        consumed_next = True
                    break
                option_index += 1
            index += 2 if consumed_next else 1
            continue
        positionals.append(tokens[index])
        index += 1
    if not in_place:
        return None
    files = positionals if script_supplied else positionals[1:]
    if not files:
        return None
    return _resolved_static_targets(CATEGORY_TRUNCATE, files)


_NEW_ITEM_OPTIONS_WITH_VALUE: Final[frozenset[str]] = frozenset(
    {
        "-credential",
        "-itemtype",
        "-options",
        "-target",
        "-type",
        "-value",
        *_POWERSHELL_COMMON_VALUE_OPTIONS,
    }
)


def _detect_forced_new_item(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if _command_name(tokens[0]) != "new-item":
        return None
    force = False
    explicit_paths: list[str] = []
    positionals: list[str] = []
    name: str | None = None
    index = 1
    while index < len(tokens):
        token = _unquote(tokens[index])
        option = token.casefold()
        if option in {"-?", "-help", "--help"} or _has_enabled_whatif(tokens):
            return None
        if option == "-force":
            force = True
            index += 1
            continue
        if option in PATH_FLAGS:
            if index + 1 >= len(tokens):
                return None
            explicit_paths.append(tokens[index + 1])
            index += 2
            continue
        if option == "-name":
            if index + 1 >= len(tokens):
                return None
            name = tokens[index + 1]
            index += 2
            continue
        if option in _NEW_ITEM_OPTIONS_WITH_VALUE:
            if index + 1 >= len(tokens):
                return None
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        positionals.append(tokens[index])
        index += 1
    if not force:
        return None
    targets = explicit_paths or positionals
    if name is not None:
        if explicit_paths:
            targets = [
                f"{_unquote(path).rstrip('/\\')}/{_unquote(name)}"
                for path in explicit_paths
            ]
        elif not targets:
            targets = [name]
    return _resolved_static_targets(CATEGORY_TRUNCATE, targets)


@dataclass(frozen=True, slots=True)
class _Redirection:
    """One redirection split off a command's argv.

    Output targets are unquoted file names.  Input targets keep the spelling
    the command used (a here-string's quotes included) because the shell
    wrapper detector parses them again as a script.
    """

    operator: str
    target: str | None
    append: bool  # writes without truncating (`>>`, `<>`)
    output: bool = True  # False for input redirections (`<`, `<<`, `<<<`, `<&`)


def _skip_balanced(token: str, index: int, opening: str, closing: str) -> int:
    depth = 0
    while index < len(token):
        if token[index] == opening:
            depth += 1
        elif token[index] == closing:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(token)


def _unquoted_redirect_index(token: str, start: int = 0) -> int | None:
    """Return the first ``<``/``>`` that the shell would treat as a redirection.

    Quoted text, command/arithmetic substitutions, parameter expansions and
    backtick spans are skipped: their own commands are evaluated as separate
    segments by :func:`_command_segments`.  A backslash outside quotes makes the
    next character literal (`echo safe\\>peer.py` prints ``safe>peer.py``; a
    glued ``>`` is literal in PowerShell too).  Inside double quotes the
    backslash is *not* honoured, so a PowerShell ``"C:\\dir\\">x`` still ends the
    quote and shows the redirection — the conservative reading for both shells.
    """
    quote: str | None = None
    index = start
    while index < len(token):
        character = token[index]
        if quote is not None:
            if character == quote:
                quote = None
            index += 1
            continue
        if character == "\\":
            index += 2
            continue
        if character in "'\"":
            quote = character
            index += 1
            continue
        if token.startswith(("$(", "${"), index):
            closing = ")" if token[index + 1] == "(" else "}"
            index = _skip_balanced(token, index + 1, token[index + 1], closing)
            continue
        if character == "`":
            closing_index = token.find("`", index + 1)
            index = len(token) if closing_index < 0 else closing_index + 1
            continue
        if character in "<>":
            return index
        index += 1
    return None


def _paren_balance(token: str) -> int:
    balance = 0
    quote: str | None = None
    for character in token:
        if quote is not None:
            if character == quote:
                quote = None
        elif character in "'\"":
            quote = character
        elif character == "(":
            balance += 1
        elif character == ")":
            balance -= 1
    return balance


def _split_glued_subshell_parens(tokens: list[str]) -> list[str]:
    """Detach a subshell's `(`/`)` glued to the first/last word of a segment.

    `(cd src && rm peer.py)` tokenizes as `(cd` … `peer.py)`; without this the
    command name is `(cd` and the delete target is the non-existent `peer.py)`.
    Arithmetic `((…))` and balanced groups such as `$(…)` are left alone.
    """
    if not tokens:
        return tokens
    result = list(tokens)
    first = result[0]
    if len(first) > 1 and first.startswith("(") and not first.startswith("(("):
        result[0:1] = ["(", first[1:]]
    last = result[-1]
    if len(last) > 1 and last.endswith(")") and _paren_balance(last) < 0:
        result[-1:] = [last[:-1], ")"]
    return result


_INPUT_REDIRECT_OPERATORS: Final[tuple[str, ...]] = ("<<<", "<<-", "<<", "<&")


def _redirect_operator_end(token: str, index: int) -> int:
    """End of the redirection operator that starts at ``token[index]``."""
    if token[index] == ">":
        if token[index + 1 : index + 2] in {">", "|", "&"}:
            return index + 2
        return index + 1
    if token.startswith("<>", index):
        return index + 2
    for operator in _INPUT_REDIRECT_OPERATORS:
        if token.startswith(operator, index):
            return index + len(operator)
    return index + 1


def _split_token_redirections(
    token: str,
) -> tuple[str | None, list[_Redirection]]:
    """Split one word into its argv part and the redirections inside it.

    ``rm peer.txt>own.log`` and ``echo x>>.smtw/ledger.json`` carry the
    redirection inside the word; bash still applies it.  Input redirections
    (``rm peer.py<in``) are split off too, so they do not disguise the argument.
    A redirection whose target is the next word is returned with ``target=None``.
    """
    if token.startswith(("<(", ">(")):
        return token, []
    redirect_index = _unquoted_redirect_index(token)
    if redirect_index is None:
        return token, []
    end = _redirect_operator_end(token, redirect_index)
    if "(" in {token[redirect_index + 1 : redirect_index + 2], token[end : end + 1]}:
        # Process substitution attached to a word: keep it as an argument.
        return token, []
    prefix = token[:redirect_index]
    operator_start = redirect_index
    word: str | None = prefix or None
    if prefix.isdigit() or prefix in {"&", "*"}:
        operator_start = 0
        word = None
    operator = token[operator_start:end]
    read_write = operator.endswith("<>")
    output = token[redirect_index] == ">" or read_write
    # `>>` appends and `<>` opens read/write: neither truncates the target.
    non_truncating = read_write or token[redirect_index : redirect_index + 2] == ">>"
    remainder = token[end:]
    next_index = _unquoted_redirect_index(remainder)
    if next_index is None:
        # An empty remainder means the target is the next word.
        return word, [_Redirection(operator, remainder or None, non_truncating, output)]
    # `a>b>c`: every further redirection inside the same word also applies.
    _nested_word, nested = _split_token_redirections(remainder[next_index:])
    return word, [
        _Redirection(operator, remainder[:next_index], non_truncating, output),
        *nested,
    ]


def _split_redirect_target_word(token: str) -> tuple[str, list[_Redirection]]:
    """Split a redirection target taken from the next word.

    ``echo x > own.log>peer.py`` truncates both files: the target ends at the
    word's first unquoted operator and the rest is parsed like any other word.
    A digit run stays part of the target here (``> 2>err`` is a bash syntax
    error; reading ``2`` as a target only adds a check).
    """
    redirect_index = _unquoted_redirect_index(token)
    if (
        redirect_index is None
        or token.startswith(("<(", ">("))
        or token[redirect_index + 1 : redirect_index + 2] == "("
    ):
        return token, []
    _word, nested = _split_token_redirections(token[redirect_index:])
    return token[:redirect_index], nested


def _has_open_quote(text: str) -> bool:
    """Whether ``text`` ends inside a quote (same rules as the redirect scan)."""
    quote: str | None = None
    index = 0
    while index < len(text):
        character = text[index]
        if quote is not None:
            if character == quote:
                quote = None
        elif character == "\\":
            index += 1
        elif character in "'\"":
            quote = character
        index += 1
    return quote is not None


def _split_redirections(
    tokens: list[str],
) -> tuple[list[str], list[_Redirection]]:
    """Separate every redirection from the argv a command really sees.

    Neither output nor input redirections are arguments: ``ln -sf a peer.py <
    /dev/null`` links ``peer.py``, and ``< /dev/null rm peer.py`` still runs
    ``rm``.  Input redirections are returned with ``output=False`` so the shell
    wrapper detector can still read a script fed on stdin (`bash < script`,
    `bash <<< "rm x"`).
    """
    argv: list[str] = []
    redirections: list[_Redirection] = []
    index = 0
    while index < len(tokens):
        word, pending = _split_token_redirections(tokens[index])
        index += 1
        if word is not None:
            argv.append(word)
        while pending:
            redirection = pending.pop(0)
            target = redirection.target
            if target is None:
                target = ""
                if index < len(tokens):
                    target, nested = _split_redirect_target_word(tokens[index])
                    index += 1
                    pending[0:0] = nested
            # The whitespace tokenizer cuts a quoted target glued to its
            # operator (`<<<'echo hi'` -> `<<<'echo` `hi'`); join it back.
            while _has_open_quote(target) and index < len(tokens):
                target = f"{target} {tokens[index]}"
                index += 1
            redirections.append(
                _Redirection(
                    redirection.operator,
                    _unquote(target) if redirection.output else target,
                    redirection.append,
                    redirection.output,
                )
            )
    return argv, redirections


def _segment_argv(text: str) -> tuple[list[str], list[_Redirection]]:
    """Tokenize one segment into its argv and its redirections.

    Redirections are split off before anything else looks for the command
    word: they may stand before it (`< /dev/null rm peer.py`) or be glued to a
    leading assignment (`FOO=a>peer.py true`), and a glued subshell paren is
    only detached once the redirection behind it is gone (`(rm peer.py)>log`).
    """
    argv, redirections = _split_redirections(_tokenize(text))
    return _split_glued_subshell_parens(argv), redirections


def _input_redirection_tokens(redirections: list[_Redirection]) -> list[str]:
    """Input redirections re-spelled as words, for a wrapped command's payload."""
    return [
        part
        for redirection in redirections
        if not redirection.output
        for part in (redirection.operator, redirection.target or "")
        if part
    ]


def _is_descriptor_duplication(redirection: _Redirection) -> bool:
    return redirection.operator.endswith(">&") and (
        redirection.target == "-" or (redirection.target or "").isdigit()
    )


def _truncating_redirect_result(
    redirections: list[_Redirection],
    *,
    powershell: bool = False,
) -> ParsedDestructiveCommand | None:
    targets: list[str] = []
    for redirection in redirections:
        if (
            not redirection.output
            or redirection.append
            or _is_descriptor_duplication(redirection)
        ):
            continue
        target = redirection.target or ""
        if powershell and target.casefold() in POWERSHELL_DISCARD_TARGETS:
            # PowerShell's discard idiom (`2>$null`, `*>$null`) writes no file.
            continue
        if not target:
            return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_target")
        validity = _validate_target(target)
        if validity == "implicit_scope":
            return _blocked(CATEGORY_TRUNCATE, "implicit_scope")
        if validity != "ok":
            return _blocked(CATEGORY_TRUNCATE, "parse_unable_target")
        if target not in targets:
            targets.append(target)
    if not targets:
        return None
    return ParsedDestructiveCommand(CATEGORY_TRUNCATE, True, tuple(targets), "")


def output_redirection_targets(command: str) -> tuple[str, ...]:
    """Expose every static output-redirection target, appends included.

    Advisory consumers (state-file friction) reuse R2's quote-aware redirect
    parser so ``x>>.smtw/ledger.json`` is seen even without surrounding spaces.
    """
    targets: list[str] = []
    segments, _malformed = _r2_command_segments(command)
    for segment in segments:
        _argv, redirections = _split_redirections(_tokenize(segment.text))
        for redirection in redirections:
            if (
                not redirection.output
                or _is_descriptor_duplication(redirection)
                or not redirection.target
            ):
                continue
            if redirection.target not in targets:
                targets.append(redirection.target)
    return tuple(targets)


def _detect_tee(tokens: list[str]) -> ParsedDestructiveCommand | None:
    command = _command_name(tokens[0])
    if command == "tee-object":
        append = False
        explicit_targets: list[str] = []
        positionals: list[str] = []
        index = 1
        while index < len(tokens):
            token = _unquote(tokens[index])
            option = token.casefold()
            if option in {"-filepath", "-literalpath"}:
                if index + 1 >= len(tokens):
                    return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_value")
                explicit_targets.append(tokens[index + 1])
                index += 2
                continue
            if option == "-append":
                append = True
                index += 1
                continue
            if option in {
                "-inputobject",
                "-variable",
                *_POWERSHELL_COMMON_VALUE_OPTIONS,
            }:
                if index + 1 >= len(tokens):
                    return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_value")
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            positionals.append(tokens[index])
            index += 1
        if append:
            return None
        targets = explicit_targets or positionals[:1]
        return _resolved_static_targets(CATEGORY_TRUNCATE, targets) if targets else None
    if command != "tee":
        return None
    append = False
    targets: list[str] = []
    after_double_dash = False
    for raw_token in tokens[1:]:
        token = _unquote(raw_token)
        if not after_double_dash and token == "--":
            after_double_dash = True
            continue
        if not after_double_dash and token in {"--help", "--version"}:
            return None
        if not after_double_dash and token == "--append":
            append = True
            continue
        if not after_double_dash and (
            token in {"--ignore-interrupts", "-p"}
            or token.startswith("--output-error=")
        ):
            continue
        if not after_double_dash and token.startswith("--"):
            return _blocked(CATEGORY_TRUNCATE, "parse_unable_pipeline")
        if not after_double_dash and token.startswith("-") and token != "-":
            short_flags = token[1:]
            if not short_flags or not set(short_flags) <= {"a", "i", "p"}:
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_pipeline")
            append = append or "a" in short_flags
            continue
        if token != "-":
            targets.append(token)
    if append or not targets:
        return None
    validated: list[str] = []
    for target in targets:
        validity = _validate_target(target)
        if validity == "ok":
            validated.append(target)
        elif validity == "implicit_scope":
            return _blocked(CATEGORY_TRUNCATE, "implicit_scope")
        else:
            return _blocked(CATEGORY_TRUNCATE, "parse_unable_target")
    return ParsedDestructiveCommand(CATEGORY_TRUNCATE, True, tuple(validated), "")


def _detect_shell_stdin_script(
    stdin: list[_Redirection],
) -> ParsedDestructiveCommand | None:
    """Classify a shell whose script arrives through an input redirection."""
    for redirection in stdin:
        if redirection.operator.startswith("<<<"):
            here_string = _unquote(redirection.target or "")
            if not here_string:
                return _blocked(CATEGORY_REMOVE, "parse_unable_wrapped")
            nested = parse_destructive_command(here_string)
            if nested is not None:
                return _blocked(nested.category, "parse_unable_wrapped")
        elif not redirection.operator.startswith("<<"):
            # `bash < script.sh`, `bash <&3`: the script text is not visible.
            return _blocked(CATEGORY_REMOVE, "parse_unable_wrapped")
    # Heredoc bodies are evaluated as their own segments (_shell_reads_heredoc).
    return None


def _detect_wrapper_indirect(
    tokens: list[str],
    stdin: list[_Redirection],
) -> ParsedDestructiveCommand | None:
    """Parse the command a wrapper runs.

    ``stdin`` holds the segment's input redirections, split off ``tokens``.  A
    shell that reads its script from stdin runs them as the script; any other
    wrapper passes them on to the wrapped command (`sudo bash < script`), and a
    shell given ``-c`` or a script file only reads them as data, so its payload
    is still checked (`bash -c 'rm peer.py' <<< x`).
    """
    wrapper = _command_name(tokens[0])
    if wrapper not in WRAPPER_COMMAND_NAMES:
        return None
    stdin_tokens = _input_redirection_tokens(stdin)
    if wrapper in SHELL_COMMAND_NAMES:
        if stdin and _shell_stdin_is_script(tokens):
            return _detect_shell_stdin_script(stdin)
        stdin_tokens = []
    payload_tokens = _wrapper_payload_tokens(tokens, wrapper)
    if not payload_tokens:
        return None
    payload_tokens = [*payload_tokens, *stdin_tokens]
    payload_command = " ".join(_unquote(token) for token in payload_tokens)
    nested = parse_destructive_command(payload_command)
    if nested is not None:
        if wrapper in OPAQUE_WRAPPER_COMMAND_NAMES:
            return _blocked(nested.category, "parse_unable_wrapped")
        return nested
    if wrapper not in OPAQUE_WRAPPER_COMMAND_NAMES:
        return None
    # An input redirection only reads, so it is left out of the read-only proof:
    # `bash -c 'echo hi' <<< x` stays allowed while `-c 'rm …'` is caught above.
    read_only_candidate = (
        payload_command
        if wrapper in INVOKE_EXPRESSION_COMMAND_NAMES
        else " ".join(_unquote(token) for token in tokens)
    )
    if is_proven_read_only_command(read_only_candidate):
        return None
    return _blocked(CATEGORY_REMOVE, "parse_unable_wrapped")


_WRAPPER_OPTIONS_WITH_VALUE: Final[dict[str, frozenset[str]]] = {
    "doas": frozenset({"-a", "-C", "-u"}),
    "env": frozenset({"-C", "-S", "-u", "--chdir", "--split-string", "--unset"}),
    "exec": frozenset({"-a"}),
    "ionice": frozenset({"-c", "-n", "-p", "-P", "-u"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "stdbuf": frozenset({"-e", "-i", "-o", "--error", "--input", "--output"}),
    "sudo": frozenset(
        {
            "-C",
            "-D",
            "-g",
            "-h",
            "-p",
            "-R",
            "-r",
            "-T",
            "-u",
            "--chdir",
            "--chroot",
            "--close-from",
            "--command-timeout",
            "--group",
            "--host",
            "--prompt",
            "--role",
            "--type",
            "--user",
        }
    ),
    "timeout": frozenset({"-k", "-s", "--kill-after", "--signal"}),
    "xargs": frozenset({"-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s"}),
}


def _joined_attached_option_value(
    tokens: list[str], index: int, separator: str
) -> tuple[str, int]:
    value = tokens[index].split(separator, 1)[1]
    quote = value[0] if value.startswith(("'", '"')) else ""
    cursor = index + 1
    pieces = [value]
    while quote and not pieces[-1].endswith(quote) and cursor < len(tokens):
        pieces.append(tokens[cursor])
        cursor += 1
    return _unquote(" ".join(pieces)), cursor


def _wrapper_payload_tokens(tokens: list[str], wrapper: str) -> list[str]:
    if wrapper == "command" and any(token in {"-v", "-V"} for token in tokens[1:]):
        return []
    index = 1
    options_with_value = _WRAPPER_OPTIONS_WITH_VALUE.get(wrapper, frozenset())
    while index < len(tokens) and tokens[index].startswith(("-", "/")):
        option = tokens[index].split("=", 1)[0]
        if wrapper == "env" and option in {"-S", "--split-string"}:
            if "=" in tokens[index]:
                split_payload, next_index = _joined_attached_option_value(
                    tokens, index, "="
                )
                return [split_payload, *tokens[next_index:]]
            return tokens[index + 1 :]
        index += 2 if option in options_with_value and "=" not in tokens[index] else 1
    if wrapper == "env":
        while index < len(tokens) and _ASSIGNMENT_RE.match(tokens[index]):
            index += 1
    if wrapper == "timeout" and index < len(tokens):
        index += 1
    return tokens[index:]


def _detect_dynamic_command_head(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if tokens and _has_environment_reference(_unquote(tokens[0])):
        return _blocked(CATEGORY_REMOVE, "parse_unable_dynamic_command")
    return None


def _detect_coproc(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if not tokens or _unquote(tokens[0]).casefold() != "coproc":
        return None
    candidates = [tokens[1:]]
    if len(tokens) >= 3 and _unquote(tokens[1]).isidentifier():
        candidates.append(tokens[2:])
    for candidate in candidates:
        if not candidate:
            continue
        nested = parse_destructive_command(
            " ".join(_unquote(token) for token in candidate)
        )
        if nested is not None:
            return nested
    return None


def _detect_find_exec(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if _command_name(tokens[0]) != "find":
        return None
    output_targets: list[str] = []
    index = 1
    while index < len(tokens):
        token = _unquote(tokens[index]).casefold()
        if token in FIND_TESTS_WITH_VALUE:
            index += 2
            continue
        if token == "-delete":
            return _blocked(CATEGORY_REMOVE, "implicit_scope")
        if token in {"-fls", "-fprint", "-fprint0", "-fprintf"}:
            if index + 1 >= len(tokens):
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_value")
            output_targets.append(tokens[index + 1])
            index += 2
            if token == "-fprintf":
                if index >= len(tokens):
                    return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_value")
                index += 1
            continue
        if token not in {"-exec", "-execdir", "-ok", "-okdir"}:
            index += 1
            continue
        payload_start = index + 1
        terminator = payload_start
        while terminator < len(tokens) and _unquote(tokens[terminator]) not in {
            ";",
            r"\;",
            "+",
        }:
            terminator += 1
        if terminator == len(tokens) or terminator == payload_start:
            return _blocked(CATEGORY_REMOVE, "parse_unable_shell_syntax")
        nested = parse_destructive_command(
            " ".join(_unquote(item) for item in tokens[payload_start:terminator])
        )
        if nested is not None:
            return nested
        index = terminator + 1
    return (
        _resolved_static_targets(CATEGORY_TRUNCATE, output_targets)
        if output_targets
        else None
    )


def _find_arithmetic_end(line: str, opening: int) -> int | None:
    depth = 1
    index = opening + 3
    while index < len(line) - 1:
        if line[index] == "\\":
            index += 2
            continue
        if line[index : index + 2] == "((":
            depth += 1
            index += 2
            continue
        if line[index : index + 2] == "))":
            depth -= 1
            if depth == 0:
                return index
            index += 2
            continue
        index += 1
    return None


def _heredoc_consumer(line: str, operator: int) -> str:
    """Return the effective command receiving one heredoc redirect."""
    tokens = _tokenize(line[:operator])
    last_boundary = -1
    for index, token in enumerate(tokens):
        if _unquote(token) in {"&&", "||", ";", "|", "&"}:
            last_boundary = index
    # `2>/dev/null bash <<EOF`: a redirection is not the consumer.
    argv, _redirections = _split_redirections(tokens[last_boundary + 1 :])
    tokens = _command_position_tokens(argv)
    while tokens and _command_name(tokens[0]) in {
        "builtin",
        "command",
        "env",
        "exec",
    }:
        wrapper = _command_name(tokens[0])
        payload = _wrapper_payload_tokens(tokens, wrapper)
        if not payload or payload == tokens:
            return ""
        tokens = _command_position_tokens(payload)
    return _command_name(tokens[0]) if tokens else ""


def _heredoc_declarations(
    line: str,
) -> tuple[list[_HeredocDeclaration], bool]:
    declarations: list[_HeredocDeclaration] = []
    quote = ""
    escaped = False
    index = 0
    while index < len(line):
        character = line[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if character == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote:
            if character == quote:
                quote = ""
            index += 1
            continue
        if character in "'\"":
            quote = character
            index += 1
            continue
        if line[index : index + 3] == "$((":
            arithmetic_end = _find_arithmetic_end(line, index)
            if arithmetic_end is None:
                return declarations, True
            index = arithmetic_end + 2
            continue
        if (
            line[index : index + 2] != "<<"
            or line[index : index + 3] == "<<<"
            or (index > 0 and line[index - 1] == "<")
        ):
            index += 1
            continue

        cursor = index + 2
        strip_tabs = cursor < len(line) and line[cursor] == "-"
        if strip_tabs:
            cursor += 1
        while cursor < len(line) and line[cursor] in " \t":
            cursor += 1
        if cursor >= len(line):
            return declarations, True

        delimiter: list[str] = []
        word_quote = ""
        quoted = False
        while cursor < len(line):
            character = line[cursor]
            if word_quote:
                if character == word_quote:
                    word_quote = ""
                elif character == "\\" and word_quote == '"' and cursor + 1 < len(line):
                    cursor += 1
                    delimiter.append(line[cursor])
                else:
                    delimiter.append(character)
                cursor += 1
                continue
            if character in "'\"":
                quoted = True
                word_quote = character
                cursor += 1
                continue
            if character == "\\" and cursor + 1 < len(line):
                quoted = True
                cursor += 1
                delimiter.append(line[cursor])
                cursor += 1
                continue
            if character.isspace() or character in ";|&<>()":
                break
            delimiter.append(character)
            cursor += 1
        if word_quote or not delimiter:
            return declarations, True
        declarations.append(
            _HeredocDeclaration(
                delimiter="".join(delimiter),
                strip_tabs=strip_tabs,
                quoted=quoted,
                consumer=_heredoc_consumer(line, index),
            )
        )
        index = cursor
    return declarations, False


def _shell_stdin_is_script(tokens: list[str]) -> bool:
    """Classify whether a shell takes its script from standard input.

    ``-c``/``--command`` and a positional script file are separate payload
    sources. Other options do not make stdin data, and unknown options stay on
    the fail-closed side when a heredoc body contains a destructive command.
    """
    stdin_forced = False
    index = 1
    while index < len(tokens):
        token = _unquote(tokens[index])
        option = token.casefold()
        if (
            token == "-c"
            or option in {"-command", "--command"}
            or option.startswith(("-command:", "--command="))
        ):
            return False
        if option in {"--help", "--version"}:
            return False
        if option == "--":
            return stdin_forced or index + 1 >= len(tokens)
        if option in {"-", "-s", "--stdin"}:
            stdin_forced = True
            index += 1
            continue
        if option in {"-o", "+o", "--init-file", "--rcfile"}:
            if index + 1 >= len(tokens):
                return True
            index += 2
            continue
        if token.startswith(("-", "+")) and token not in {"-", "+"}:
            short_flags = token[1:] if not token.startswith("--") else ""
            if "c" in short_flags:
                return False
            if token.startswith("-") and "s" in short_flags:
                stdin_forced = True
            index += 1
            continue
        return stdin_forced
    return True


def _shell_reads_heredoc(line: str, declaration: _HeredocDeclaration) -> bool:
    if declaration.consumer not in SHELL_COMMAND_NAMES:
        return False
    operator = line.find("<<")
    if operator < 0:
        return False
    tokens = _tokenize(line[:operator])
    last_boundary = -1
    for index, token in enumerate(tokens):
        if _unquote(token) in {"&&", "||", ";", "|", "&"}:
            last_boundary = index
    # `bash 2>/dev/null <<EOF` still runs the body: a redirection is not the
    # script operand that would make stdin plain data.
    argv, _redirections = _split_redirections(tokens[last_boundary + 1 :])
    tokens = _command_position_tokens(argv)
    while tokens and _command_name(tokens[0]) in {
        "builtin",
        "command",
        "env",
        "exec",
    }:
        payload = _wrapper_payload_tokens(tokens, _command_name(tokens[0]))
        if not payload or payload == tokens:
            return True
        tokens = _command_position_tokens(payload)
    if not tokens or _command_name(tokens[0]) not in SHELL_COMMAND_NAMES:
        return False
    return _shell_stdin_is_script(tokens)


def _heredoc_expansion_commands(body: str) -> tuple[list[str], bool]:
    """Extract commands expanded by the outer shell in an unquoted heredoc."""
    commands: list[str] = []
    malformed = _has_ambiguous_nested_backticks(body)
    index = 0
    while index < len(body):
        character = body[index]
        if character == "\\":
            index += 2
            continue
        if body[index : index + 2] == "$(":
            closing = _find_substitution_end(body, index + 1)
            if closing is None:
                return commands, True
            commands.append(body[index + 2 : closing])
            index = closing + 1
            continue
        if character == "`":
            closing = _find_backtick_end(body, index)
            if closing is None:
                return commands, True
            commands.append(body[index + 1 : closing])
            index = closing + 1
            continue
        index += 1
    return commands, malformed


def _without_heredoc_bodies(command: str) -> tuple[str, bool, list[str]]:
    pending: list[tuple[_HeredocDeclaration, bool, list[str]]] = []
    retained: list[str] = []
    executable_bodies: list[str] = []
    malformed = False
    for line in command.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        line_ending = line[len(content) :]
        if pending:
            declaration, executable, body = pending[0]
            candidate = content.lstrip("\t") if declaration.strip_tabs else content
            retained.append(line_ending)
            if candidate == declaration.delimiter:
                pending.pop(0)
                body_text = "".join(body)
                if executable:
                    executable_bodies.append(body_text)
                elif not declaration.quoted:
                    expansions, invalid = _heredoc_expansion_commands(body_text)
                    executable_bodies.extend(expansions)
                    malformed = malformed or invalid
            else:
                body.append(line)
            continue
        retained.append(line)
        declarations, invalid = _heredoc_declarations(content)
        pending.extend(
            (
                declaration,
                _shell_reads_heredoc(content, declaration),
                [],
            )
            for declaration in declarations
        )
        malformed = malformed or invalid
    return "".join(retained), malformed or bool(pending), executable_bodies


def _find_backtick_end(command: str, opening: int) -> int | None:
    index = opening + 1
    while index < len(command):
        if command[index] == "\\":
            index += 2
            continue
        if command[index] == "`":
            return index
        index += 1
    return None


def _find_substitution_end(command: str, open_paren: int) -> int | None:
    """Find the balanced end of a ``$(`` command substitution."""
    depth = 1
    state = _ShellScanState.EXPECT_COMMAND
    return_state = _ShellScanState.EXPECT_COMMAND
    index = open_paren + 1
    while index < len(command):
        character = command[index]
        if state is _ShellScanState.ESCAPED:
            state = return_state
            index += 1
            continue
        if state is _ShellScanState.IN_SINGLE_QUOTE:
            if character == "'":
                state = _ShellScanState.IN_ARGUMENTS
            index += 1
            continue
        if state is _ShellScanState.IN_DOUBLE_QUOTE:
            if character == "\\":
                return_state = state
                state = _ShellScanState.ESCAPED
            elif character == '"':
                state = _ShellScanState.IN_ARGUMENTS
            elif character == "`":
                nested_end = _find_backtick_end(command, index)
                if nested_end is None:
                    return None
                index = nested_end + 1
                continue
            elif command[index : index + 2] == "$(":
                nested_end = _find_substitution_end(command, index + 1)
                if nested_end is None:
                    return None
                index = nested_end + 1
                continue
            index += 1
            continue
        if character == "\\":
            return_state = state
            state = _ShellScanState.ESCAPED
        elif character == "'":
            state = _ShellScanState.IN_SINGLE_QUOTE
        elif character == '"':
            state = _ShellScanState.IN_DOUBLE_QUOTE
        elif character == "`":
            nested_end = _find_backtick_end(command, index)
            if nested_end is None:
                return None
            index = nested_end + 1
            continue
        elif command[index : index + 2] == "$(":
            nested_end = _find_substitution_end(command, index + 1)
            if nested_end is None:
                return None
            index = nested_end + 1
            continue
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _starts_case_construct(segment: str) -> bool:
    tokens = [_unquote(token).casefold() for token in _tokenize(segment)]
    return bool(tokens) and tokens[0] == "case" and "in" in tokens[1:]


def _structured_execution_bodies(command: str) -> list[str]:
    bodies = [match.group("body") for match in _FUNCTION_CALL_RE.finditer(command)]
    for case_match in _CASE_BLOCK_RE.finditer(command):
        clauses = case_match.group("body")
        bodies.extend(
            match.group("body") for match in _CASE_CLAUSE_RE.finditer(clauses)
        )
    return bodies


def _has_ambiguous_nested_backticks(command: str) -> bool:
    visible: list[str] = []
    in_single_quote = False
    escaped = False
    for character in command:
        if escaped:
            visible.append(" ")
            escaped = False
            continue
        if character == "\\" and not in_single_quote:
            visible.append(" ")
            escaped = True
            continue
        if character == "'":
            in_single_quote = not in_single_quote
            visible.append(" ")
            continue
        visible.append(" " if in_single_quote else character)
    return _AMBIGUOUS_NESTED_BACKTICK_RE.search("".join(visible)) is not None


def _command_segments(command: str) -> tuple[list[_CommandSegment], bool]:
    """Split shell text only at executable command boundaries.

    Quoted and escaped operators remain arguments.  Outside those lexical
    states, ``&`` and physical newlines open a command position just like the
    separators handled by the original parser.
    """
    segments: list[_CommandSegment] = []
    nested_segments: list[_CommandSegment] = []
    malformed = _has_ambiguous_nested_backticks(command)
    start = 0
    state = _ShellScanState.EXPECT_COMMAND
    return_state = _ShellScanState.EXPECT_COMMAND
    case_active = False
    case_expects_pattern = False
    incoming_separator: str | None = None
    index = 0
    while index < len(command):
        character = command[index]
        if state is _ShellScanState.ESCAPED:
            state = return_state
            index += 1
            continue
        if state is _ShellScanState.IN_SINGLE_QUOTE:
            if character == "'":
                state = _ShellScanState.IN_ARGUMENTS
            index += 1
            continue
        if state is _ShellScanState.IN_DOUBLE_QUOTE:
            if character == "\\":
                return_state = state
                state = _ShellScanState.ESCAPED
            elif character == '"':
                state = _ShellScanState.IN_ARGUMENTS
            elif command[index : index + 2] == "$(":
                closing = _find_substitution_end(command, index + 1)
                if closing is None:
                    malformed = True
                    index += 2
                    continue
                nested, nested_malformed = _command_segments(
                    command[index + 2 : closing]
                )
                nested_segments.extend(nested)
                malformed = malformed or nested_malformed
                index = closing + 1
                continue
            elif character == "`":
                closing = _find_backtick_end(command, index)
                if closing is None:
                    malformed = True
                    index += 1
                    continue
                nested, nested_malformed = _command_segments(
                    command[index + 1 : closing]
                )
                nested_segments.extend(nested)
                malformed = malformed or nested_malformed
                index = closing + 1
                continue
            index += 1
            continue
        if character == "\\":
            return_state = state
            state = _ShellScanState.ESCAPED
            index += 1
            continue
        if character == "'":
            state = _ShellScanState.IN_SINGLE_QUOTE
            index += 1
            continue
        if character == '"':
            state = _ShellScanState.IN_DOUBLE_QUOTE
            index += 1
            continue
        if command[index : index + 2] == "$(":
            closing = _find_substitution_end(command, index + 1)
            if closing is None:
                malformed = True
                index += 2
                continue
            nested, nested_malformed = _command_segments(command[index + 2 : closing])
            nested_segments.extend(nested)
            malformed = malformed or nested_malformed
            index = closing + 1
            continue
        if command[index : index + 2] in {"<(", ">("}:
            closing = _find_substitution_end(command, index + 1)
            if closing is None:
                malformed = True
                index += 2
                continue
            nested, nested_malformed = _command_segments(command[index + 2 : closing])
            nested_segments.extend(nested)
            malformed = malformed or nested_malformed
            index = closing + 1
            continue
        if character == "`":
            closing = _find_backtick_end(command, index)
            if closing is None:
                malformed = True
                index += 1
                continue
            nested, nested_malformed = _command_segments(command[index + 1 : closing])
            nested_segments.extend(nested)
            malformed = malformed or nested_malformed
            index = closing + 1
            continue

        separator_width = 0
        if character in "\r\n":
            separator_width = 2 if command[index : index + 2] == "\r\n" else 1
        elif character == ";":
            if command[index : index + 3] == ";;&":
                separator_width = 3
            elif command[index : index + 2] in {";;", ";&"}:
                separator_width = 2
            else:
                separator_width = 1
        elif character == "|" and (index == 0 or command[index - 1] != ">"):
            separator_width = 2 if command[index : index + 2] in {"||", "|&"} else 1
        elif character == "&" and not (
            (index > 0 and command[index - 1] == ">")
            or (index + 1 < len(command) and command[index + 1] == ">")
        ):
            if command[start:index].strip():
                separator_width = 2 if command[index : index + 2] == "&&" else 1

        if separator_width:
            segment = command[start:index]
            if _starts_case_construct(segment):
                case_active = True
            if case_expects_pattern and segment.strip():
                segment = f"case _ in {segment}"
                case_expects_pattern = False
            segments.append(_CommandSegment(segment, incoming_separator))
            separator = command[index : index + separator_width]
            if case_active and separator in {";;", ";&", ";;&"}:
                case_expects_pattern = True
            index += separator_width
            start = index
            incoming_separator = separator
            state = _ShellScanState.AFTER_SEPARATOR
            continue
        if not character.isspace():
            state = _ShellScanState.IN_ARGUMENTS
        index += 1
    final_segment = command[start:]
    if case_expects_pattern and final_segment.strip():
        final_segment = f"case _ in {final_segment}"
    segments.append(_CommandSegment(final_segment, incoming_separator))
    return segments + nested_segments, malformed


_FALLBACK_SCAN_WINDOW: Final[int] = 6


def _detect_obfuscated_fallback(tokens: list[str]) -> ParsedDestructiveCommand | None:
    # 난독화된 파괴 명령은 corpus 전 사례에서 명령 앞부분(토큰 0~2)에 등장한다. 스캔
    # 창을 앞부분으로 제한해, python -c "<긴 스크립트 문자열>"처럼 뒤쪽 인자 안에 우연히
    # rm/git 같은 단어가 데이터로 섞여 있는 무관한 명령까지 오탐하지 않게 한다.
    scan_window = (
        tokens[:_FALLBACK_SCAN_WINDOW]
        if tokens and _NOISE_RE.search(tokens[0])
        else tokens[:1]
    )
    for token in scan_window:
        if not _NOISE_RE.search(token):
            # 노이즈 문자(따옴표/백슬래시/괄호/&/$)가 전혀 없는 깨끗한 토큰은 이미
            # 전용 탐지기가 정확히 평가했다 — 여기서 재매치하면 "git commit"처럼
            # 무관한 정상 서브커맨드까지 오탐한다.
            continue
        name = _deobfuscated_name(token)
        if not name:
            continue
        if name == "git":
            return _blocked(CATEGORY_GIT, "parse_unable_obfuscated")
        if name in REMOVE_COMMAND_NAMES:
            return _blocked(CATEGORY_REMOVE, "parse_unable_obfuscated")
        if name in TRUNCATE_COMMAND_NAMES:
            return _blocked(CATEGORY_TRUNCATE, "parse_unable_obfuscated")
    return None


_DETECTORS: Final[
    tuple[Callable[[list[str]], ParsedDestructiveCommand | None], ...]
] = (
    _detect_tee,
    _detect_git,
    _detect_remove,
    _detect_truncate_cmdlet,
    _detect_truncate_file,
    _detect_dd_output,
    _detect_transfer,
    _detect_rsync,
    _detect_robocopy,
    _detect_forced_link,
    _detect_sed_in_place,
    _detect_perl_in_place,
    _detect_forced_new_item,
    _detect_find_exec,
)


def _command_position_tokens(tokens: list[str]) -> list[str]:
    if (
        len(tokens) >= 3
        and _unquote(tokens[0]).casefold() == "coproc"
        and _unquote(tokens[1]).isidentifier()
        and _unquote(tokens[1]).upper() == _unquote(tokens[1])
    ):
        tokens = [tokens[0], *tokens[2:]]
    if tokens and _unquote(tokens[0]).casefold() == "case":
        in_index = next(
            (
                index
                for index, token in enumerate(tokens[1:], start=1)
                if _unquote(token).casefold() == "in"
            ),
            None,
        )
        if in_index is None:
            return []
        pattern_end = next(
            (
                index
                for index, token in enumerate(
                    tokens[in_index + 1 :], start=in_index + 1
                )
                if ")" in _unquote(token)
            ),
            None,
        )
        if pattern_end is None:
            return []
        pattern_token = _unquote(tokens[pattern_end])
        _pattern, _closing, remainder = pattern_token.partition(")")
        tokens = ([remainder] if remainder else []) + tokens[pattern_end + 1 :]

    state = _ShellScanState.EXPECT_COMMAND
    index = 0
    closing_stack: list[str] = []
    command_states = {
        _ShellScanState.EXPECT_COMMAND,
        _ShellScanState.AFTER_CONTROL_WORD,
    }
    while index < len(tokens) and state in command_states:
        token = _unquote(tokens[index])
        if token.casefold() in _COMMAND_POSITION_CONTROL_WORDS:
            if token == "(":
                closing_stack.append(")")
            elif token == "{":
                closing_stack.append("}")
            state = _ShellScanState.AFTER_CONTROL_WORD
            index += 1
            continue
        if _ASSIGNMENT_RE.match(token):
            state = _ShellScanState.EXPECT_COMMAND
            index += 1
            continue
        state = _ShellScanState.IN_ARGUMENTS
    normalized = tokens[index:]
    if normalized and all(
        _unquote(token).casefold() in _COMMAND_TERMINATORS for token in normalized
    ):
        return []
    while (
        normalized and closing_stack and _unquote(normalized[-1]) == closing_stack[-1]
    ):
        normalized = normalized[:-1]
        closing_stack.pop()
    return normalized


_EXECUTABLE_PREFIX_WRAPPERS: Final[frozenset[str]] = frozenset(
    WRAPPER_COMMAND_NAMES - OPAQUE_WRAPPER_COMMAND_NAMES - {"xargs"}
)
_PIPELINE_SHELL_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    SHELL_COMMAND_NAMES | {"cmd", "powershell", "pwsh"}
)


def _effective_command_tokens(raw_tokens: list[str]) -> list[str]:
    tokens = _command_position_tokens(raw_tokens)
    while tokens and _command_name(tokens[0]) in _EXECUTABLE_PREFIX_WRAPPERS:
        wrapper = _command_name(tokens[0])
        payload = _wrapper_payload_tokens(tokens, wrapper)
        if not payload or payload == tokens:
            return []
        tokens = _command_position_tokens(payload)
    return tokens


def _invoke_expression_uses_pipeline_input(tokens: list[str]) -> bool:
    command = _command_name(tokens[0])
    return (
        command in INVOKE_EXPRESSION_COMMAND_NAMES
        and not _wrapper_payload_tokens(tokens, command)
        and not any(
            _unquote(token).partition(":")[0].casefold() == "-command"
            for token in tokens[1:]
        )
    )


def _detect_pipeline_shell_receiver(
    segment: _CommandSegment,
    pipeline_literal: str | None,
) -> ParsedDestructiveCommand | None:
    if segment.incoming_separator not in {"|", "|&"}:
        return None
    # `curl … | sh 2>&1`: a redirection is not a script operand, so the shell
    # still runs whatever the pipe feeds it.
    argv, _redirections = _segment_argv(segment.text)
    tokens = _effective_command_tokens(argv)
    command = _command_name(tokens[0]) if tokens else ""
    if (
        tokens
        and command in _PIPELINE_SHELL_COMMAND_NAMES
        and _shell_stdin_is_script(tokens)
    ):
        return _blocked(CATEGORY_REMOVE, "parse_unable_pipeline")
    if (
        tokens
        and pipeline_literal is None
        and _invoke_expression_uses_pipeline_input(tokens)
    ):
        return _blocked(CATEGORY_REMOVE, "parse_unable_pipeline")
    return None


def _r2_command_segment_groups(
    command: str,
) -> tuple[list[_CommandSegment], list[_CommandSegment], bool]:
    """Split R2 segments into the command text itself and its re-parsed bodies.

    The first list is what the invoked shell runs directly (substitutions
    included).  The second holds heredoc, function and case bodies that R2
    evaluates again on their own; they are POSIX constructs, so dialect
    exceptions for the host shell never apply to them.
    """
    normalized_command = _normalize_attached_here_strings(command)
    executable_text, heredoc_malformed, heredoc_bodies = _without_heredoc_bodies(
        normalized_command
    )
    segments, substitution_malformed = _command_segments(executable_text)
    body_segments: list[_CommandSegment] = []
    nested_malformed = False
    for body in [*heredoc_bodies, *_structured_execution_bodies(executable_text)]:
        nested_segments, body_malformed = _command_segments(body)
        body_segments.extend(nested_segments)
        nested_malformed = nested_malformed or body_malformed
    return (
        segments,
        body_segments,
        heredoc_malformed or substitution_malformed or nested_malformed,
    )


def _r2_command_segments(command: str) -> tuple[list[_CommandSegment], bool]:
    """Return the executable segments consumed by the R2 parser."""
    segments, body_segments, malformed = _r2_command_segment_groups(command)
    return [*segments, *body_segments], malformed


def executable_command_positions(command: str) -> tuple[tuple[str, ...], ...]:
    """Expose R2's command-position normalization for advisory consumers.

    The returned tokens identify the effective executable after lexical
    assignments and the transparent ``env``/``command``/``exec`` prefixes R2
    already understands.  This is parser reuse only; callers must not treat the
    result as an authorization decision.
    """
    positions: list[tuple[str, ...]] = []
    segments, _malformed = _r2_command_segments(command)
    for segment in segments:
        argv, _redirections = _segment_argv(segment.text)
        tokens = _effective_command_tokens(argv)
        if tokens:
            positions.append(tuple(_unquote(token) for token in tokens))
    return tuple(positions)


_CD_FLAG_RE: Final = re.compile(r"-[lpen@]+")
_PUSHD_ROTATION_RE: Final = re.compile(r"[+-]\d+")
_MSYS_DRIVE_RE: Final = re.compile(r"^/(?P<drive>[A-Za-z])(?:/(?P<rest>.*))?$")


def _change_directory_target(tokens: list[str]) -> str | None:
    """Static destination of one ``cd``-family command, or None if unknown."""
    positionals: list[str] = []
    index = 1
    while index < len(tokens):
        raw = tokens[index]
        token = _unquote(raw)
        option = token.casefold()
        if option in {"-path", "-literalpath"}:
            if index + 1 >= len(tokens):
                return None
            positionals.append(tokens[index + 1])
            index += 2
            continue
        if option.startswith(("-path:", "-literalpath:")):
            positionals.append(token.split(":", 1)[1])
            index += 1
            continue
        if option == "-stackname":
            index += 2
            continue
        if option in {"--", "-passthru"} or _CD_FLAG_RE.fullmatch(option):
            index += 1
            continue
        if option == "/d" and index + 1 < len(tokens):
            index += 1
            continue
        positionals.append(raw)
        index += 1
    if not positionals:
        home = os.path.expanduser("~")
        return None if home.startswith("~") else home
    if len(positionals) != 1 or _is_powershell_dynamic_path(positionals[0]):
        return None
    target = _unquote(positionals[0])
    if not target or target == "-" or _PUSHD_ROTATION_RE.fullmatch(target):
        return None
    if target.startswith("~"):
        target = os.path.expanduser(target)
        if target.startswith("~"):
            return None
    if _has_glob(target) or _has_environment_reference(target):
        return None
    return target


def command_working_directories(command: str) -> tuple[tuple[str, ...], bool]:
    """Return the static ``cd`` destinations inside one command, in order.

    Repeats are kept: ``cd src && cd src`` stands in ``src/src``, so dropping
    the second ``src`` would leave that directory unchecked.

    The second value is True when a directory change could not be resolved
    statically (``cd "$DIR"``, ``cd -``, ``popd`` without a matching ``pushd``).
    Advisory and R2 consumers use this to evaluate relative paths against every
    directory the command may be standing in, not only the project root.
    """
    directories: list[str] = []
    unresolvable = False
    pushed = False
    segments, _malformed = _r2_command_segments(command)
    for segment in segments:
        # `cd .smtw < /dev/null`: an input redirection is not a second operand.
        argv, _redirections = _segment_argv(segment.text)
        tokens = _effective_command_tokens(argv)
        if not tokens:
            continue
        name = _command_name(tokens[0])
        if name in RETURN_DIRECTORY_COMMAND_NAMES:
            # popd returns to a directory already in the candidate set only when
            # this command pushed it; otherwise the destination is unknown.
            unresolvable = unresolvable or not pushed
            continue
        if name not in CHANGE_DIRECTORY_COMMAND_NAMES:
            continue
        pushed = pushed or name in {"pushd", "push-location"}
        target = _change_directory_target(tokens)
        if target is None:
            unresolvable = True
        else:
            directories.append(target)
    return tuple(directories), unresolvable


def host_shell_cwd(payload: Mapping[str, JsonValue]) -> str:
    """The shell working directory the host reported for this tool call.

    Adapters forward it as ``shell_cwd`` (never as ``cwd``, which core modules
    use as a project-root fallback) so relative destructive targets are also
    checked where the shell really stands after an earlier ``cd``.
    """
    value = payload.get("cwd")
    return value if isinstance(value, str) else ""


def _msys_drive_path(path: str, windows: bool) -> str | None:
    """Translate Git Bash ``/c/Users/...`` into ``C:/Users/...`` on Windows."""
    if not windows:
        return None
    match = _MSYS_DRIVE_RE.match(path.replace("\\", "/"))
    if match is None:
        return None
    return f"{match.group('drive').upper()}:/{match.group('rest') or ''}"


def _is_rooted_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.startswith("/") or Path(normalized).is_absolute()


def working_directory_bases(
    root: str,
    command: str,
    shell_cwd: str | None = None,
    *,
    windows: bool | None = None,
) -> tuple[tuple[str, ...], bool]:
    """Every directory a relative path in ``command`` may be resolved against.

    The project root stays first so the historical interpretation is always
    checked.  The host-reported shell cwd and each ``cd`` destination (applied
    to every earlier candidate, because subshells and ``||`` make the real order
    ambiguous) are added on top: checking more interpretations only adds
    protection, never removes it.
    """
    is_windows = os.name == "nt" if windows is None else windows
    base_root = os.path.abspath(root)
    bases: list[str] = [base_root]
    if shell_cwd:
        cwd = _msys_drive_path(shell_cwd, is_windows) or shell_cwd
        candidate = os.path.abspath(os.path.join(base_root, cwd))
        if candidate not in bases:
            bases.append(candidate)
    directories, unresolvable = command_working_directories(command)
    for directory in directories:
        directory = _msys_drive_path(directory, is_windows) or directory
        for base in tuple(bases):
            candidate = os.path.abspath(os.path.join(base, directory))
            if candidate not in bases:
                bases.append(candidate)
        if len(bases) > MAX_WORKING_DIRECTORY_BASES:
            return tuple(bases[:MAX_WORKING_DIRECTORY_BASES]), True
    return tuple(bases), unresolvable


def path_interpretations(
    raw_path: str,
    bases: tuple[str, ...],
    *,
    windows: bool | None = None,
) -> tuple[str, ...]:
    """The raw path plus its reading under every non-root working directory."""
    is_windows = os.name == "nt" if windows is None else windows
    interpretations = [raw_path]
    normalized = raw_path.strip().strip("'\"")
    msys = _msys_drive_path(normalized, is_windows)
    if msys is not None:
        interpretations.append(msys)
    elif normalized and not _is_rooted_path(normalized):
        interpretations.extend(os.path.join(base, normalized) for base in bases[1:])
    return tuple(dict.fromkeys(interpretations))


def _static_pipeline_literal(segment: _CommandSegment) -> str | None:
    text = segment.text.strip()
    if len(text) < 2 or text[0] != text[-1] or text[0] not in "'\"":
        return None
    tokens = _tokenize(text)
    return tokens[0] if len(tokens) == 1 and tokens[0] == text else None


def _bind_static_pipeline_input(
    tokens: list[str],
    pipeline_literal: str | None,
) -> list[str]:
    if pipeline_literal is None:
        return tokens
    command = _command_name(tokens[0])
    if command in {"copy-item", "move-item", "remove-item", "rename-item"}:
        if not any(
            _unquote(token).casefold() in {"-path", "-literalpath"}
            for token in tokens[1:]
        ):
            return [tokens[0], "-path", pipeline_literal, *tokens[1:]]
    elif _invoke_expression_uses_pipeline_input(tokens):
        return [*tokens, pipeline_literal]
    return tokens


def _powershell_parse_failure_category(command: str) -> str:
    return (
        CATEGORY_REMOVE
        if command in {"move-item", "remove-item", "rename-item", "invoke-expression"}
        else CATEGORY_TRUNCATE
    )


def _parse_destructive_segment(
    command: str,
    pipeline_literal: str | None = None,
    *,
    powershell: bool = False,
) -> tuple[ParsedDestructiveCommand, ...]:
    """연산자 경계가 제거된 단일 shell segment를 분류한다.

    출력 리디렉션은 명령 인자가 아니다. 리디렉션 대상과 명령 자체의 파괴 대상을
    따로 모두 돌려준다 — `rm peer.txt > own.log`·`rm peer.txt 2>/dev/null`에서
    리디렉션만 보고 삭제 대상을 놓치지 않기 위해서다. ``powershell``은 host가
    PowerShell 도구로 실행한다고 보고한 segment에서만 `$null` 버리기를 허용한다.
    """
    coproc = _detect_coproc(_split_glued_subshell_parens(_tokenize(command)))
    if coproc is not None:
        return (coproc,)
    argv, redirections = _segment_argv(command)
    tokens = _command_position_tokens(argv)
    redirect = _truncating_redirect_result(redirections, powershell=powershell)
    command_result = (
        _parse_command_tokens(
            tokens,
            pipeline_literal,
            [redirection for redirection in redirections if not redirection.output],
        )
        if tokens
        else None
    )
    return tuple(
        result for result in (command_result, redirect) if result is not None
    )


def _parse_command_tokens(
    tokens: list[str],
    pipeline_literal: str | None,
    stdin: list[_Redirection],
) -> ParsedDestructiveCommand | None:
    if _is_bare_sc_service_command(tokens):
        return None
    tokens, normalization_error = _normalize_powershell_parameters(tokens)
    if normalization_error is not None:
        return _blocked(
            _powershell_parse_failure_category(_command_name(tokens[0])),
            normalization_error,
        )
    tokens = _bind_static_pipeline_input(tokens, pipeline_literal)
    dotnet_file = _detect_dotnet_file(tokens)
    if dotnet_file is not None:
        return dotnet_file
    dynamic = _detect_dynamic_command_head(tokens)
    if dynamic is not None:
        return dynamic
    for detector in _DETECTORS:
        result = detector(tokens)
        if result is not None:
            return result
    wrapped = _detect_wrapper_indirect(tokens, stdin)
    if wrapped is not None:
        return wrapped
    return _detect_obfuscated_fallback(tokens)


def parse_destructive_commands(
    command: str,
    *,
    powershell: bool = False,
) -> tuple[ParsedDestructiveCommand, ...]:
    """모든 shell segment의 파괴 분류 결과를 원래 순서대로 반환한다.

    quote 또는 escape 안의 연산자는 _command_segments()가 경계로 취급하지 않는다.
    non-destructive segment는 제외한다. resolved=False 결과가 하나라도 있으면 호출자는
    전체 command를 fail-closed로 처리해야 한다.

    ``powershell=True``는 host가 PowerShell 도구로 실행한다고 확인해 준 경우에만
    넘긴다. 그때도 heredoc·함수·case 본문처럼 다시 파싱하는 segment와
    `bash -c` 같은 중첩 셸 본문에는 `$null` 예외를 적용하지 않는다.
    """
    direct_segments, body_segments, malformed = _r2_command_segment_groups(command)
    segments = [*direct_segments, *body_segments]
    parsed: list[ParsedDestructiveCommand] = []
    if malformed:
        parsed.append(_blocked(CATEGORY_REMOVE, "parse_unable_shell_syntax"))
    for index, segment in enumerate(segments):
        pipeline_literal = (
            _static_pipeline_literal(segments[index - 1])
            if index > 0 and segment.incoming_separator in {"|", "|&"}
            else None
        )
        receiver = _detect_pipeline_shell_receiver(segment, pipeline_literal)
        results = (
            (receiver,)
            if receiver is not None
            else _parse_destructive_segment(
                segment.text,
                pipeline_literal,
                powershell=powershell and index < len(direct_segments),
            )
        )
        for result in results:
            if result not in parsed:
                parsed.append(result)
    return tuple(parsed)


def parse_destructive_command(command: str) -> ParsedDestructiveCommand | None:
    """첫 파괴 segment를 반환하는 기존 단수 API 호환 래퍼다.

    복수 segment를 정책 판정할 때는 parse_destructive_commands()를 사용해야 한다.
    """
    parsed = parse_destructive_commands(command)
    return parsed[0] if parsed else None


# --- §6-3/§6-4 게이트 판정 ---------------------------------------------------


def _default_lookup_path_attribution(
    ledger: JsonObject, canonical_path: str
) -> JsonObject | None:
    # F1(core/ledger_v2.py) 동결 계약 — 병행 구현 중이라 아직 없을 수 있다. 지연 임포트로
    # ImportError/AttributeError를 호출부(evaluate_r2_destructive_gate)의 try에서
    # "귀속 신뢰 불가 = degraded"로 흡수시킨다(§6-3 fail-closed).
    from .ledger_v2 import lookup_path_attribution  # type: ignore[attr-defined]

    return lookup_path_attribution(ledger, canonical_path)


def _default_attribution_health(ledger: JsonObject) -> JsonObject:
    from .ledger_v2 import attribution_health  # type: ignore[attr-defined]

    return attribution_health(ledger)


def _canonicalize_target(root: str, target: str) -> tuple[str, str | None]:
    # Candidate recording and R2 target evaluation must use the exact same path rules.
    return canonicalize_project_path(root, target)


def _canonicalize_lexical_target(root: str, target: str) -> str | None:
    """Return an in-project key without following target symlinks."""
    normalized = target.strip().strip("'\"").replace("\\", "/")
    if not normalized:
        return None
    base = os.path.abspath(root)
    candidate = Path(normalized)
    absolute = os.path.abspath(
        normalized if candidate.is_absolute() else Path(base) / candidate
    )
    try:
        relative = os.path.relpath(absolute, base).replace("\\", "/")
    except ValueError:
        return None
    if relative == "." or relative == ".." or relative.startswith("../"):
        return None
    return canonical_manifest_key(relative, os.name == "nt")


def _is_state_dir_key(canonical: str) -> bool:
    # Runtime state, its preserved legacy copy, and migration control paths never
    # enter attribution. Protect all generations lexically and after resolution.
    head = canonical.split("/", 1)[0]
    return is_protected_state_name(head)


def _has_durable_corrupt_marker(root: str) -> bool:
    try:
        return any(state_dir(root).glob("*.corrupt-*.bak"))
    except OSError:
        return False


def _load_ledger_for_r2(root: str) -> tuple[JsonObject, bool]:
    # R2-first invariant(§6-3): 이 함수가 어댑터 전체에서 최초의 상태 접근이어야 한다.
    # schema 예외 포함 어떤 예외도 여기서 흡수해 degraded로 전환한다 — 이후 단계의
    # 광역 except fail-open이 R2 판정을 되돌리지 못하게 하기 위함(mco-codex-r2.md RC1).
    # marker 확인은 load 이후에 한다: 손상 JSON은 load_ledger()가 내부에서 삼키고(예외를
    # 던지지 않고) .corrupt-*.bak을 이번 호출에서 막 생성하므로, load 전에만 확인하면
    # 그 첫 호출의 신호를 놓친다(§6-3 "첫 호출 뒤 소실 방지").
    try:
        ledger = load_ledger({"project_root": root})
    except Exception:  # noqa: BLE001
        return {}, True
    return ledger, _has_durable_corrupt_marker(root)


def _identity_agent_key(payload: Mapping[str, JsonValue]) -> str:
    host = payload.get("host")
    session_id = payload.get("session_id")
    agent = payload.get("agent")
    return ":".join(
        value if isinstance(value, str) and value else "unknown"
        for value in (host, session_id, agent)
    )


def _owned_by_unsettled_peer(record: JsonObject | None, caller_agent_key: str) -> bool:
    if not isinstance(record, dict):
        return False
    owners = record.get("owners")
    if not isinstance(owners, list):
        return False
    for owner in owners:
        if not isinstance(owner, dict):
            continue
        if owner.get("agent_key") == caller_agent_key:
            continue
        if owner.get("settled") is not True:
            return True
    return False


def _peer_open_invocation_candidates(
    ledger: JsonObject, caller_agent_key: str, root: str
) -> frozenset[str]:
    from .ledger_v2 import open_peer_invocation_candidates

    return frozenset(open_peer_invocation_candidates(ledger, caller_agent_key, root))


def _block(reason_code: str) -> Decision:
    return {
        "decision": "block",
        "coordination_reason_code": _coordination_reason_for_block(reason_code).value,
        "reason": (
            f"[smtw] R2: 파괴 조치가 차단되었습니다({reason_code}). "
            f"{_block_explanation(reason_code)} {RESOLUTION_PROCEDURES}"
        ),
    }


def _block_explanation(reason_code: str) -> str:
    if reason_code == "parse_unable_dynamic_command":
        return (
            "셸 구간 선두의 런타임 변수 또는 동적 명령을 정적으로 해석할 수 없어 "
            "파괴 명령 가능성을 배제할 수 없으므로 fail-closed로 차단합니다."
        )
    if reason_code == "parse_unable_dynamic_expression":
        return (
            "동적 경로 표현식을 정적으로 해석할 수 없어 파괴 대상을 확정할 수 없으므로 "
            "fail-closed로 차단합니다."
        )
    if reason_code.startswith("parse_unable_"):
        return (
            "파괴 명령 또는 대상을 정적으로 해석할 수 없어 파괴 가능성을 배제할 수 "
            "없으므로 fail-closed로 차단합니다."
        )
    return (
        "귀속을 확정할 수 없거나(파싱 불능/암시적 전체 범위) 타 에이전트의 미정산 변경 "
        "대상이라 fail-closed로 차단합니다."
    )


def _coordination_reason_for_block(reason_code: str) -> CoordinationReason:
    return R2_COORDINATION_REASON_MAP.get(
        reason_code,
        CoordinationReason.UNRESOLVABLE_TARGET,
    )


def _allow(message: str) -> Decision:
    return {"decision": "allow", "message": message}


def evaluate_r2_destructive_gate(
    payload: Mapping[str, JsonValue],
    *,
    lookup_path_attribution: Callable[
        [JsonObject, str], JsonObject | None
    ] = _default_lookup_path_attribution,
    attribution_health: Callable[
        [JsonObject], JsonObject
    ] = _default_attribution_health,
) -> Decision:
    # 판정 로직 자체는 _decide_r2에 그대로 있다 — 이 래퍼는 block 판정에 한해
    # quarantine 백업(B1 Q-1~Q-4)을 best-effort로 덧붙일 뿐, decision/
    # coordination_reason_code는 절대 바꾸지 않는다(백업 성패와 무관).
    decision = _decide_r2(
        payload,
        lookup_path_attribution=lookup_path_attribution,
        attribution_health=attribution_health,
    )
    if decision.get("decision") == "block":
        decision = _apply_quarantine_backup(payload, decision)
    return decision


def _apply_quarantine_backup(
    payload: Mapping[str, JsonValue], decision: Decision
) -> Decision:
    try:
        command = payload.get("command")
        if not isinstance(command, str) or not command.strip():
            return decision
        root = payload.get("project_root")
        root = root if isinstance(root, str) and root else "."
        reason_code = decision.get("coordination_reason_code")
        reason_code_text = reason_code if isinstance(reason_code, str) else "unknown"

        from .quarantine import backup_blocked_command

        saved = backup_blocked_command(
            root,
            command=command,
            agent_key=_identity_agent_key(payload),
            reason_code=reason_code_text,
            target=_display_targets(command),
        )
        if saved is None:
            return _append_quarantine_reason(decision, _quarantine_failure_note())
        existing_reason = decision.get("reason")
        reason_text = existing_reason if isinstance(existing_reason, str) else ""
        merged = dict(decision)
        merged["reason"] = reason_text + _quarantine_note(saved)
        return merged
    except Exception:  # noqa: BLE001 - quarantine backup must never affect the deny decision.
        return _append_quarantine_reason(decision, _quarantine_failure_note())


def _display_targets(command: str) -> str:
    try:
        parsed_commands = parse_destructive_commands(command)
    except Exception:  # noqa: BLE001 - purely informational, never fatal.
        return ""
    targets = [target for parsed in parsed_commands for target in parsed.targets]
    return ", ".join(targets)


def _quarantine_note(path: Path) -> str:
    from .quarantine import read_record

    record = read_record(path)
    if record is None:
        return _quarantine_failure_note()
    if record.truncated is True or record.record_status == "incomplete":
        return (
            " 작성하려던 내용은 일부만 보관됐습니다. 완전한 명령으로 적용하지 마세요. "
            f"quarantine 경로: `{path}`. 오케스트레이터에게 "
            "회수(smtw quarantine show/list)를 요청하세요. / "
            "Blocked content was only partially preserved; do not apply as a "
            f"complete command. Quarantine path: {path}."
        )
    if record.truncated is False and record.record_status == "complete":
        return (
            " 작성하려던 내용은 완전히 보관됐습니다. "
            f"quarantine 경로: `{path}`. 오케스트레이터에게 "
            "회수(smtw quarantine show/list)를 요청하세요. / "
            f"Blocked content preserved completely. Quarantine path: {path}."
        )
    return (
        " quarantine 파일의 완전성 메타데이터를 확인할 수 없습니다. 완전한 "
        "명령으로 적용하지 마세요. / Quarantine completeness metadata is "
        "unavailable; do not apply as a complete command."
    )


def _quarantine_failure_note() -> str:
    return (
        " 작성하려던 내용을 quarantine에 보관하지 못했습니다. R2 차단은 그대로 "
        "유지됩니다. / Blocked content could not be preserved in quarantine; "
        "the R2 block remains in effect."
    )


def _append_quarantine_reason(decision: Decision, note: str) -> Decision:
    existing_reason = decision.get("reason")
    reason_text = existing_reason if isinstance(existing_reason, str) else ""
    merged = dict(decision)
    merged["reason"] = reason_text + note
    return merged


def _decide_r2(
    payload: Mapping[str, JsonValue],
    *,
    lookup_path_attribution: Callable[
        [JsonObject, str], JsonObject | None
    ] = _default_lookup_path_attribution,
    attribution_health: Callable[
        [JsonObject], JsonObject
    ] = _default_attribution_health,
) -> Decision:
    tool = payload.get("tool_name")
    if tool not in SHELL_TOOLS:
        return _allow("not a shell command")
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        return _allow("no command")
    # `2>$null` discards output only in PowerShell; under bash `$null` is a
    # variable, so the exception needs the host to say the tool is PowerShell.
    parsed_commands = parse_destructive_commands(
        command, powershell=tool == "PowerShell"
    )
    if not parsed_commands:
        return _allow("not destructive-shaped")
    for parsed in parsed_commands:
        if not parsed.resolved:
            return _block(parsed.reason)

    root = payload.get("project_root")
    root = root if isinstance(root, str) and root else "."

    # `cd src && rm peer.py` deletes src/peer.py, not <root>/peer.py.  Every
    # relative target is checked under the root *and* each directory the shell
    # may be standing in (host cwd, in-command cd).  An unknown cd destination
    # makes relative targets unresolvable, which fails closed like any other
    # unparseable target.
    try:
        shell_cwd = payload.get("shell_cwd")
        bases, working_directory_unknown = working_directory_bases(
            root,
            command,
            shell_cwd if isinstance(shell_cwd, str) else None,
        )
    except Exception:  # noqa: BLE001 - must not fall through to a fail-open path.
        return _block("parse_unable_target")
    if working_directory_unknown and any(
        _is_relative_target(raw_target)
        for parsed in parsed_commands
        for raw_target in (*parsed.targets, *parsed.container_targets)
    ):
        return _block("parse_unable_target")

    # Lexical state-dir protection must run before ledger I/O and physical resolve:
    # `.fable-lite` may itself be a symlink that resolves outside the project.
    for parsed in parsed_commands:
        for raw_target in (*parsed.targets, *parsed.container_targets):
            for candidate in path_interpretations(raw_target, bases):
                lexical = _canonicalize_lexical_target(root, candidate)
                if lexical is not None and _is_state_dir_key(lexical):
                    return _block("state_dir_protected")

    # R2-first: 이 load가 어댑터 전체에서 최초의 상태 접근이다(§6-3).
    ledger, degraded = _load_ledger_for_r2(root)
    if degraded:
        return _block("ledger_degraded")

    try:
        health = attribution_health(ledger)
    except Exception:  # noqa: BLE001
        return _block("attribution_health_unavailable")
    if health.get("degraded") or health.get("capacity_exceeded"):
        return _block("attribution_degraded_or_capacity_exceeded")

    caller_agent_key = _identity_agent_key(payload)
    peer_candidates = _peer_open_invocation_candidates(ledger, caller_agent_key, root)
    for parsed in parsed_commands:
        for raw_target in (
            candidate
            for target in parsed.targets
            for candidate in path_interpretations(target, bases)
        ):
            disposition, canonical = _canonicalize_target(root, raw_target)
            if disposition == _CANON_UNRESOLVABLE:
                # resolve 자체가 실패한 경로(순환 심볼릭·초장경로 등): 고의 예외 유발로 루트 안
                # peer 파일을 우회할 수 없도록 fail-closed 차단(agy High).
                return _block("canonicalization_unavailable")
            if disposition == _CANON_OUT_OF_ROOT or canonical is None:
                # 루트 밖 대상: 이 프로젝트 path_attribution에 없어 타 에이전트 미정산 변경일 수
                # 없다 → R2 무관, 이 타겟은 건너뛴다. 다른 프로젝트/시스템 파일 보호는 비범위(§9).
                continue
            if _is_state_dir_key(canonical):
                # provenance/감사 상태(.fable-lite): 소유권 무관 하드 차단(agy Critical).
                return _block("state_dir_protected")
            try:
                record = lookup_path_attribution(ledger, canonical)
            except Exception:  # noqa: BLE001
                return _block("attribution_lookup_unavailable")
            if _owned_by_unsettled_peer(record, caller_agent_key):
                return _block("peer_unsettled_revision")
            if canonical in peer_candidates:
                return _block("peer_open_invocation_candidate")
        for raw_container in (
            candidate
            for target in parsed.container_targets
            for candidate in path_interpretations(target, bases)
        ):
            disposition, container = _canonicalize_target(root, raw_container)
            if disposition == _CANON_UNRESOLVABLE:
                return _block("canonicalization_unavailable")
            if disposition == _CANON_OUT_OF_ROOT or container is None:
                continue
            if any(
                _is_path_within_container(candidate, container)
                for candidate in peer_candidates
            ):
                return _block("peer_open_invocation_candidate")
            attribution_index = ledger.get("path_attribution")
            if not isinstance(attribution_index, dict):
                continue
            for candidate in attribution_index:
                if not _is_path_within_container(candidate, container):
                    continue
                try:
                    record = lookup_path_attribution(ledger, candidate)
                except Exception:  # noqa: BLE001
                    return _block("attribution_lookup_unavailable")
                if _owned_by_unsettled_peer(record, caller_agent_key):
                    return _block("peer_unsettled_revision")
    return _allow("r2 pass")


def _is_relative_target(raw_target: str) -> bool:
    normalized = raw_target.strip().strip("'\"")
    if normalized.replace("\\", "/").casefold() in SINK_PATHS:
        return False
    return bool(normalized) and not _is_rooted_path(normalized)


def _is_path_within_container(candidate: str, container: str) -> bool:
    scope = container.rstrip("/")
    return candidate == scope or candidate.startswith(f"{scope}/")
