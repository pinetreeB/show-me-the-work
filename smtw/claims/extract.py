from __future__ import annotations

import re

from .model import Budget, Claim


EXT = r'(?:md|txt|jsonl?|[cm]?js|tsx?|jsx|py|sh|ps1|toml|ya?ml|sql|docx|log|xml|csv|tsv|go|rs|html|css)'
PATH = re.compile(r'(?:file:///)?(?:[A-Za-z]:[\\/])?(?:[\w.@~<>+-]+[\\/])*[\w.@-]+\.' + EXT + r'\b', re.I)
REFERENCE = re.compile(r'(?P<path>(?:file:///)?(?:[A-Za-z]:[\\/])?(?:[\w.@~<>()+-]+[\\/])*[\w.@-]+\.' + EXT + r'):(?:L)?(?P<start>\d+)(?:\s*[-–]\s*L?(?P<end>\d+))?', re.I)
TICK = re.compile(r'`([^`\n]+)`')
QUOTE = re.compile(r'“([^”\n]{8,})”|"([^"\n]{8,})"|‘([^’\n]{8,})’')
NUMBER = re.compile(r'(?<![\w.])([\d,]+(?:\.\d+)?)\s*(줄(?:짜리)?|bytes?|바이트|KB|MB|KiB|MiB|B|건|개|회|%|ms)(?![A-Za-z])', re.I)
IDENTIFIER = re.compile(r'[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:\(\))?\Z')
CONTEXT = re.compile(r'함수|변수|설정\s*키|메서드|필드|\b키\b|function|method|variable|config|field', re.I)
ITEM = re.compile(r'(?<![\w/\\.-])(?:F\d+-[A-Z]\d+|[RDB]\d+(?:-[A-Z]\d+)?|§\s*\d+(?:\.\d+)*|#\d+|항목\s*\d+)(?![\w.-])')
URL = re.compile(r'https?://[^\s<>`"\u201d]+')


def sentence(line: str, pos: int) -> str:
    # Table marks apply to a cell; outside a table, sentence punctuation delimits.
    bounds = [0, len(line)]
    if line.lstrip().startswith('|'):
        bounds += [m.start() for m in re.finditer(r'(?<!\\)\|', line)]
    else:
        bounds += [m.end() for m in re.finditer(r'[.!?](?:\s+|$)', line)]
    bounds.sort()
    left = max(x for x in bounds if x <= pos)
    right = min((x for x in bounds if x > pos), default=len(line))
    return line[left:right]


def marked(context: str) -> bool:
    visible = TICK.sub('', context)
    visible = re.sub(r'“[^”]*”|"[^"]*"|‘[^’]*’', '', visible)
    if re.search(r'예시|가상|예를\s*들', visible):
        return False
    return bool(re.search(r'\[미확인\]|\[추정\]|추정\s*:|(?<![\w])미확인(?![\w])', visible))


def paths_in(line: str) -> list[tuple[int, int, str]]:
    result = []
    covered = []
    for m in TICK.finditer(line):
        value = m.group(1)
        # Preserve spaces and parentheses in explicitly quoted paths.
        path = re.sub(r':L?\d+(?:-L?\d+)?$', '', value)
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


