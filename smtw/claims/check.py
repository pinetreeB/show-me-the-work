from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
import re
import json
import subprocess
from typing import NamedTuple

from .extract import extract
from .markup import cell_spans, normalized_quote
from .model import Budget, Claim, DeadlineExceeded, Evidence, Transcript, summarize
from .paths import matches_path, normalize_path, normalize_url
from .sources import Repositories, content_lines, file_lines, local_source
from .transcripts import discover_agy, load_measurement_pointer, load_view_pointer, read_transcript, resolve_agy


@lru_cache(maxsize=1024)
def boundary(token: str) -> re.Pattern:
    return re.compile(r'(?<![\w$])' + re.escape(token) + r'(?![\w$])')


def definition(token: str, text: str) -> bool:
    name = re.escape(token)
    return bool(re.search(r'(?:\b(?:function|def|class)\s+' + name + r'\b|["\']' + name
                          + r'["\']\s*:|(?<![\w$])' + name + r'\s*(?:=(?!=)|:\s*[^=]|\([^\n]*\)\s*\{))', text))


class Result(NamedTuple):
    verdict: str
    reason: str
    signature: str = ''
    code: str = 'no_evidence'


def missing(e: Evidence, dirty: bool = False) -> Result:
    if dirty or e.unsupported or not e.complete:
        return Result('unverifiable', '필요한 전체 범위 미확보 또는 손상·미지원 영역',
                      code='decode_damage' if dirty else 'unsupported_format' if e.unsupported else 'partial_range')
    return Result('absent', '완전히 확보한 검색 범위에 대상 없음', 'absent', 'not_found')


def value_text(value) -> str:
    if isinstance(value, (list, dict, bool)) or value is None:
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    return str(value)


def expected_value(value: str) -> str:
    if value.lstrip().startswith(('[', '{')):
        try:
            return value_text(json.loads(value))
        except ValueError:
            pass
    return value


def json_entity_value(c: Claim, e: Evidence, budget: Budget) -> Result | None:
    if not e.complete or c.unit != 'value' or not e.path.lower().endswith('.json'):
        return None
    text = '\n'.join(line for _, line in content_lines(e, budget))
    values, duplicates = [], []
    def pairs(items):
        seen = set()
        for key, _ in items:
            if key in seen:
                duplicates.append(key)
            seen.add(key)
        values.extend(value_text(value) for key, value in items if key == c.entity)
        return dict(items)
    try:
        json.loads(text, object_pairs_hook=pairs)
    except ValueError:
        return None
    if duplicates or len(set(values)) > 1:
        return Result('unverifiable', 'JSON 키·값 후보가 여러 개', code='binding_ambiguous')
    if not values:
        return missing(e, '\ufffd' in text)
    if '\ufffd' in values[0]:
        return Result('unverifiable', 'JSON 엔터티 값 손상', code='decode_damage')
    okay = values[0] == expected_value(c.value)
    return Result('match' if okay else 'mismatch', 'JSON 엔터티 값 ' + ('일치' if okay else '불일치'),
                  values[0], 'value_match' if okay else 'value_mismatch')


def line_values(c: Claim, e: Evidence) -> Result:
    counts = dict(e.counts)
    if e.total_lines is not None and 'L_lines' not in counts and e.total_lines in e.lines and not e.lines[e.total_lines].strip():
        counts.update(L_lines=max(0, e.total_lines-1), L_view=e.total_lines)
    if 'wc -l' in c.context:
        values = [counts['L_nl']] if 'L_nl' in counts else []
    elif 'Total Lines' in c.context:
        values = [e.total_lines] if e.total_lines is not None else []
    else:
        values = [counts[k] for k in ('L_lines', 'L_view') if k in counts]
        if not values and e.total_lines is not None:
            values = [e.total_lines]
    if not values:
        return Result('unverifiable', '줄 수 계측 미확보', code='measurement_missing')
    okay = Decimal(c.value) in values
    return Result('match' if okay else 'mismatch', f'줄 수 기준 {sorted(set(values))}',
                  str(sorted(set(values))), 'value_match' if okay else 'value_mismatch')


