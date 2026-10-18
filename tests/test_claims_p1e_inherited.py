import pytest

from smtw.claims.extract import extract
from smtw.claims.model import Budget
from tests.test_claims_p1e_binding import inspect_files


def source():
    rows = [""] * 50
    rows[10:12] = ["def helper():", "    return 'ok'"]
    rows[39:41] = ["def make():", '    return Item("x", flag=True)']
    rows[47] = "# last sentinel line"
    return "\n".join(rows) + "\n"


def references(report):
    claims, _ = extract(report, Budget(10))
    return [c for c in claims if c.check_type == "K1"]


@pytest.mark.parametrize("join", [", ", " 및 ", "， "])
@pytest.mark.parametrize("target", [":40-45", ":41"])
def test_omitted_path_reference_owns_its_paraphrase(tmp_path, join, target):
    report = '`pkg/module.py:10-20` (`helper`)' + join + '`' + target + '` (`Item(..., flag=...)`)'
    refs = references(report)
    inherited = [c for c in refs if c.start in {40, 41}]
    assert len(inherited) == 1
    assert inherited[0].path == "pkg/module.py" and inherited[0].inherited_binding
    assert inherited[0].quote == "Item(..., flag=...)"
    assert not refs[0].quote
    result = inspect_files(tmp_path, report, {"pkg/module.py": source()})
    assert result["status"] == "INCOMPLETE"
    assert any(c["check_type"] == "K1" and c["reason_code"] == "paraphrased_quote"
               for c in result["claims"])


@pytest.mark.parametrize("quote", [
    "Item(..., flag=False)", "MissingItem(..., flag=...)",
    "return helper()", "return 'ok'",
])
def test_inherited_range_wrong_name_value_or_wrong_line_still_fails(tmp_path, quote):
    report = '`pkg/module.py:10-20` (`helper`), `:40-45` (`' + quote + '`)'
    refs = references(report)
    inherited = [c for c in refs if c.start == 40]
    assert inherited and inherited[0].quote == quote
    result = inspect_files(tmp_path, report, {"pkg/module.py": source()})
    assert result["status"] == "FAIL"
    assert any(c["check_type"] == "K1" and c["reason_code"] == "quote_mismatch"
               for c in result["claims"])


def test_bare_inherited_range_and_chained_reference(tmp_path):
    report = ('`pkg/module.py:10-20` (`helper`), :40-45 (`Item(..., flag=True)`), '
              '`:48` "last sentinel line"')
    refs = references(report)
    assert [(c.start, c.end, c.inherited_binding) for c in refs] == [
        (10, 20, False), (40, 45, True), (48, 48, True)]
    result = inspect_files(tmp_path, report, {"pkg/module.py": source()})
    assert result["status"] != "FAIL"


def test_nearest_explicit_file_owns_following_omitted_range():
    refs = references('`alpha.py:10`, `beta.py:20`, `:40-45` "a sufficiently long quote"')
    assert refs[-1].path == "beta.py" and refs[-1].start == 40 and refs[-1].inherited_binding


@pytest.mark.parametrize("tail", [
    "10:30", "3:1", "http://localhost:8080", "http://[::1]:8080",
    "`10:30`", "`3:1`", '"literal :40"', "`call(:40)`",
])
def test_time_ratio_port_and_literal_are_not_inherited_ranges(tail):
    refs = references("`alpha.py:10`, " + tail)
    assert [(c.path, c.start) for c in refs] == [("alpha.py", 10)]


@pytest.mark.parametrize("report", [
    "`:40-45` (`Item(..., flag=...)`)",
    "`alpha.py`, `:40-45`",
    "`alpha.py:10`. `:40-45`",
    "`alpha.py:10`; `:40-45`",
    "`alpha.py:10`<br>, `:40-45`",
    "| `alpha.py:10` | , `:40-45` |",
    "① `alpha.py:10` ② , `:40-45`",
    "`alpha.py:10` (`brief.md`), `:40-45`",
    "`alpha.py:10` 설명을 계속하고, `:40-45`",
])
def test_orphan_or_nonadjacent_range_does_not_inherit(report):
    assert not any(c.inherited_binding for c in references(report))
    assert not any(c.start == 40 for c in references(report))


@pytest.mark.parametrize("parenthesized", [False, True])
@pytest.mark.parametrize("value,expected", [("first literal sentence", "FAIL"), ("other literal sentence", "PASS")])
def test_direct_quote_belongs_only_to_inherited_range(tmp_path, parenthesized, value, expected):
    quote = "emit('" + value + "')"
    tail = "`" + quote + "`"
    if parenthesized:
        tail = "(" + tail + ")"
    report = "`alpha.py:1`, `:3` " + tail
    refs = references(report)
    assert refs[-1].start == 3 and refs[-1].quote == quote
    result = inspect_files(tmp_path, report, {
        "alpha.py": "emit('first literal sentence')\n\nemit('other literal sentence')\n"})
    assert result["status"] == expected
