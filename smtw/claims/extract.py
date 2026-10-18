from __future__ import annotations

import re
from dataclasses import replace

from .model import Budget, Claim
from .markup import (QUOTE, TICK, bind_path, cell_spans, masked,
                     parent_span, same_scope, sentence_span)


EXT = r'(?:md|txt|jsonl?|[cm]?js|tsx?|jsx|py|sh|ps1|toml|ya?ml|sql|docx|log|xml|csv|tsv|go|rs|html|css)'
PATH = re.compile(r'(?:file:///)?(?:[A-Za-z]:[\\/])?(?:[\w.@~<>+-]+[\\/])*[\w.@-]+\.' + EXT + r'\b', re.I)
REFERENCE = re.compile(r'(?P<path>(?:file:///)?(?:[A-Za-z]:[\\/])?(?:[\w.@~<>()+-]+[\\/])*[\w.@-]+\.' + EXT + r'):(?:L)?(?P<start>\d+)(?:\s*[-–~]\s*L?(?P<end>\d+))?', re.I)
REFERENCE_TAIL = re.compile(r',\s*L?(?P<start>\d+)(?:\s*[-–~]\s*L?(?P<end>\d+))?(?![\w.]|\s*(?:B\b|bytes?\b|KB\b|MB\b|개|건|회|%))', re.I)
SINGLE_LINE = re.compile(r'(?<![\w.])(?P<n>\d+)\s*줄(?!짜리)')
LOCATION = re.compile(r'(?<![\w.])(?:L(?P<l>\d+)(?:\s*[-–~]\s*L?(?P<le>\d+))?|(?:line\s+|줄\s+)(?P<p>\d+)(?:\s*[-–~]\s*(?P<pe>\d+))?|(?P<a>\d+)\s*[-–~]\s*(?P<b>\d+)\s*(?:행|줄)|(?P<n>\d+)\s*(?:행|번째\s*줄))(?![A-Za-z0-9_])', re.I)
NUMBER = re.compile(r'(?<![\w.-])(?:`|\*\*|__)?(\d[\d,]*(?:\.\d+)?)(?:`|\*\*|__)?\s*(줄(?:짜리)?|lines?|bytes?|바이트|KB|MB|KiB|MiB|B|건|개|회|%|ms)(?![A-Za-z0-9])', re.I)
IDENTIFIER = re.compile(r'[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:\(\))?\Z')
CONTEXT = re.compile(r'함수|변수|설정\s*키|메서드|필드|\b키\b|\b(?:function|method|variable|config|field)\b', re.I)
STATUS_WORD = re.compile(r'(?:PASS|FAIL|INCOMPLETE|ERROR|RUNNING)\Z', re.I)
TOOL_OR_REPOSITORY = re.compile(r'(?:\w*(?:repo|repository|project)|(?:pytest|unittest|git|python|node|npm|pnpm|pip|cargo|docker|view_file|run_command|read_file|write_to_file))\Z', re.I)
HEX_SHA = re.compile(r'(?<![\w])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![\w])', re.I)
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
    if re.fullmatch(r':\d+(?:\s*,\s*:\d+)+', value):
        return False
    symbols = re.findall(r'->|=>|==|!=|<=|>=|[=()"\x27$;{}\[\]:,+*/]', value)
    return len(symbols) >= 2 and bool(re.search(r'\s|[(),;:{}\[\]]', value))


def code_shaped(value: str) -> bool:
    return bool(re.search(r'[a-z][A-Z]|_|\.|\(\)', value))


def general_identifier(value: str) -> bool:
    """Names for tools, repositories, and status prose are not K4 targets."""
    return bool(STATUS_WORD.fullmatch(value) or TOOL_OR_REPOSITORY.fullmatch(value))


def touches_hex_sha(line: str, start: int, end: int) -> bool:
    return any(match.start() < end and start < match.end() for match in HEX_SHA.finditer(line))


