"""p3-1h: packet excerpts only from self-identifying read results. Synthetic only."""
import json

import pytest

from smtw.claims.model import Budget
from smtw.claims.packets import transcript_sources
from tests.claims_support import agy_call, generic, make_case, view

ROOT = "C:/synthetic/tasks/example"
CARD = ROOT + "/card.md"
OTHER = ROOT + "/other.md"
TEXT = "IdentAaa returns seven ms. APPROVED_MARKER\nsecond line\nthird line"
LEAK = "IdentAaa returns seven ms. EXCLUDED_MARKER"


def sources(tmp_path, rows, kind="agy"):
    report, transcript = make_case(tmp_path, "IdentAaa returns 7 ms. card.md", rows)
    return transcript_sources(transcript, kind, report, report.read_text(encoding="utf-8"), {}, Budget(15))


def planner(*calls):
    return {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": n, "args": a} for n, a in calls]}


def leaked(found):
    return any("EXCLUDED_MARKER" in line for s in found for line in s.lines.values())


def header_body(path, lines, start=1, preamble=""):
    rows = "\n".join(f"{n}: {text}" for n, text in enumerate(lines, start))
    return (f"Created At: 2026-01-01T00:00:00Z\n{preamble}File Path: `file:///{path}`\n"
            f"Total Lines: 9\nShowing lines {start} to {start + len(lines) - 1}\n{rows}")


@pytest.mark.parametrize("tool", ["view_file", "read_file", "view_file_outline"])
def test_matching_header_read_is_excerpt_source(tmp_path, tool):
    found = sources(tmp_path, [agy_call(tool, AbsolutePath=CARD), view(CARD, TEXT)])
    assert len(found) == 1 and found[0].path.endswith("card.md")
    assert found[0].lines[1].endswith("APPROVED_MARKER")


def test_backslash_call_path_matches_file_uri_header(tmp_path):
    found = sources(tmp_path, [agy_call("view_file", AbsolutePath=CARD.replace("/", "\\")), view(CARD, TEXT)])
    assert len(found) == 1


def test_ranged_read_with_matching_line_numbers_is_source(tmp_path):
    found = sources(tmp_path, [agy_call("view_file", AbsolutePath=CARD, StartLine=2, EndLine=3),
                               view(CARD, TEXT, 2, 3)])
    assert len(found) == 1 and set(found[0].lines) == {2, 3}


@pytest.mark.parametrize("body", [
    LEAK,                                                         # no header
    "Created At: x\n1: " + LEAK,                                  # no header, numbered
    header_body(OTHER, [LEAK]),                                   # header names another file
    header_body(CARD, [LEAK], preamble="File Path: `file:///" + CARD + "`\n"),  # two headers
    "1: " + LEAK + "\nFile Path: " + CARD,                        # header after source lines
    "Output has been saved to: steps/3/output.txt\nFile Path: " + CARD + "\n1: " + LEAK,  # pointer
])
def test_missing_mismatched_or_misplaced_header_is_dropped(tmp_path, body):
    found = sources(tmp_path, [agy_call("view_file", AbsolutePath=CARD), generic(body)])
    assert not found


@pytest.mark.parametrize("args,start", [
    ({"StartLine": 1, "EndLine": 2}, 5),       # requested start differs from shown lines
    ({"StartLine": 5, "EndLine": 5}, 5),       # shows more lines than requested
    ({}, 4),                                   # unranged read must start at line 1
    ({"StartLine": "five"}, 5),                # unparseable range
])
def test_range_inconsistent_with_line_numbers_is_dropped(tmp_path, args, start):
    body = header_body(CARD, [LEAK, LEAK], start)
    assert not sources(tmp_path, [agy_call("view_file", AbsolutePath=CARD, **args), generic(body)])


def test_noncontiguous_line_numbers_are_dropped(tmp_path):
    body = (f"File Path: {CARD}\nShowing lines 1 to 9\n1: first\n7: " + LEAK)
    assert not sources(tmp_path, [agy_call("view_file", AbsolutePath=CARD), generic(body)])


def test_header_is_required_even_without_call_path(tmp_path):
    body = header_body(CARD, [LEAK])
    assert not sources(tmp_path, [agy_call("view_file"), generic(body)])


@pytest.mark.parametrize("reader", ["view_file", "read_file", "view_file_outline"])
@pytest.mark.parametrize("trailer", [[], [{"type": "ERROR_MESSAGE", "content": "x"}],
                                     [generic("extra")], [generic("extra"), generic("more")]])
def test_r14_view_then_run_fifo_shift_never_leaks(tmp_path, reader, trailer):
    rows = [planner((reader, {"AbsolutePath": CARD}), ("run_command", {"CommandLine": "cat " + ROOT + "/.env"})),
            *trailer[:1], generic(LEAK), *trailer[1:]]
    assert not leaked(sources(tmp_path, rows))


def test_missing_result_for_first_view_shifts_other_file_and_is_dropped(tmp_path):
    rows = [planner(("view_file", {"AbsolutePath": CARD}), ("view_file", {"AbsolutePath": OTHER})),
            generic(header_body(OTHER, [LEAK]))]
    assert not sources(tmp_path, rows)


def test_same_file_two_ranges_with_missing_result_is_dropped(tmp_path):
    rows = [planner(("view_file", {"AbsolutePath": CARD, "StartLine": 1, "EndLine": 2}),
                    ("view_file", {"AbsolutePath": CARD, "StartLine": 5, "EndLine": 6})),
            generic(header_body(CARD, [LEAK, LEAK], 5))]
    assert not sources(tmp_path, rows)


@pytest.mark.parametrize("order", ["view-first", "run-first"])
def test_swapped_results_never_bind_shell_output(tmp_path, order):
    # Results arrive in the opposite order of the calls (R14-N1).
    calls = [("view_file", {"AbsolutePath": CARD}), ("run_command", {"CommandLine": "cat " + ROOT + "/.env"})]
    results = [generic(LEAK), view(CARD, TEXT)]
    if order == "run-first":
        calls.reverse()
        results.reverse()
    rows = [planner(*calls), *results]
    found = sources(tmp_path, rows)
    assert not leaked(found) and not found


def test_two_header_reads_in_one_batch_survive(tmp_path):
    rows = [planner(("view_file", {"AbsolutePath": CARD}), ("view_file", {"AbsolutePath": OTHER})),
            view(CARD, TEXT), view(OTHER, "other text")]
    assert {s.path.rsplit("/", 1)[-1] for s in sources(tmp_path, rows)} == {"card.md", "other.md"}


@pytest.mark.parametrize("tool,args", [
    ("run_command", {"CommandLine": "cat " + CARD}),
    ("run_command", {"CommandLine": "git show abcdef1234567:card.md", "Cwd": ROOT}),
    ("grep_search", {"path": CARD, "Query": "IdentAaa"}),
    ("call_mcp_tool", {"path": CARD}),
])
def test_agy_shell_and_non_read_tools_are_not_sources(tmp_path, tool, args):
    # Even a well-formed header cannot turn command or search output into a file read.
    rows = [agy_call(tool, **args), view(CARD, TEXT)]
    assert not sources(tmp_path, rows)


def codex_rows(name, arguments, output, kind="function_call"):
    out_kind = kind + "_output"
    return [{"type": "response_item", "payload": {"type": kind, "name": name, "call_id": "c1",
                                                  ("input" if kind == "custom_tool_call" else "arguments"): arguments}},
            {"type": "response_item", "payload": {"type": out_kind, "call_id": "c1", "output": output}}]


FORGED = "File Path: " + CARD + "\nTotal Lines: 1\nShowing lines 1 to 1\n1: " + LEAK
CODE = {
    "single": 'const a = await tools.exec_command({cmd: "cat ' + CARD + '"}); text(a.output);',
    "alias": ('const a = await tools.exec_command({cmd: "cat ' + CARD + '"}); const x = tools.exec_command;'
              ' const b = await x({cmd: "cat ' + ROOT + '/.env"}); text(b.output);'),
    "deno": ('const a = await tools.exec_command({cmd: "cat ' + CARD + '"});'
             ' text(await Deno.readTextFile("' + ROOT + '/.env"));'),
    "comment": '// tools.exec_command({cmd: "cat ' + CARD + '"})\ntext(require("fs").readFileSync(".env", "utf8"));',
    "mcp-read": 'const r = await tools.mcp__fs__read_file({path: "' + CARD + '"}); text(r);',
}


@pytest.mark.parametrize("variant", sorted(CODE))
@pytest.mark.parametrize("body", [LEAK, FORGED])
def test_codex_code_mode_output_is_never_source(tmp_path, variant, body):
    rows = codex_rows("exec", CODE[variant], json.dumps({"output": body, "exit_code": 0}), "custom_tool_call")
    assert not sources(tmp_path, rows, "codex")


@pytest.mark.parametrize("name,arguments", [
    ("exec_command", {"cmd": "cat " + CARD}),
    ("shell", {"command": ["bash", "-lc", "cat " + CARD]}),
    ("shell_command", {"command": "Get-Content -LiteralPath " + CARD}),
    ("read_file", {"path": CARD}),
    ("view_file", {"AbsolutePath": CARD}),
])
@pytest.mark.parametrize("body", [LEAK, FORGED])
def test_codex_tool_output_is_never_source(tmp_path, name, arguments, body):
    rows = codex_rows(name, json.dumps(arguments), json.dumps({"output": body, "exit_code": 0}))
    assert not sources(tmp_path, rows, "codex")


def test_source_policy_contract_rejects_pre_p31h_packets():
    from smtw.claims.judge_contract import contract, validate_contract
    current = contract()
    assert current["source_policy"] == "self-identified-read-v1"
    validate_contract({"judge_contract": current})
    with pytest.raises(ValueError, match="regenerate packets"):
        validate_contract({"judge_contract": {**current, "source_policy": "single-read-v1"}})