def check_one(c: Claim, e: Evidence, budget: Budget) -> Result:
    if e.control_only:
        return Result('unverifiable', '명령 성공 제어 정보는 파일 원문이 아님', code='no_read_of_file')
    if e.problem:
        return Result('unverifiable', e.problem, code=e.problem_code or 'read_failed')
    if c.check_type == 'K1' or c.check_type == 'K5' and c.start:
        if c.start < 1 or c.end < c.start:
            return Result('mismatch', '유효하지 않은 줄 범위', 'range', 'invalid_range')
        if e.total_lines is not None and c.end > e.total_lines:
            return Result('mismatch', f'파일 줄 범위 초과: 총 {e.total_lines}', str(e.total_lines), 'range_exceeded')
        if any(n not in e.lines for n in range(c.start, c.end + 1)):
            return Result('unverifiable', '인용 줄이 표시 범위 밖', code='partial_range')
        text = '\n'.join(e.lines[n] for n in range(c.start, c.end+1))
        if '\ufffd' in text:
            return Result('unverifiable', '인용 구간에 손상된 글자', code='decode_damage')
        if not c.quote:
            return Result('range_only', '줄 범위만 확인 (내용 인용 없음)', 'range', 'range_only')
        numbered = [re.fullmatch(r'\s*(?:line\s+)?(\d+): ?(.*)', line, re.I)
                    for line in c.quote.splitlines()]
        if numbered and all(numbered):
            pairs = [(int(match.group(1)), match.group(2)) for match in numbered]
            if any(not c.start <= n <= c.end for n, _ in pairs):
                return Result('mismatch', '번호 붙은 인용이 명시한 줄 범위 밖', text, 'quote_range_mismatch')
            okay = all(normalized_quote(quote) in normalized_quote(e.lines[n]) for n, quote in pairs)
            return Result('match' if okay else 'mismatch', '줄 번호 접두사를 제외한 코드 인용 ' + ('일치' if okay else '불일치'),
                          '\n'.join(e.lines[n] for n, _ in pairs), 'quote_match' if okay else 'quote_mismatch')
        prose = not c.quote_code
        if normalized_quote(c.quote, prose=prose) not in normalized_quote(text, prose=prose):
            return Result('mismatch', f'대응 줄 원문과 인용 불일치: {text[:240]}', text, 'quote_mismatch')
        return Result('match', '파일 줄 범위·인용 일치', text, 'quote_match')
    if c.check_type in {'K2', 'K6'} and c.unit == '줄' and (c.check_type == 'K2' or not c.entity):
        return line_values(c, e)
    if c.check_type == 'K2':
        multiplier = {'b': 1, 'byte': 1, 'bytes': 1, '바이트': 1,
                      'kb': 1000, 'mb': 1000000, 'kib': 1024, 'mib': 1048576}.get(c.unit.lower())
        if multiplier is None or e.total_bytes is None:
            return Result('unverifiable', '파일 바이트 계측 미확보 (출력 바이트와 별개)', code='measurement_missing')
        wanted = Decimal(c.value) * multiplier
        return Result('match' if wanted == e.total_bytes else 'mismatch',
                      f'파일 크기 원문 {e.total_bytes} B', str(e.total_bytes),
                      'value_match' if wanted == e.total_bytes else 'value_mismatch')
    if c.check_type == 'K4':
        dirty, mentioned = False, False
        token_pattern = boundary(c.value)
        for n, line in content_lines(e, budget):
            dirty |= '\ufffd' in line
            if c.value in line and token_pattern.search(line):
                if '\ufffd' in line:
                    continue
                mentioned = True
                if not c.definition or definition(c.value, line):
                    return Result('match', f'{n}행에서 ' + ('정의 확인' if c.definition else '토큰 언급 확인'), line, 'token_match')
        if mentioned and c.definition and e.complete and not dirty and not e.unsupported:
            return Result('absent', '토큰 언급은 있으나 정의·설정 키는 없음', 'no definition', 'definition_missing')
        return missing(e, dirty)
    if c.check_type == 'K5':
        dirty = False
        needle = normalized_quote(c.quote, prose=not c.quote_code)
        window = ''
        for n, line in content_lines(e, budget):
            dirty |= '\ufffd' in line
            window = window + ' ' + normalized_quote(line, prose=not c.quote_code)
            if needle in window and '\ufffd' not in window[window.find(needle):window.find(needle)+len(needle)]:
                return Result('match', f'원문 인용 확인 ({e.locations.get(n, str(n)+"행")})', needle, 'quote_match')
            window = window[-max(4096, len(needle)*2):]
        result = missing(e, dirty)
        if result.verdict == 'absent':
            return Result('mismatch', '연결 원문의 글자와 인용 불일치', 'quote mismatch', 'quote_mismatch')
        return result
    if c.check_type == 'K6':
        if not c.entity:
            return Result('unverifiable', '숫자 엔터티 연결 없음', code='binding_ambiguous')
        structured = json_entity_value(c, e, budget)
        if structured is not None:
            return structured
        dirty, entity_seen = False, False
        incomplete_value = False
        values = []
        for n, line in content_lines(e, budget):
            if c.entity and not boundary(c.entity).search(line):
                continue
            if '\ufffd' in line:
                dirty = True
                continue
            entity_seen = True
            if c.unit == 'value':
                key = re.search(r'["\']?' + re.escape(c.entity) + r'["\']?\s*[:=]\s*(.+)', line)
                if key:
                    raw = key.group(1).strip()
                    try:
                        value, _ = json.JSONDecoder().raw_decode(raw)
                        values.append(value_text(value))
                    except ValueError:
                        if raw.startswith(('[', '{')):
                            incomplete_value = True
                        else:
                            values.append(re.split(r'[,;\n"\'}]', raw.strip('"\''))[0].strip())
            else:
                unit = re.escape(c.unit)
                values += re.findall(r'([\d,]+(?:\.\d+)?)\s*' + unit + r'(?!\w)', line)
        if dirty:
            return Result('unverifiable', '연결 엔터티의 원문 손상', code='decode_damage')
        if incomplete_value:
            return Result('unverifiable', '구조화된 엔터티 값 전체 미확보', code='partial_range')
        c_value = expected_value(c.value) if c.unit == 'value' else c.value
        unique = sorted(set(v if c.unit == 'value' else v.replace(',', '') for v in values))
        if len(unique) > 1:
            return Result('unverifiable', '연결 엔터티에 대응하는 값이 여러 개', code='binding_ambiguous')
        if unique == [c_value]:
            return Result('match', '연결 엔터티 값 일치', str(unique), 'value_match')
        if values:
            return Result('mismatch', f'연결 엔터티 값 불일치: {values[:8]}', str(unique), 'value_mismatch')
        return missing(e, dirty) if not entity_seen else Result('unverifiable', '대응 단위 값 미확보', code='measurement_missing')
    if c.check_type == 'K9':
        if c.ordinal:
            return ordinal_item(c, e, budget)
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
            return Result('unverifiable', '항목 구간 원문 손상', code='decode_damage')
        okay = normalized_quote(c.quote) in normalized_quote(text)
        if not okay and not ended and not e.complete:
            return Result('unverifiable', '항목의 전체 구간 미확보', code='partial_range')
        return Result('match' if okay else 'mismatch', '항목 ID와 제목·인용 ' + ('일치' if okay else '불일치'),
                      text, 'item_match' if okay else 'item_mismatch')
    return Result('unverifiable', '대응 검사 근거 미확보')


