"""Conservative structural explanations for code quotes, never proof of a match."""
from __future__ import annotations

import ast
from functools import lru_cache
import re
import textwrap

from .markup import masked, normalized_quote
from .model import Evidence

_HOLE = "__smtw_paraphrase_omission__"


@lru_cache(maxsize=64)
def _parse(text: str):
    text = textwrap.dedent(text)
    try:
        return text, ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return text, None


def _units(evidence: Evidence):
    numbers = sorted(evidence.lines)
    start = previous = None
    lines = []
    for number in numbers:
        if previous is not None and number != previous + 1:
            text, tree = _parse("\n".join(lines))
            if tree is not None:
                yield start, text, tree
            lines = []
        if not lines:
            start = number
        lines.append(evidence.lines[number])
        previous = number
    if lines:
        text, tree = _parse("\n".join(lines))
        if tree is not None:
            yield start, text, tree


def _same(left, right) -> bool:
    return ast.dump(left, include_attributes=False) == ast.dump(right, include_attributes=False)


def _ellipsis(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is Ellipsis


def _arguments(pattern, actual) -> bool:
    reachable = {0}
    for item in pattern:
        if _ellipsis(item):
            reachable = set(range(min(reachable), len(actual) + 1)) if reachable else set()
        else:
            reachable = {index + 1 for index in reachable
                         if index < len(actual) and _same(item, actual[index])}
        if not reachable:
            return False
    return len(actual) in reachable


def _call_matches(pattern: ast.Call, actual: ast.Call) -> bool:
    if not _same(pattern.func, actual.func) or not _arguments(pattern.args, actual.args):
        return False
    for keyword in pattern.keywords:
        if keyword.arg is None:
            if isinstance(keyword.value, ast.Name) and keyword.value.id == _HOLE:
                continue
            return False
        candidates = [entry for entry in actual.keywords if entry.arg == keyword.arg]
        if len(candidates) != 1:
            return False
        if not _ellipsis(keyword.value) and not _same(keyword.value, candidates[0].value):
            return False
    return True


def code_ellipsis(quote: str) -> bool:
    visible = masked(quote)
    return bool(("..." in visible or "…" in visible)
                and re.match(r"^\s*(?:return\s+)?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*\(", visible)
                and visible.rstrip().endswith(")"))


def _quote_call(quote: str):
    visible = masked(quote)
    markers = list(re.finditer(r"\.\.\.|…", visible))
    if not markers or _HOLE in quote:
        return None
    parts, offset = [], 0
    for match in markers:
        parts.append(normalized_quote(quote[offset:match.start()]).strip(" ,"))
        offset = match.end()
    parts.append(normalized_quote(quote[offset:]).strip(" ,"))
    meaningful = [part for part in parts if re.search(r"\w", part)]
    if not meaningful or any(len(part) < 3 for part in meaningful):
        return None
    normalized = quote
    for match in reversed(markers):
        normalized = normalized[:match.start()] + "..." + normalized[match.end():]
    _, tree = _parse(normalized)
    if tree is None:
        # Informal trailing omissions after keyword arguments are not valid
        # Python syntax. Give only those standalone omissions a structural slot.
        normalized = re.sub(r",\s*\.\.\.\s*(?=[,)])", ", **" + _HOLE, normalized)
        _, tree = _parse(normalized)
    if tree is None or len(tree.body) != 1:
        return None
    statement = tree.body[0]
    if not isinstance(statement, (ast.Expr, ast.Return)) or not isinstance(statement.value, ast.Call):
        return None
    call = statement.value
    if not any(not _ellipsis(arg) for arg in call.args) and not any(k.arg is not None for k in call.keywords):
        return None
    if not any(_ellipsis(node) or isinstance(node, ast.Name) and node.id == _HOLE for node in ast.walk(call)):
        return None
    return call, isinstance(statement, ast.Return), parts


def _in_range(node, base: int, start: int, end: int) -> bool:
    return not start or start <= base + node.lineno - 1 <= base + node.end_lineno - 1 <= end


def _ordered(parts: list[str], source: str) -> bool:
    source, cursor = normalized_quote(source), 0
    for part in parts:
        if not part:
            continue
        found = source.find(part, cursor)
        if found < 0:
            return False
        cursor = found + len(part)
    return True


def paraphrase_rule(quote: str, evidence: Evidence, start: int = 0, end: int = 0) -> str:
    """Return an explanation only when the observed syntax supports that rewrite."""
    if not quote or evidence.problem or evidence.unsupported or not evidence.lines:
        return ""
    call_pattern = _quote_call(quote)
    qualifier = re.fullmatch(r"\s*([A-Za-z_]\w*)\.(.+)", quote)
    quote_text = quote.strip()
    _, quote_tree = _parse(quote_text)
    assignment = (quote_tree.body[0] if quote_tree is not None and len(quote_tree.body) == 1
                  and isinstance(quote_tree.body[0], ast.Assign) else None)
    if assignment is not None and (len(assignment.targets) != 1
                                   or not isinstance(assignment.targets[0], ast.Name)):
        assignment = None
    quote_indent = len(quote[:len(quote) - len(quote.lstrip(" \t"))].expandtabs(8))
    if not (call_pattern or qualifier or assignment):
        return ""
    for base, text, tree in _units(evidence):
        if call_pattern:
            pattern, needs_return, parts = call_pattern
            for node in ast.walk(tree):
                target = node.value if needs_return and isinstance(node, ast.Return) else node
                if (isinstance(target, ast.Call) and (not needs_return or isinstance(node, ast.Return))
                        and _in_range(node, base, start, end) and _call_matches(pattern, target)
                        and _ordered(parts, ast.get_source_segment(text, node) or "")):
                    return "R-ell"
        if qualifier:
            owner, rest = qualifier.groups()
            _, rest_tree = _parse(rest)
            rest_node = rest_tree.body[0] if rest_tree is not None and len(rest_tree.body) == 1 else None
            rest_target = (rest_node.target if isinstance(rest_node, ast.AnnAssign) else
                           rest_node.targets[0] if isinstance(rest_node, ast.Assign) and len(rest_node.targets) == 1 else None)
            classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == owner]
            if len(classes) == 1 and isinstance(rest_target, ast.Name):
                for node in classes[0].body:
                    target = (node.target if isinstance(node, ast.AnnAssign) else
                              node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
                    if (isinstance(target, ast.Name) and target.id == rest_target.id
                            and _in_range(node, base, start, end)):
                        segment = ast.get_source_segment(text, node) or ""
                        if normalized_quote(rest) == normalized_quote(segment):
                            return "R-qual"
        if assignment is not None and assignment.lineno == assignment.end_lineno:
            name = assignment.targets[0].id
            quote_tail = quote_text.encode("utf-8")[assignment.value.col_offset:].decode("utf-8")
            for node in ast.walk(tree):
                if (not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name)
                        or node.target.id != name or node.value is None
                        or node.lineno != node.end_lineno or not _in_range(node, base, start, end)):
                    continue
                original = evidence.lines[base + node.lineno - 1]
                indent = len(original[:len(original) - len(original.lstrip(" \t"))].expandtabs(8))
                if indent != quote_indent:
                    continue
                source_line = text.splitlines()[node.lineno - 1]
                source_tail = source_line.encode("utf-8")[node.value.col_offset:].decode("utf-8")
                if normalized_quote(quote_tail) == normalized_quote(source_tail):
                    return "R-ann"
    return ""