def negated_context(line: str, span: tuple[int, int]) -> bool:
    """Recognize only a claim's own narrow negative or recommendation phrasing."""
    before, after = line[:span[0]], line[span[1]:]
    if (re.search(r'\bno\s*$', before, re.I)
            or re.search(r'\bhas\s+no\s+(?:function\s*)?$', before, re.I)
            or re.search(r'\b(?:does\s+not\s+use|no\s+longer\s+calls?)\s*$', before, re.I)):
        return True
    if re.match(r'\s*(?:(?:is|are|was|were|do|does|did)\s+not|not\s+used|'
                r'should\s+not\s+be\s+used|does\s+not\s+use|'
                r'(?:is|are|was|were)\s+no\s+longer(?:\s+uses?|\s+used)?|'
                r'no\s+longer(?:\s+uses?|\s+used)?|was\s+removed)\b', after, re.I):
        return True
    if re.match(r'\s*(?:은|는|이|가|을|를)?\s*'
                r'(?:(?:의존|import|함수|메서드|필드|설정\s*키)\s*(?:은|는|이|가|을|를)?\s*)?'
                r'(?:없다|없음|없습니다|없어요)(?!\w)', after):
        return True
    if re.match(r'\s*(?:은|는|이|가|을|를)?\s*'
                r'(?:(?:함수|메서드)?(?:은|는|이|가|을|를)?\s*)?'
                r'(?:(?:호출하지|쓰지|쓰이지|사용하지|존재하지|포함되어\s*있지)\s*않(?:다|는다|습니다)|않는다)(?!\w)', after):
        return True
    if re.match(r'\s*(?:은|는|이|가|을|를)?\s*(?:호출(?:이|을)?\s*)?'
                r'제거(?:한다|됨|했다|되었다|됐다|되었습니다|했습니다|했고|해야\s*한다)?(?!\w)', after):
        return True
    return bool(re.match(r'\s*(?:을|를)?\s*(?:(?:함수|메서드|필드|설정\s*키)를?\s*)?(?:사용을?\s*)?'
                         r'(?:추가할\s*것을\s*)?(?:권장|권고)(?:하지\s*않는다|한다|됨|했다|합니다)?(?!\w)', after))


def proposed_or_recommended(context: str) -> bool:
    prose = list(masked(context))
    for start, end, _ in paths_in(context):
        prose[start:end] = ' ' * (end-start)
    return bool(re.search(r'바꿔야|수정\s*제안|제안|권장|권고|\b(?:should|proposed|recommended)\b', ''.join(prose), re.I))


def source_only_binding(line: str, pos: int, path: str, paths) -> bool:
    """A trailing parenthetical citation is evidence, not the measured target."""
    mentions = [(a, b) for a, b, candidate in paths if candidate == path]
    if not mentions:
        return False
    sentence_end = sentence_span(line, pos)[1]
    for a, _ in mentions:
        parent = parent_span(line, a)
        if (not parent or parent[0] <= pos <= parent[1] or a <= pos
                or not REFERENCE.search(line[parent[0]:parent[1]])
                or line[parent[1]+1:sentence_end].strip(' .。;!')):
            return False
    return True


def quote_scope(line: str, a: int, b: int) -> bool:
    return same_scope(line, a, b) and parent_span(line, a) == parent_span(line, b)


