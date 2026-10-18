from __future__ import annotations

import re
from dataclasses import replace

from .model import Budget, Claim
from .markup import (QUOTE, TICK, bind_path, cell_spans, normalized_quote,
                     parent_span, same_scope, sentence_span)


EXT = r'(?:md|txt|jsonl?|[cm]?js|tsx?|jsx|py|sh|ps1|toml|ya?ml|sql|docx|log|xml|csv|tsv|go|rs|html|css)'
PATH = re.compile(r'(?:file:///)?(?:[A-Za-z]:[\\/])?(?:[\w.@~<>+-]+[\\/])*[\w.@-]+\.' + EXT + r'\b', re.I)
REFERENCE = re.compile(r'(?P<path>(?:file:///)?(?:[A-Za-z]:[\\/])?(?:[\w.@~<>()+-]+[\\/])*[\w.@-]+\.' + EXT + r'):(?:L)?(?P<start>\d+)(?:\s*[-–~]\s*L?(?P<end>\d+))?', re.I)
REFERENCE_TAIL = re.compile(r',\s*L?(?P<start>\d+)(?:\s*[-–~]\s*L?(?P<end>\d+))?(?![\w.]|\s*(?:B\b|bytes?\b|KB\b|MB\b|개|건|회|%))', re.I)
SINGLE_LINE = re.compile(r'(?<![\w.])(?P<n>\d+)\s*줄(?!짜리)')
LOCATION = re.compile(r'(?<![\w.])(?:L(?P<l>\d+)(?:\s*[-–~]\s*L?(?P<le>\d+))?|(?:line\s+|줄\s+)(?P<p>\d+)(?:\s*[-–~]\s*(?P<pe>\d+))?|(?P<a>\d+)\s*[-–~]\s*(?P<b>\d+)\s*(?:행|줄)|(?P<n>\d+)\s*(?:행|번째\s*줄))(?![A-Za-z0-9_])', re.I)
NUMBER = re.compile(r'(?<![\w.])(?:`|\*\*|__)?([\d,]+(?:\.\d+)?)(?:`|\*\*|__)?\s*(줄(?:짜리)?|lines?|bytes?|바이트|KB|MB|KiB|MiB|B|건|개|회|%|ms)(?![A-Za-z])', re.I)
IDENTIFIER = re.compile(r'[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:\(\))?\Z')
CONTEXT = re.compile(r'함수|변수|설정\s*키|메서드|필드|\b키\b|function|method|variable|config|field', re.I)
ITEM = re.compile(r'(?<![\w/\\.-])(?:F\d+-[A-Z]\d+|[RDB]\d+(?:-[A-Z]\d+)?|§\s*\d+(?:\.\d+)*|#\d+|항목\s*\d+)(?![\w.-])')
URL = re.compile(r'https?://[^\s<>`"\u201d]+')


def sentence(line: str, pos: int) -> str:
    a, b = sentence_span(line, pos)
    return line[a:b]


def marked(context: str) -> bool:
    visible = TICK.sub('', context)
    visible = QUOTE.sub('', visible)
    if re.search(r'예시|가상|예를\s*들', visible):
        return False
    return bool(re.search(r'\[미확인\]|\[추정\]|추정\s*:|(?<![\w])미확인(?![\w])', visible))


def paths_in(line: str) -> list[tuple[int, int, str]]:
    result = []
    covered = []
    for m in TICK.finditer(line):
        value = m.group(1)
        # Preserve spaces and parentheses in explicitly quoted paths.
        path = re.sub(r':L?\d+(?:\s*[-–~]\s*L?\d+)?(?:,\s*L?\d+(?:\s*[-–~]\s*L?\d+)?)*$', '', value)
        if re.search(r'\.' + EXT + r'$', path, re.I) and not re.search(r'\s[=;]|https?://', path):
            result.append((m.start(), m.end(), path))
            covered.append((m.start(), m.end()))
    for m in PATH.finditer(line):
        if any(a <= m.start() < b for a, b in covered):
            continue
        if line[max(0, m.start()-8):m.start()].endswith(('https://', 'http://')):
            continue
        result.append((m.start(), m.end(), m.group()))
    return sorted(result)


def linked_path(paths, pos: int, fallback: str) -> str:
    if len(paths) == 1:
        return paths[0][2]
    before = [p for p in paths if p[0] <= pos]
    if before:
        return before[-1][2]
    return paths[0][2] if paths else fallback


