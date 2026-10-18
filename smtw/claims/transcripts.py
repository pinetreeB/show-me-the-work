from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import ast
from pathlib import Path
import posixpath
import re

from .model import Budget, Evidence, Transcript, line_counts
from .paths import matches_path, normalize_path, normalize_url
from .sources import content_lines, decode, file_lines


FILE_TOKEN = re.compile(r'''(?:[A-Za-z]:[\\/]|/|\.?\.?[/\\])?[^\s`"'<>;|{}=]+\.(?:md|txt|jsonl?|[cm]?js|tsx?|jsx|py|sh|ps1|toml|ya?ml|sql|docx|log|xml|csv|tsv)(?:@[\da-f]+)?''', re.I)
READ_COMMAND = re.compile(r'\b(?:Get-Content|Get-Item|Select-String|cat|type|head|tail|sed|rg|grep|wc|stat|git|readFileSync|readFile|read_text|read_bytes|open)\b', re.I)
WRITE_COMMAND = re.compile(r'\b(?:writeFileSync|writeFile|write_text|write_bytes|WriteAllText|WriteAllLines|Set-Content|Add-Content|Out-File|apply_patch|Remove-Item|Move-Item)\b|\bopen\s*\([^\n]+,[\s]*["\'][wax]|(?:^|[;&|])\s*(?:rm|mv|cp|tee)\s|(?<![2\d])>\s*[\w./\\]', re.I)
GENERATED = re.compile(r'\b(?:echo|Write-Output)\b|\bprint\s*\(|console\.log|stdout\.write', re.I)
GENERATED_LITERAL = re.compile(r'''(?:\bprint|console\.log|stdout\.write)\s*\(\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')\s*\)''', re.I)
TRUNCATED = re.compile(r'<truncated|\[.{0,12}truncated|output (?:has been )?truncated|Warning: truncated output|\.\.\.\s*\d+ (?:tokens|chars).*truncated', re.I)
NUMBERED = re.compile(r'^\s*(?:line\s+)?(\d+): ?(.*)$', re.I)
BROWSER_READ = re.compile(r'navigate|snapshot|fetch|(?:browser|mcp|page).*read|read.*(?:page|url)', re.I)
SEARCH_TOOL = re.compile(r'search|query|find', re.I)


def success_marker(command: str) -> tuple[str, str]:
    """Recognize only a literal, conditional trailing marker; never execute shell text."""
    literal = r'''(?:"([^"$`\r\n]*)"|'([^'\r\n]*)'|([\w.-]+))'''
    patterns = [r'\s*&&\s*(?:echo|Write-Output)\s+' + literal + r'\s*;?\s*$',
                r'\s*;\s*if\s*\(\s*\$\?\s*\)\s*\{\s*(?:echo|Write-Output)\s+' + literal + r'\s*;?\s*\}\s*$']
    for pattern in patterns:
        match = re.search(pattern, command, re.I)
        if match:
            marker = next(g for g in match.groups() if g is not None)
            if marker:
                return command[:match.start()], marker
    return command, ''


@dataclass
class Call:
    name: str
    args: dict
    order: int
    timestamp: str
    call_id: str = ''

    @property
    def command(self):
        return str(self.args.get('CommandLine') or self.args.get('command') or self.args.get('cmd') or '')

    @property
    def path(self):
        return str(self.args.get('AbsolutePath') or self.args.get('TargetFile')
                   or self.args.get('file_path') or self.args.get('path') or '')

    @property
    def url(self):
        return str(self.args.get('Url') or self.args.get('URL') or self.args.get('url') or '')

    @property
    def cwd(self):
        return str(self.args.get('Cwd') or self.args.get('cwd') or self.args.get('workdir') or '')

    @property
    def write(self):
        return bool(re.search(r'write|replace|edit|apply_patch|delete|move', self.name, re.I)
                    or WRITE_COMMAND.search(self.command))

    @property
    def read(self):
        if self.write:
            return False
        if self.url and re.search(r'web.*run|read_url|open_url', self.name, re.I):
            return True
        if BROWSER_READ.search(self.name) and not SEARCH_TOOL.search(self.name):
            return True
        command, marker = success_marker(self.command)
        if re.search(r'\b(?:echo|Write-Output|printf)\b', command, re.I):
            return False
        if GENERATED_LITERAL.search(command):
            return False
        if GENERATED.search(command) and not re.search(r'\b(?:readFileSync|readFile|read_text|read_bytes|open|Get-Content)\b', command):
            return False
        if re.search(r'view_file|read_file|read_url|open_url|grep|search|fetch', self.name, re.I):
            return True
        return bool(READ_COMMAND.search(self.command) or marker)


