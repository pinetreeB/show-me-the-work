"""Synthetic line-qualified parentheses; no recorded inputs or model calls."""
import os
from pathlib import Path

import pytest

from smtw.claims.check import run
from smtw.claims.extract import extract, parenthetical_line_quotes
from smtw.claims.js_definitions import JSParserConfig
from smtw.claims.model import Budget
from tests.claims_support import agy_call, make_case, view


def inspect(tmp_path, text, *, source='if ready:', observed=True, partial=False,
            beta='unrelated destination\nif ready and extra:\nend'):
    rows = []
    if observed:
        content = '\n'.join([source, 'other source line', 'pointer anchor', 'last anchor'])
        rows += [agy_call('view_file', AbsolutePath='C:/synthetic/alpha.md'),
                 view('C:/synthetic/alpha.md', content, start=2 if partial else 1)]
    rows += [agy_call('view_file', AbsolutePath='C:/synthetic/beta.tsx'),
             view('C:/synthetic/beta.tsx', beta)]
    report, transcript = make_case(tmp_path, text, rows)
    node, package = os.environ.get('SMTW_TEST_JS_NODE'), os.environ.get('SMTW_TEST_JS_PACKAGE')
    config = JSParserConfig(Path(node), Path(package)) if node and package else None
    result = run(report, agy=str(transcript), js_parser=config)
    extracted, _ = extract(text, Budget(10))
    assert len(extracted) == len(result['claims'])
    for claim, atom in zip(result['claims'], extracted):
        assert claim['text'] == atom.text
        claim.update(quote=atom.quote, path=atom.path, start=atom.start, end=atom.end)
    return result


MARKERS = ['행 1', 'line 1', 'LINE 1', 'L 1', 'L1', '1행', '1 행', '줄 1', '1줄']


@pytest.mark.parametrize('marker', MARKERS)
@pytest.mark.parametrize('pointer', ['`alpha.md:3`', '`alpha.md:3-4`',
                                    '`alpha.md:3, 4`', 'alpha.md:3-4'])
@pytest.mark.parametrize('mutated', [False, True])
def test_claims_f8_previous_file_exact_line(tmp_path, marker, pointer, mutated):
    quote = 'if ready and extra:' if mutated else 'if ready:'
    result = inspect(tmp_path, f'| proof | {pointer} ({marker}: `{quote}`), `beta.tsx:1, 2-3` |')
    quoted = [c for c in result['claims'] if c.get('quote')]
    claim, = quoted
    assert (claim['check_type'], claim['path'], claim['start'], claim['end']) == ('K1', 'alpha.md', 1, 1)
    assert (claim['verdict'], claim['reason_code']) == (
        ('mismatch', 'quote_mismatch') if mutated else ('match', 'quote_match'))
    assert all(c['reason_code'] == 'range_only' for c in result['claims'] if c is not claim)


@pytest.mark.parametrize('quote', ['요청 처리/응답 정리/오류 안내', '3 items', 'ready',
                                  '42', 'call()', 'text = "alpha.md:9"', 'left | right'])
def test_claims_f8_explicit_literal_is_neither_label_nor_duplicate(tmp_path, quote):
    result = inspect(tmp_path, f'`alpha.md:3` (행 1: `{quote}`), `beta.tsx:1`', source=quote)
    claim, = [c for c in result['claims'] if c.get('quote')]
    assert (claim['path'], claim['start'], claim['reason_code']) == ('alpha.md', 1, 'quote_match')
    assert len(result['claims']) == 3


@pytest.mark.parametrize('marker', MARKERS)
@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('quote', ['if ready and extra:', 'if tampered:'])
def test_claims_f8_missing_source_never_uses_next_pointer(tmp_path, marker, partial, quote):
    result = inspect(tmp_path, f'`alpha.md:3` ({marker}: `{quote}`), `beta.tsx:2`',
                     observed=partial, partial=partial)
    claim, = [c for c in result['claims'] if c.get('quote')]
    assert (claim['path'], claim['start'], claim['verdict']) == ('alpha.md', 1, 'unverifiable')
    assert claim['reason_code'] == ('partial_range' if partial else 'no_read_of_file')