def normalized_quote(value: str) -> str:
    return re.sub(r'\s+', ' ', value.translate(str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'"}))).strip()


def extract(report: str, budget: Budget) -> tuple[list[Claim], list[dict]]:
    lines = report.splitlines()
    claims, warnings = [], []
    heading_path = ''
    heading_time = ''
    fenced = False
    fence_claim: Claim | None = None
    fence_lines: list[str] = []
    previous_path = ''
    previous_refs: list[Claim] = []
    table_headers: list[str] = []
    table_rows: list[list[str]] = []
    last_table: list[list[str]] = []
    for line_no, line in enumerate(lines, 1):
        budget.check()
        if re.match(r'^\s*(```|~~~)', line):
            if fenced:
                if fence_claim:
                    fence_claim.quote = '\n'.join(fence_lines)
                fence_claim, fence_lines = None, []
            else:
                fence_claim = previous_refs[-1] if previous_refs else None
            fenced = not fenced
            continue
        if fenced:
            if fence_claim:
                fence_lines.append(line)
            continue
        paths = paths_in(line)
        if line.startswith('#'):
            heading_path = paths[0][2] if len(paths) == 1 else ''
            heading_time = line
        fallback = heading_path
        if line.lstrip().startswith('>') and not paths:
            fallback = previous_path or fallback
        row = line.lstrip().startswith('|')
        if row:
            cells = [s.strip() for s in line.strip().strip('|').split('|')]
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
            c = Claim(line_no, text, kind, path=kwargs.pop('path', linked_path(paths, pos, fallback)),
                      context=context, author_marked=marked(context),
                      current=bool(re.search(r'현재\s*(?:HEAD|파일|상태)|지금\s*파일|current\s+HEAD', scope, re.I)),
                      revision=rev.group(1) if rev else '', when=when, **kwargs)
            claims.append(c)
            return c

        refs = []
        for m in REFERENCE.finditer(line):
            c = add('K1', m.group(), m.start(), path=m.group('path'),
                    start=int(m.group('start')), end=int(m.group('end') or m.group('start')))
            refs.append(c)
        ticks = list(TICK.finditer(line))
        quoted = []
        for m in QUOTE.finditer(line):
            if any(t.start() <= m.start() < t.end() for t in ticks):
                continue
            if re.search(r'번역\s*:', sentence(line, m.start())):
                continue
            value = next(g for g in m.groups() if g is not None)
            quoted.append((m.start(), value))
            quote_path = linked_path(paths, m.start(), fallback)
            urls = list(URL.finditer(line))
            if not quote_path and len(urls) == 1:
                quote_path = urls[0].group().rstrip('.,;)]}')
            add('K5', m.group(), m.start(), path=quote_path, quote=value)
        if line.lstrip().startswith('>'):
            value = re.sub(r'^\s*>\s?', '', line)
            if len(value) >= 8 and not re.search(r'번역\s*:', value):
                quoted.append((0, value))
                add('K5', value, 0, quote=value)
        numbered = re.match(r'^\s*(?:`)?(\d+):\s+(.{8,}?)(?:`)?$', line)
        if numbered and (paths or previous_path or heading_path):
            add('K5', line.strip(), 0, path=previous_path or heading_path, quote=numbered.group(2),
                start=int(numbered.group(1)), end=int(numbered.group(1)))
        for c in refs or previous_refs:
            same = [quote for p, quote in quoted if not paths or linked_path(paths, p, fallback) == c.path]
            if same:
                c.quote = same[0]
        for m in ticks:
            value = m.group(1)
            if re.match(r'(?:[A-Za-z]:[\\/]|file://|https?://)', value) or '/' in value or '\\' in value:
                continue
            if REFERENCE.search(value) or paths_in(value) or re.fullmatch(r'[0-9a-f]{7,40}', value, re.I):
                continue
            kv = re.fullmatch(r'([A-Za-z_$][\w.$]*)\s*[:=]\s*["\u201c]?([^"\u201d]+?)["\u201d]?', value)
            if kv and linked_path(paths, m.start(), fallback) and (re.search(r'\s[:=]|[:=]\s|["\u201c]', value) or CONTEXT.search(sentence(line, m.start()))):
                add('K4', m.group(), m.start(), value=kv.group(1), definition=True)
                add('K6', m.group(), m.start(), value=kv.group(2).strip(), entity=kv.group(1), unit='value')
                continue
            if not IDENTIFIER.fullmatch(value):
                continue
            path = linked_path(paths, m.start(), fallback)
            is_code = bool(re.search(r'[a-z][A-Z]|_|\.|\(\)', value))
            if not path and not (is_code and CONTEXT.search(sentence(line, m.start()))):
                continue
            # Bare words in backticks need an explicit code relationship, even beside a path.
            if not is_code and not CONTEXT.search(sentence(line, m.start())):
                continue
            definition = bool(re.search(r'정의|설정\s*키|필드|\b키\b|function|함수', sentence(line, m.start()), re.I))
            add('K4', m.group(), m.start(), value=value.removesuffix('()'), definition=definition)
        for m in NUMBER.finditer(line):
            if any(a <= m.start() < b for a, b, _ in paths):
                continue
            path = linked_path(paths, m.start(), fallback)
            nearby = [t.group(1) for t in ticks if t.end() <= m.start() and IDENTIFIER.fullmatch(t.group(1))]
            entity = nearby[-1] if nearby else ''
            if not path and not entity:
                continue
            unit = m.group(2).removesuffix('짜리')
            file_metric = bool(path) and unit.lower() in {'줄', 'b', 'bytes', 'byte', '바이트', 'kb', 'mb', 'kib', 'mib'}
            add('K2' if file_metric else 'K6', m.group(), m.start(), value=m.group(1).replace(',', ''), unit=unit, entity=entity)
        if re.search(r'commit|커밋|HEAD|해시|SHA|revision|리비전', line, re.I):
            for m in re.finditer(r'(?<![\w])[0-9a-f]{7,40}(?![\w])', line, re.I):
                add('K3', m.group(), m.start(), value=m.group(), path='')
        for m in URL.finditer(line):
            value = m.group().rstrip('.,;)]}')
            add('K8', value, m.start(), value=value, path='')
        for m in ITEM.finditer(line):
            if any(a <= m.start() < b for a, b, _ in paths):
                continue
            path = linked_path(paths, m.start(), fallback)
            paired = [t.group(1) for t in ticks if t.start() > m.end() and not paths_in(t.group(1))]
            paired += [q for p, q in quoted if p > m.end()]
            title = re.match(r'\s*(?:[:：—–]|\|)\s*([^|]+)', line[m.end():])
            expected = paired[0] if paired else title.group(1).strip(' *`') if title else ''
            if path and expected:
                add('K9', line[m.start():].strip(), m.start(), value=m.group(), quote=expected)
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
            previous_path = paths[-1][2]
        elif line.strip() and not line.lstrip().startswith('>'):
            previous_path = heading_path
        previous_refs = refs if line.strip() else previous_refs
    return claims, warnings
