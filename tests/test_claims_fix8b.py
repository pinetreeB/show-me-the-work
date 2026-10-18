from __future__ import annotations

from smtw.claims.check import run
from tests.claims_support import agy_call, make_case, view


def inspect(tmp_path, report, source, name='notes.md'):
    display = 'C:/synthetic/project/' + name
    report_path, transcript = make_case(
        tmp_path, report,
        [agy_call('view_file', AbsolutePath=display), view(display, source)],
    )
    return run(report_path, agy=str(transcript))


def atoms(result, kind):
    return [claim for claim in result['claims'] if claim['check_type'] == kind]


def test_self_assigned_item_with_multiple_backticks_is_one_unknown_k9(tmp_path):
    result = inspect(
        tmp_path,
        '`notes.md` R4-N1: 함수 `alpha_fn`과 설정 키 `beta_flag`를 확인했다.\n'
        '두 토큰은 같은 자체 검토 항목의 설명이다.',
        '# Reference\ndef alpha_fn(): pass\nbeta_flag = True\n',
    )
    k9 = atoms(result, 'K9')
    assert len(k9) == 1
    assert k9[0]['verdict'] == 'unverifiable'
    assert k9[0]['reason_code'] == 'self_assigned_item_id'
    k4 = atoms(result, 'K4')
    assert {claim['text'] for claim in k4} >= {'`alpha_fn`', '`beta_flag`'}


def test_existing_item_multibacktick_description_matches_prose_source(tmp_path):
    result = inspect(
        tmp_path,
        '`notes.md` R1: functions `alphaHelper` and `betaHelper` are used together',
        '# R1\nfunctions alphaHelper and betaHelper are used together\n',
    )
    k9 = atoms(result, 'K9')
    assert len(k9) == 1
    assert k9[0]['verdict'] == 'match'
    assert {claim['text'] for claim in atoms(result, 'K4')} >= {'`alphaHelper`', '`betaHelper`'}


def test_existing_item_with_wrong_full_description_mismatches(tmp_path):
    result = inspect(
        tmp_path,
        '`notes.md` R1: always publish every secret',
        '# R1\nnever publish secrets\n',
    )
    k9 = atoms(result, 'K9')
    assert len(k9) == 1
    assert k9[0]['verdict'] == 'mismatch'
    assert k9[0]['reason_code'] == 'item_mismatch'
    assert result['status'] == 'FAIL'


def test_missing_item_in_established_prefix_family_is_absent(tmp_path):
    source = '\n'.join(f'# R{number}\nitem {number} description' for number in range(1, 9)) + '\n'
    result = inspect(tmp_path, '`notes.md` R9: invented ninth requirement', source)
    k9 = atoms(result, 'K9')
    assert len(k9) == 1
    assert k9[0]['verdict'] == 'absent'
    assert k9[0]['reason_code'] == 'not_found'
    assert result['status'] == 'FAIL'


def test_single_missing_prefix_item_remains_self_assigned_unknown(tmp_path):
    result = inspect(tmp_path, '`notes.md` R9: report-local review label', '# R1\nsource item\n')
    k9 = atoms(result, 'K9')
    assert len(k9) == 1
    assert k9[0]['verdict'] == 'unverifiable'
    assert k9[0]['reason_code'] == 'self_assigned_item_id'


def test_nested_item_prefix_family_uses_outer_number(tmp_path):
    source = '# R4-N1\nfirst nested item\n# R4-N2\nsecond nested item\n# R5-N1\nother family\n'
    result = inspect(tmp_path, '`notes.md` R4-N9: invented nested item', source)
    k9 = atoms(result, 'K9')
    assert len(k9) == 1
    assert k9[0]['verdict'] == 'absent'
    assert k9[0]['reason_code'] == 'not_found'


def test_multiline_item_description_stops_at_next_item_boundary(tmp_path):
    report = (
        '`notes.md` R1: first requirement\n'
        'continuation with exact detail\n'
        '- nested supporting sentence\n'
        'R2: second requirement\n'
        'second continuation'
    )
    source = (
        '# R1\nfirst requirement\ncontinuation with exact detail\n- nested supporting sentence\n'
        '# R2\nsecond requirement\nsecond continuation\n'
    )
    result = inspect(tmp_path, report, source)
    k9 = atoms(result, 'K9')
    assert len(k9) == 2
    assert [claim['verdict'] for claim in k9] == ['match', 'match']