def ordinal_item(c: Claim, e: Evidence, budget: Budget) -> Result:
    """Use explicit numbers, or an entire table/list's row order; never nearby text."""
    wanted = int(re.search(r'\d+', c.value).group())
    candidates, table, list_index, in_list = [], [], 0, False
    def flush_table():
        if len(table) < 2 or not all(re.fullmatch(r':?-+:?', x.strip()) for x in table[1]):
            return
        for index, cells in enumerate(table[2:], 1):
            label = re.fullmatch(r'#?(\d+)\.?', cells[0].strip(' *`')) if cells else None
            number = int(label.group(1)) if label else index
            if number == wanted:
                candidates.append(' | '.join(cells))
    for _, line in content_lines(e, budget):
        if line.lstrip().startswith('|'):
            table.append([line[a:b].strip() for a, b in cell_spans(line)])
            in_list = False
            continue
        if table:
            flush_table()
            table = []
        numbered = re.match(r'^\s*(?:#{1,6}\s+)?(?:#|항목\s*)?(\d+)[.):\s]\s*(.+)', line)
        bullet = re.match(r'^\s*[-+*]\s+(.+)', line)
        if numbered:
            if int(numbered.group(1)) == wanted:
                candidates.append(numbered.group(2))
        elif bullet:
            list_index = list_index + 1 if in_list else 1
            if list_index == wanted and e.complete:
                candidates.append(bullet.group(1))
        in_list = bool(bullet)
    flush_table()
    if not candidates:
        return missing(e)
    if any('\ufffd' in text for text in candidates):
        return Result('unverifiable', '항목 구간 원문 손상', code='decode_damage')
    if len(set(candidates)) != 1:
        return Result('unverifiable', '같은 번호의 원문 항목이 여러 개', code='binding_ambiguous')
    text = candidates[0]
    okay = bool(boundary(c.quote).search(text)) if re.fullmatch(r'[\w.$]+', c.quote) else normalized_quote(c.quote) in normalized_quote(text)
    return Result('match' if okay else 'mismatch', '항목 번호와 원문 행 ' + ('일치' if okay else '불일치'),
                  text, 'item_match' if okay else 'item_mismatch')