def call_paths(call: Call) -> list[str]:
    if call.path:
        return [call.path]
    paths = []
    command, _ = success_marker(call.command)
    for m in FILE_TOKEN.finditer(command):
        path = m.group().rstrip('),')
        if path not in paths:
            paths.append(path)
    return paths


def observed_path(value: str, cwd: str) -> str:
    path = normalize_path(value)
    if not path or path.startswith('/') or re.match(r'^[a-z]:/', path) or not cwd:
        return value
    return posixpath.normpath(normalize_path(cwd) + '/' + path)


def aux_root(path: Path) -> Path:
    if (path.parent / 'aux').is_dir():
        return path.parent / 'aux'
    return path.parent.parent


def pointer_path(pointer: str, root: Path) -> Path | None:
    pointer = pointer.strip(' `"\'.,').replace('\\', '/')
    match = re.search(r'(?:^|/)(steps/\d+/[^/\s]+|tasks/task-[\w-]+\.log)$', pointer)
    if not match:
        return None
    resolved = (root / match.group(1)).resolve()
    return resolved if resolved.is_relative_to(root.resolve()) else None


def text_value(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(text_value(x) for x in value)
    if isinstance(value, dict):
        return text_value(value.get('text', value.get('output', value.get('content', ''))))
    return ''


def unwrapped(value) -> list[tuple[str, int | None, int | None]]:
    """Decode only tool output containers, never assistant-provided call arguments."""
    if isinstance(value, list):
        answer = []
        for item in value:
            answer.extend(unwrapped(item))
        return answer
    if isinstance(value, dict):
        inner = value.get('value', value)
        if isinstance(inner, dict) and 'output' in inner:
            return [(text_value(inner['output']), inner.get('exit_code'), value.get('i'))]
        if isinstance(inner, dict) and ('url' in inner or 'pageUrl' in inner or 'isError' in inner or 'success' in inner):
            metadata = {k: v for k, v in inner.items() if k not in {'content', 'text'}}
            body = json.dumps(metadata, ensure_ascii=False) + '\n' + text_value(inner)
            return [(body, None, value.get('i'))]
        if inner is not value:
            return [(body, code, value.get('i', index)) for body, code, index in unwrapped(inner)]
        return unwrapped(value.get('text', value.get('content', '')))
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, (dict, list)):
            return unwrapped(parsed)
    except (ValueError, TypeError):
        pass
    # functions.exec can concatenate JSON records without newlines.
    decoder = json.JSONDecoder()
    records, index = [], 0
    while index < len(value):
        at = value.find('{', index)
        if at < 0:
            break
        try:
            parsed, size = decoder.raw_decode(value[at:])
        except ValueError:
            index = at + 1
            continue
        if isinstance(parsed, dict) and ('output' in parsed or 'value' in parsed):
            records.extend(unwrapped(parsed))
        index = at + size
    if records:
        return records
    if value.startswith('Script completed\nWall time') and value.rstrip().endswith('Output:'):
        return []
    exit_match = re.search(r'(?:Process exited with code|Exit code:)\s*(\d+)', value)
    body = re.split(r'\n(?:Final output:|Output:)\n', value, maxsplit=1)[-1]
    return [(body, int(exit_match.group(1)) if exit_match else None, None)]


