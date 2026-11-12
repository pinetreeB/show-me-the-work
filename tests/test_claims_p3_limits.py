import pytest
from smtw.claims import judge
from smtw.claims.assist import select_candidates


def test_default_selection_limit_is_forty():
    selected = select_candidates("\n".join(f"Latency {i} ms." for i in range(50)), {"claims": []})
    assert selected["limit"] == 40
    assert selected["selected_count"] == 40 and selected["deferred_count"] == 10


@pytest.mark.parametrize("five,week", [(40, 0), (0, 55), (41, 56)])
def test_new_usage_thresholds(five, week):
    with pytest.raises(ValueError, match="threshold"):
        judge.usage_gate({"known": True, "five_hour": five, "weekly": week}, False)


def test_below_usage_thresholds_allowed():
    judge.usage_gate({"known": True, "five_hour": 39.99, "weekly": 54.99}, False)


@pytest.mark.parametrize("maximum", [9, 10, 0, True])
def test_max_calls_rejected_before_any_reads(maximum):
    with pytest.raises(ValueError, match="between 1 and 8"):
        judge.judge(None, scope_file=None, output_root=None, max_calls=maximum)


def test_max_calls_eight_passes_numeric_gate():
    with pytest.raises(ValueError, match="visible terminal"):
        judge.judge(None, scope_file=None, output_root=None, max_calls=8, tty=lambda: False)