@pytest.mark.parametrize('pointer,marker,partial,table', [
    ('`nosuch.md:1`', '행 1', False, False),
    ('`nosuch.md:1`', '행 1', False, True),
    ('`nosuch.md:1-4`', '행 1', False, False),
    ('nosuch.md:1-2', '행 1', False, False),
    ('`alpha.md:3`', '행 1', True, False),
    ('`nosuch.md:1`', 'L 1', False, False),
    ('`alpha.md:3`', 'L 1', True, False),
    ('`nosuch.md:1`', '1줄', False, False),
    ('`alpha.md:3`', '1줄', True, False),
])
def test_claims_f8_unobserved_tampered_quote_matches_direct_form(
        tmp_path, pointer, marker, partial, table):
    # A known synthetic mutation is still unverifiable without its target row.
    # This locks measured behavior, not a decision that a verdict downgrade is safe.
    path = 'alpha.md' if partial else 'nosuch.md'
    parenthetical = f'{pointer} ({marker}: `if tampered:`), `beta.tsx:2`'
    if table:
        parenthetical = f'| proof | {parenthetical} |'
    direct = f'`{path}:1` `if tampered:`'
    beta = 'unrelated destination\nobserved beta line\nend'
    assert 'if tampered:' not in beta
    for index, text in enumerate((parenthetical, direct)):
        folder = tmp_path / str(index)
        folder.mkdir()
        result = inspect(folder, text, observed=partial, partial=partial, beta=beta)
        claim, = [c for c in result['claims'] if c.get('quote')]
        assert (claim['check_type'], claim['path'], claim['start'], claim['end'],
                claim['quote'], claim['verdict'], claim['reason_code']) == (
            'K1', path, 1, 1, 'if tampered:', 'unverifiable',
            'partial_range' if partial else 'no_read_of_file')
        assert result['status'] == 'INCOMPLETE'
        assert result['summary']['match'] == result['summary']['mismatch'] == 0


@pytest.mark.parametrize('text', [
    '`alpha.md:3` 행 1: `if ready and extra:`, `beta.tsx:1`',
    '`alpha.md:3` (행 1 `if ready and extra:`), `beta.tsx:1`',
    '`alpha.md:3` (행 1: `if ready and extra:`',
    '`alpha.md:3` 설명 (행 1: `if ready and extra:`), `beta.tsx:1`',
    '`alpha.md:3`, (행 1: `if ready and extra:`), `beta.tsx:1`',
    '| `alpha.md:3` | (행 1: `if ready and extra:`), `beta.tsx:1` |',
    '`alpha.md:3` <br> (행 1: `if ready and extra:`), `beta.tsx:1`',
    '`alpha.md:3` (행 1: `if ready:` 또는 `if extra:`), `beta.tsx:1`',
    '`log("alpha.md:3")` (행 1: `if ready:`), `beta.tsx:1`',
])
def test_claims_f8_ambiguous_forms_do_not_enter_new_binding(text):
    assert not parenthetical_line_quotes(text)


def test_claims_f8_multiple_files_and_table_cells(tmp_path):
    result = inspect(tmp_path, '| `alpha.md:3` (행 1: `if ready:`), '
                     '`beta.tsx:1` (line 2: `if ready and extra:`) | '
                     '`alpha.md:4` (1행: `if changed:`) |')
    assert [(c['path'], c['start'], c['reason_code'])
            for c in result['claims'] if c.get('quote')] == [
        ('alpha.md', 1, 'quote_match'), ('beta.tsx', 2, 'quote_match'),
        ('alpha.md', 1, 'quote_mismatch')]


def test_claims_f8_following_quote_still_checked(tmp_path):
    result = inspect(tmp_path, '`alpha.md:3` (행 1: `if ready:`), '
                     '`beta.tsx:1` `if fabricated:`')
    assert [(c['path'], c['reason_code']) for c in result['claims'] if c.get('quote')] == [
        ('beta.tsx', 'quote_mismatch'), ('alpha.md', 'quote_match')]
