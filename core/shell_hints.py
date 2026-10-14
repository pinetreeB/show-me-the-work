from __future__ import annotations

import ast
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass

from .destructive_guard import executable_command_positions, output_redirection_targets


_REDIRECT = re.compile(r"(?:^|\s)(?:\d?>{1,2})\s*(?:'([^']+)'|\"([^\"]+)\"|([^\s;|&]+))")
# 인라인 스크립트(python -c / node -e)의 상태파일 쓰기 탐지. Path('x')는 read_text()·
# exists() 같은 읽기에도 붙으므로, 뒤에 **쓰기 메서드가 체이닝될 때만** 대상으로 잡는다
# (읽기 오탐 제거). 변수 경유(p = Path('x'); p.write_text())는 이 정규식이 못 잡으나 그건
# friction의 기존 한계이며 §6-5 이중 근거 교차 확인이 실질 방어다.
_INLINE = re.compile(
    r"fs\.(?:writeFileSync|appendFileSync|rmSync|unlinkSync)\(\s*(?:'([^']+)'|\"([^\"]+)\")"
)

_PATH_WRITE_METHODS = frozenset(
    {
        "chmod",
        "hardlink_to",
        "mkdir",
        "rmdir",
        "symlink_to",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
)
_PATH_MOVE_METHODS = frozenset({"rename", "replace"})


def shell_candidate_paths(command: str) -> tuple[str, ...]:
    paths: list[str] = []
    paths.extend(_redirect_paths(command))
    paths.extend(_inline_paths(command))
    paths.extend(_token_paths(_tokens(command)))
    return tuple(dict.fromkeys(path for path in paths if _path(path)))


def _redirect_paths(command: str) -> Iterable[str]:
    for match in _REDIRECT.finditer(command):
        yield next(value for value in match.groups() if value is not None)
    # R2's quote-aware parser also sees redirections glued to a word
    # (`echo x>.smtw/ledger.json`, `x>>.smtw/ledger.json`) and appends.
    yield from output_redirection_targets(command)


def _inline_paths(command: str) -> Iterable[str]:
    for match in _INLINE.finditer(command):
        yield next(value for value in match.groups() if value is not None)
    for source in _python_inline_sources(command):
        yield from _python_write_paths(source)


def _python_inline_sources(command: str) -> Iterable[str]:
    for tokens in executable_command_positions(command):
        if _is_python_executable(tokens[0]):
            yield from _python_c_source(tokens[1:])
    # HINT-03: `env -S/--split-string "..."` payloads.  R2's non-posix
    # tokenizer fragments quoted payloads (embedded \" closes the quoted
    # region), so executable_command_positions cannot see the inner command.
    # Re-extract the payload from a posix token stream instead.  Advisory
    # friction only — R2's own verdict is computed separately and unchanged.
    yield from _env_split_string_sources(command, set())


def _env_split_string_sources(
    command: str, seen: set[str]
) -> Iterable[str]:
    if command in seen:
        return
    seen.add(command)
    tokens = _tokens(command)
    for index, token in enumerate(tokens):
        if not _is_env_executable(token):
            continue
        payload = _split_string_payload(tokens, index)
        if payload is None:
            continue
        argv = _tokens(payload)
        argv_index = 0
        while (
            argv_index < len(argv)
            and not argv[argv_index].startswith("-")
            and "=" in argv[argv_index]
        ):
            argv_index += 1  # skip leading VAR=value assignments
        if argv_index < len(argv) and _is_python_executable(argv[argv_index]):
            yield from _python_c_source(argv[argv_index + 1 :])
        # Nested wrappers inside the payload (e.g. env -S "env -S '...'").
        yield from _env_split_string_sources(payload, seen)


def _split_string_payload(
    tokens: tuple[str, ...], env_index: int
) -> str | None:
    cursor = env_index + 1
    while cursor < len(tokens):
        argument = tokens[cursor]
        if argument in {"-S", "--split-string"}:
            return tokens[cursor + 1] if cursor + 1 < len(tokens) else None
        if argument.startswith("-S") and len(argument) > 2:
            return argument[2:]
        if argument == "--":
            return None
        if not argument.startswith("-") and "=" not in argument:
            return None  # the command word began without -S
        cursor += 1
    return None


def _executable_name(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return name.removesuffix(".exe")


def _is_python_executable(token: str) -> bool:
    name = _executable_name(token)
    return name == "py" or re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", name) is not None


def _is_env_executable(token: str) -> bool:
    return _executable_name(token) == "env"


def _python_c_source(args: tuple[str, ...]) -> Iterable[str]:
    for argument_index, argument in enumerate(args[:-1]):
        if argument == "-c":
            yield args[argument_index + 1]
            break


def _python_write_paths(source: str) -> Iterable[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ()
    path_names, module_names = _pathlib_bindings(tree)
    visitor = _PythonWritePathVisitor(path_names, module_names)
    visitor.visit(tree)
    return tuple(visitor.paths)


class _PythonWritePathVisitor(ast.NodeVisitor):
    """Extract literal write hints; this remains advisory friction, not authorization."""

    def __init__(
        self,
        path_names: frozenset[str],
        module_names: frozenset[str],
    ) -> None:
        self.paths: list[str] = []
        self.path_names = path_names
        self.module_names = module_names

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast visitor API
        if isinstance(node.func, ast.Attribute):
            receiver = _path_constructor_literal(
                node.func.value,
                self.path_names,
                self.module_names,
            )
            method = node.func.attr
            if receiver is not None and method in _PATH_WRITE_METHODS:
                self.paths.append(receiver)
            elif receiver is not None and method in _PATH_MOVE_METHODS:
                self.paths.append(receiver)
                destination = _call_argument_literal(
                    node,
                    0,
                    "target",
                    self.path_names,
                    self.module_names,
                )
                if destination is not None:
                    self.paths.append(destination)
            elif receiver is not None and method == "open":
                mode = _call_argument_literal(
                    node,
                    0,
                    "mode",
                    self.path_names,
                    self.module_names,
                )
                if _is_writable_mode(mode):
                    self.paths.append(receiver)
        elif isinstance(node.func, ast.Name) and node.func.id == "open":
            path = _call_argument_literal(
                node,
                0,
                "file",
                self.path_names,
                self.module_names,
            )
            mode = _call_argument_literal(
                node,
                1,
                "mode",
                self.path_names,
                self.module_names,
            )
            if path is not None and _is_writable_mode(mode):
                self.paths.append(path)
        self.generic_visit(node)


def _pathlib_bindings(tree: ast.AST) -> tuple[frozenset[str], frozenset[str]]:
    # Bare Path remains a compatibility spelling for the pre-HINT-02 friction
    # detector.  Aliases and module-qualified calls require a pathlib binding.
    path_names = {"Path"}
    module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            for alias in node.names:
                if alias.name == "Path":
                    path_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pathlib":
                    module_names.add(alias.asname or alias.name)
    return frozenset(path_names), frozenset(module_names)


def _path_constructor_literal(
    node: ast.AST,
    path_names: frozenset[str],
    module_names: frozenset[str],
) -> str | None:
    if not isinstance(node, ast.Call) or not node.args:
        return None
    constructor = node.func
    is_path = isinstance(constructor, ast.Name) and constructor.id in path_names
    is_path = is_path or (
        isinstance(constructor, ast.Attribute)
        and constructor.attr == "Path"
        and isinstance(constructor.value, ast.Name)
        and constructor.value.id in module_names
    )
    return _literal_string(node.args[0]) if is_path else None


def _call_argument_literal(
    call: ast.Call,
    position: int,
    keyword: str,
    path_names: frozenset[str],
    module_names: frozenset[str],
) -> str | None:
    for argument in call.keywords:
        if argument.arg == keyword:
            return _path_or_string_literal(
                argument.value,
                path_names,
                module_names,
            )
    if position < len(call.args):
        return _path_or_string_literal(
            call.args[position],
            path_names,
            module_names,
        )
    return None


def _path_or_string_literal(
    node: ast.AST,
    path_names: frozenset[str],
    module_names: frozenset[str],
) -> str | None:
    return (
        _path_constructor_literal(node, path_names, module_names)
        or _literal_string(node)
    )


def _literal_string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_writable_mode(mode: str | None) -> bool:
    return mode is not None and any(marker in mode for marker in "wax+")


def _tokens(command: str) -> tuple[str, ...]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";|&>")
        lexer.whitespace_split = True
        return tuple(lexer)
    except ValueError:
        return ()


def _token_paths(tokens: tuple[str, ...]) -> Iterable[str]:
    index = 0
    while index < len(tokens):
        command = tokens[index].lower()
        end = _command_end(tokens, index + 1)
        # Redirections are not arguments: `install a .smtw/x > own.log` installs
        # to `.smtw/x`, not `own.log`.  Their targets come from _redirect_paths.
        args = _without_redirections(tokens[index + 1 : end])
        if command == "tee":
            yield from (arg for arg in args if not arg.startswith("-"))
        elif command in {"cp", "mv"}:
            values = [arg for arg in args if not arg.startswith("-")]
            if values:
                yield values[-1]
        elif command == "rm":
            yield from (arg for arg in args if not arg.startswith("-"))
        elif command == "sed" and any(arg.startswith("-i") for arg in args):
            values = [arg for arg in args if not arg.startswith("-")]
            yield from values[1:]
        elif command in {"set-content", "add-content", "out-content", "out-file"}:
            yield from _powershell_path(args)
        elif command == "install":
            yield from _install_paths(args)
        elif command == "patch":
            yield from _patch_paths(args)
        elif command == "curl":
            yield from _option_value_paths(args, _CURL_OPTIONS)
        elif command == "wget":
            yield from _option_value_paths(args, _WGET_OPTIONS)
        index = end + 1


# Write destinations of commands that R2 does not model.  Friction only needs the
# paths they may create or overwrite; reads (sources, patch input) are ignored.
# Short options may be bundled (`-Dt DIR`, `-Dtdir`) and GNU long options may be
# abbreviated to any unambiguous prefix (`--target=DIR`), so both are decoded.
_INSTALL_SHORT_VALUE_LETTERS = frozenset("gmoSt")
_INSTALL_LONG_OPTIONS = frozenset(
    {
        "--backup",
        "--compare",
        "--context",
        "--debug",
        "--directory",
        "--group",
        "--help",
        "--mode",
        "--no-target-directory",
        "--owner",
        "--preserve-context",
        "--preserve-timestamps",
        "--strip",
        "--strip-program",
        "--suffix",
        "--target-directory",
        "--verbose",
        "--version",
    }
)
_INSTALL_LONG_VALUE_OPTIONS = frozenset(
    {"--group", "--mode", "--owner", "--strip-program", "--suffix", "--target-directory"}
)
_PATCH_SHORT_VALUE_LETTERS = frozenset("BDFVYdgioprz")
_PATCH_SHORT_WRITE_LETTERS = frozenset("dor")
_PATCH_LONG_OPTIONS = frozenset(
    {
        "--backup",
        "--backup-if-mismatch",
        "--basename-prefix",
        "--batch",
        "--binary",
        "--context",
        "--directory",
        "--dry-run",
        "--ed",
        "--follow-symlinks",
        "--force",
        "--forward",
        "--fuzz",
        "--get",
        "--help",
        "--ifdef",
        "--ignore-whitespace",
        "--input",
        "--merge",
        "--no-backup-if-mismatch",
        "--normal",
        "--output",
        "--posix",
        "--prefix",
        "--quiet",
        "--quoting-style",
        "--read-only",
        "--reject-file",
        "--reject-format",
        "--remove-empty-files",
        "--reverse",
        "--set-time",
        "--set-utc",
        "--silent",
        "--strip",
        "--suffix",
        "--unified",
        "--verbose",
        "--version",
        "--version-control",
    }
)
_PATCH_LONG_VALUE_OPTIONS = frozenset(
    {
        "--basename-prefix",
        "--directory",
        "--fuzz",
        "--get",
        "--ifdef",
        "--input",
        "--output",
        "--prefix",
        "--quoting-style",
        "--read-only",
        "--reject-file",
        "--reject-format",
        "--strip",
        "--suffix",
        "--version-control",
    }
)
_PATCH_LONG_WRITE_OPTIONS = frozenset({"--directory", "--output", "--reject-file"})


@dataclass(frozen=True)
class _DownloadOptions:
    short_values: frozenset[str]
    short_writes: frozenset[str]
    long_writes: frozenset[str]


_CURL_OPTIONS = _DownloadOptions(
    short_values=frozenset("AbcCdDeEFHKmoPQrtTuUwxXyYz"),
    short_writes=frozenset("cDo"),
    long_writes=frozenset(
        {
            "--alt-svc",
            "--cookie-jar",
            "--dump-header",
            "--etag-save",
            "--hsts",
            "--libcurl",
            "--output",
            "--output-dir",
            "--stderr",
            "--trace",
            "--trace-ascii",
        }
    ),
)
_WGET_OPTIONS = _DownloadOptions(
    short_values=frozenset("ABDIOPQRTUXaeilotwY"),
    short_writes=frozenset("OPao"),
    long_writes=frozenset(
        {
            "--append-output",
            "--directory-prefix",
            "--output-document",
            "--output-file",
            "--save-cookies",
            "--warc-file",
        }
    ),
)


def _is_output_redirect_operator(token: str) -> bool:
    # `_tokens` emits `>`, `>>`, `>|`, `>&`, `&>` as punctuation tokens.
    return ">" in token and not token.strip(">&|")


def _without_redirections(args: tuple[str, ...]) -> tuple[str, ...]:
    """Drop input/output redirections (operator and target) from an argv."""
    kept: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if _is_output_redirect_operator(arg):
            if kept and (kept[-1].isdigit() or kept[-1] == "*"):
                # `2>err`/`*>log` lex as `2` `>` `err`: the fd is not an argument.
                kept.pop()
            index += 2
            continue
        if arg in {"<", "<<", "<<<"}:
            index += 2
            continue
        if not arg.startswith("<"):
            kept.append(arg)
        index += 1
    return tuple(kept)


@dataclass(frozen=True)
class _ParsedOption:
    """One option word (or word pair) of a GNU-style command line."""

    flags: str  # short flag letters before any value-taking letter
    name: str  # long option name, or the value-taking short letter
    value: str | None
    next_index: int


def _gnu_option(
    args: tuple[str, ...],
    index: int,
    *,
    short_values: frozenset[str],
    long_options: frozenset[str],
    long_values: frozenset[str],
) -> _ParsedOption:
    """Decode the option at ``args[index]`` the way getopt_long does.

    A bundle stops at its first value-taking letter, whose value is the rest of
    the word or the next word (`-Dt DIR`, `-DtDIR`).  A long option may be any
    unambiguous prefix of its name (`--target=DIR`, `--targ DIR`).
    """
    arg = args[index]
    following = args[index + 1] if index + 1 < len(args) else ""
    if arg.startswith("--"):
        prefix, separator, attached = arg.partition("=")
        matches = [option for option in long_options if option.startswith(prefix)]
        name = prefix if prefix in long_options or len(matches) != 1 else matches[0]
        if name not in long_values:
            return _ParsedOption("", name, None, index + 1)
        if separator:
            return _ParsedOption("", name, attached, index + 1)
        return _ParsedOption("", name, following, index + 2)
    for position, letter in enumerate(arg[1:], start=1):
        if letter in short_values:
            attached = arg[position + 1 :]
            if attached:
                return _ParsedOption(arg[1:position], letter, attached, index + 1)
            return _ParsedOption(arg[1:position], letter, following, index + 2)
    return _ParsedOption(arg[1:], "", None, index + 1)


def _install_paths(args: tuple[str, ...]) -> Iterable[str]:
    positionals: list[str] = []
    target_directories: list[str] = []
    directories_mode = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            positionals.extend(args[index + 1 :])
            break
        if not arg.startswith("-") or arg == "-":
            positionals.append(arg)
            index += 1
            continue
        option = _gnu_option(
            args,
            index,
            short_values=_INSTALL_SHORT_VALUE_LETTERS,
            long_options=_INSTALL_LONG_OPTIONS,
            long_values=_INSTALL_LONG_VALUE_OPTIONS,
        )
        if "d" in option.flags or option.name == "--directory":
            directories_mode = True
        if option.name in {"t", "--target-directory"} and option.value is not None:
            target_directories.append(option.value)
        index = option.next_index
    if directories_mode:
        yield from positionals
    elif target_directories:
        yield from target_directories
    elif positionals:
        yield positionals[-1]


def _patch_paths(args: tuple[str, ...]) -> Iterable[str]:
    positionals: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            positionals.extend(args[index + 1 :])
            break
        if not arg.startswith("-") or arg == "-":
            positionals.append(arg)
            index += 1
            continue
        option = _gnu_option(
            args,
            index,
            short_values=_PATCH_SHORT_VALUE_LETTERS,
            long_options=_PATCH_LONG_OPTIONS,
            long_values=_PATCH_LONG_VALUE_OPTIONS,
        )
        if option.value and (
            option.name in _PATCH_SHORT_WRITE_LETTERS
            or option.name in _PATCH_LONG_WRITE_OPTIONS
        ):
            yield option.value
        index = option.next_index
    if positionals:
        # `patch FILE [PATCHFILE]`: the first operand is modified in place.
        yield positionals[0]


def _option_value_paths(
    args: tuple[str, ...],
    options: _DownloadOptions,
) -> Iterable[str]:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("--"):
            name, separator, value = arg.partition("=")
            if separator and name in options.long_writes:
                yield value
            elif not separator and arg in options.long_writes and index + 1 < len(args):
                yield args[index + 1]
                index += 1
            index += 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            # Short option cluster (`-sSo out`, `-qO-`): the first option that
            # takes a value consumes the rest of the cluster or the next word.
            for position, letter in enumerate(arg[1:], start=1):
                if letter not in options.short_values:
                    continue
                attached = arg[position + 1 :]
                value = attached or (args[index + 1] if index + 1 < len(args) else "")
                if letter in options.short_writes and value not in {"", "-"}:
                    yield value
                if not attached:
                    index += 1
                break
        index += 1


def _command_end(tokens: tuple[str, ...], index: int) -> int:
    for end in range(index, len(tokens)):
        if tokens[end] in {";", "&&", "||", "|"}:
            return end
    return len(tokens)


def _powershell_path(args: tuple[str, ...]) -> Iterable[str]:
    switches = {"-path", "-literalpath", "-filepath"}
    for index, arg in enumerate(args[:-1]):
        if arg.lower() in switches:
            yield args[index + 1]


def _path(value: str) -> str:
    path = value.strip().strip("'\"").replace("\\", "/")
    return "" if not path or path.startswith("-") else path