def reported_claim(line: str, pos: int) -> bool:
    """Only third-party claims before an explicit correction are attributed."""
    a, b = sentence_span(line, pos)
    visible = masked(line)
    sentence_text = visible[a:b]
    relative = pos - a
    # Explicitly attributed fabrication is a reported assertion, including a
    # review-round label followed by a source citation. Self claims stay checked.
    fabrication = re.search(r'날조|지어(?:냄|냈|낸)|\bfabricated\b', sentence_text, re.I)
    cited = any(source_only_binding(line, pos, path, paths_in(line))
                for _, _, path in paths_in(line))
    attributed_fabrication = re.search(
        r'(?:이전|기존|옛)\s*(?:보고|주장)|검토자|리뷰어|\b(?:they|reviewer|old report)\b|\d+차',
        sentence_text, re.I)
    if fabrication and fabrication.start() > relative and cited and attributed_fabrication:
        return True
    contrasts = list(re.finditer(
        r'실제(?:로)?|사실은|(?:했|였|었|이)지만|하지만|그러나|\b(?:actually|in fact|but|whereas)\b',
        sentence_text, re.I,
    ))
    correction = next((marker for marker in contrasts if marker.start() > relative), None)
    if correction is None:
        return False
    attributed = sentence_text[:correction.start()]
    if re.search(
        r'(?<![가-힣\w])(?:저는|제가|나는|내가|우리는|본\s*보고서(?:는|가)?|이\s*보고서(?:는|가)?|현재\s*보고서(?:는|가)?)'
        r'(?:[^.;]{0,80})?(?:주장|보고|적|기록|썼)|'
        r'\b(?:I|we|the\s+report|this\s+report|our\s+report|my\s+report|current\s+report)\b'
        r'(?:[^.;]{0,80})?\b(?:claimed|reported|said)\b',
        attributed, re.I,
    ):
        return False
    third_party = bool(re.search(
        r'(?:이전|기존|옛|지난|old|earlier|previous)\s*(?:보고서?|주장|report|claim)|'
        r'(?<![가-힣\w])보고서(?:가|이|는|은)\s*(?:[^.;]{0,80})?(?:주장|보고|적|기록|썼)|'
        r'\b(?:reviewer|they|he|she)\b\s+(?:[^.;]{0,80})?(?:claimed|reported|said)',
        attributed, re.I,
    ) or re.search(r'\b(?!I\b|We\b|The\b|This\b|Our\b|My\b|Current\b)[A-Z][A-Za-z0-9_-]+\s+(?:[^.;]{0,80})?'
                     r'(?:claimed|reported|said)\b', attributed)
        or re.search(r'(?<![가-힣\w])(?!(?:저|제|나|내|우리|본\s*보고서|이\s*보고서))'
                     r'(?:X|검토자|리뷰어|작성자|[가-힣]{2,})'
                     r'(?:가|이|는|은)\s*(?:[^.;]{0,80})?'
                     r'(?:주장|보고|적|기록|썼)', attributed))
    return third_party


def explicitly_ordered_quotes(line: str, quote_matches: list[re.Match], ref_count: int) -> bool:
    """A short quote is evidence only when the ordering marker binds the quote list."""
    if ref_count < 2 or len(quote_matches) != ref_count:
        return False
    first, last = quote_matches[0], quote_matches[-1]
    before = line[:first.start()]
    if re.search(r'(?:각각|\brespectively\b)[^.!?;]*$', before, re.I):
        return True
    after = line[last.end():]
    return bool(re.match(r'\s*[,，]?\s*respectively\b', after, re.I))


def proposed_name(context: str, span: tuple[int, int]) -> bool:
    """Only the named atom's proposal syntax, never a sentence-wide cue."""
    before, after = masked(context[:span[0]]), context[span[1]:]
    before = re.split(r'[,;]|\bbut\b|하지만|그러나', before, flags=re.I)[-1]
    noun = r'\s*(?:[은는이가을를]\s*)?(?:(?:클래스|상수|함수|메서드|필드|설정\s*키)[은는이가을를]?\s*)?'
    if re.match(noun + r'(?:(?:새로\s*)?(?:추가|도입|신설)(?:하면|해야|할|하려)|(?:새로\s*)?제안|필요하|가정하면)', after):
        return True
    if re.search(r'새로\s*$', before) and re.match(noun + r'(?:추가|도입|신설)한다', after):
        return True
    return bool(re.search(r'(?:제안|신설|도입할)\s*[:：]?\s*$', before)
                or re.search(r'\b(?:(?:will|should|must|would|could)\s+(?:add|introduce|create|use)|'
                             r'consider\s+adding|propose\s+(?:adding|introducing))\s+(?:(?:a|new|function|class|constant)\s+)*$', before, re.I))