def js_calls(code: str, order: int, timestamp: str) -> list[Call]:
    """Recognize literal exec_command calls without evaluating JavaScript."""
    calls = []
    string = r'"(?:\\.|[^"\\])*"'
    for m in re.finditer(r'tools\.(exec_command|shell_command)\s*\(\s*\{', code):
        # End at the next call, not at braces embedded in a shell string.
        end = code.find('tools.', m.end())
        fragment = code[m.end():end if end >= 0 else len(code)]
        args = {}
        for kv in re.finditer(r'\b(cmd|command|workdir|cwd)"?\s*:\s*(' + string + ')', fragment):
            try:
                args[kv.group(1)] = json.loads(kv.group(2))
            except ValueError:
                pass
        if args.get('cmd') or args.get('command'):
            calls.append((m.start(), Call(m.group(1), args, order, timestamp)))
    for m in re.finditer(r'tools\.(web__run|web_run|mcp__[A-Za-z0-9_]+)\s*\(\s*', code):
        try:
            args, _ = js_literal(code, m.end())
        except (ValueError, SyntaxError):
            continue
        if isinstance(args, dict):
            calls.append((m.start(), Call(m.group(1), args, order, timestamp)))
    return [call for _, call in sorted(calls, key=lambda item: item[0])]


def js_literal(code: str, index: int = 0):
    """Parse a data literal only; identifiers, interpolation and execution fail closed."""
    while index < len(code) and code[index].isspace():
        index += 1
    if index >= len(code):
        raise ValueError('missing literal')
    token = code[index]
    if token in {'"', "'"}:
        end = index + 1
        while end < len(code):
            if code[end] == '\\':
                end += 2
            elif code[end] == token:
                literal = code[index:end+1]
                return (json.loads(literal) if token == '"' else ast.literal_eval(literal)), end + 1
            else:
                end += 1
        raise ValueError('unterminated literal')
    if token in '{[':
        value = {} if token == '{' else []
        close = '}' if token == '{' else ']'
        index += 1
        while True:
            while index < len(code) and code[index].isspace():
                index += 1
            if index >= len(code):
                raise ValueError('unterminated collection')
            if code[index] == close:
                return value, index+1
            key = None
            if token == '{':
                if code[index] in {'"', "'"}:
                    key, index = js_literal(code, index)
                else:
                    match = re.match(r'[A-Za-z_$][\w$]*', code[index:])
                    if not match:
                        raise ValueError('not a data key')
                    key, index = match.group(), index + match.end()
                while index < len(code) and code[index].isspace():
                    index += 1
                if index >= len(code) or code[index] != ':':
                    raise ValueError('not a data property')
                index += 1
            item, index = js_literal(code, index)
            if token == '{':
                value[key] = item
            else:
                value.append(item)
            while index < len(code) and code[index].isspace():
                index += 1
            if index < len(code) and code[index] == ',':
                index += 1
            elif index >= len(code) or code[index] != close:
                raise ValueError('not a data collection')
    match = re.match(r'true\b|false\b|null\b|-?\d+(?:\.\d+)?', code[index:])
    if not match:
        raise ValueError('dynamic expression')
    return json.loads(match.group()), index + match.end()


def js_writes(code: str, order: int, timestamp: str) -> list[Call]:
    calls = []
    for match in re.finditer(r'tools\.apply_patch\s*\(\s*("(?:\\.|[^"\\])*")', code):
        try:
            calls.append(Call('apply_patch', {'input': json.loads(match.group(1))}, order, timestamp))
        except ValueError:
            pass
    return calls