def numbered_snippet(value: str) -> bool:
    return bool(re.fullmatch(r'\s*(?:line\s+)?\d+:\s*\S.*', value, re.I))


def code_snippet(value: str) -> bool:
    """A lone identifier (including f()) is a file-level K4 claim, not a quote."""
    if IDENTIFIER.fullmatch(value):
        return False
    symbols = re.findall(r'->|=>|==|!=|<=|>=|[=()"\x27$;{}\[\]:,+*/]', value)
    return len(symbols) >= 2 and bool(re.search(r'\s|[(),;:{}\[\]]', value))


def adjacent_snippet(line: str, span: tuple[int, int], snippet: tuple) -> bool:
    if not snippet[4] or numbered_snippet(snippet[2]):
        return True
    a, b = span
    gap = line[b:snippet[0]] if b <= snippet[0] else line[snippet[1]:a]
    return bool(re.fullmatch(r'[\s`*:：,，;()\[\]{}=→-]*', gap))


def paragraphs(lines: list[str]) -> dict[int, str]:
    result, pending = {}, []
    def flush():
        text = '\n'.join(value for _, value in pending)
        for number, _ in pending:
            result[number] = text
        pending.clear()
    for number, line in enumerate(lines, 1):
        boundary = bool(re.match(r'^\s*(?:#{1,6}\s|[-+*]\s|\d+[.)]\s|\||```|~~~)', line))
        if not line.strip() or boundary:
            flush()
        if line.strip():
            pending.append((number, line))
        if line.lstrip().startswith(('|', '#', '```', '~~~')):
            flush()
    flush()
    return result


def metric_context(line: str, start: int, end: int, paths, unit: str, header: str = '') -> bool:
    """Distinguish a file's dimensions from positions and values stored inside it."""
    a, b = sentence_span(line, start)
    visible = list(line)
    for x, y, _ in paths:
        visible[x:y] = ' ' * (y-x)
    local = ''.join(visible[a:b])
    if re.search(r'파일\s*(?:안|내용)|안의|내부|저장된|담긴|내용|기록|레코드|배열|필드|키의?\s*값|\b(?:content|record|array|field)\b', local, re.I):
        return False
    if unit == '줄':
        if re.search(r'줄\s*수|행\s*수|\blines?\b', header, re.I):
            return True
        if re.search(r'총|전체|줄\s*수|행\s*수|Total Lines|wc\s+-l|Get-Content|\btotal\b|line\s*count', local, re.I):
            return True
        if line[end:].startswith('짜리') or line[max(0,end-2):end] == '짜리':
            return True
        parent = parent_span(line, start)
        if parent and re.fullmatch(r'\s*[`*_]*[\d,]+[`*_]*\s*줄[`*_]*\s*', line[parent[0]:parent[1]]):
            return True
        tick = next((m for m in TICK.finditer(line) if m.start() <= start < m.end()), None)
        if tick and re.fullmatch(r'\d+\s*줄', tick.group(1)):
            return False
        if re.match(r'[`*_]*(?:에서|의|은)', line[end:]):
            return False
    elif re.search(r'크기|용량|file\s*size', local, re.I):
        return True
    before = [(x, y) for x, y, _ in paths if y <= start and same_scope(line, x, start)]
    return bool(before and re.fullmatch(r'[\s`*_:(（=\[\]-]*', line[before[-1][1]:start]))


