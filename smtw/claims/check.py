from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pathlib import Path
import re
import subprocess

from .extract import extract, normalized_quote
from .model import Budget, Claim, DeadlineExceeded, Evidence, Transcript, summarize
from .paths import matches_path, normalize_path, normalize_url
from .sources import Repositories, content_lines, local_source
from .transcripts import read_transcript, resolve_agy


def boundary(token: str) -> re.Pattern:
    return re.compile(r'(?<![\w$])' + re.escape(token) + r'(?![\w$])')


def definition(token: str, text: str) -> bool:
    name = re.escape(token)
    return bool(re.search(r'(?:\b(?:function|def|class)\s+' + name + r'\b|["\']' + name
                          + r'["\']\s*:|(?<![\w$])' + name + r'\s*(?:=(?!=)|:\s*[^=]|\([^\n]*\)\s*\{))', text))


def missing(e: Evidence, dirty: bool = False) -> tuple[str, str, str]:
    if dirty or e.unsupported or not e.complete:
        return 'unverifiable', '필요한 전체 범위 미확보 또는 손상·미지원 영역', ''
    return 'absent', '완전히 확보한 검색 범위에 대상 없음', 'absent'


def line_values(c: Claim, e: Evidence) -> tuple[str, str, str]:
    counts = e.counts
    if 'wc -l' in c.context:
        values = [counts['L_nl']] if 'L_nl' in counts else []
    elif 'Total Lines' in c.context:
        values = [e.total_lines] if e.total_lines is not None else []
    else:
        values = [counts[k] for k in ('L_lines', 'L_view') if k in counts]
        if not values and e.total_lines is not None:
            values = [e.total_lines]
    if not values:
        # wc/stat outputs are data only when the corresponding read command ran.
        if 'wc' in e.command:
            hit = re.search(r'^\s*(\d+)\s+(?:\S+\s*)?$', e.text.strip())
            if hit:
                values = [int(hit.group(1))]
    if not values:
        return 'unverifiable', '줄 수 계측 미확보', ''
    okay = Decimal(c.value) in values
    return ('match' if okay else 'mismatch', f'줄 수 기준 {sorted(set(values))}', str(sorted(set(values))))