def test_multiline_item_does_not_consume_next_item_text(tmp_path):
    report = '`notes.md` R1: first requirement\nwrong continuation\nR2: second requirement'
    source = '# R1\nfirst requirement\nright continuation\n# R2\nsecond requirement\n'
    result = inspect(tmp_path, report, source)
    k9 = atoms(result, 'K9')
    assert len(k9) == 2
    assert k9[0]['verdict'] == 'mismatch'
    assert k9[1]['verdict'] == 'match'


def test_item_section_stops_at_same_or_higher_markdown_heading(tmp_path):
    report = (
        '`notes.md` R1: first requirement\n'
        'continuation detail\n'
        '## Separate summary\n'
        'this summary is not part of R1'
    )
    source = '# R1\nfirst requirement\ncontinuation detail\n'
    result = inspect(tmp_path, report, source)
    k9 = atoms(result, 'K9')
    assert len(k9) == 1
    assert k9[0]['verdict'] == 'match'


def test_item_section_keeps_lower_markdown_heading(tmp_path):
    report = (
        '## `notes.md`\n'
        '### `R1`: first requirement\n'
        '#### Detail\n'
        'child detail\n'
        '## Summary\n'
        'not part of the item'
    )
    source = '# R1\nfirst requirement\n#### Detail\nchild detail\n'
    result = inspect(tmp_path, report, source)
    k9 = atoms(result, 'K9')
    assert len(k9) == 1
    assert k9[0]['verdict'] == 'match'


def test_inline_existing_item_wrong_description_is_preserved(tmp_path):
    result = inspect(
        tmp_path,
        '참고로 `notes.md`의 R1: “always publish secrets”를 확인했다.',
        '# R1\nnever publish secrets\n# R2\nsecond item\n',
    )
    k9 = atoms(result, 'K9')
    assert len(k9) == 1
    assert k9[0]['verdict'] == 'mismatch'
    assert k9[0]['reason_code'] == 'item_mismatch'


def test_inline_missing_item_in_known_family_is_preserved_as_absent(tmp_path):
    result = inspect(
        tmp_path,
        '참고로 `notes.md`의 R9: “invented requirement”를 확인했다.',
        '# R1\nfirst item\n# R2\nsecond item\n',
    )
    k9 = atoms(result, 'K9')
    assert len(k9) == 1
    assert k9[0]['verdict'] == 'absent'
    assert k9[0]['reason_code'] == 'not_found'


def test_inline_item_multibacktick_description_is_one_k9(tmp_path):
    result = inspect(
        tmp_path,
        '참고로 `notes.md`의 R4-N1: 함수 `alpha_fn`과 설정 키 `beta_flag`를 확인했다.',
        '# Reference\ndef alpha_fn(): pass\nbeta_flag = True\n',
    )
    k9 = atoms(result, 'K9')
    assert len(k9) == 1
    assert k9[0]['reason_code'] == 'self_assigned_item_id'
    assert {claim['text'] for claim in atoms(result, 'K4')} >= {'`alpha_fn`', '`beta_flag`'}


def test_complete_inline_item_quote_does_not_consume_unrelated_paragraphs(tmp_path):
    result = inspect(tmp_path, '`notes.md` R1: “validate every input”\n\n'
                     'Reference URL https://example.invalid/help\n\nSummary follows.',
                     '# R1\nvalidate every input\n')
    claim, = atoms(result, 'K9')
    assert claim['verdict'] == 'match'
    assert 'Reference URL' not in claim['text']


def test_wrong_inline_item_quote_still_fails_before_unrelated_paragraph(tmp_path):
    result = inspect(tmp_path, '`notes.md` R1: “skip every validation”\n\nSummary follows.',
                     '# R1\nvalidate every input\n')
    claim, = atoms(result, 'K9')
    assert claim['verdict'] == 'mismatch'
    assert result['status'] == 'FAIL'