def parse_result(call: Call, body: str, origin: str, root: Path,
                 budget: Budget, exit_code: int | None = None) -> list[Evidence]:
    if re.search(r'web.*run', call.name, re.I) and isinstance(call.args.get('open'), list):
        observations = []
        requests = call.args['open']
        headings = list(re.finditer(r'^.*?\((https?://[^)\s]+)\)\s*$', body, re.M))
        for request in call.args['open']:
            url = request.get('ref_id', '') if isinstance(request, dict) else ''
            if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
                continue
            # The result itself must identify the requested page, not just search hits.
            if url not in body:
                observations.append(Evidence(url=url, tool=call.name, origin=origin,
                                             order=call.order, problem='웹 결과의 URL 연결 미확인'))
                continue
            content = body
            if len(requests) > 1:
                section = next((i for i, h in enumerate(headings) if normalize_url(h.group(1)) == normalize_url(url)), None)
                if section is None:
                    observations.append(Evidence(url=url, tool=call.name, origin=origin,
                                                 order=call.order, problem='웹 결과별 본문 분리 미확인'))
                    continue
                content = body[headings[section].start():headings[section+1].start() if section+1 < len(headings) else len(body)]
            observations += parse_result(Call('read_url_content', {'Url': url}, call.order, call.timestamp),
                                         content, origin, root, budget, exit_code)
        return observations
    if not call.read:
        return []
    budget.check()
    command, marker = success_marker(call.command)
    observed_marker = ''
    if exit_code is None:
        status = re.search(r'(?:Process exited with code|Exit code:|exited with code)\s*(-?\d+)', body, re.I)
        if status:
            exit_code = int(status.group(1))
    if marker:
        # A success marker is control data, never quoted file content.
        rows = body.splitlines(keepends=True)
        markers = [i for i, row in enumerate(rows) if row.strip('\r\n') == marker]
        if markers:
            observed_marker = marker
            rows.pop(markers[-1])
            body = ''.join(rows)
    if BROWSER_READ.search(call.name) and not SEARCH_TOOL.search(call.name) and not re.search(r'read_url|open_url', call.name, re.I):
        urls = re.findall(r'(?:["\']?(?:URL|pageUrl|page_url|currentUrl)["\']?\s*[:=]\s*["\']?|^\s*[-*]?\s*Page URL:\s*)(https?://[^\s"\'<>]+)', body, re.I | re.M)
        if not urls:
            urls = re.findall(r'https?://[^\s"\'<>]+', body)
        urls = list(dict.fromkeys(u.rstrip('.,;)]}') for u in urls))
        if call.url:
            urls = [u for u in urls if normalize_url(u) == normalize_url(call.url)]
        if len(urls) != 1:
            return []
        e_url = urls[0]
    else:
        e_url = '' if SEARCH_TOOL.search(call.name) else call.url
    paths = call_paths(call)
    e = Evidence(path=call.path, url=e_url, command=call.command, cwd=call.cwd,
                 tool=call.name, origin=origin, order=call.order, timestamp=call.timestamp,
                 conditional_marker=marker, success_marker=observed_marker,
                 control_only=bool(marker and not READ_COMMAND.search(command)),
                 exit_code=0 if observed_marker and exit_code is None else exit_code)
    if exit_code not in (None, 0) or re.search(r'^(?:Error:|Error executing|File not found|Access denied)|"isError"\s*:\s*true|"success"\s*:\s*false|"status"\s*:\s*"(?:ERROR|FAILED)"', body, re.M | re.I):
        e.problem = '읽기 도구 실패'
        e.problem_code = 'read_failed'
    if TRUNCATED.search(body):
        e.problem = '잘린 도구 출력'
        e.problem_code = 'partial_range'
    path_header = re.search(r'^File Path:\s*`?([^\r\n`]+)', body, re.M)
    if path_header:
        actual = path_header.group(1).strip()
        if call.path and normalize_path(call.path) != normalize_path(actual):
            e.problem = '호출 경로와 결과 경로 불일치'
            e.problem_code = 'binding_ambiguous'
        e.path = actual
    if call.url:
        observed_url = re.search(r'^(?:Source|URL):\s*(https?://\S+)', body, re.M | re.I)
        if observed_url and normalize_url(observed_url.group(1)) != normalize_url(call.url):
            e.problem = '호출 URL과 결과 URL 불일치'
            e.problem_code = 'binding_ambiguous'
    for attr, header in [('total_lines', 'Total Lines'), ('total_bytes', 'Total Bytes')]:
        m = re.search(header + r':\s*([\d,]+)', body)
        if m:
            setattr(e, attr, int(m.group(1).replace(',', '')))
    shown = re.search(r'Showing lines (\d+) to (\d+)', body)
    numbered = {int(m.group(1)): m.group(2) for line in body.splitlines() if (m := NUMBERED.match(line))}
    if path_header or ('view_file' in call.name and numbered):
        e.lines = numbered
        if e.total_lines is not None:
            # Some exporters omit the final empty numbered line but retain Total Lines.
            if shown and int(shown.group(2)) == e.total_lines and len(numbered) == e.total_lines - 1:
                candidate = '\n'.join(e.lines.values()) + '\n'
                if e.total_bytes in {len(candidate.encode('utf-8')), len(candidate.replace('\n', '\r\n').encode('utf-8'))}:
                    e.lines.setdefault(e.total_lines, '')
            e.complete = set(e.lines) == set(range(1, e.total_lines + 1))
            if e.complete:
                text = '\n'.join(e.lines.values())
                e.counts = line_counts(text)
                e.counts['L_view'] = e.total_lines
            if e.total_lines in e.lines and not e.lines[e.total_lines].strip():
                e.counts.update(L_lines=max(0, e.total_lines - 1), L_view=e.total_lines)
        e.text = '\n'.join(e.lines.values())
    elif call.command and numbered and len(paths) == 1:
        e.path, e.lines = paths[0], numbered
        e.text = '\n'.join(numbered.values())
        e.complete = False  # Optional line N: form is always a partial read.
    else:
        e.text = body
        e.complete = not e.problem
        if call.command and len(paths) == 1:
            e.path = paths[0]
            if re.search(r'\b(?:cat|Get-Content|readFileSync|read_text|read_bytes)\b', command, re.I) and not re.search(r'\b(?:Select-Object|head|tail|slice|split|sed|rg|grep|Measure-Object)\b|\.(?:Count|Length)\b', command, re.I):
                e.lines = dict(enumerate(body.splitlines(), 1))
                e.counts = line_counts(body)
                e.total_lines = e.counts['L_view']
            else:
                e.complete = False
    # Multi-file print loops are accepted only with explicit per-file output headings.
    sections = list(re.finditer(r'^FILE\s+(.+)$', body, re.M))
    if len(paths) > 1 and sections:
        result = []
        for i, section in enumerate(sections):
            path = section.group(1).strip()
            if not any(matches_path(p, path) or matches_path(path, p) for p in paths):
                continue
            text = body[section.end():sections[i+1].start() if i+1 < len(sections) else len(body)].lstrip('\r\n')
            sub = Evidence(path=path, text=text, lines=dict(enumerate(text.splitlines(), 1)),
                           complete=not e.problem, problem=e.problem, problem_code=e.problem_code, tool=call.name,
                           origin=origin, command=call.command, cwd=call.cwd,
                           order=call.order, timestamp=call.timestamp)
            result.append(sub)
        if result:
            return result
    pointer_values = re.findall(r'(?:has been saved to:|Task logs are available at:)\s*([^\r\n]+)', body, re.I)
    if not pointer_values and 'output.txt' in body:
        # Do not backtrack a greedy path pattern from every slash in a long web
        # line. First select output-file tokens, then validate their small suffix.
        for line in body.splitlines():
            if 'output.txt' not in line or not re.search(r'steps[/\\]\d+[/\\]output\.txt', line):
                continue
            pointer_values.extend(token.strip('`"') for token in line.split()
                                  if 'output.txt' in token and re.search(r'steps[/\\]\d+[/\\]output\.txt', token))
    if pointer_values:
        target = pointer_path(pointer_values[0], root)
        if target is None or not target.is_file():
            e.problem = '보조 파일 포인터 없음'
            e.problem_code = 'pointer_missing'
            e.complete = False
        else:
            e.aux_path = str(target)
            e.text, e.lines = '', {}
            task = re.search(r'task-([\w-]+)\.log$', target.name)
            if task:
                e.task_id = task.group(1)
                e.complete = False
    elif re.search(r'background task|status[:\s]+RUNNING', body, re.I):
        task = re.search(r'task-(\d+)', body)
        if task:
            target = root / 'tasks' / f'task-{task.group(1)}.log'
            e.task_id = task.group(1)
            e.aux_path = str(target) if target.is_file() else ''
        e.complete = False
        e.problem = '비동기 완료 정보 미확보'
        e.problem_code = 'async_incomplete'
    if call.command:
        revision = re.search(r'\bgit\s+(?:-C\s+\S+\s+)?show\s+([0-9a-f]{7,40}):([^\s;"\']+)', call.command, re.I)
        if revision:
            e.revision, e.path = revision.group(1), revision.group(2)
        if len(paths) == 1 and not e.problem:
            measured = []
            for line in body.splitlines():
                value = re.fullmatch(r'\s*(\d+)(?:\s+(\S+))?\s*', line)
                if value and (not value.group(2) or matches_path(paths[0], value.group(2)) or matches_path(value.group(2), paths[0])):
                    measured.append(int(value.group(1)))
            if len(measured) == 1:
                if re.search(r'\bGet-Content\b', command, re.I) and re.search(r'\.(?:Count|Length)\b|Measure-Object\s+-Line', command, re.I):
                    e.counts['L_lines'] = measured[0]
                elif re.search(r'\bwc\s+-l\b', command):
                    e.counts['L_nl'] = measured[0]
                elif re.search(r'\bwc\s+-c\b|\bstat\s+[^;\n]*%s|\bGet-Item\b[^;\n]*\.Length', call.command, re.I):
                    e.total_bytes = measured[0]
    return [e]