def check_one(c: Claim, e: Evidence, budget: Budget) -> tuple[str, str, str]:
    if e.problem:
        return 'unverifiable', e.problem, ''
    if c.check_type == 'K1' or c.check_type == 'K5' and c.start:
        if c.start < 1 or c.end < c.start:
            return 'mismatch', '유효하지 않은 줄 범위', 'range'
        if e.total_lines is not None and c.end > e.total_lines:
            return 'mismatch', f'파일 줄 범위 초과: 총 {e.total_lines}', str(e.total_lines)
        if any(n not in e.lines for n in range(c.start, c.end + 1)):
            return 'unverifiable', '인용 줄이 표시 범위 밖', ''
        text = '\n'.join(e.lines[n] for n in range(c.start, c.end+1))
        if '\ufffd' in text:
            return 'unverifiable', '인용 구간에 손상된 글자', ''
        if c.quote and normalized_quote(c.quote) not in normalized_quote(text):
            return 'mismatch', f'대응 줄 원문과 인용 불일치: {text[:240]}', text
        return 'match', '파일 줄 범위·인용 일치', text
    if c.check_type in {'K2', 'K6'} and c.unit == '줄':
        return line_values(c, e)
    if c.check_type == 'K2':
        multiplier = {'b': 1, 'byte': 1, 'bytes': 1, '바이트': 1,
                      'kb': 1000, 'mb': 1000000, 'kib': 1024, 'mib': 1048576}.get(c.unit.lower())
        if multiplier is None or e.total_bytes is None:
            return 'unverifiable', '파일 바이트 계측 미확보 (출력 바이트와 별개)', ''
        wanted = Decimal(c.value) * multiplier
        return ('match' if wanted == e.total_bytes else 'mismatch',
                f'파일 크기 원문 {e.total_bytes} B', str(e.total_bytes))
    if c.check_type == 'K4':
        dirty, mentioned = False, False
        for n, line in content_lines(e, budget):
            dirty |= '\ufffd' in line
            if boundary(c.value).search(line):
                if '\ufffd' in line:
                    continue
                mentioned = True
                if not c.definition or definition(c.value, line):
                    return 'match', f'{n}행에서 ' + ('정의 확인' if c.definition else '토큰 언급 확인'), line
        if mentioned and c.definition and e.complete and not dirty and not e.unsupported:
            return 'absent', '토큰 언급은 있으나 정의·설정 키는 없음', 'no definition'
        return missing(e, dirty)
    if c.check_type == 'K5':
        dirty = False
        needle = normalized_quote(c.quote)
        window = ''
        for n, line in content_lines(e, budget):
            dirty |= '\ufffd' in line
            window = (window + ' ' + normalized_quote(line))[-max(4096, len(needle)*2):]
            if needle in window and '\ufffd' not in window[window.find(needle):window.find(needle)+len(needle)]:
                return 'match', f'원문 인용 확인 ({e.locations.get(n, str(n)+"행")})', needle
        verdict, reason, signature = missing(e, dirty)
        if verdict == 'absent':
            return 'mismatch', '연결 원문의 글자와 인용 불일치', 'quote mismatch'
        return verdict, reason, signature
    if c.check_type == 'K6':
        dirty, entity_seen = False, False
        values = []
        for n, line in content_lines(e, budget):
            if c.entity and not boundary(c.entity).search(line):
                continue
            if '\ufffd' in line:
                dirty = True
                continue
            entity_seen = True
            if c.unit == 'value':
                key = re.search(r'["\']?' + re.escape(c.entity) + r'["\']?\s*[:=]\s*["\']?([^,;\n"\'}]+)', line)
                if key:
                    values.append(key.group(1).strip())
            else:
                unit = re.escape(c.unit)
                values += re.findall(r'([\d,]+(?:\.\d+)?)\s*' + unit + r'(?!\w)', line)
        if c.value in [v.replace(',', '') for v in values]:
            return 'match', '연결 엔터티 값 일치', str(sorted(set(values)))
        if dirty:
            return 'unverifiable', '연결 엔터티의 원문 손상', ''
        if values:
            return 'mismatch', f'연결 엔터티 값 불일치: {values[:8]}', str(sorted(set(values)))
        return missing(e, dirty) if not entity_seen else ('unverifiable', '대응 단위 값 미확보', '')
    if c.check_type == 'K9':
        dirty = False
        section, found, ended = [], False, False
        item = boundary(re.sub(r'\s+', '', c.value))
        for n, line in content_lines(e, budget):
            dirty |= '\ufffd' in line
            if item.search(re.sub(r'\s+', '', line)):
                found = True
                section.append(line)
                if line.lstrip().startswith('|'):
                    ended = True
                    break
                continue
            if found:
                if re.match(r'^\s*#{1,6}\s|^\s*(?:[RDB]\d+|F\d+-[A-Z]\d+|§\d+|항목\s*\d+)[\s:.)|]', line):
                    ended = True
                    break
                section.append(line)
        if not found:
            return missing(e, dirty)
        text = '\n'.join(section)
        if '\ufffd' in text:
            return 'unverifiable', '항목 구간 원문 손상', ''
        okay = normalized_quote(c.quote) in normalized_quote(text)
        if not okay and not ended and not e.complete:
            return 'unverifiable', '항목의 전체 구간 미확보', ''
        return ('match' if okay else 'mismatch', '항목 ID와 제목·인용 ' + ('일치' if okay else '불일치'), text)
    return 'unverifiable', '대응 검사 근거 미확보', ''


