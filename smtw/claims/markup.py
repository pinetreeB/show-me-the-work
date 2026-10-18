"""Small Markdown span helpers, deliberately independent of case content."""
from __future__ import annotations

import re


TICK = re.compile(r'`([^`\n]+)`')
QUOTE = re.compile(r'“([^”\n]*?)”|"((?:\\.|[^"\\\n])*?)"|‘([^’\n]*?)’|(?<!\w)\x27((?:\\.|[^\x27\\\n])*?)\x27')


def protected_spans(line: str) -> list[tuple[int, int]]:
    spans = [(m.start(), m.end()) for m in TICK.finditer(line)]
    spans.extend((m.start(), m.end()) for m in QUOTE.finditer(line)
                 if not any(a <= m.start() < b for a, b in spans))
    return spans


def masked(line: str) -> str:
    chars = list(line)
    for a, b in protected_spans(line):
        chars[a:b] = ' ' * (b-a)
    return ''.join(chars)


def cell_spans(line: str) -> list[tuple[int, int]]:
    if not line.lstrip().startswith('|'):
        return [(0, len(line))]
    protected = protected_spans(line)
    bars = [m.start() for m in re.finditer(r'(?<!\\)\|', line)
            if not any(a <= m.start() < b for a, b in protected)]
    bounds = [0, *bars, len(line)]
    return [(a+int(a in bars), b) for a, b in zip(bounds, bounds[1:]) if line[a:b].strip(' |')]


def sentence_span(line: str, pos: int, *, cells: bool = True) -> tuple[int, int]:
    if cells and line.lstrip().startswith('|'):
        return next(((a, b) for a, b in cell_spans(line) if a <= pos <= b), (0, len(line)))
    visible = masked(line)
    boundaries = [0, *[m.start()+1 for m in re.finditer(r'[.!?;](?:\s+|$)', visible)], len(line)]
    return (max(b for b in boundaries if b <= pos),
            min((b for b in boundaries if b > pos), default=len(line)))


def parent_span(line: str, pos: int) -> tuple[int, int] | None:
    visible = masked(line)
    stack, spans = [], []
    for i, char in enumerate(visible):
        if char in '(（':
            stack.append(i)
        elif char in ')）' and stack:
            a = stack.pop()
            if a < pos < i:
                spans.append((a+1, i))
    return min(spans, key=lambda s: s[1]-s[0]) if spans else None


def bind_path(line: str, pos: int, paths: list[tuple[int, int, str]],
              inherited: list[str]) -> tuple[str, tuple[str, ...]]:
    scopes = [s for s in (parent_span(line, pos), sentence_span(line, pos)) if s]
    if line.lstrip().startswith('|'):
        scopes.append((0, len(line)))
    for a, b in scopes:
        candidates = list(dict.fromkeys(p for start, end, p in paths if a <= start and end <= b))
        if candidates:
            return (candidates[0], ()) if len(candidates) == 1 else ('', tuple(candidates))
    candidates = list(dict.fromkeys(inherited))
    return (candidates[0], ()) if len(candidates) == 1 else ('', tuple(candidates))


def same_scope(line: str, a: int, b: int) -> bool:
    pa, pb = parent_span(line, a), parent_span(line, b)
    if pa is not None and pb is not None and pa != pb:
        return False
    return sentence_span(line, a) == sentence_span(line, b)


def normalized_quote(value: str, *, prose: bool = False) -> str:
    if prose:
        # Paired delimiters only: preserve a_b, a * b and other code operators.
        value = re.sub(r'\[([^\]\n]+)\]\((?:[^()\n]|\([^()\n]*\))*\)', r'\1', value)
        value = re.sub(r'`([^`\n]+)`', r'\1', value)
        for marker in ('**', '__', '*', '_'):
            escaped = re.escape(marker)
            pattern = r'(?<![A-Za-z0-9*_' + '`' + r'])' + escaped + r'(\S(?:.*?\S)?)' + escaped + r'(?![A-Za-z0-9*_])'
            value = re.sub(pattern, r'\1', value)
    translated = value.translate(str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'"}))
    return re.sub(r'\s+', ' ', translated).strip()


def quote_fragments(value: str, *, prose: bool) -> list[str]:
    # Ellipses in prose denote omission; literal code keeps its operators.
    parts = re.split(r'…|\.{3}', value) if prose else [value]
    fragments = [text for part in parts if (text := normalized_quote(part, prose=prose))]
    if len(parts) > 1 and fragments and max(map(len, fragments)) < 8:
        # Weak fragments keep the literal comparison instead of becoming wildcards.
        return [normalized_quote(value, prose=prose)]
    return fragments


def quote_in_text(quote: str, text: str, *, prose: bool) -> bool:
    if quote and quote in text and '\ufffd' not in quote:
        return True
    parts = quote_fragments(quote, prose=prose)
    text = normalized_quote(text, prose=prose)
    cursor = 0
    for index, part in enumerate(parts):
        offset = text.find(part, cursor)
        if offset < 0 or '\ufffd' in text[cursor if index else offset:offset+len(part)]:
            return False
        cursor = offset + len(part)
    return bool(parts)
