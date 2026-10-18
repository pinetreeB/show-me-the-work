from __future__ import annotations

from collections.abc import Iterator
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

    def commit(self, sha: str, roots: list[Path] | None = None) -> tuple[str, str, dict]:
        answers = []
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
                        self.cache[key] = (('match', 'commit 객체 확인') if obj.returncode == 0 and obj.stdout.strip() == b'commit'
                                           else ('mismatch', 'commit 아닌 객체') if obj.returncode == 0
                                           else ('unverifiable', '객체 종류 조회 실패'))
            answers.append(self.cache[key])
        source = {'search_scope': [str(r) for r in selected], 'tool': 'git cat-file -t'}
        if not answers:
            return 'unverifiable', '조회할 저장소 없음', source
        if any(v == 'unverifiable' for v, _ in answers):
            return 'unverifiable', '저장소 조회 실패 또는 모호한 SHA', source
        if any(v == 'match' for v, _ in answers):
            return 'match', '후보 저장소에서 commit 객체 확인', source
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
                    answer = (('match', 'commit 객체 확인') if kind == 'commit'
                              else ('absent', '저장소 객체 범위에 SHA 없음') if kind == 'missing'
                              else ('unverifiable', '짧은 SHA가 여러 객체에 대응') if kind == 'ambiguous'
                              else ('mismatch', 'commit 아닌 객체'))
                self.cache[('sha', str(root), sha)] = answer

    def identifier(self, token: str, *, revision: str = '', when: str = '') -> tuple[bool, bool, list[str]]:
        found, complete, scopes = False, bool(self.roots), []
        for root in self.roots:
            fixed = self.commits.get(normalize_path(str(root)), 'HEAD')
            if when or revision and not (fixed.startswith(revision) or revision.startswith(fixed)):
                complete = False
                continue
            key = ('grep', str(root), fixed, token)
            if key not in self.cache:
                r = self.git(root, 'grep', '-q', '-w', '-F', '-e', token, fixed, '--')
                self.cache[key] = (r.returncode == 0, r.returncode in (0, 1))
            hit, ok = self.cache[key]
            found |= hit
            complete &= ok
            scopes.append(f'{root}@{fixed} git grep -w -F')
        return found, complete, scopes