def malformed_metrics(line: str, paths: list[tuple[int, int, str]]):
    """Yield only comma-only file metrics; ordinary comma prose remains prose."""
    for match in re.finditer(r'(?<![\w.])(?:`|\*\*|__)?(?P<number>,+)(?:`|\*\*|__)?\s*'
                            r'(?P<unit>줄(?:짜리)?|lines?|bytes?|바이트|KB|MB|KiB|MiB|B|건|개|회|%|ms)(?![A-Za-z0-9])', line, re.I):
        unit = match.group('unit').removesuffix('짜리')
        if unit.lower() in {'line', 'lines'}:
            unit = '줄'
        if metric_context(line, match.start(), match.end(), paths, unit):
            yield match, unit


def adjacent_snippet(line: str, span: tuple[int, int], snippet: tuple) -> bool:
    if not snippet[4] or numbered_snippet(snippet[2]):
        return True
    a, b = span
    visible = masked(line)
    gap = visible[b:snippet[0]] if b <= snippet[0] else visible[snippet[1]:a]
    gap = re.sub(r'실측|원문|인용|\b(?:actual|quote)\b', '', gap, flags=re.I)
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


def report_item_sections(lines: list[str]) -> dict[int, list[tuple[int, int, str, str, str]]]:
    """Return structural item starts with their full description through the next item."""
    boundaries = []
    last_context_path = ''
    for line_no, line in enumerate(lines, 1):
        line_paths = list(dict.fromkeys(path for _, _, path in paths_in(line)))
        for match in ITEM.finditer(line):
            if re.fullmatch(r'#\d+|항목\s*\d+', match.group()):
                continue  # Ordinal rows retain their existing structured mapping.
            prefix = list(line[:match.start()])
            for start, end, _ in paths_in(line):
                if end <= match.start():
                    prefix[start:end] = ' ' * (end-start)
            if re.sub(r'[\s`*_#|:：—–+.,;()\[\]-]', '', ''.join(prefix)):
                continue
            explicit_paths = list(dict.fromkeys(path for start, end, path in paths_in(line)
                                                if end <= match.start()))
            item_path = explicit_paths[0] if len(explicit_paths) == 1 else last_context_path
            description_start = match.end()
            if line.startswith('`', description_start):
                description_start += 1
            elif line.startswith('**', description_start) or line.startswith('__', description_start):
                description_start += 2
            boundaries.append((line_no, match.start(), description_start, match.group(), item_path))
            last_context_path = item_path
            break
        else:
            if len(line_paths) == 1:
                last_context_path = line_paths[0]
    result = {}
    for index, (line_no, start, end, item_id, item_path) in enumerate(boundaries):
        next_line = boundaries[index+1][0] if index+1 < len(boundaries) else len(lines)+1
        heading = re.match(r'^\s*(#{1,6})\s', lines[line_no-1])
        item_heading_level = len(heading.group(1)) if heading else 7
        first_tail = lines[line_no-1][end:].strip(' \t:：—–|')
        # A complete quoted inline reference does not open a Markdown section.
        # Explicit headings/list items and colon descriptions keep continuation.
        if (not heading and not re.match(r'^\s*[-*+]\s+', lines[line_no-1])
                and (QUOTE.fullmatch(first_tail) or TICK.fullmatch(first_tail))):
            next_line = line_no + 1
        for candidate_line in range(line_no+1, next_line):
            candidate_heading = re.match(r'^\s*(#{1,6})\s', lines[candidate_line-1])
            if candidate_heading and len(candidate_heading.group(1)) <= item_heading_level:
                next_line = candidate_line
                break
        section = [lines[line_no-1][end:].strip(' \t:：—–|')]
        section.extend(lines[line_no:next_line-1])
        while section and not section[-1].strip():
            section.pop()
        description = '\n'.join(section).strip()
        quote = QUOTE.fullmatch(description)
        if quote:
            description = next(group for group in quote.groups() if group is not None)
        tick = TICK.fullmatch(description)
        if tick:
            description = tick.group(1)
        result.setdefault(line_no, []).append((start, end, item_id, description, item_path))
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
        if re.search(r'\d+\s*lines?\b', local, re.I) or reported_claim(line, start):
            return True
        correction_prefix = masked(line[a:start])
        if re.search(r'(?:실제(?:로)?|사실은|\b(?:actually|in fact)\b)[^.;]{0,24}$',
                     correction_prefix, re.I):
            return True
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
    return bool(before and re.fullmatch(r'[\s`*_:(（=\[\]-]*(?:(?:은|는|총)\s*)*', line[before[-1][1]:start]))


