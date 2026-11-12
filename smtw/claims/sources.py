from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
import ast
from pathlib import Path
import re
import subprocess
import zipfile
from xml.etree import ElementTree as ET

from .model import Budget, Evidence, line_counts
from .paths import matches_path, normalize_path


TEXT_SUFFIXES = {'.md', '.txt', '.json', '.jsonl', '.tsv', '.csv', '.py', '.js',
                 '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.sh', '.ps1', '.toml',
                 '.yaml', '.yml', '.xml', '.html', '.css', '.sql', '.go', '.rs',
                 '.java', '.c', '.h', '.cpp', '.cs', '.log', '.ini', '.cfg', '.bat'}
DOCUMENT_SUFFIXES = {'.md', '.txt', '.log', '.csv', '.tsv', '.docx'}
CODE_SUFFIXES = {'.py', '.pyi', '.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx',
                 '.sh', '.ps1', '.bat', '.sql', '.go', '.rs', '.java', '.c', '.h',
                 '.cc', '.cpp', '.hpp', '.cs', '.rb', '.php', '.swift', '.kt',
                 '.kts', '.scala', '.lua', '.pl', '.r'}


def code_path(path: str) -> bool:
    parts = path.replace(chr(92), '/').casefold().split('/')
    return Path(parts[-1]).suffix in CODE_SUFFIXES and not {'docs', 'reviews'}.intersection(parts[:-1])