def read_transcript(path: Path, kind: str, report: Path, report_text: str,
                    aliases: dict[str, str], budget: Budget) -> Transcript:
    result = Transcript(version=f'{kind}/jsonl-v1')
    root = aux_root(path)
    pending: deque[Call] = deque()
    codex_calls: dict[str, list[Call]] = {}
    writes: dict[str, int] = {}
    report_targets = {normalize_path(str(report), aliases)}
    report_writes: list[int] = []
    completions: dict[str, int] = {}
    recognized = 0
    conversation_cwd = ''

    def register(call):
        if not call.write:
            return
        for target in call_paths(call):
            writes.setdefault(normalize_path(observed_path(target, call.cwd), aliases), call.order)
        content = str(call.args.get('CodeContent') or call.args.get('content') or '')
        if call.path and (normalize_path(observed_path(call.path, call.cwd), aliases) in report_targets
                          or content and content == report_text):
            report_targets.add(normalize_path(observed_path(call.path, call.cwd), aliases))
            report_writes.append(call.order)
        # apply_patch is never evidence, but its path headers mark future self-reads.
        patch = str(call.args.get('patch', call.args.get('input', '')))
        for target in re.findall(r'^\*\*\* (?:Add|Update|Delete) File: (.+)$', patch, re.M):
            writes.setdefault(normalize_path(observed_path(target, call.cwd), aliases), call.order)

    def accept(call, body, n, exit_code=None):
        observations = parse_result(call, body, f'{path}:{n}', root, budget, exit_code)
        for e in observations:
            e.path = observed_path(e.path, call.cwd)
            normalized = normalize_path(e.path, aliases)
            if normalized in report_targets or normalized in writes and writes[normalized] <= call.order:
                continue
            result.evidence.append(e)

    with path.open('rb') as stream:
        for n, raw in enumerate(stream, 1):
            budget.check()
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, ValueError):
                result.valid = False
                result.warnings.append(f'전사 형식 오류: {path}:{n}')
                continue
            if not isinstance(row, dict):
                result.valid = False
                continue
            stamp = str(row.get('created_at', row.get('timestamp', '')))
            if kind == 'agy':
                typ = row.get('type')
                if typ == 'PLANNER_RESPONSE':
                    recognized += 1
                    if pending:
                        result.valid = False
                        pending.clear()
                    for tool in row.get('tool_calls', []):
                        if not isinstance(tool, dict) or not isinstance(tool.get('args', {}), dict):
                            result.valid = False
                            continue
                        call = Call(str(tool.get('name', '')), tool.get('args', {}), n, stamp)
                        register(call)
                        pending.append(call)
                elif typ == 'GENERIC' and pending:
                    call = pending.popleft()
                    code = 1 if row.get('status') in {'ERROR', 'FAILED'} else None
                    accept(call, text_value(row.get('content', '')), n, code)
                elif typ == 'SYSTEM_MESSAGE':
                    # System content supplies control metadata ONLY, never evidence text.
                    body = text_value(row.get('content', ''))
                    task = re.search(r'Task id\s+(?:task-)?([\w-]+).*?finished with result:.*?exited with code\s+(\d+)', body, re.S | re.I)
                    if task:
                        completions[task.group(1)] = int(task.group(2))
            else:
                if row.get('type') in {'turn_context', 'session_meta'} and isinstance(row.get('payload'), dict):
                    value = row['payload'].get('cwd')
                    if isinstance(value, str):
                        conversation_cwd = value
                if row.get('type') != 'response_item':
                    continue
                item = row.get('payload', {})
                if not isinstance(item, dict):
                    continue
                typ = item.get('type')
                if typ in {'function_call', 'custom_tool_call'}:
                    recognized += 1
                    name = str(item.get('name', ''))
                    args = item.get('arguments', item.get('input', {}))
                    if name.rsplit('.', 1)[-1] == 'exec' and isinstance(args, str):
                        calls = js_calls(args, n, stamp)
                        for write in js_writes(args, n, stamp):
                            write.args.setdefault('cwd', conversation_cwd)
                            register(write)
                    else:
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except ValueError:
                                args = {'input': args}
                        calls = [Call(name, args if isinstance(args, dict) else {}, n, stamp)]
                    codex_calls[str(item.get('call_id', ''))] = calls
                    for call in calls:
                        if not call.cwd:
                            call.args['cwd'] = conversation_cwd
                        register(call)
                elif typ in {'function_call_output', 'custom_tool_call_output'}:
                    calls = codex_calls.pop(str(item.get('call_id', '')), [])
                    outputs = unwrapped(item.get('output', ''))
                    for i, (body, code, output_index) in enumerate(outputs):
                        selected = output_index if output_index is not None else i
                        if len(calls) == len(outputs) or output_index is not None and selected < len(calls):
                            accept(calls[selected], body, n, code)
                        elif len(calls) == 1:
                            accept(calls[0], body, n, code)
                        else:
                            result.warnings.append(f'복합 도구 결과 연결 미확인: {path}:{n}')
    cutoff = max(report_writes, default=10**18)
    filtered = []
    for e in result.evidence:
        if normalize_path(e.path, aliases) in report_targets:
            continue
        e.after_report = e.order > cutoff
        if e.task_id:
            code = completions.get(e.task_id)
            e.exit_code = code
            if code == 0 and e.aux_path:
                e.complete = True
                if e.problem == '비동기 완료 정보 미확보':
                    e.problem = ''
                    e.problem_code = ''
            else:
                e.problem = '비동기 완료 정보 미확보' if code is None else '비동기 명령 실패 또는 로그 없음'
                e.problem_code = 'async_incomplete' if code is None else 'read_failed'
        filtered.append(e)
    result.evidence = filtered
    if not recognized:
        result.valid = False
        result.warnings.append(f'지원하는 {kind} 도구 호출 없음 (형식 {result.version})')
    return result