class Checker:
    def __init__(self, transcript: Transcript, sources: dict[str, str], repos: Repositories,
                 aliases: dict[str, str], budget: Budget):
        self.transcript, self.sources, self.repos = transcript, sources, repos
        self.aliases, self.budget = aliases, budget
        self.loaded: dict[tuple[str, str], Evidence] = {}

    def select(self, c: Claim) -> list[Evidence]:
        observed = [e for e in self.transcript.evidence
                    if (c.current or not e.after_report)
                    and c.path and (matches_path(c.path, e.path, self.aliases)
                                    or c.path.startswith(('https://', 'http://')) and normalize_url(c.path) == normalize_url(e.url))]
        if c.revision:
            observed = [e for e in observed if e.revision and
                        (e.revision.startswith(c.revision) or c.revision.startswith(e.revision))]
        if c.when and c.when not in {'before', 'after'}:
            observed = [e for e in observed if e.timestamp == c.when]
        if observed and (c.current or c.when in {'before', 'after'}):
            groups = defaultdict(list)
            for e in observed:
                groups[normalize_path(e.path, self.aliases)].append(e)
            observed = [(min if c.when == 'before' else max)(g, key=lambda e: e.order) for g in groups.values()]
        if observed:
            if c.check_type not in {'K1', 'K5'} or not c.start or any(
                e.total_lines is not None and c.end > e.total_lines or all(n in e.lines for n in range(c.start, c.end+1))
                for e in observed
            ):
                return observed
        fixed = []
        for name, path in self.sources.items():
            if c.path and (matches_path(c.path, name, self.aliases) or matches_path(name, c.path, self.aliases)):
                key = (name, path)
                if key not in self.loaded:
                    self.loaded[key] = local_source(Path(path).expanduser(), name, self.budget)
                e = self.loaded[key]
                # Human snapshots carry no invented timestamp; do not override explicit time.
                if not c.revision and not c.when:
                    fixed.append(e)
        if fixed:
            return fixed
        fixed = self.repos.observations(c.path, fixed=True) if c.path else []
        if c.revision or c.when:
            fixed = [e for e in fixed if not c.when and e.revision and c.revision and
                     (e.revision.startswith(c.revision) or c.revision.startswith(e.revision))]
        return fixed or observed

    def apply(self, c: Claim):
        self.budget.check()
        if c.check_type == 'K7':
            total, part = int(c.value), int(c.entity)
            okay = total == part if c.unit == 'table' else part <= total
            c.verdict = 'match' if okay else 'mismatch'
            c.reason = f'보고서 내부 산술 대조: 전체 {total}, 부분/표 행 {part}'
            c.source = {'location': f'보고서:{c.report_line}', 'search_scope': '보고서 내부 집계'}
            return
        if c.check_type == 'K8':
            relevant = [e for e in self.transcript.evidence if normalize_url(e.url) == normalize_url(c.value)
                        and (c.current or not e.after_report)]
            if any(not e.problem and e.complete for e in relevant):
                c.verdict, c.reason = 'match', '성공한 URL 읽기 결과 확인'
                c.source = next(e.metadata() for e in relevant if not e.problem and e.complete)
            else:
                c.verdict = 'unverifiable' if relevant or not self.transcript.valid or not self.transcript.version else 'absent'
                c.reason = '성공한 URL 읽기 결과 없음 (검색 질의·본문 URL 제외)'
                c.source = {'search_scope': '대화의 성공한 URL 읽기 도구 결과'}
            return
        if c.check_type == 'K3':
            for e in self.transcript.evidence:
                if e.problem or not e.complete or e.after_report and not c.current or not re.search(r'\bgit\b', e.command):
                    continue
                for n, text in content_lines(e, self.budget):
                    if boundary(c.value).search(text) and '\ufffd' not in text:
                        c.verdict, c.reason, c.source = 'match', '연결된 git 읽기 명령 출력에서 SHA 확인', e.metadata()
                        return
            c.verdict, c.reason, c.source = self.repos.commit(c.value)
            return
        observations = self.select(c)
        if not c.path and c.check_type == 'K4':
            results = []
            for e in self.transcript.evidence:
                if not e.after_report or c.current:
                    results.append((check_one(c, e, self.budget), e))
            found = next(((r, e) for r, e in results if r[0] == 'match'), None)
            if found:
                (c.verdict, c.reason, _), e = found
                c.source = e.metadata()
                return
            hit, complete, scopes = self.repos.identifier(c.value)
            # git grep proves mention, not definition.
            reads_complete = all(r[0] != 'unverifiable' for r, _ in results)
            c.verdict = 'match' if hit and not c.definition else 'absent' if complete and not hit and reads_complete and self.transcript.valid else 'unverifiable'
            c.reason = '연결 파일 없는 식별자: 대화 읽기 결과 + 저장소 HEAD 토큰 검색'
            c.source = {'search_scope': ['대화 읽기 결과', *scopes], 'existence': 'mention'}
            return
        if not observations:
            c.reason = '연결된 당시 읽기 결과 또는 지정 기준 원문 미확보'
            if c.path:
                current = self.repos.observations(c.path, fixed=False)
                c.source = {'search_scope': c.path, 'current_observations': []}
                for e in current:
                    verdict, reason, _ = check_one(c, e, self.budget)
                    c.source['current_observations'].append({**e.metadata(), 'verdict': verdict, 'reason': reason})
            return
        results = [(check_one(c, e, self.budget), e) for e in observations]
        c.source = {'observations': [e.metadata() for _, e in results],
                    'search_scope': sorted(set(e.path or e.url or e.origin for _, e in results))}
        # Multiple snapshots of one file require equal relevant content, not equal verdict alone.
        groups = defaultdict(set)
        for (verdict, _, signature), e in results:
            groups[normalize_path(e.path, self.aliases)].add((verdict, signature))
        verdicts = {r[0] for r, _ in results}
        if 'unverifiable' in verdicts or len(verdicts) != 1 or any(len(v) > 1 for v in groups.values()):
            c.verdict, c.reason = 'unverifiable', '필요 범위 미확보 또는 여러 관측·동명 파일의 결론/내용이 다름'
        else:
            c.verdict, c.reason = results[0][0][:2]