def extract(report: str, budget: Budget) -> tuple[list[Claim], list[dict]]:
    lines = report.splitlines()
    paragraph_contexts = paragraphs(lines)
    item_sections = report_item_sections(lines)
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
                # A single blank line before a fence is ordinary Markdown layout.
                fence_claim = (previous_refs[-1] if previous_refs
                               and line_no - previous_refs[-1].report_line <= 2
                               and not proposed_or_recommended(previous_refs[-1].context) else None)
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
            path = bound if explicit is None else explicit
            locally_bound = any(p == path and (same_scope(line, pos, start) or row)
                                for start, _, p in paths)
            inherited_context = paragraph_contexts.get(line_no, line)
            contextual_paths = ({p.replace('\\', '/').casefold()
                                 for _, _, p in paths_in(inherited_context)}
                                | {p.replace('\\', '/').casefold() for p in heading_paths})
            inherited_binding = bool(
                path and explicit is None and not locally_bound
                and any(p != path.replace('\\', '/').casefold() for p in contextual_paths)
            )
            inherited_binding |= bool(kind == 'K2' and path and source_only_binding(line, pos, path, paths))
            contextual_reason = kwargs.pop('contextual_reason', '')
            if kind in {'K1', 'K2', 'K6'} and reported_claim(line, pos):
                contextual_reason = 'reported_claim'
            c = Claim(line_no, text, kind, path=bound if explicit is None else explicit,
                      binding_candidates=candidates if explicit is None else (),
                      repository_contexts=(context, line, paragraph_contexts.get(line_no, line)) if kind == 'K3' else (),
                      context=context, author_marked=marked(context),
                      contextual_reason=contextual_reason, inherited_binding=inherited_binding,
                      current=bool(re.search(r'현재\s*(?:HEAD|파일|상태)|지금\s*파일|current\s+HEAD', scope, re.I)),
                      revision=rev.group(1) if rev else '', when=when, **kwargs)
            claims.append(c)
            return c

        refs = []
        ref_spans = []
        reference_groups = []
        for m in REFERENCE.finditer(line):
            c = add('K1', m.group(), m.start(), path=m.group('path'),
                    start=int(m.group('start')), end=int(m.group('end') or m.group('start')))
            refs.append(c)
            ref_spans.append((m.start(), m.end(), c))
            group = [c]
            cursor = m.end()
            while tail := REFERENCE_TAIL.match(line, cursor):
                c = add('K1', m.group('path') + ' … ' + tail.group().lstrip(', '), tail.start(),
                        path=m.group('path'), start=int(tail.group('start')),
                        end=int(tail.group('end') or tail.group('start')))
                refs.append(c)
                ref_spans.append((tail.start(), tail.end(), c))
                group.append(c)
                cursor = tail.end()
            if len(group) > 1:
                reference_groups.append(group)
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
        ref_spans.sort(key=lambda span: span[0])
        ticks = list(TICK.finditer(line))
        quoted = []
        snippets = []
        quote_claims = {}
        quote_matches = list(QUOTE.finditer(line))
        for m in quote_matches:
            if any(t.start() <= m.start() < t.end() for t in ticks):
                continue
            if re.search(r'번역\s*:', sentence(line, m.start())):
                continue
            value = next(g for g in m.groups() if g is not None)
            explicitly_ordered = bool(any(same_scope(line, m.start(), a) for a, _, _ in ref_spans)
                                      and explicitly_ordered_quotes(line, quote_matches, len(ref_spans)))
            if not value.strip() or len(value) < 8 and not explicitly_ordered:
                continue
            quoted.append((m.start(), value))
            snippets.append((m.start(), m.end(), value, m.group(), False))
            quote_path, ambiguous = binding(m.start())
            urls = list(URL.finditer(line))
            if not quote_path and len(urls) == 1:
                quote_path = urls[0].group().rstrip('.,;)]}')
            c = add('K5', m.group(), m.start(), path=quote_path, quote=value)
            c.binding_candidates = ambiguous
            quote_claims[m.start()] = c
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
        snippet_ticks = [m for m in ticks if numbered_snippet(m.group(1)) or code_snippet(m.group(1))]
        for m in ticks:
            if paths_in(m.group(1)) or REFERENCE.search(m.group(1)) or LOCATION.fullmatch(m.group(1)) or re.fullmatch(r'[\d,.]+', m.group(1)):
                continue
            ordered_snippet = explicitly_ordered_quotes(line, snippet_ticks, len(ref_spans))
            if (numbered_snippet(m.group(1))
                    or code_snippet(m.group(1)) and (len(m.group(1)) >= 8 or ordered_snippet)):
                snippets.append((m.start(), m.end(), m.group(1), m.group(), True))
        attached: dict[int, list[tuple]] = {}
        grouped_snippets = set()
        # The same list may repeat the filename instead of using comma tails.
        for a, _, c in ref_spans:
            if any(any(c is member for member in group) for group in reference_groups):
                continue
            local = [s for s in snippets if quote_scope(line, a, s[0])]
            group = [other for x, y, other in ref_spans
                     if quote_scope(line, a, x) and local and y <= local[0][0]]
            if len(group) > 1:
                reference_groups.append(group)
        for group in reference_groups:
            spans = [(a, b) for a, b, c in ref_spans if any(c is member for member in group)]
            local = [s for s in snippets if s[0] >= spans[-1][1] and quote_scope(line, s[0], spans[0][0])
                     and not any(spans[-1][1] <= a < s[0] for a, _, c in ref_spans if all(c is not member for member in group))]
            if not local:
                continue
            numbered = [re.match(r'\s*(?:line\s+)?(\d+):', s[2], re.I) for s in local]
            if all(numbered):
                continue  # Explicit numbered snippets use the exact-range path below.
            before_quotes = masked(line[spans[-1][1]:local[0][0]])
            after_quotes = line[local[-1][1]:sentence_span(line, spans[0][0])[1]]
            ordered = (re.search(r'각각|\brespectively\b', before_quotes, re.I)
                       or re.match(r'\s*[,，]?\s*respectively\b', after_quotes, re.I))
            if len(local) == len(group) and ordered:
                grouped_snippets.update(s[0] for s in local)
                for c, snippet in zip(group, local):
                    attached.setdefault(id(c), []).append(snippet)
            else:
                ranges = tuple((member.path, member.start, member.end) for member in group)
                direct_gap = bool(re.fullmatch(r'[\s`*:：,，;()\[\]{}=→·/-]*',
                                               line[spans[-1][1]:local[0][0]]))
                closed = bool((ordered or len(local) == 1 and direct_gap)
                              and not any(s[4] and '...' in s[2] for s in local))
                grouped_snippets.update(s[0] for s in local)
                for snippet in local:
                    group_claim = quote_claims.get(snippet[0])
                    if group_claim is None:
                        group_claim = add('K5', snippet[3], snippet[0], path='', quote=snippet[2],
                                          quote_code=snippet[4])
                    group_claim.path = ''
                    group_claim.binding_candidates = ()
                    group_claim.candidate_ranges = ranges
                    group_claim.candidate_quotes = ((snippet[2], snippet[4]),)
                    group_claim.candidate_group_closed = closed
                    group_claim.contextual_reason = '' if closed else 'binding_ambiguous'
        for snippet in snippets:
            if snippet[0] in grouped_snippets:
                continue
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
            candidates = attached.get(id(c), [])
            if len(candidates) > 1 and not any(numbered_snippet(s[2]) for s in candidates):
                c.candidate_quotes = tuple((s[2], s[4]) for s in candidates)
            for index, snippet in enumerate(candidates):
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
            spaced_value = re.fullmatch(r'([A-Za-z_$][\w.$]*)\s+(\d+(?:\.\d+)?)', value)
            if spaced_value and (general_identifier(spaced_value.group(1)) or not (
                    reported_claim(line, m.start())
                    or re.search(r'실제|사실은|\b(?:actually|in fact)\b', sentence(line, m.start()), re.I))):
                spaced_value = None
            kv = kv or spaced_value
            bound, candidates = binding(m.start())
            if kv and (bound or candidates) and (spaced_value or re.search(r'\s[:=]|[:=]\s|["\u201c]', value) or CONTEXT.search(sentence(line, m.start()))):
                add('K4', m.group(), m.start(), value=kv.group(1), definition=True,
                    contextual_reason='reported_claim' if reported_claim(line, m.start()) else '')
                add('K6', m.group(), m.start(), value=kv.group(2).strip(), entity=kv.group(1), unit='value')
                continue
            if not IDENTIFIER.fullmatch(value):
                continue
            path = bound
            is_code = code_shaped(value)
            context = sentence(line, m.start())
            prose_chars = list(masked(context))
            for a, b, _ in paths_in(context):
                prose_chars[a:b] = ' ' * (b-a)
            prose = ''.join(prose_chars)
            code_context = bool(CONTEXT.search(prose))
            if general_identifier(value) and not code_context:
                continue
            if not path and not candidates and not (is_code and code_context):
                continue
            if not (is_code or code_context):
                continue
            definition = bool(re.search(r'정의|설정\s*키|필드|\b키\b|\bfunction\b|함수', prose, re.I))
            add('K4', m.group(), m.start(), value=value.removesuffix('()'), definition=definition,
                contextual_reason='proposed_name' if proposed_name(context, (
                    m.start() - sentence_span(line, m.start())[0], m.end() - sentence_span(line, m.start())[0])) else '',
                negated_context=negated_context(context, (m.start() - sentence_span(line, m.start())[0],
                                                           m.end() - sentence_span(line, m.start())[0])))
        for m, unit in malformed_metrics(line, paths):
            add('K2', m.group(), m.start(), value=m.group('number'), unit=unit, malformed_number=True)
        for m in NUMBER.finditer(line):
            if any(a <= m.start() < b for a, b, _ in paths + ref_spans):
                continue
            if touches_hex_sha(line, m.start(), m.end()):
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
                number = re.fullmatch(r'(?:`|\*\*|__)?(\d[\d,]*)(?:`|\*\*|__)?', value)
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
        for start, end, item_id, description, item_path in item_sections.get(line_no, []):
            if any(a <= start < b for a, b, _ in paths):
                continue
            path, candidates = binding(start)
            path = path or item_path
            if path or candidates:
                add('K9', item_id + (' ' + description if description else ''), start,
                    path=path or None, value=item_id, quote=description,
                    contextual_reason='' if description else 'item_token_only')
        structural_starts = {start for start, _, _, _, _ in item_sections.get(line_no, [])}
        inline_items = [match for match in ITEM.finditer(line)
                        if match.start() not in structural_starts
                        and not re.fullmatch(r'#\d+|항목\s*\d+', match.group())]
        for index, match in enumerate(inline_items):
            start, description_start = match.start(), match.end()
            if line.startswith('`', description_start):
                description_start += 1
            elif line.startswith('**', description_start) or line.startswith('__', description_start):
                description_start += 2
            sentence_end = sentence_span(line, start)[1]
            description_end = (inline_items[index+1].start()
                               if index+1 < len(inline_items) else sentence_end)
            raw_tail = line[description_start:description_end]
            directly_bound = bool(re.match(r'\s*[:：—–|]', raw_tail))
            description = raw_tail.strip(' \t:：—–|')
            quote = QUOTE.match(description)
            if quote:
                description = next(group for group in quote.groups() if group is not None)
            tick = TICK.match(description)
            if tick:
                description = tick.group(1)
            path, candidates = binding(start)
            if path or candidates:
                add('K9', match.group() + (' ' + description if description else ''), start,
                    value=match.group(), quote=description,
                    contextual_reason=('' if directly_bound and description else
                                       'binding_ambiguous' if description else 'item_token_only'))
        item_matches = [(m.start(), m.end(), m.group(), True) for m in ITEM.finditer(line)
                        if re.fullmatch(r'#\d+|항목\s*\d+', m.group())]
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
            short_item_ticks = [t for t in ticks if t.start() >= end and not paths_in(t.group(1))]
            ordered_pairing = explicitly_ordered_quotes(line, short_item_ticks, len(item_matches))
            paired = [(t.group(1), t.group()) for t in ticks
                      if t.start() >= end and not paths_in(t.group(1))
                      and (len(t.group(1)) >= 8 or ordered_pairing)
                      and (IDENTIFIER.fullmatch(t.group(1))
                           or re.fullmatch(r'[\da-f]{7,40}', t.group(1), re.I))]
            paired += [(q, q) for p, q in quoted if p >= end]
            for sha in re.finditer(r'(?<!\w)[\da-f]{7,40}(?!\w)', line[end:], re.I):
                if not any(sha.group() == v for v, _ in paired):
                    paired.append((sha.group(), sha.group()))
            title = re.match(r'\s*(?:[:：—–]|\|)\s*([^|]+)', line[end:])
            if not paired and title:
                paired = [(title.group(1).strip(' *`'), title.group(1).strip())]
            if not paired:
                description = line[end:].strip()
                if re.fullmatch(r'[A-Za-z가-힣][\w-]*(?:\s+[\w-]+){2,}[.!]?', description):
                    paired = [(description, description)]
            if path or candidates:
                for expected, raw in paired:
                    tail = line[end:sentence_span(line, end)[1]].strip(' :：—–|')
                    token_only = bool(re.fullmatch(r'`[^`]+`|[\d-]+', raw)
                                      and tail.rstrip('.') == raw)
                    # Explicit ordinal rows with code identifiers are a supported
                    # structured mapping, unlike bare issue/review labels.
                    ordinal_mapping = bool((ordinal or re.fullmatch(r'#\d+|항목\s*\d+', item_id))
                                           and paths and code_shaped(expected) and IDENTIFIER.fullmatch(expected))
                    add('K9', item_id+' '+raw, start, value=item_id, quote=expected,
                        contextual_reason='item_token_only' if token_only and not ordinal_mapping else '',
                        ordinal=ordinal or bool(re.fullmatch(r'#\d+|항목\s*\d+', item_id)))
        malformed_arithmetic = re.search(
            r'(?<![\w.])(?:(?P<total>,+)\s*건\s*중\s*(?P<part>\d[\d,]*)\s*건|'
            r'(?P<total_right>\d[\d,]*)\s*건\s*중\s*(?P<part_right>,+)\s*건)', line)
        if malformed_arithmetic:
            add('K7', malformed_arithmetic.group(), malformed_arithmetic.start(),
                value=malformed_arithmetic.group('total') or malformed_arithmetic.group('total_right'),
                entity=malformed_arithmetic.group('part') or malformed_arithmetic.group('part_right'),
                malformed_number=True)
        arithmetic = re.search(r'(\d[\d,]*)\s*건\s*중\s*(\d[\d,]*)\s*건', line)
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