def load_view_pointer(e: Evidence, budget: Budget) -> None:
    """Load a linked view-file export only when a file claim actually needs it."""
    if e.view_loaded or not e.aux_path or 'view_file' not in e.tool or e.problem:
        return
    lines = {}
    for _, text in file_lines(Path(e.aux_path), budget):
        header = re.match(r'File Path:\s*`?([^`\r\n]+)', text)
        if header and normalize_path(header.group(1)) != normalize_path(e.path):
            e.problem, e.problem_code = '보조 view_file 경로 불일치', 'binding_ambiguous'
        for attr, label in [('total_lines', 'Total Lines'), ('total_bytes', 'Total Bytes')]:
            value = re.match(label + r':\s*([\d,]+)', text)
            if value:
                setattr(e, attr, int(value.group(1).replace(',', '')))
        numbered = NUMBERED.match(text)
        if numbered:
            lines[int(numbered.group(1))] = numbered.group(2)
        if TRUNCATED.search(text):
            e.problem, e.problem_code = '잘린 보조 view_file 출력', 'partial_range'
    e.lines = dict(sorted(lines.items()))
    e.complete = not e.problem and e.total_lines is not None and set(lines) == set(range(1, e.total_lines+1))
    if e.complete:
        e.counts = line_counts('\n'.join(e.lines.values()))
        e.counts['L_view'] = e.total_lines
    if e.total_lines in lines and not lines[e.total_lines].strip():
        e.counts.update(L_lines=max(0, e.total_lines-1), L_view=e.total_lines)
    e.view_loaded = True