def extract(report: str, budget: Budget) -> tuple[list[Claim], list[dict]]:
    lines = report.splitlines()
    paragraph_contexts = paragraphs(lines)
    claims, warnings = [], []
    heading_path = ''
    heading_paths: list[str] = []
    heading_time = ''
    fenced = False
    fence_claim: Claim | None = None
    fence_lines: list[str] = []
    previous_path = ''
    previous_paths: list[str] = []
    previous_refs: list[Claim] = []
    table_headers: list[str] = []
    table_rows: list[list[str]] = []
    last_table: list[list[str]] = []
    item_stack: list[tuple[int, list[str]]] = []
    paragraph_paths: list[str] = []
    blank_before = True
    for line_no, line in enumerate(lines, 1):
        budget.check()
        if re.match(r'^\s*(```|~~~)', line):
            if fenced:
                if fence_claim:
                    fence_claim.quote = '\n'.join(fence_lines)
                    fence_claim.quote_code = True
                fence_claim, fence_lines = None, []
            else:
                fence_claim = previous_refs[-1] if previous_refs and previous_refs[-1].report_line == line_no-1 else None
            fenced = not fenced
            continue
        if fenced:
            if fence_claim:
                fence_lines.append(line)
            continue
        paths = paths_in(line)
        if line.startswith('#'):
            heading_paths = list(dict.fromkeys(p[2] for p in paths))
            heading_path = paths[0][2] if len(paths) == 1 else ''
            heading_time = line
            item_stack.clear()
            paragraph_paths = []
        item = re.match(r'^(\s*)(?:[-+*]|\d+[.)])\s+', line)
        if item:
            indent = len(item.group(1))
            while item_stack and item_stack[-1][0] >= indent:
                item_stack.pop()
            inherited = item_stack[-1][1] if item_stack else heading_paths
            item_stack.append((indent, [p[2] for p in paths] or inherited))
            paragraph_paths = []
        elif blank_before and line.strip() and not line[0].isspace() and not line.lstrip().startswith(('|', '>')):
            item_stack.clear()
        inherited = (item_stack[-1][1] if item_stack else paragraph_paths) or heading_paths
        if line.lstrip().startswith('>') and not paths:
            inherited = previous_paths or inherited
        if not inherited and previous_refs and not blank_before:
            inherited = list(dict.fromkeys(c.path for c in previous_refs if c.path))
        def binding(pos):
            return bind_path(line, pos, paths, inherited)
        row = line.lstrip().startswith('|')
        if row:
            cells = [line[a:b].strip() for a, b in cell_spans(line)]
            if all(re.fullmatch(r':?-+:?', c or ' ') for c in cells):
                table_rows = []
            elif not table_headers:
                table_headers = cells
            else:
                table_rows.append(cells)
        elif table_headers:
            last_table = [table_headers, *table_rows]
            table_headers, table_rows = [], []

        def add(kind: str, text: str, pos: int, **kwargs):
            context = sentence(line, pos)
            scope = context + ' ' + heading_time
            rev = re.search(r'(?:commit|커밋|revision|리비전|HEAD|@)\s*[:=]?\s*`?([0-9a-f]{7,40})\b', scope, re.I)
            temporal = TICK.sub('', re.sub(r'“[^”]*”|"[^"]*"|‘[^’]*’', '', scope))
            when = ('before' if re.search(r'수정\s*전|변경\s*전|\bbefore\b', temporal, re.I)
                    else 'after' if re.search(r'수정\s*후|변경\s*후|\bafter\b', temporal, re.I) else '')
            stamp = re.search(r'\d{4}-\d{2}-\d{2}T[\d:]+Z', scope)
            if stamp:
                when = stamp.group()
            bound, candidates = binding(pos)
            explicit = kwargs.pop('path', None)
            c = Claim(line_no, text, kind, path=bound if explicit is None else explicit,
                      binding_candidates=candidates if explicit is None else (),
                      repository_contexts=(context, line, paragraph_contexts.get(line_no, line)) if kind == 'K3' else (),
                      context=context, author_marked=marked(context),
                      current=bool(re.search(r'현재\s*(?:HEAD|파일|상태)|지금\s*파일|current\s+HEAD', scope, re.I)),
                      revision=rev.group(1) if rev else '', when=when, **kwargs)
            claims.append(c)
            return c

        refs = []
        ref_spans = []
        for m in REFERENCE.finditer(line):
            c = add('K1', m.group(), m.start(), path=m.group('path'),
                    start=int(m.group('start')), end=int(m.group('end') or m.group('start')))
            refs.append(c)
            ref_spans.append((m.start(), m.end(), c))
            cursor = m.end()
            while tail := REFERENCE_TAIL.match(line, cursor):
                c = add('K1', m.group('path') + ' … ' + tail.group().lstrip(', '), tail.start(),
                        path=m.group('path'), start=int(tail.group('start')),
                        end=int(tail.group('end') or tail.group('start')))
                refs.append(c)
                ref_spans.append((tail.start(), tail.end(), c))
                cursor = tail.end()
        quoted_spans = [(m.start(), m.end()) for m in QUOTE.finditer(line)]
        for m in LOCATION.finditer(line):
            if any(a <= m.start() < b for a, b, _ in ref_spans + paths) or any(a <= m.start() < b for a, b in quoted_spans):
                continue
            start = m.group('l') or m.group('p') or m.group('a') or m.group('n')
            end = m.group('le') or m.group('pe') or m.group('b') or start
            c = add('K1', m.group(), m.start(), start=int(start), end=int(end))
            refs.append(c)
            ref_spans.append((m.start(), m.end(), c))
        for m in SINGLE_LINE.finditer(line):
            if any(a <= m.start() < b for a, b, _ in ref_spans + paths) or any(a <= m.start() < b for a, b in quoted_spans):
                continue
            header = next((table_headers[i] for i, (a,b) in enumerate(cell_spans(line)) if row and a <= m.start() < b and i < len(table_headers)), '')
            if metric_context(line, m.start(), m.end(), paths, '줄', header):
                continue
            in_tick = any(t.start() <= m.start() < t.end() and re.fullmatch(r'\d+\s*줄', t.group(1)) for t in TICK.finditer(line))
            if in_tick or re.match(r'[`*_]*(?:에서|의|은)', line[m.end():]):
                c = add('K1', m.group(), m.start(), start=int(m.group('n')), end=int(m.group('n')))
                refs.append(c)
                ref_spans.append((m.start(), m.end(), c))
        ticks = list(TICK.finditer(line))
        quoted = []
        snippets = []
        for m in QUOTE.finditer(line):
            if any(t.start() <= m.start() < t.end() for t in ticks):
                continue
            if re.search(r'번역\s*:', sentence(line, m.start())):
                continue
            value = next(g for g in m.groups() if g is not None)
            if len(value) < 8 or not value.strip():
                continue
            quoted.append((m.start(), value))
            snippets.append((m.start(), m.end(), value, m.group(), False))
            quote_path, ambiguous = binding(m.start())
            urls = list(URL.finditer(line))
            if not quote_path and len(urls) == 1:
                quote_path = urls[0].group().rstrip('.,;)]}')
            c = add('K5', m.group(), m.start(), path=quote_path, quote=value)
            c.binding_candidates = ambiguous
        if line.lstrip().startswith('>'):
            value = re.sub(r'^\s*>\s?', '', line)
            if len(value) >= 8 and not re.search(r'번역\s*:', value):
                quoted.append((0, value))
                add('K5', value, 0, quote=value)
                snippets.append((0, len(line), value, line.strip(), False))
        numbered = re.match(r'^\s*(?:`)?(\d+):\s+(.{8,}?)(?:`)?$', line)
        if numbered:
            c = add('K5', line.strip(), 0, quote=numbered.group(2), quote_code=True,
                    start=int(numbered.group(1)), end=int(numbered.group(1)))
            if not c.path and not c.binding_candidates:
                c.path = previous_path or heading_path
            snippets.append((0, len(line), numbered.group(2), line.strip(), True))
        for m in ticks:
            if paths_in(m.group(1)) or REFERENCE.search(m.group(1)) or LOCATION.fullmatch(m.group(1)) or re.fullmatch(r'[\d,.]+', m.group(1)):
                continue
            if numbered_snippet(m.group(1)) or code_snippet(m.group(1)):
                snippets.append((m.start(), m.end(), m.group(1), m.group(), True))
        attached: dict[int, list[tuple]] = {}
        for snippet in snippets:
            pos = snippet[0]
            possible = [(a, b, c) for a, b, c in ref_spans if same_scope(line, pos, a) and adjacent_snippet(line, (a, b), snippet)
                        and not (snippet[0] <= a < snippet[1])]
            if possible:
                numbered_position = re.match(r'\s*(?:line\s+)?(\d+):', snippet[2], re.I)
                if numbered_position:
                    exact = [r for r in possible if r[2].start <= int(numbered_position.group(1)) <= r[2].end]
                    possible = exact or possible
                preceding = [r for r in possible if r[1] <= pos]
                selected = preceding[-1] if preceding else possible[0]
                attached.setdefault(id(selected[2]), []).append(snippet)
        for c in refs:
            reference_text = c.text
            for index, snippet in enumerate(attached.get(id(c), [])):
                target = c if index == 0 else replace(c)
                if index:
                    claims.append(target)
                target.quote, target.quote_code = snippet[2], snippet[4]
                target.text = reference_text + ' ' + snippet[3]
        if not refs and previous_refs and snippets:
            for c in previous_refs:
                if c.report_line == line_no-1 and not c.quote and (
                    not snippets[0][4] or numbered_snippet(snippets[0][2]) or
                    line.strip() == snippets[0][3]):
                    c.quote, c.quote_code = snippets[0][2], snippets[0][4]
        for m in ticks:
            value = m.group(1)
            if re.match(r'(?:[A-Za-z]:[\\/]|file://|https?://)', value) or '/' in value or '\\' in value:
                continue
            if REFERENCE.search(value) or paths_in(value) or LOCATION.fullmatch(value) or SINGLE_LINE.fullmatch(value) or re.fullmatch(r'[0-9a-f]{7,40}', value, re.I):
                continue
            exit_claim = re.search(r'\b(?:exit(?:\s+code)?|rc)\s*[:=]?\s*(-?\d+)\b|종료\s*(?:코드)?\s*[:=]?\s*(-?\d+)', sentence(line, m.start()), re.I)
            if exit_claim and re.fullmatch(r'[\w .:-]+', value):
                add('K4', m.group(), m.start(), value=value,
                    command_exit=int(next(g for g in exit_claim.groups() if g is not None)))
                continue
            kv = re.fullmatch(r'([A-Za-z_$][\w.$]*)\s*[:=]\s*["\u201c]?([^"\u201d]+?)["\u201d]?', value)
            bound, candidates = binding(m.start())
            if kv and (bound or candidates) and (re.search(r'\s[:=]|[:=]\s|["\u201c]', value) or CONTEXT.search(sentence(line, m.start()))):
                add('K4', m.group(), m.start(), value=kv.group(1), definition=True)
                add('K6', m.group(), m.start(), value=kv.group(2).strip(), entity=kv.group(1), unit='value')
                continue
            if not IDENTIFIER.fullmatch(value):
                continue
            path = bound
            is_code = bool(re.search(r'[a-z][A-Z]|_|\.|\(\)', value))
            if not path and not candidates and not (is_code and CONTEXT.search(sentence(line, m.start()))):
                continue
            definition = bool(re.search(r'정의|설정\s*키|필드|\b키\b|function|함수', sentence(line, m.start()), re.I))
            add('K4', m.group(), m.start(), value=value.removesuffix('()'), definition=definition)
        for m in NUMBER.finditer(line):
            if any(a <= m.start() < b for a, b, _ in paths + ref_spans):
                continue
            if any(t.start() < m.start() and t.end() >= m.end() and re.search(r'[:=]', t.group(1)) for t in ticks):
                continue
            path, candidates = binding(m.start())
            local = sentence(line, m.start())
            nearby = [t.group(1) for t in ticks if t.end() <= m.start() and IDENTIFIER.fullmatch(t.group(1)) and not paths_in(t.group(1)) and same_scope(line, t.start(), m.start())]
            entity = nearby[-1] if nearby else ''
            key = re.search(r'([A-Za-z_$][\w.$]*)\s*[:=]\s*$', line[:m.start()])
            if key:
                entity = key.group(1)
            if not path and not entity and not candidates:
                continue
            unit = m.group(2).removesuffix('짜리')
            if unit.lower() in {'line', 'lines'}:
                unit = '줄'
            setting = bool(key or re.search(r'제한|한도|최대|최소|\blimit\b|\bmax(?:imum)?\b|\bmin(?:imum)?\b|설정값', local, re.I))
            header = next((table_headers[i] for i, (a,b) in enumerate(cell_spans(line)) if row and a <= m.start() < b and i < len(table_headers)), '')
            file_metric = bool(path or candidates) and not setting and not entity and unit.lower() in {'줄', 'b', 'bytes', 'byte', '바이트', 'kb', 'mb', 'kib', 'mib'} and metric_context(line, m.start(), m.end(), paths, unit, header)
            if not file_metric and not entity:
                continue
            add('K2' if file_metric else 'K6', m.group(), m.start(), value=m.group(1).replace(',', ''), unit=unit, entity=entity)
        if row and table_headers:
            for i, (a, b) in enumerate(cell_spans(line)):
                value = line[a:b].strip()
                number = re.fullmatch(r'(?:`|\*\*|__)?([\d,]+)(?:`|\*\*|__)?', value)
                header = table_headers[i] if i < len(table_headers) else ''
                path, candidates = binding(a)
                metric = '줄' if re.search(r'줄\s*수|행\s*수|lines?', header, re.I) else 'B' if re.search(r'바이트|bytes?|크기', header, re.I) else ''
                if number and metric and (path or candidates) and not re.search(r'제한|설정|한도', header):
                    add('K2', value, a, value=number.group(1).replace(',', ''), unit=metric)
        for m in re.finditer(r'(?<![\w])[0-9a-f]{7,40}(?![\w])', line, re.I):
            if re.fullmatch(r'20\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])', m.group()) and not re.search(r'(?:\bcommit|\bSHA|해시|커밋)\s*[:=]?\s*`?$', line[:m.start()], re.I):
                continue
            nearby = sentence(line, m.start())
            in_tick = any(t.start() <= m.start() < t.end() for t in ticks)
            if len(m.group()) == 40 or in_tick or re.search(r'commit|커밋|HEAD|해시|SHA|revision|리비전|parent|부모|대상|\bbase\b|\btarget\b|\bmerge\b', nearby, re.I):
                add('K3', m.group(), m.start(), value=m.group(), path='')
        for m in URL.finditer(line):
            value = m.group().rstrip('.,;)]}')
            add('K8', value, m.start(), value=value, path='')
        item_matches = [(m.start(), m.end(), m.group(), False) for m in ITEM.finditer(line)]
        if row:
            spans = cell_spans(line)
            if spans:
                a, b = spans[0]
                identifier = line[a:b].strip(' *`')
                if re.fullmatch(r'#?\d+\.?', identifier):
                    item_matches.append((a, b, identifier.rstrip('.'), True))
        for start, end, item_id, ordinal in item_matches:
            if any(a <= start < b for a, b, _ in paths):
                continue
            path, candidates = binding(start)
            paired = [(t.group(1), t.group()) for t in ticks if t.start() >= end and not paths_in(t.group(1)) and (IDENTIFIER.fullmatch(t.group(1)) or re.fullmatch(r'[\da-f]{7,40}', t.group(1), re.I))]
            paired += [(q, q) for p, q in quoted if p >= end]
            for sha in re.finditer(r'(?<!\w)[\da-f]{7,40}(?!\w)', line[end:], re.I):
                if not any(sha.group() == v for v, _ in paired):
                    paired.append((sha.group(), sha.group()))
            title = re.match(r'\s*(?:[:：—–]|\|)\s*([^|]+)', line[end:])
            if not paired and title:
                paired = [(title.group(1).strip(' *`'), title.group(1).strip())]
            if path or candidates:
                for expected, raw in paired:
                    add('K9', item_id+' '+raw, start, value=item_id, quote=expected,
                        ordinal=ordinal or bool(re.fullmatch(r'#\d+|항목\s*\d+', item_id)))
        arithmetic = re.search(r'([\d,]+)\s*건\s*중\s*([\d,]+)\s*건', line)
        if arithmetic:
            add('K7', arithmetic.group(), arithmetic.start(), value=arithmetic.group(1).replace(',', ''),
                entity=arithmetic.group(2).replace(',', ''), path='')
        total = re.search(r'(?:합계|총)\s*[:：]?\s*(\d+)\s*건', line)
        if total and last_table:
            c = add('K7', total.group(), total.start(), value=total.group(1), unit='table', path='')
            c.entity = str(len(last_table) - 1)
        if last_table:
            status_column = next((i for i, cell in enumerate(last_table[0]) if re.search(r'판정|결과|status|verdict', cell, re.I)), None)
            if status_column is not None:
                for count in re.finditer(r'\b(PASS|FAIL|INCOMPLETE|일치|불일치|미확인)\s*[:：]?\s*(\d+)\s*건', line, re.I):
                    label = count.group(1).casefold()
                    actual = sum(len(row) > status_column and row[status_column].strip(' *`').casefold() == label for row in last_table[1:])
                    add('K7', count.group(), count.start(), value=count.group(2), entity=str(actual), unit='table', path='')
        action = re.search(r'(?:제가|나는|본\s*에이전트가|에이전트가)\s*.{0,60}(?:전화|방문|현장\s*확인|유선\s*상담).{0,20}(?:확인|했|완료)|(?:출처|근거)\s*[:|]\s*.{0,40}유선\s*상담\s*기준', line)
        if action and not re.search(r'하지\s*않|안\s*했|못\s*했|미수행|예시|가상|과거\s*사례|인용', line) and not line.lstrip().startswith('>') and not any(p <= action.start() < p+len(q)+2 for p, q in quoted):
            warnings.append({'check_type': 'K10', 'report_line': line_no, 'text': action.group(),
                             'reason': '에이전트의 직접 전화·방문 수행 주장: 독립 확인 필요'})
        if paths:
            previous_paths = list(dict.fromkeys(p[2] for p in paths))
            previous_path = previous_paths[0] if len(previous_paths) == 1 else ''
            if not row and not item:
                paragraph_paths = [p[2] for p in paths]
        elif line.strip() and not line.lstrip().startswith('>'):
            previous_path = heading_path
            previous_paths = heading_paths
        previous_refs = refs if line.strip() else previous_refs
        blank_before = not line.strip()
        if blank_before:
            paragraph_paths = []
    return claims, warnings