class Checker:
    def __init__(self, transcript: Transcript, sources: dict[str, str], repos: Repositories,
                 aliases: dict[str, str], budget: Budget):
        self.transcript, self.sources, self.repos = transcript, sources, repos
        self.aliases, self.budget = aliases, budget
        self.loaded: dict[tuple[str, str], Evidence] = {}

    def select(self, c: Claim) -> list[Evidence]:
        observed = [e for e in self.transcript.evidence
                    if not e.control_only and (c.current or not e.after_report)
                    and c.path and (matches_path(c.path, e.path, self.aliases)
                                    or c.path.startswith(('https://', 'http://')) and normalize_url(c.path) == normalize_url(e.url))]
        file_reads = observed
        for e in observed:
            load_view_pointer(e, self.budget)
            if c.check_type == 'K2':
                load_measurement_pointer(e, self.budget)
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
        if c.check_type == 'K2' and file_reads:
            return observed or [replace(e, problem='파일 읽기 관측은 있으나 명시 시점에 연결하지 못함',
                                        problem_code='observation_time_unresolved') for e in file_reads]
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
        if c.binding_candidates:
            c.reason, c.reason_code = '같은 문맥에 연결 파일 후보가 여러 개', 'binding_ambiguous'
            c.source = {'binding_candidates': list(c.binding_candidates)}
            return
        if c.command_exit is not None:
            relevant = [e for e in self.transcript.evidence if e.conditional_marker and
                        (c.current or not e.after_report) and (not c.path or matches_path(c.path, e.path, self.aliases))]
            for e in relevant:
                if e.problem or e.exit_code != 0:
                    continue
                marker = e.success_marker
                if not marker and e.complete and e.aux_path:
                    last = ''
                    for _, text in file_lines(Path(e.aux_path), self.budget):
                        if text.strip() and not re.search(r'(?:Exit code:|exited with code)\s*-?\d+', text, re.I):
                            last = text
                    if last == e.conditional_marker:
                        marker = last
                if marker == c.value and '\ufffd' not in marker:
                    c.verdict = 'match' if c.command_exit == 0 else 'mismatch'
                    c.reason_code = 'command_marker_match' if c.command_exit == 0 else 'command_exit_mismatch'
                    c.reason = '조건부 성공 표식과 종료 코드 대조'
                    c.source = {**e.metadata(), 'success_marker': marker}
                    return
            c.reason, c.reason_code = '연결된 명령 성공 표식 미확보', 'command_result_missing'
            c.source = {'observations': [e.metadata() for e in relevant]}
            return
        if c.check_type == 'K7':
            total, part = int(c.value), int(c.entity)
            okay = total == part if c.unit == 'table' else part <= total
            c.verdict = 'match' if okay else 'mismatch'
            c.reason = f'보고서 내부 산술 대조: 전체 {total}, 부분/표 행 {part}'
            c.source = {'location': f'보고서:{c.report_line}', 'search_scope': '보고서 내부 집계'}
            c.reason_code = 'value_match' if okay else 'value_mismatch'
            return
        if c.check_type == 'K8':
            relevant = [e for e in self.transcript.evidence if normalize_url(e.url) == normalize_url(c.value)
                        and (c.current or not e.after_report)]
            if any(not e.problem and e.complete for e in relevant):
                c.verdict, c.reason = 'match', '성공한 URL 읽기 결과 확인'
                c.source = next(e.metadata() for e in relevant if not e.problem and e.complete)
                c.reason_code = 'visited_url'
            else:
                c.verdict = 'unverifiable' if relevant or not self.transcript.valid or not self.transcript.version else 'absent'
                c.reason = '성공한 URL 읽기 결과 없음 (검색 질의·본문 URL 제외)'
                c.source = {'search_scope': '대화의 성공한 URL 읽기 도구 결과'}
                c.reason_code = next((e.problem_code for e in relevant if e.problem_code), 'no_successful_visit')
            return
        if c.check_type == 'K3':
            scope = self.repos.scope(c.repository_contexts)
            scoped = scope if len(scope) == 1 else None
            for e in self.transcript.evidence:
                if e.problem or not e.complete or e.after_report and not c.current or not re.search(r'\bgit\b', e.command):
                    continue
                if scoped:
                    cwd = normalize_path(e.cwd, self.aliases)
                    root = normalize_path(str(scoped[0]), self.aliases)
                    if not cwd or not (cwd == root or cwd.startswith(root + '/')):
                        continue
                for n, text in content_lines(e, self.budget):
                    if boundary(c.value).search(text) and '\ufffd' not in text:
                        c.verdict, c.reason, c.source = 'match', '연결된 git 읽기 명령 출력에서 SHA 확인', e.metadata()
                        c.reason_code = 'sha_observed'
                        return
            c.verdict, c.reason, c.source = self.repos.commit(c.value, scoped)
            c.reason_code = {'match': 'commit_exists', 'mismatch': 'not_commit', 'absent': 'commit_not_found',
                             'unverifiable': 'commit_unresolved'}[c.verdict]
            c.source['repository_binding'] = 'explicit' if scoped else 'unknown'
            if not scoped and (c.verdict == 'absent' or not self.repos.roots):
                c.verdict, c.reason, c.reason_code = 'unverifiable', 'SHA가 속한 저장소 범위 미확정', 'repo_scope_unknown'
            return
        observations = self.select(c)
        if not c.path and c.check_type == 'K4':
            results = []
            for e in self.transcript.evidence:
                if not e.control_only and (not e.after_report or c.current):
                    results.append((check_one(c, e, self.budget), e))
            found = next(((r, e) for r, e in results if r[0] == 'match'), None)
            if found:
                result, e = found
                c.verdict, c.reason, c.reason_code = result.verdict, result.reason, result.code
                c.source = e.metadata()
                return
            hit, complete, scopes = self.repos.identifier(c.value)
            # git grep proves mention, not definition.
            reads_complete = all(r[0] != 'unverifiable' for r, _ in results)
            c.verdict = 'match' if hit and not c.definition else 'absent' if complete and not hit and reads_complete and self.transcript.valid else 'unverifiable'
            c.reason = '연결 파일 없는 식별자: 대화 읽기 결과 + 저장소 HEAD 토큰 검색'
            c.source = {'search_scope': ['대화 읽기 결과', *scopes], 'existence': 'mention'}
            c.reason_code = 'token_match' if c.verdict == 'match' else 'not_found' if c.verdict == 'absent' else 'partial_range'
            return
        if not observations:
            c.reason = '연결된 당시 읽기 결과 또는 지정 기준 원문 미확보'
            c.reason_code = ('historical_no_commit' if c.revision or c.when else
                             'commit_missing_file' if self.repos.commits and c.path else 'no_read_of_file')
            if c.path:
                current = self.repos.observations(c.path, fixed=False)
                c.source = {'search_scope': c.path, 'current_observations': []}
                for e in current:
                    result = check_one(c, e, self.budget)
                    c.source['current_observations'].append({**e.metadata(), 'verdict': result.verdict, 'reason': result.reason})
            return
        identities = {normalize_path(e.path, self.aliases) or normalize_url(e.url) for e in observations}
        if len(identities) > 1:
            c.reason, c.reason_code = '서로 다른 파일 후보가 여러 개', 'binding_ambiguous'
            c.source = {'observations': [e.metadata() for e in observations]}
            return
        # A compatible partial observation adds no competing version of a full read.
        # Failed reads and conflicting content/measurements remain unresolved.
        full = [e for e in observations if e.complete and e.lines and not e.problem]
        observations = [e for e in observations if not (
            not e.complete and not e.problem and e.lines and any(
                all(f.lines.get(n) == text for n, text in e.lines.items())
                and (e.total_lines is None or e.total_lines == f.total_lines)
                and all(k not in f.counts or f.counts[k] == v for k, v in e.counts.items())
                for f in full))]
        results = [(check_one(c, e, self.budget), e) for e in observations]
        line_metric = c.check_type in {'K2', 'K6'} and c.unit == '줄' and (c.check_type == 'K2' or not c.entity)
        if c.check_type == 'K2' and any(r.verdict != 'unverifiable' for r, _ in results):
            results = [(r, e) for r, e in results if r.code != 'measurement_missing']
        c.source = {'observations': [e.metadata() for _, e in results],
                    'search_scope': sorted(set(e.path or e.url or e.origin for _, e in results))}
        # Multiple snapshots of one file require equal relevant content, not equal verdict alone.
        groups = defaultdict(set)
        for result, e in results:
            signature = result.signature
            if line_metric:
                signature = ''  # Compare each count convention below, not differently sized candidate sets.
            groups[normalize_path(e.path, self.aliases)].add((result.verdict, signature))
        count_conflict = False
        if line_metric:
            counts = defaultdict(set)
            for _, e in results:
                for key, value in e.counts.items():
                    counts[key].add(value)
                if e.total_lines is not None:
                    counts['L_view'].add(e.total_lines)
            count_conflict = any(len(values) > 1 for values in counts.values())
            count_conflict |= any(view - lines not in (0, 1) for view in counts['L_view'] for lines in counts['L_lines'])
        verdicts = {r[0] for r, _ in results}
        if 'unverifiable' in verdicts or len(verdicts) != 1 or count_conflict or any(len(v) > 1 for v in groups.values()):
            c.verdict, c.reason = 'unverifiable', '필요 범위 미확보 또는 여러 관측·동명 파일의 결론/내용이 다름'
            unresolved = [r for r, _ in results if r.verdict == 'unverifiable']
            c.reason_code = unresolved[0].code if unresolved else 'observation_conflict'
        else:
            c.verdict, c.reason = results[0][0][:2]
            c.reason_code = results[0][0].code