def load_measurement_pointer(e: Evidence, budget: Budget) -> None:
    if e.measure_loaded or not e.aux_path or not e.complete or e.problem:
        return
    command, _ = success_marker(e.command)
    kind = ('L_nl' if re.search(r'\bwc\s+-l\b', command) else
            'L_lines' if re.search(r'\bGet-Content\b', command, re.I) and re.search(r'\.(?:Count|Length)\b|Measure-Object\s+-Line', command, re.I) else
            'bytes' if re.search(r'\bwc\s+-c\b|\bstat\s+[^;\n]*%s|\bGet-Item\b[^;\n]*\.Length', command, re.I) else '')
    if not kind:
        return
    measured = []
    for _, text in content_lines(e, budget):
        match = re.fullmatch(r'\s*(\d+)(?:\s+(\S+))?\s*', text)
        if match and (not match.group(2) or matches_path(match.group(2), e.path) or matches_path(e.path, match.group(2))):
            measured.append(int(match.group(1)))
    if len(measured) == 1:
        if kind == 'bytes':
            e.total_bytes = measured[0]
        else:
            e.counts[kind] = measured[0]
    e.measure_loaded = True


def resolve_agy(value: str, brain_root: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_file() or path.suffix.lower() == '.jsonl':
        return path
    return brain_root.expanduser() / value / '.system_generated' / 'logs' / 'transcript_full.jsonl'


def discover_agy(report: Path, report_text: str, brain_root: Path,
                 aliases: dict[str, str], budget: Budget) -> list[Path]:
    """Find exact last-write matches; never select among ambiguous conversations."""
    candidates = []
    target = normalize_path(str(report), aliases)
    for transcript in brain_root.expanduser().glob('*/.system_generated/logs/transcript_full.jsonl'):
        budget.check()
        last_content = None
        pending = deque()
        try:
            with transcript.open('rb') as stream:
                for raw in stream:
                    budget.check()
                    row = json.loads(raw)
                    if row.get('type') == 'PLANNER_RESPONSE':
                        pending.clear()
                        pending.extend(row.get('tool_calls', []))
                    elif row.get('type') == 'GENERIC' and pending:
                        tool = pending.popleft()
                        args = tool.get('args', {})
                        path = args.get('TargetFile', args.get('AbsolutePath', ''))
                        if not isinstance(path, str) or normalize_path(path, aliases) != target:
                            continue
                        if not re.search(r'write|replace|edit', str(tool.get('name', '')), re.I):
                            continue
                        if row.get('status') != 'DONE' or re.search(r'^Error', text_value(row.get('content')), re.M):
                            continue
                        # A final partial edit cannot prove byte equality with the report.
                        last_content = args.get('CodeContent') if 'write' in tool.get('name', '') else None
        except (OSError, UnicodeDecodeError, ValueError, AttributeError, TypeError):
            continue
        if last_content == report_text:
            candidates.append(transcript)
    return candidates