def run(report: Path, *, agy: str | None = None, codex: Path | None = None,
        brain_root: Path | None = None, repos: list[str] | None = None,
        commits: dict[str, str] | None = None, sources: dict[str, str] | None = None,
        aliases: dict[str, str] | None = None, time_limit: float = 60,
        budget: Budget | None = None) -> dict:
    budget = budget or Budget(time_limit)
    report_text = report.read_bytes().decode('utf-8-sig')
    aliases = aliases or {}
    claims, warnings = [], []
    transcript = Transcript()
    timed_out, unchecked = False, 0
    completed = 0
    try:
        claims, warnings = extract(report_text, budget)
        if agy:
            transcript = read_transcript(resolve_agy(agy, brain_root or Path('~/.gemini/antigravity-cli/brain')),
                                         'agy', report, report_text, aliases, budget)
        elif codex:
            transcript = read_transcript(codex, 'codex', report, report_text, aliases, budget)
        warnings.extend(transcript.warnings)
        repositories = Repositories(repos or [], commits or {}, aliases, budget)
        repositories.preload_commits([c.value for c in claims if c.check_type == 'K3'])
        checker = Checker(transcript, sources or {}, repositories, aliases, budget)
        for c in claims:
            checker.apply(c)
            completed += 1
    except (DeadlineExceeded, subprocess.TimeoutExpired):
        timed_out = True
        unchecked = len(claims) - completed
        for c in claims[completed:]:
            c.verdict, c.reason = 'unverifiable', '시간 상한으로 검사하지 못함'
    result = summarize(claims, timed_out=timed_out, unchecked_count=unchecked,
                       warnings=warnings, input_incomplete=not transcript.valid)
    result['transcript_format'] = transcript.version
    result['elapsed_seconds'] = round(budget.clock() - budget.started, 6)
    return result