def run(report: Path, *, agy: str | None = None, codex: Path | None = None,
        agy_auto: bool = False,
        brain_root: Path | None = None, repos: list[str] | None = None,
        commits: dict[str, str] | None = None, sources: dict[str, str] | None = None,
        default_file: dict[str, str] | None = None,
        aliases: dict[str, str] | None = None, time_limit: float = 60,
        budget: Budget | None = None) -> dict:
    budget = budget or Budget(time_limit)
    if any(not re.fullmatch(r'[0-9a-fA-F]{7,40}', revision) for revision in (commits or {}).values()):
        raise ValueError('--commit 기준은 7~40자리 16진 SHA여야 합니다.')
    report_text = report.read_bytes().decode('utf-8-sig')
    aliases = aliases or {}
    sources = dict(sources or {})
    if default_file:
        if len(default_file) != 1:
            raise ValueError('--default-file은 표기=실제파일 한 개만 지정해야 합니다.')
        name, actual = next(iter(default_file.items()))
        if not name or not actual or name in sources and sources[name] != actual:
            raise ValueError('--default-file이 비어 있거나 --source-file과 충돌합니다.')
        sources[name] = actual
    claims, warnings = [], []
    transcript = Transcript()
    timed_out, unchecked = False, 0
    completed = 0
    try:
        claims, warnings = extract(report_text, budget)
        if default_file:
            for c in claims:
                if c.check_type in {'K1', 'K5'} and c.start and not c.path and not c.binding_candidates:
                    c.path = name
        if agy_auto:
            candidates = discover_agy(report, report_text, brain_root or Path('~/.gemini/antigravity-cli/brain'), aliases, budget)
            if len(candidates) == 1:
                agy = str(candidates[0])
            else:
                transcript.valid = False
                transcript.warnings.append(f'보고서 마지막 쓰기와 바이트가 일치하는 agy 대화 {len(candidates)}개: 자동 선택 안 함')
        if agy:
            transcript = read_transcript(resolve_agy(agy, brain_root or Path('~/.gemini/antigravity-cli/brain')),
                                         'agy', report, report_text, aliases, budget)
        elif codex:
            transcript = read_transcript(codex, 'codex', report, report_text, aliases, budget)
        warnings.extend(transcript.warnings)
        repositories = Repositories(repos or [], commits or {}, aliases, budget)
        repositories.preload_commits([c.value for c in claims if c.check_type == 'K3'])
        checker = Checker(transcript, sources, repositories, aliases, budget)
        for c in claims:
            checker.apply(c)
            completed += 1
    except (DeadlineExceeded, subprocess.TimeoutExpired):
        timed_out = True
        unchecked = len(claims) - completed
        for c in claims[completed:]:
            c.verdict, c.reason = 'unverifiable', '시간 상한으로 검사하지 못함'
            c.reason_code = 'timed_out'
    result = summarize(claims, timed_out=timed_out, unchecked_count=unchecked,
                       warnings=warnings, input_incomplete=not transcript.valid)
    result['transcript_format'] = transcript.version
    result['elapsed_seconds'] = round(budget.clock() - budget.started, 6)
    return result
