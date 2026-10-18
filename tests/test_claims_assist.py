import json
import subprocess
import sys
from copy import deepcopy

import pytest

from smtw.claims.assist import select_candidates


def checked(rows):
    return {"claims": [{"report_line": i + 1, "check_type": "K5", "text": text,
                        "verdict": verdict, "reason_code": reason}
                       for i, (text, verdict, reason) in enumerate(rows)]}


def test_priorities_cap_and_no_input_mutation():
    rows = [
        ("alpha statement.", "unverifiable", "partial_range"),
        ("beta statement.", "range_only", "range_only"),
        ("gamma statement.", "unverifiable", "paraphrased_quote"),
        ("delta statement.", "unverifiable", "observation_conflict"),
    ]
    report = "\n".join(x[0] for x in rows) + '\n성능은 7 ms였다.\n'
    original = checked(rows)
    snapshot = deepcopy(original)
    result = select_candidates(report, original, limit=3, fixed_commit=True)
    assert original == snapshot
    assert [x["branch"] for x in result["items"]] == [
        "observation_conflict", "paraphrased_quote", "not_extracted_numeric_or_quote"]
    assert result["candidate_count"] == 5
    assert result["deferred_count"] == 2
    assert select_candidates(report, original, limit=3, fixed_commit=True) == result


@pytest.mark.parametrize("reason", [
    "command_result_missing", "binding_ambiguous", "malformed_number",
    "measurement_missing", "repo_scope_unknown", "commit_missing_file",
])
def test_excluded_atoms_not_reintroduced_as_sentences(reason):
    rows = [('`SomeName` 설명이 있다.', "unverifiable", reason)]
    assert select_candidates(rows[0][0], checked(rows))["candidate_count"] == 0


@pytest.mark.parametrize("verdict", ["absent", "mismatch", "match"])
def test_decided_atoms_are_not_selected(verdict):
    rows = [('`SomeName` 설명이 있다.', verdict, "observation_conflict")]
    assert select_candidates(rows[0][0], checked(rows))["candidate_count"] == 0


@pytest.mark.parametrize("reason", ["pointer_missing", "no_read_of_file", "partial_range"])
def test_fixed_commit_gate(reason):
    rows = [("Alpha function exists.", "unverifiable", reason)]
    assert select_candidates(rows[0][0], checked(rows))["selected_count"] == 0
    assert select_candidates(rows[0][0], checked(rows), fixed_commit=True)["selected_count"] == 1


def test_unextracted_sentence_on_same_line_and_code_punctuation():
    report = 'alpha.py:1 첫 설명. renderWidget 호출은 7 ms였다. 일반 설명.'
    claims = [{"report_line": 1, "check_type": "K1", "text": "alpha.py:1 첫 설명",
               "verdict": "range_only", "reason_code": "range_only"}]
    result = select_candidates(report, {"claims": claims})
    assert len(result["items"]) == 2
    missing = next(x for x in result["items"] if x["branch"].startswith("not_extracted"))
    assert missing["claim"] == "renderWidget 호출은 7 ms였다."
    assert missing["before"] == "alpha.py:1 첫 설명."
    assert missing["after"] == "일반 설명."
    assert "renderWidget" in missing["identifiers"]


def test_unextracted_triggers_and_plain_text_exclusion():
    report = '위치는 alpha.py다.\n위치는 L8이다.\n성능은 9 ms다.\n“간단한 문구”라고 설명했다.\nrenderWidget은 결과를 반환한다.\n평범한 설명입니다.'
    result = select_candidates(report, {"claims": []}, limit=20)
    assert result["candidate_count"] == 5


def test_fences_and_table_cells():
    report = '```python\nx = "example"\n```\n| 설명 | 5 ms였다. |\n|---|---|'
    result = select_candidates(report, {"claims": []})
    assert result["candidate_count"] == 1
    assert result["items"][0]["claim"] == "5 ms였다."


def test_zero_cap_and_invalid_limit():
    result = select_candidates("8 ms", {"claims": []}, limit=0)
    assert result["selected_count"] == 0 and result["deferred_count"] == 1
    with pytest.raises(ValueError):
        select_candidates("", {"claims": []}, limit=-1)


def test_cli_select_aggregate_and_checker_unchanged(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("renderWidget은 결과를 반환한다.\n", encoding="utf-8")
    base = [sys.executable, "-B", "-m", "smtw", "claims"]
    before = subprocess.run([*base, "check", "--report", str(report), "--format", "json"],
                            capture_output=True, text=True, encoding="utf-8")
    selected = subprocess.run([*base, "assist", "--report", str(report), "--select-only",
                               "--aggregate", "--format", "json"],
                              capture_output=True, text=True, encoding="utf-8")
    after = subprocess.run([*base, "check", "--report", str(report), "--format", "json"],
                           capture_output=True, text=True, encoding="utf-8")
    assert selected.returncode == 0, selected.stdout + selected.stderr
    data = json.loads(selected.stdout)
    assert "items" not in data and data["candidate_count"] == 1
    a, b = json.loads(before.stdout), json.loads(after.stdout)
    a.pop("elapsed_seconds")
    b.pop("elapsed_seconds")
    assert before.returncode == after.returncode and a == b
