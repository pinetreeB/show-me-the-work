import pytest

from smtw.claims.check import run
from smtw.claims.extract import extract
from smtw.claims.model import Budget


def inspect_files(tmp_path, report, files):
    report_path = tmp_path / "audit.md"
    report_path.write_text(report, encoding="utf-8")
    sources = {}
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        sources[name] = str(path)
    return run(report_path, sources=sources)


@pytest.mark.parametrize("separator", [" ② ", ". ② ", " (2) ", "<br>", "<br/>", " | "])
def test_enumeration_html_and_cell_boundaries_do_not_attach_next_quote(tmp_path, separator):
    report = '| ① `alpha.py:1` 설명' + separator + '지시서(`brief.md` §제한)에서 "다시 켜지 않는다"고 명시함. |'
    result = inspect_files(tmp_path, report, {"alpha.py": "VALUE = 7\n", "brief.md": "다시 켜지 않는다\n"})
    assert result["status"] != "FAIL"
    references, _ = extract(report, Budget(10))
    assert all(not c.quote for c in references if c.check_type == "K1" and c.path == "alpha.py")
    assert any(c["check_type"] == "K5" and c["verdict"] == "match" for c in result["claims"])


def test_three_enumerated_items_keep_parenthetical_code_reference_separate(tmp_path):
    report = ('| ① `alpha.py:1-5`에는 값이 있다. ② `beta.py:9-20`는 '
              '`LIMIT = 1`(`gamma.py:23`) 초과 위험. ③ 지시서(`brief.md` §제한)에서 '
              '"다시 켜지 않는다"고 명시함. |')
    files = {"alpha.py": "value\n" * 5, "beta.py": "value\n" * 20,
             "gamma.py": "\n" * 22 + "LIMIT = 1\n", "brief.md": "다시 켜지 않는다\n"}
    result = inspect_files(tmp_path, report, files)
    assert result["status"] != "FAIL"
    references, _ = extract(report, Budget(10))
    gamma = [c for c in references if c.check_type == "K1" and c.path == "gamma.py"]
    assert gamma and all(c.quote != "다시 켜지 않는다" for c in gamma)


@pytest.mark.parametrize("separator", [" ② ", " (2) ", "<br>", " | "])
def test_same_item_wrong_quote_remains_fail(tmp_path, separator):
    report = '| ① 앞 설명' + separator + '`alpha.py:1` "there is no such text" |'
    result = inspect_files(tmp_path, report, {"alpha.py": "VALUE = 7\n"})
    assert result["status"] == "FAIL"
    assert any(c["check_type"] == "K1" and c["reason_code"] == "quote_mismatch"
               for c in result["claims"])


def test_adjacent_wrong_quote_without_boundary_is_still_fail(tmp_path):
    result = inspect_files(tmp_path, '`alpha.py:1` "there is no such text"', {"alpha.py": "VALUE = 7\n"})
    assert result["status"] == "FAIL"


@pytest.mark.parametrize("source", ["`brief.md`에서", "지시서(`brief.md` §제한)에서", "문서 §제한에서"])
def test_new_explicit_source_does_not_borrow_earlier_range(tmp_path, source):
    report = '| `alpha.py:1` 요약; ' + source + ' "다시 켜지 않는다"고 명시함. |'
    result = inspect_files(tmp_path, report, {"alpha.py": "VALUE = 7\n", "brief.md": "다시 켜지 않는다\n"})
    assert result["status"] != "FAIL"
    assert any(c["check_type"] == "K5" for c in result["claims"])


def test_unscoped_quote_after_enumeration_is_retained_unknown(tmp_path):
    report = '| ① `alpha.py:1` 설명 ② "there is no such text" |'
    result = inspect_files(tmp_path, report, {"alpha.py": "VALUE = 7\n"})
    quotes = [c for c in result["claims"] if c["check_type"] == "K5"]
    assert quotes and all(c["verdict"] == "unverifiable" for c in quotes)
    assert result["status"] != "FAIL"


def test_enumeration_characters_inside_quote_are_literal_not_boundaries(tmp_path):
    result = inspect_files(tmp_path, '`brief.md:1` "first ① second (2) third"',
                           {"brief.md": "first ① second (2) third\n"})
    assert result["status"] == "PASS"


def test_path_used_as_data_does_not_override_numbered_code_source(tmp_path):
    report = ('`alpha.py:1-3`는 `C:/synthetic/brief.md`에서 부분을 분리하고 '
              '`brief.md`를 이름으로 처리하며, `2: return missing_name(value)`')
    result = inspect_files(tmp_path, report, {"alpha.py": "def run(value):\n    return real_name(value)\n",
                                              "brief.md": "다시 켜지 않는다\n"})
    assert result["status"] == "FAIL"