def decode(data: bytes) -> str:
    for encoding in ('utf-8-sig', 'cp949'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode('utf-8', errors='replace')


def file_lines(path: Path, budget: Budget) -> Iterator[tuple[int, str]]:
    # Decode per line so a damaged line does not poison unrelated observations.
    with path.open('rb') as stream:
        for n, raw in enumerate(stream, 1):
            budget.check()
            yield n, decode(raw).rstrip('\r\n')


def content_lines(e: Evidence, budget: Budget) -> Iterator[tuple[int, str]]:
    if e.lines:
        for n, text in e.lines.items():
            budget.check()
            yield n, text
    elif e.aux_path:
        pending = []
        for n, text in file_lines(Path(e.aux_path), budget):
            if e.conditional_marker and text == e.conditional_marker:
                yield from pending
                pending = [(n, text)]
            elif pending and (not text.strip() or re.search(r'(?:Exit code:|exited with code)\s*-?\d+', text, re.I)):
                pending.append((n, text))
            else:
                yield from pending
                pending = []
                yield n, text
        # A trailing conditional marker and exit footer are control data.
    else:
        for n, text in enumerate(e.text.splitlines(), 1):
            budget.check()
            yield n, text


def raw_evidence(data: bytes, **kwargs) -> Evidence:
    text = decode(data)
    counts = line_counts(text)
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n') if text else []
    return Evidence(text=text, lines=dict(enumerate(lines, 1)), complete=True,
                    counts=counts, total_lines=counts['L_view'], total_bytes=len(data), **kwargs)


def local_source(path: Path, name: str, budget: Budget) -> Evidence:
    budget.check()
    if not path.is_file():
        return Evidence(path=name, origin=str(path), problem='기준 원문 파일 없음', problem_code='no_read_of_file')
    suffix = path.suffix.lower()
    # Snapshot names can append @revision without changing their content format.
    suffix = Path(path.name.split('@', 1)[0]).suffix.lower()
    if suffix == '.docx':
        return docx_source(path, name, budget)
    if suffix not in TEXT_SUFFIXES:
        return Evidence(path=name, origin=str(path), problem='미지원 원문 형식', problem_code='unsupported_format')
    return raw_evidence(path.read_bytes(), path=name, origin=str(path), tool='source-file')


def docx_source(path: Path, name: str, budget: Budget) -> Evidence:
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    q = '{' + ns['w'] + '}'
    e = Evidence(path=name, origin=str(path), tool='source-file/docx', complete=True)
    try:
        with zipfile.ZipFile(path) as archive:
            budget.check()
            doc = ET.fromstring(archive.read('word/document.xml'))
            e.unsupported = any(re.match(r'word/(?:footnotes|endnotes|header|footer)', n)
                                for n in archive.namelist())
            e.unsupported |= any(doc.find('.//w:' + tag, ns) is not None
                                 for tag in ('txbxContent', 'ins', 'del', 'moveFrom', 'moveTo'))
            body = doc.find('w:body', ns)
            if body is None:
                e.problem = 'docx 본문 없음'
                e.problem_code = 'unsupported_format'
                return e
            def paragraph_text(node):
                if node.tag in {q+t for t in ('txbxContent', 'ins', 'del', 'moveFrom', 'moveTo')}:
                    return ''
                return (node.text or '') if node.tag == q+'t' else ''.join(paragraph_text(c) for c in node)
            paragraphs = tables = 0
            for child in body:
                budget.check()
                if child.tag == q+'p':
                    paragraphs += 1
                    e.lines[len(e.lines)+1] = paragraph_text(child)
                    e.locations[len(e.lines)] = f'문단 {paragraphs}'
                elif child.tag == q+'tbl':
                    tables += 1
                    for r, row in enumerate(child.findall('w:tr', ns), 1):
                        for c, cell in enumerate(row.findall('w:tc', ns), 1):
                            e.lines[len(e.lines)+1] = paragraph_text(cell)
                            e.locations[len(e.lines)] = f'표 {tables} 행 {r} 셀 {c}'
            e.text = '\n'.join(e.lines.values())
    except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        e.problem = f'docx 읽기 실패: {type(exc).__name__}'
        e.problem_code = 'read_failed'
        e.complete = False
    return e


class Repositories:
    def __init__(self, roots: list[str], commits: dict[str, str], aliases: dict[str, str], budget: Budget):
        self.roots = [Path(p).resolve() for p in dict.fromkeys([*roots, *commits])]
        self.scope_roots = list(dict.fromkeys(Path(p).resolve() for p in roots))
        self.commits = {normalize_path(str(Path(k).resolve())): v for k, v in commits.items()}
        self.aliases, self.budget = aliases, budget
        self.cache: dict[tuple, object] = {}

    def git(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        self.budget.check()
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                              timeout=self.budget.remaining())

    def files(self, root: Path, revision: str) -> list[str] | None:
        key = ('files', str(root), revision)
        if key not in self.cache:
            result = self.git(root, 'ls-tree', '-r', '--name-only', '-z', revision)
            self.cache[key] = decode(result.stdout).split('\0') if result.returncode == 0 else None
            by_basename = {}
            for name in self.cache[key] or []:
                by_basename.setdefault(name.rsplit('/', 1)[-1].casefold(), []).append(name)
            self.cache[('basenames', str(root), revision)] = by_basename
        return self.cache[key]

    def observations(self, path: str, *, fixed: bool) -> list[Evidence]:
        result = []
        for root in self.roots:
            revision = self.commits.get(normalize_path(str(root))) if fixed else 'HEAD'
            if not revision:
                continue
            names = self.files(root, revision)
            if names is None:
                result.append(Evidence(path=path, origin=str(root), revision=revision,
                                       problem='저장소 revision 조회 실패', problem_code='revision_unresolved'))
                continue
            basename = normalize_path(path, self.aliases).rsplit('/', 1)[-1]
            for name in self.cache[('basenames', str(root), revision)].get(basename, []):
                if not name or not (matches_path(path, str(root / name), self.aliases)
                                    or matches_path(path, name, self.aliases)):
                    continue
                key = ('show', str(root), revision, name)
                if key not in self.cache:
                    value = self.git(root, 'show', f'{revision}:{name}')
                    if value.returncode:
                        e = Evidence(path=str(root / name), problem='git show 읽기 실패', problem_code='read_failed')
                    else:
                        e = raw_evidence(value.stdout, path=str(root / name))
                    e.origin = f'{root}@{revision}:{name}'
                    e.revision = revision
                    e.tool = '기준 commit 관측' if fixed else '현재 상태 관측'
                    self.cache[key] = e
                result.append(self.cache[key])
        return result

    def scope(self, contexts: tuple[str, ...]) -> list[Path]:
        """Explicit repository paths/names only; CLI availability is not report scope."""
        for context in contexts:
            text = context.replace('\\', '/').casefold()
            for alias, actual in self.aliases.items():
                text = text.replace(alias.replace('\\', '/').casefold(), actual.replace('\\', '/').casefold())
            quoted = {normalize_path(value, self.aliases) for value in re.findall(r'`([^`]+)`', context)}
            names = {normalize_path(str(root)) for root in self.scope_roots} | {root.name.casefold() for root in self.scope_roots}
            found = []
            recognized_cues = []
            for root in self.scope_roots:
                full, name = normalize_path(str(root)), root.name.casefold()
                full_mention = re.search(r'(?<![\w./-])' + re.escape(full) + r'(?![\w./-])', text)
                cue = (r'(?:\brepo(?:sitory)?\b|저장소)\s*[:=]?\s*["\x27]?' + re.escape(name) + r'(?![\w./-])|'
                       r'(?<![\w./-])' + re.escape(name) + r'["\x27]?\s*(?:저장소|\brepo(?:sitory)?\b)')
                if full in quoted or name in quoted or full_mention or re.search(cue, text):
                    found.append(root)
                    recognized_cues.extend((m.start(), m.end()) for m in re.finditer(cue, text))
            remainder = list(text)
            for start, end in recognized_cues:
                remainder[start:end] = ' ' * (end-start)
            declarations = re.findall(r'(?:\brepo(?:sitory)?\b|저장소)\s*[:=]?\s+(?:`([^`]+)`|"([^"]+)"|([^\s,;|]+))', ''.join(remainder), re.I)
            if any(normalize_path(next(value for value in groups if value), self.aliases) not in names for groups in declarations):
                return []
            if found:
                return found
        return []

    def commit(self, sha: str, roots: list[Path] | None = None,
               object_kind: str = '') -> tuple[str, str, dict]:
        answers = []
        kinds = set()
        selected = self.roots if roots is None else roots
        for root in selected:
            key = ('sha', str(root), sha)
            if key not in self.cache:
                valid = self.git(root, 'rev-parse', '--git-dir')
                if valid.returncode:
                    self.cache[key] = ('unverifiable', '저장소 조회 실패')
                else:
                    ids = self.git(root, 'rev-parse', f'--disambiguate={sha}').stdout.splitlines()
                    if len(ids) > 1:
                        self.cache[key] = ('unverifiable', '짧은 SHA가 여러 객체에 대응')
                    elif not ids:
                        self.cache[key] = ('absent', '저장소 객체 범위에 SHA 없음')
                    else:
                        obj = self.git(root, 'cat-file', '-t', sha)
                        if obj.returncode == 0:
                            self.cache[('object_kind', str(root), sha)] = decode(obj.stdout).strip()
                        self.cache[key] = (('match', 'commit 객체 확인') if obj.returncode == 0 and obj.stdout.strip() == b'commit'
                                           else ('mismatch', 'commit 아닌 객체') if obj.returncode == 0
                                           else ('unverifiable', '객체 종류 조회 실패'))
            answer = self.cache[key]
            actual = self.cache.get(('object_kind', str(root), sha), '')
            if actual:
                kinds.add(actual)
                expected = object_kind or 'commit'
                if actual == expected:
                    answer = ('match', f'{expected} 객체 확인')
                elif object_kind:
                    answer = ('mismatch', f'{expected} 아닌 {actual} 객체')
                else:
                    answer = ('unverifiable', 'commit 아닌 객체의 종류가 보고서에 명시되지 않음')
            answers.append(answer)
        source = {'search_scope': [str(r) for r in selected], 'tool': 'git cat-file -t'}
        if object_kind in {'tree', 'blob'} or kinds - {'commit'}:
            source.update(object_kind=sorted(kinds), requested_object_kind=object_kind)
        if not answers:
            return 'unverifiable', '조회할 저장소 없음', source
        if any(v == 'unverifiable' for v, _ in answers):
            if not object_kind and kinds - {'commit'}:
                return 'unverifiable', 'commit 아닌 객체의 종류가 보고서에 명시되지 않음', source
            return 'unverifiable', '저장소 조회 실패 또는 모호한 SHA', source
        if any(v == 'match' for v, _ in answers):
            return 'match', f'후보 저장소에서 {object_kind or "commit"} 객체 확인', source
        if any(v == 'mismatch' for v, _ in answers):
            return next((v, reason, source) for v, reason in answers if v == 'mismatch')
        return answers[0][0], answers[0][1], source

    def preload_commits(self, shas: list[str]) -> None:
        """One git process per repository for all requested object identities."""
        shas = list(dict.fromkeys(shas))
        if not shas:
            return
        for root in self.roots:
            self.budget.check()
            result = subprocess.run(['git', '-C', str(root), 'cat-file', '--batch-check=%(objectname) %(objecttype)'],
                                    input=('\n'.join(shas)+'\n').encode('ascii'), capture_output=True,
                                    timeout=self.budget.remaining())
            rows = decode(result.stdout).splitlines()
            for index, sha in enumerate(shas):
                if result.returncode or index >= len(rows):
                    answer = ('unverifiable', '저장소 객체 조회 실패')
                else:
                    kind = rows[index].rsplit(' ', 1)[-1]
                    if kind in {'commit', 'tree', 'blob', 'tag'}:
                        self.cache[('object_kind', str(root), sha)] = kind
                    answer = (('match', 'commit 객체 확인') if kind == 'commit'
                              else ('absent', '저장소 객체 범위에 SHA 없음') if kind == 'missing'
                              else ('unverifiable', '짧은 SHA가 여러 객체에 대응') if kind == 'ambiguous'
                              else ('mismatch', 'commit 아닌 객체'))
                self.cache[('sha', str(root), sha)] = answer

    @staticmethod
    def _identifier_hit(path: str, line_number: int, line: str, token: str,
                        revision: str, word: re.Pattern, definition: re.Pattern,
                        suffix: str) -> dict | None:
        if not code_path(path):
            return None
        stripped = line.lstrip()
        if stripped.startswith(('#', '//', '/*', '*', '<!--', ';')):
            return None
        if token not in line or not word.search(line):
            return None
        is_definition = bool(definition.search(line))
        if not is_definition and not re.search(r'[=(){}\[\];:]|\b(?:return|import|from|new|if|for|while)\b', line):
            return None
        return {'path': path, 'line': line_number, 'revision': revision,
                'kind': 'definition' if is_definition else 'mention'}

    def _mask_noncode(self, text: str, suffix: str, *, legacy_keys: bool = False) -> list[str]:
        """Mask strings and comments while preserving source line positions."""
        python_like = suffix in {'.py', '.sh', '.ps1', '.yaml', '.yml', '.toml'}
        slash_comments = suffix in {'.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.java',
                                    '.c', '.h', '.cpp', '.cs', '.go', '.rs', '.css'}
        result = list(text)
        quote = ''
        block_comment = False
        escaped = False
        index = 0
        next_budget_check = 0
        while index < len(text):
            if index >= next_budget_check:
                self.budget.check()
                next_budget_check = index + 65536
            if block_comment:
                if text.startswith('*/', index):
                    result[index:index+2] = '  '
                    block_comment = False
                    index += 2
                else:
                    if text[index] != '\n':
                        result[index] = ' '
                    index += 1
                continue
            if quote:
                if quote in {'\"\"\"', "'''"} and text.startswith(quote, index):
                    result[index:index+3] = '   '
                    quote = ''
                    index += 3
                    continue
                char = text[index]
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif quote in {'\"', "'", '`'} and char == quote:
                    quote = ''
                if char != '\n':
                    result[index] = ' '
                index += 1
                continue
            if python_like and text.startswith(('\"\"\"', "'''"), index):
                quote = text[index:index+3]
                result[index:index+3] = '   '
                index += 3
                continue
            if slash_comments and text.startswith('/*', index):
                block_comment = True
                result[index:index+2] = '  '
                index += 2
                continue
            # Preserve the pre-p4-1 lookup's key-shaped code mentions only in
            # its own cache. They are never evidence for the new definition path.
            if legacy_keys and text[index] in {'\"', "'"}:
                delimiter = text[index]
                closing = index + 1
                while closing < len(text):
                    if text[closing] == '\n':
                        closing = -1
                        break
                    if text[closing] == delimiter and text[closing-1] != '\\':
                        break
                    closing += 1
                if closing > index and closing < len(text) and re.match(r'[ \t]*:', text[closing+1:]):
                    result[index] = result[closing] = ' '
                    index = closing + 1
                    continue
            if python_like and text[index] == '#' or slash_comments and text.startswith('//', index):
                while index < len(text) and text[index] != '\n':
                    result[index] = ' '
                    index += 1
                continue
            if text[index] in {'\"', "'"} or slash_comments and text[index] == '`':
                quote = text[index]
                result[index] = ' '
            index += 1
        return ''.join(result).splitlines()

    def _definition_sites(self, text: str, suffix: str, masked: list[str],
                          tokens: list[str]) -> set[tuple[str, int]]:
        """Python AST bindings in direct module/class bodies only.

        Class members use qualified names; a member is not a module binding.
        The legacy lookup and its masking/classification remain independent.
        """
        sites = set()
        if suffix not in {'.py', '.pyi'}:
            return sites
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return sites
        wanted = set(tokens)

        def targets(node, prefix):
            if isinstance(node, ast.Name):
                yield prefix + node.id, node.lineno
            elif isinstance(node, ast.Attribute) and not prefix:
                parts, current = [], node
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    yield '.'.join([current.id, *reversed(parts)]), node.lineno
            elif isinstance(node, (ast.Tuple, ast.List)):
                for item in node.elts:
                    yield from targets(item, prefix)
            elif isinstance(node, ast.Starred):
                yield from targets(node.value, prefix)

        pending = [('', tree.body)]
        while pending:
            prefix, body = pending.pop()
            for node in body:
                self.budget.check()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = prefix + node.name
                    if name in wanted:
                        sites.add((name, node.lineno))
                    if isinstance(node, ast.ClassDef):
                        pending.append((name + '.', node.body))
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        sites.update((name, line) for name, line in targets(target, prefix) if name in wanted)
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    sites.update((name, line) for name, line in targets(node.target, prefix) if name in wanted)
        return sites

    def _preload_masked_files(self, root: Path, revision: str, paths: list[str], tokens: list[str]) -> bool:
        paths = [path for path in dict.fromkeys(paths)
                 if ('masked_lines', str(root), revision, path) not in self.cache
                 or any(('definition_sites', str(root), revision, path, token) not in self.cache for token in tokens)]
        if not paths:
            return True
        self.budget.check()
        result = subprocess.run(
            ['git', '-C', str(root), 'cat-file', '--batch'],
            input=''.join(f'{revision}:{path}\n' for path in paths).encode('utf-8'),
            capture_output=True, timeout=self.budget.remaining(),
        )
        data, offset = result.stdout, 0
        complete = result.returncode == 0
        for path in paths:
            end = data.find(b'\n', offset)
            if end < 0:
                complete = False
                break
            header = decode(data[offset:end]).rsplit(' ', 2)
            offset = end + 1
            if len(header) != 3 or header[1] != 'blob' or not header[2].isdigit():
                complete = False
                continue
            size = int(header[2])
            blob = data[offset:offset+size]
            offset += size + 1
            text, suffix = decode(blob), Path(path).suffix.lower()
            masked = self._mask_noncode(text, suffix)
            self.cache[('masked_lines', str(root), revision, path)] = masked
            self.cache[('legacy_lines', str(root), revision, path)] = self._mask_noncode(
                text, suffix, legacy_keys=True)
            sites = self._definition_sites(text, suffix, masked, tokens)
            for token in tokens:
                self.cache[('definition_sites', str(root), revision, path, token)] = {
                    line for name, line in sites if name == token}
        return complete and all(('masked_lines', str(root), revision, path) in self.cache
                                for path in paths)

    def preload_identifiers(self, requests: list[tuple[str, str, str]]) -> None:
        """Run one fixed-string, word-boundary grep per repository revision."""
        plans = []
        for root in self.roots:
            fixed = self.commits.get(normalize_path(str(root)), 'HEAD')
            tokens = list(dict.fromkeys(
                token for token, revision, _ in requests
                if token and (not revision or fixed.startswith(revision) or revision.startswith(fixed))
            ))
            tokens = [token for token in tokens if ('grep', str(root), fixed, token) not in self.cache]
            if not tokens:
                continue
            args = ['grep', '-n', '-w', '-F']
            for token in tokens:
                args.extend(('-e', token))
            exclusions = tuple(f':(exclude)*{suffix}' for suffix in sorted(DOCUMENT_SUFFIXES))
            plans.append((root, fixed, tokens, (*args, fixed, '--', *exclusions)))
        if not plans:
            return
        with ThreadPoolExecutor(max_workers=min(8, len(plans))) as pool:
            futures = [pool.submit(self.git, root, *args) for root, _, _, args in plans]
            results = [future.result() for future in futures]
        for (root, fixed, tokens, _), result in zip(plans, results):
            hits = {token: [] for token in tokens}
            definition_hits = {token: [] for token in tokens}
            word_patterns = {
                token: re.compile(r'(?<![A-Za-z0-9_$])' + re.escape(token) + r'(?![A-Za-z0-9_$])')
                for token in tokens
            }
            # Exact pre-p4-1 classification for the existing not_found lookup.
            # Its historical "definition" label is not a declaration proof.
            definition_patterns = {
                token: re.compile(
                    r'\b(?:def|class|function|const|let|var)\s+' + re.escape(token) + r'\b|'
                    + word_patterns[token].pattern + r'\s*(?::[^:=\n]+)?(?:=|:)')
                for token in tokens
            }
            parsed_rows: list[tuple[str, int, str, str]] = []
            if result.returncode == 0:
                for row in decode(result.stdout).splitlines():
                    self.budget.check()
                    parts = row.split(':', 3)
                    if len(parts) != 4 or not parts[2].isdigit():
                        continue
                    _, path, line_number, raw_line = parts
                    suffix = Path(path).suffix.lower()
                    if code_path(path):
                        parsed_rows.append((path, int(line_number), raw_line, suffix))
                blobs_complete = self._preload_masked_files(
                    root, fixed, [path for path, _, _, _ in parsed_rows], tokens)
                for path, line_number, _, suffix in parsed_rows:
                    self.budget.check()
                    lines = self.cache.get(('legacy_lines', str(root), fixed, path), [])
                    line = lines[line_number-1] if line_number <= len(lines) else ''
                    for token in tokens:
                        hit = self._identifier_hit(
                            path, line_number, line, token, fixed,
                            word_patterns[token], definition_patterns[token], suffix)
                        if hit:
                            hits[token].append(hit)
                        if line_number in self.cache.get(
                                ('definition_sites', str(root), fixed, path, token), set()):
                            definition_hits[token].append({'path': path, 'line': line_number,
                                                          'revision': fixed, 'kind': 'definition'})
            else:
                blobs_complete = result.returncode == 1
            complete = result.returncode in (0, 1) and blobs_complete
            for token in tokens:
                definitions = [hit for hit in hits[token] if hit['kind'] == 'definition']
                selected = definitions or hits[token]
                self.cache[('grep', str(root), fixed, token)] = (bool(selected), complete, selected)
                declarations = definition_hits[token]
                self.cache[('definitions', str(root), fixed, token)] = (bool(declarations), complete, declarations)

    def _qualified_python_definitions(self, root: Path, revision: str, token: str) -> tuple[bool, bool, list[dict]]:
        """Resolve class-qualified AST names without changing the legacy grep."""
        key = ('qualified_definitions', str(root), revision, token)
        if key not in self.cache:
            result = self.git(root, 'grep', '-l', '-z', '-w', '-F', '-e', token.rsplit('.', 1)[-1],
                              revision, '--', '*.py', '*.pyi')
            paths = [row[len(revision)+1:] for row in decode(result.stdout).split('\0')
                     if row.startswith(revision + ':')]
            paths = [path for path in paths if code_path(path) and Path(path).suffix.lower() in {'.py', '.pyi'}]
            complete = result.returncode in (0, 1)
            if complete:
                complete = self._preload_masked_files(root, revision, paths, [token])
            hits = []
            for path in paths:
                self.budget.check()
                for line in sorted(self.cache.get(('definition_sites', str(root), revision, path, token), set())):
                    hits.append({'path': path, 'line': line, 'revision': revision, 'kind': 'definition'})
            self.cache[key] = (bool(hits), complete, hits)
        return self.cache[key]

    def identifier(self, token: str, *, revision: str = '', when: str = '',
                   other_definition: str = '') -> tuple[bool, bool, list[str], list[dict]]:
        found, complete, scopes, locations = False, bool(self.roots), [], []
        for root in self.roots:
            fixed = self.commits.get(normalize_path(str(root)), 'HEAD')
            target_files = set()
            if other_definition:
                # The new definition fallback is restricted to the same fixed tree.
                if normalize_path(str(root)) not in self.commits:
                    continue
                names = self.files(root, fixed)
                if names is None:
                    complete = False
                    scopes.append(f'{root}@{fixed}: revision 조회 실패')
                    continue
                basename = normalize_path(other_definition, self.aliases).rsplit('/', 1)[-1]
                candidates = self.cache[('basenames', str(root), fixed)].get(basename, [])
                target_files = {name for name in candidates if name and (
                    matches_path(other_definition, str(root/name), self.aliases)
                    or matches_path(other_definition, name, self.aliases))}
                if not target_files:
                    continue
            if revision and not (fixed.startswith(revision) or revision.startswith(fixed)):
                complete = False
                scopes.append(f'{root}@{fixed}: 명시 revision {revision} 불일치로 건너뜀')
                continue
            key = ('definitions' if other_definition else 'grep', str(root), fixed, token)
            if other_definition and '.' in token:
                hit, ok, hits = self._qualified_python_definitions(root, fixed, token)
            else:
                if key not in self.cache:
                    self.preload_identifiers([(token, revision, when)])
                hit, ok, hits = self.cache.get(key, (False, False, []))
            if other_definition:
                hits = [item for item in hits if item['kind'] == 'definition'
                        and item['path'] not in target_files]
                hit = bool(hits)
            found |= hit
            complete &= ok
            locations.extend(hits)
            scopes.append(f'{root}@{fixed} git grep -w -F')
        return found, complete, scopes, locations
