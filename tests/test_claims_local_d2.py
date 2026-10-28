import json

import pytest

from smtw.claims.local_http import LocalHttpError
from smtw.claims.local_judge import LocalConfig, LocalJudgeRunError, run_local_judge
from smtw.claims.code_identity import code_sha256
from smtw.claims.packets import write_packets
from tests.claims_judge_support import make_scope


def d2_config():
    return LocalConfig(address="http://127.0.0.1:8099", model="judge-qwen38-27b",
                       deadline_seconds=180, input_tokens=14000, output_tokens=1024,
                       json_mode="schema", protocol="openai-compatible", seed=42,
                       temperature=0, reasoning="off")


def folder(tmp_path):
    item = {"id": "d2-001", "claim": "The limit is 7.", "before": "", "after": "",
            "branch": "unverifiable", "report_line": 1, "redactions": {},
            "fixed_commit": True, "target_evidence": [{"path": "~/example-repo/a.py", "start_line": 1,
                                                           "end_line": 1, "excerpt_index": 1}],
            "atom": {"check_type": "x", "ordinal": 1},
            "excerpts": [{"path": "~/example-repo/a.py", "ref": 1, "text": "1: MAX_LIMIT = 7",
                          "source": "fixed_commit", "revision": "a" * 40, "redactions": {}}]}
    result = tmp_path / "packets"
    write_packets(result, [{"version": 1, "instructions": "data", "data_block": [item]}],
                  {"case_id": "example-case"})
    return result


def good_response():
    return {"model": "judge-qwen38-27b", "status": 200,
            "body": json.dumps({"judgments": [{"id": "d2-001", "verdict": "supported",
                                                   "rationale": "Literal excerpt.", "evidence_ref": 1,
                                                   "evidence_quote": "MAX_LIMIT = 7"}]}),
            "usage": {"input_tokens": 10, "output_tokens": 10}, "finish_reason": "stop"}


def test_d2_request_strips_local_metadata_and_persists_no_raw_response(tmp_path):
    seen = []
    result = run_local_judge(folder(tmp_path), scope_file=make_scope(tmp_path), output_root=tmp_path / "out",
                             config=d2_config(), transport=lambda request: seen.append(request) or good_response(),
                             max_calls=12)
    request = seen[0]
    rendered = json.dumps(request["untrusted_data"])
    visible_item = request["untrusted_data"]["data_block"][0]
    for hidden in ("fixed_commit", "target_evidence", "other_file_evidence", "atom"):
        assert hidden not in visible_item
    assert request["claim_ids"] == ["d2-001"] and "run_deadline" in request
    written = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "out").rglob("*")
                        if path.is_file())
    assert '"body"' not in written
    assert result["not_evaluated"] == 0


def test_d2_structured_transport_error_stops_run_and_marks_remaining(tmp_path):
    safe = {"request_id": "req", "claim_ids": ["d2-001"], "stage": "chat", "kind": "connection_dropped",
            "http_status": None, "elapsed_ms": 3, "not_evaluated": True,
            "server_computation_ended": "UNKNOWN"}
    with pytest.raises(LocalJudgeRunError) as raised:
        run_local_judge(folder(tmp_path), scope_file=make_scope(tmp_path), output_root=tmp_path / "out",
                        config=d2_config(), transport=lambda _request: (_ for _ in ()).throw(LocalHttpError(
                            safe, {"input_tokens": 14001, "input_token_method": "conservative_fallback",
                                   "tokenizer_tokens": 10, "estimated_template_tokens": 14001})))
    assert raised.value.result["not_evaluated"] == 1
    call = json.loads((tmp_path / "out" / "calls.jsonl").read_text(encoding="utf-8"))
    assert {key: call[key] for key in safe} == safe
    assert call["code_sha256"] == code_sha256()
    assert call["transport_receipt"] == {"input_tokens": 14001,
                                           "input_token_method": "conservative_fallback",
                                           "tokenizer_tokens": 10, "estimated_template_tokens": 14001}
    summary = json.loads(next((tmp_path / "out").rglob("run-summary.json")).read_text(encoding="utf-8"))
    assert summary["remaining_item_ids"] == ["d2-001"]
    assert summary["code_sha256"] == code_sha256()
    assert {key: summary[key] for key in ("error", "stage", "http_status",
                                          "server_computation_ended", "next_request", "operator_notice")} == {
        "error": "connection_dropped", "stage": "chat", "http_status": None,
        "server_computation_ended": "UNKNOWN", "next_request": "HOLD",
        "operator_notice": "서버 연산 종료 미확인 — 다음 요청 보류"}


def test_d2_run_limit_is_injected_and_stops_before_transport(tmp_path):
    ticks = iter((0.0, 901.0, 902.0, 903.0, 904.0))
    with pytest.raises(LocalJudgeRunError) as raised:
        run_local_judge(folder(tmp_path), scope_file=make_scope(tmp_path), output_root=tmp_path / "out",
                        config=d2_config(), transport=lambda _request: pytest.fail("transport must not run"),
                        clock=lambda: next(ticks))
    assert raised.value.result["error"] == "run_deadline_exceeded"
