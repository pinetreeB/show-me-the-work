from __future__ import annotations

import ast
import re
import shlex
from collections.abc import Iterable


_REDIRECT = re.compile(r"(?:^|\s)(?:\d?>{1,2})\s*(?:'([^']+)'|\"([^\"]+)\"|([^\s;|&]+))")
# 인라인 스크립트(python -c / node -e)의 상태파일 쓰기 탐지. Path('x')는 read_text()·
# exists() 같은 읽기에도 붙으므로, 뒤에 **쓰기 메서드가 체이닝될 때만** 대상으로 잡는다
# (읽기 오탐 제거). 변수 경유(p = Path('x'); p.write_text())는 이 정규식이 못 잡으나 그건
# friction의 기존 한계이며 §6-5 이중 근거 교차 확인이 실질 방어다.
_INLINE = re.compile(
    r"fs\.(?:writeFileSync|appendFileSync|rmSync|unlinkSync)\(\s*(?:'([^']+)'|\"([^\"]+)\")"
    r"|Path\(\s*(?:'([^']+)'|\"([^\"]+)\")\s*\)\s*\.\s*"
    r"(?:write_text|write_bytes|unlink|touch|mkdir|rmdir|rename|replace|chmod"
    r"|symlink_to|hardlink_to|open\(\s*['\"][wax])"
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


def _inline_paths(command: str) -> Iterable[str]:
    for match in _INLINE.finditer(command):
        yield next(value for value in match.groups() if value is not None)
    for source in _python_inline_sources(_tokens(command)):
        yield from _python_write_paths(source)


def _python_inline_sources(tokens: tuple[str, ...]) -> Iterable[str]:
    index = 0
    while index < len(tokens):
        end = _command_end(tokens, index + 1)
        executable = tokens[index].replace("\\", "/").rsplit("/", 1)[-1].casefold()
        if executable.endswith(".exe"):
            executable = executable[:-4]
        if executable == "py" or re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", executable):
            args = tokens[index + 1 : end]
            for argument_index, argument in enumerate(args[:-1]):
                if argument == "-c":
                    yield args[argument_index + 1]
                    break
        index = end + 1


def _python_write_paths(source: str) -> Iterable[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ()
    visitor = _PythonWritePathVisitor()
    visitor.visit(tree)
    return tuple(visitor.paths)


class _PythonWritePathVisitor(ast.NodeVisitor):
    """Extract literal write hints; this remains advisory friction, not authorization."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast visitor API
        if isinstance(node.func, ast.Attribute):
            receiver = _path_constructor_literal(node.func.value)
            method = node.func.attr
            if receiver is not None and method in _PATH_WRITE_METHODS:
                self.paths.append(receiver)
            elif receiver is not None and method in _PATH_MOVE_METHODS:
                self.paths.append(receiver)
                destination = _call_argument_literal(node, 0, "target")
                if destination is not None:
                    self.paths.append(destination)
            elif receiver is not None and method == "open":
                mode = _call_argument_literal(node, 0, "mode")
                if _is_writable_mode(mode):
                    self.paths.append(receiver)
        elif isinstance(node.func, ast.Name) and node.func.id == "open":
            path = _call_argument_literal(node, 0, "file")
            mode = _call_argument_literal(node, 1, "mode")
            if path is not None and _is_writable_mode(mode):
                self.paths.append(path)
        self.generic_visit(node)


def _path_constructor_literal(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call) or not node.args:
        return None
    constructor = node.func
    is_path = isinstance(constructor, ast.Name) and constructor.id == "Path"
    is_path = is_path or isinstance(constructor, ast.Attribute) and constructor.attr == "Path"
    return _literal_string(node.args[0]) if is_path else None


def _call_argument_literal(call: ast.Call, position: int, keyword: str) -> str | None:
    for argument in call.keywords:
        if argument.arg == keyword:
            return _path_or_string_literal(argument.value)
    if position < len(call.args):
        return _path_or_string_literal(call.args[position])
    return None


def _path_or_string_literal(node: ast.AST) -> str | None:
    return _path_constructor_literal(node) or _literal_string(node)


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
        args = tokens[index + 1 : end]
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
        index = end + 1


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
