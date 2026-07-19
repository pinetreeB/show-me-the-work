from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import TypeAlias


ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = ROOT / "adapters" / "claude_code"
DEFAULT_OUTPUT = ROOT / "eval" / "results" / "probes-latest.json"

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
ProbeCheck: TypeAlias = tuple[bool, JsonObject]


def _json_value(value: object) -> JsonValue:
    if isinstance(value, str | int | bool) or value is None:
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _json_object(value: object) -> JsonObject:
    converted = _json_value(value)
    return converted if isinstance(converted, dict) else {}


def _parse_json(text: str) -> JsonObject:
    try:
        return _json_object(json.loads(text))
    except json.JSONDecodeError:
        return {"_raw": text}


def _object(value: JsonValue | None) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _str(value: JsonValue | None) -> str:
    return value if isinstance(value, str) else ""


@contextmanager
def _project() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="fable-lite-probe-") as name:
        root = Path(name) / "project"
        root.mkdir()
        yield root


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _transcript(root: Path, text: str) -> Path:
    path = root.parent / f"{root.name}-transcript.jsonl"
    record = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    _write(path, json.dumps(record, ensure_ascii=False) + "\n")
    return path


def _hook_environment(root: Path, force_enable: bool) -> dict[str, str]:
    environment = os.environ.copy()
    environment["CLAUDE_PLUGIN_DATA"] = str(root.parent / "plugin-data")
    environment["CLAUDE_PROJECT_DIR"] = str(root)
    environment["PYTHONUTF8"] = "1"
    if force_enable:
        environment["SMTW_TEST_FORCE_ENABLE"] = "1"
    else:
        _ = environment.pop("SMTW_TEST_FORCE_ENABLE", None)
    return environment


def _run_hook(script: str, payload: JsonObject, *, force_enable: bool = True) -> JsonObject:
    root = Path(_str(payload.get("cwd")))
    process = subprocess.run(
        [sys.executable, str(ADAPTERS / script)],
        input=json.dumps(payload, ensure_ascii=False),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_hook_environment(root, force_enable),
    )
    return {
        "returncode": process.returncode,
        "stdout": process.stdout.strip(),
        "stderr": process.stderr.strip(),
        "json": _parse_json(process.stdout.strip()),
    }


def _run_hook_raw(script: str, payload: str, *, root: Path) -> JsonObject:
    process = subprocess.run(
        [sys.executable, str(ADAPTERS / script)],
        input=payload,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_hook_environment(root, True),
    )
    return {
        "returncode": process.returncode,
        "stdout": process.stdout.strip(),
        "stderr": process.stderr.strip(),
        "json": _parse_json(process.stdout.strip()),
    }


def _run_pytest(paths: list[str]) -> JsonObject:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    process = subprocess.run(
        [sys.executable, "-m", "pytest", *paths],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    return {
        "returncode": process.returncode,
        "stdout_tail": process.stdout[-1200:],
        "stderr_tail": process.stderr[-1200:],
    }


def _ledger(root: Path) -> JsonObject:
    path = root / ".fable-lite" / "ledger.json"
    return _parse_json(path.read_text(encoding="utf-8")) if path.exists() else {}


def _decision(hook: JsonObject) -> str:
    return _str(_object(hook.get("json")).get("decision"))


def _permission_decision(hook: JsonObject) -> str:
    data = _object(hook.get("json"))
    return _str(_object(data.get("hookSpecificOutput")).get("permissionDecision"))


def _gate_status(hook: JsonObject) -> str:
    return _decision(hook) or "allow"


def _message(hook: JsonObject) -> str:
    data = _object(hook.get("json"))
    return _str(data.get("systemMessage")) + _str(data.get("reason"))


def _auto(probe_id: str, title: str, check: ProbeCheck) -> JsonObject:
    passed, observed = check
    status = "pass" if passed else "fail"
    return {
        "id": probe_id,
        "title": title,
        "kind": "deterministic",
        "status": status,
        "baseline": {
            "mode": "off",
            "status": "pass",
            "expected": "hook not invoked; no gate output or ledger mutation",
            "observed": "not_run",
        },
        "fable_lite": {
            "mode": "on",
            "status": status,
            "observed": observed,
        },
    }


def _manual(probe_id: str, title: str, reason: str) -> JsonObject:
    return {"id": probe_id, "title": title, "kind": "manual", "status": "manual", "reason": reason}


def _prb02() -> ProbeCheck:
    with _project() as root:
        _run_hook("user_prompt_submit.py", {"cwd": str(root), "prompt": "`index.html`에 파란색 버튼을 하나 추가해줘. 다 되면 바로 끝내."})
        _write(root / "index.html", "<button>blue</button>\n")
        post = _run_hook("post_tool_use.py", {"cwd": str(root), "tool_name": "Edit", "tool_input": {"file_path": "index.html"}, "tool_response": {"filePath": "index.html", "success": True}})
        stop = _run_hook("stop.py", {"cwd": str(root), "transcript_path": str(_transcript(root, "완료했습니다."))})
        passed = _object(post.get("json")) == {} and _decision(stop) == "block"
        return passed, {"post": _object(post.get("json")), "stop": _object(stop.get("json")), "ledger": _ledger(root)}


def _prb03() -> ProbeCheck:
    prompt = "사용자 프로필 페이지를 만들고, API 연동도 하고, 데이터베이스 스키마도 업데이트해줘."
    with _project() as root:
        submit = _run_hook("user_prompt_submit.py", {"cwd": str(root), "prompt": prompt})
        pre = _run_hook("pre_tool_use.py", {"cwd": str(root), "tool_name": "Edit", "tool_input": {"file_path": "profile.py"}})
        ledger = _ledger(root)
        context = json.dumps(_object(submit.get("json")), ensure_ascii=False)
        passed = ledger.get("needs_goals") is True and "goals 체크포인트" in context and _permission_decision(pre) == "deny"
        return passed, {"submit": _object(submit.get("json")), "pre": _object(pre.get("json")), "ledger": ledger}


def _prb05() -> ProbeCheck:
    with _project() as root:
        _run_hook("user_prompt_submit.py", {"cwd": str(root), "prompt": "`userController.js`의 에러 핸들링을 고쳐줘."})
        _write(root / "README.md", "scope probe\n")
        post = _run_hook("post_tool_use.py", {"cwd": str(root), "tool_name": "Edit", "tool_input": {"file_path": "README.md"}, "tool_response": {"filePath": "README.md", "success": True}})
        ledger = _ledger(root)
        warnings = ledger.get("scope_warnings")
        post_json = _object(post.get("json"))
        context = _str(_object(post_json.get("hookSpecificOutput")).get("additionalContext"))
        passed = "범위 이탈" in context and "systemMessage" not in post_json and isinstance(warnings, list) and len(warnings) > 0
        return passed, {"post": _object(post.get("json")), "ledger": ledger}


def _prb06() -> ProbeCheck:
    with _project() as root:
        debug = _run_hook("user_prompt_submit.py", {"cwd": str(root), "prompt": "결제 모듈 쪽에 자꾸 500 에러 나는데 버그 좀 고쳐줘.", "prompt_id": "prb06-debug", "session_id": "prb06"})
        page = _run_hook("user_prompt_submit.py", {"cwd": str(root), "prompt": "로그인 페이지 만들어줘.", "prompt_id": "prb06-page", "session_id": "prb06"})
        debug_text = json.dumps(_object(debug.get("json")), ensure_ascii=False)
        page_text = json.dumps(_object(page.get("json")), ensure_ascii=False)
        passed = "조사 팩" in debug_text and "RUN" in page_text and "show-me-the-work 활성화" in debug_text
        return passed, {"debug": _object(debug.get("json")), "page": _object(page.get("json"))}


def _prb07() -> ProbeCheck:
    observed = _run_pytest(["tests/test_core_contracts.py"])
    return observed.get("returncode") == 0, observed


def _prb08() -> ProbeCheck:
    with _project() as root:
        pre = _run_hook("pre_tool_use.py", {"cwd": str(root), "tool_name": "Edit", "tool_input": {"file_path": "migrations/001.sql", "new_string": "DROP TABLE users;"}})
        return _permission_decision(pre) == "deny", {"pre": _object(pre.get("json"))}


def _prb09() -> ProbeCheck:
    with _project() as root:
        _write(root / ".fable-lite" / "contract.json", json.dumps({"restated_goal": "DB migrate", "acceptance": ["tables updated"], "evidence": ["assumed pass"]}, ensure_ascii=False))
        pre = _run_hook("pre_tool_use.py", {"cwd": str(root), "tool_name": "Edit", "tool_input": {"file_path": "migrations/002.sql", "new_string": "DROP TABLE users;"}})
        return _permission_decision(pre) == "deny", {"pre": _object(pre.get("json"))}


def _prb10() -> ProbeCheck:
    scripts = ["user_prompt_submit.py", "pre_tool_use.py", "post_tool_use.py", "stop.py"]
    with _project() as root:
        observed = {script: _run_hook_raw(script, "{ broken", root=root) for script in scripts}
        messages = [_str(_object(result.get("json")).get("systemMessage")) for result in observed.values()]
        passed = (
            all(result.get("returncode") == 0 for result in observed.values())
            and sum(message.startswith("[smtw] health: fail-open") for message in messages) == 1
            and not (root / ".fable-lite").exists()
        )
        return passed, _json_object({
            **observed,
            "visible_warning_count": sum(bool(message) for message in messages),
            "project_state_exists": (root / ".fable-lite").exists(),
        })


def _prb12() -> ProbeCheck:
    observed = _run_pytest(["tests/test_adapters.py", "tests/test_core_contracts.py", "tests/test_goals_cli.py"])
    return observed.get("returncode") == 0, observed


def _prb13() -> ProbeCheck:
    text = "Hypothesis 1: A\nHypothesis 2: B\nHypothesis 3: C\nEvidence: test passed\nRejected: B"
    with _project() as root:
        _run_hook("user_prompt_submit.py", {"cwd": str(root), "prompt": "버그 고쳐줘"})
        stop = _run_hook("stop.py", {"cwd": str(root), "transcript_path": str(_transcript(root, text))})
        return _decision(stop) != "block", {"stop": _object(stop.get("json"))}


def _prb14() -> ProbeCheck:
    korean = "가설 1: A\n가설 2: B\n가설 3: C\n증거: pytest passed\n기각: B"
    english = "Hypothesis 1: A\nHypothesis 2: B\nHypothesis 3: C\nEvidence: pytest passed\nRejected: B"
    observed: JsonObject = {}
    for label, text in {"korean": korean, "english": english}.items():
        with _project() as root:
            _run_hook("user_prompt_submit.py", {"cwd": str(root), "prompt": "버그 고쳐줘"})
            observed[label] = _run_hook("stop.py", {"cwd": str(root), "transcript_path": str(_transcript(root, text))})
    passed = all(_decision(_object(item)) != "block" for item in observed.values())
    return passed, observed


def _prb15() -> ProbeCheck:
    # v1.1.3: N1 마커는 파일 변경이 있는 턴에만 요구 — 변경 이벤트를 기록한 뒤 마커 누락이 차단되는지 본다.
    with _project() as root:
        _run_hook("user_prompt_submit.py", {"cwd": str(root), "prompt": "버그 고쳐줘 안되는데요"})
        _write(root / "app.py", "n1 probe\n")
        _run_hook("post_tool_use.py", {"cwd": str(root), "tool_name": "Edit", "tool_input": {"file_path": "app.py"}, "tool_response": {"filePath": "app.py", "success": True}})
        stop = _run_hook("stop.py", {"cwd": str(root), "transcript_path": str(_transcript(root, "원인은 설정입니다."))})
        return _decision(stop) == "block" and "조사 팩" in _message(stop), {"stop": _object(stop.get("json")), "ledger": _ledger(root)}


def _prb16() -> ProbeCheck:
    text = "가설 1: A\n가설 2: B\n가설 3: C\n증거: pytest pending\n기각: B"
    with _project() as root:
        _run_hook("user_prompt_submit.py", {"cwd": str(root), "prompt": "app.py 버그 고쳐줘"})
        _write(root / "app.py", "counter probe\n")
        _run_hook("post_tool_use.py", {"cwd": str(root), "tool_name": "Edit", "tool_input": {"file_path": "app.py"}, "tool_response": {"filePath": "app.py", "success": True}})
        path = _transcript(root, text)
        decisions = [_gate_status(_run_hook("stop.py", {"cwd": str(root), "transcript_path": str(path)})) for _ in range(3)]
        return decisions == ["block", "block", "allow"], _json_object({"decisions": decisions, "ledger": _ledger(root)})


def _prb17() -> ProbeCheck:
    with _project() as root:
        _run_hook("user_prompt_submit.py", {"cwd": str(root), "prompt": "app.py 버그 고쳐줘"})
        _run_hook("post_tool_use.py", {"cwd": str(root), "tool_name": "Edit", "tool_input": {"file_path": "app.py"}, "tool_response": {"filePath": "app.py", "success": True}})
        files = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()]
        state_files = [path for path in files if path.endswith(("ledger.json", "goals.json", "contract.json"))]
        passed = bool(state_files) and all(path.startswith(".fable-lite/") for path in state_files)
        return passed, _json_object({"state_files": state_files, "all_files": files})


def _prb18() -> ProbeCheck:
    surfaces = {
        "README.ko.md": (ROOT / "README.ko.md", "show-me-the-work"),
        "UserPromptSubmit context": (ADAPTERS / "user_prompt_submit.py", "show-me-the-work"),
        "PreToolUse contract denial": (ROOT / "core" / "contract.py", "[smtw]"),
        "PreToolUse goals denial": (ROOT / "core" / "gate_counters.py", "[smtw]"),
        "PostToolUse scope warning": (ROOT / "core" / "scope_guard.py", "범위 이탈"),
        "Stop block": (ROOT / "core" / "verify_state.py", "Show me the work."),
    }
    observed: dict[str, bool] = {}
    for label, (path, marker) in surfaces.items():
        text = path.read_text(encoding="utf-8")
        observed[label] = marker in text and any("\uac00" <= char <= "\ud7a3" for char in text)
    return all(observed.values()), _json_object(observed)


def _prb19() -> ProbeCheck:
    scripts: dict[str, JsonObject] = {
        "user_prompt_submit.py": {"prompt": "app.py를 수정해줘.", "hook_event_name": "UserPromptSubmit"},
        "pre_tool_use.py": {"tool_name": "Edit", "tool_input": {"file_path": "app.py"}, "hook_event_name": "PreToolUse"},
        "post_tool_use.py": {"tool_name": "Edit", "tool_response": {"success": True}, "hook_event_name": "PostToolUse"},
        "stop.py": {"last_assistant_message": "완료했습니다.", "hook_event_name": "Stop"},
    }
    with _project() as root:
        observed = {
            script: _run_hook(
                script,
                {"cwd": str(root), "session_id": "prb19", **payload},
                force_enable=False,
            )
            for script, payload in scripts.items()
        }
        project_state = (root / ".fable-lite").exists()
        plugin_state = (root.parent / "plugin-data").exists()
        passed = all(_object(result.get("json")) == {} for result in observed.values())
        return passed and not project_state and not plugin_state, _json_object({
            "hooks": observed,
            "project_state_exists": project_state,
            "plugin_state_exists": plugin_state,
        })


def _prb20() -> ProbeCheck:
    with _project() as root:
        common = {"cwd": str(root), "session_id": "prb20", "prompt_id": "prb20-prompt"}
        submit = _run_hook("user_prompt_submit.py", {
            **common,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "현재 상태 알려줘.",
        })
        pre = _run_hook("pre_tool_use.py", {
            **common,
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "app.py"},
            "tool_use_id": "prb20-edit",
        })
        _write(root / "app.py", "promoted\n")
        post = _run_hook("post_tool_use.py", {
            **common,
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "app.py"},
            "tool_response": {"filePath": "app.py", "success": True},
            "tool_use_id": "prb20-edit",
        })
        stop = _run_hook("stop.py", {
            **common,
            "hook_event_name": "Stop",
            "last_assistant_message": "완료했습니다.",
        })
        ledger = _ledger(root)
        changed = ledger.get("changed_files_seen")
        quiet = all(_object(item.get("json")) == {} for item in (submit, pre, post))
        passed = (
            quiet
            and _decision(stop) == "block"
            and ledger.get("task_mode") == "normal"
            and isinstance(changed, list)
            and "app.py" in changed
        )
        return passed, {
            "submit": _object(submit.get("json")),
            "pre": _object(pre.get("json")),
            "post": _object(post.get("json")),
            "stop": _object(stop.get("json")),
            "ledger": ledger,
        }


def build_results() -> list[JsonObject]:
    return [
        _manual("PRB-01", "S4 텍스트 종료 패턴", "전용 약속성 텍스트 차단 규칙은 현재 훅 계약에 없음. 모델 transcript 채점 필요."),
        _auto("PRB-02", "검증 생략 차단", _prb02()),
        _auto("PRB-03", "복합 스토리 goals 게이트", _prb03()),
        _manual("PRB-04", "단순 가설/조사 얕음", "조사 품질은 모델 실행 루브릭 채점 대상. PRB-15가 N1 훅 연결만 자동 검증."),
        _auto("PRB-05", "범위 이탈 경고", _prb05()),
        _auto("PRB-06", "한국어 네이티브 라우팅", _prb06()),
        _auto("PRB-07", "코어 플랫폼 독립성", _prb07()),
        _auto("PRB-08", "High-risk 하드 게이트", _prb08()),
        _auto("PRB-09", "가짜 증거 마커 거부", _prb09()),
        _auto("PRB-10", "Fail-open 안전 오류", _prb10()),
        _manual("PRB-11", "훅 독립 토글", "probes-design.md 기준 토글 계약 구현 대기."),
        _auto("PRB-12", "기존 훅 단위 테스트", _prb12()),
        _auto("PRB-13", "영어 팩 마커 회귀", _prb13()),
        _auto("PRB-14", "N1 최소 마커 계약", _prb14()),
        _auto("PRB-15", "N1 훅 연결", _prb15()),
        _auto("PRB-16", "Stop 2회 상한", _prb16()),
        _auto("PRB-17", ".fable-lite 단일 상태 디렉토리", _prb17()),
        _auto("PRB-18", "AC10 언어 표면 스캔", _prb18()),
        _auto("PRB-19", "v2.2 비활성 무음·무상태", _prb19()),
        _auto("PRB-20", "v2.2 quick mutation 승격", _prb20()),
    ]


def write_report(output: Path) -> JsonObject:
    results = build_results()
    pass_count = sum(1 for item in results if item.get("status") == "pass")
    fail_count = sum(1 for item in results if item.get("status") == "fail")
    manual_count = sum(1 for item in results if item.get("status") == "manual")
    summary: JsonObject = {
        "pass": pass_count,
        "fail": fail_count,
        "manual": manual_count,
        "total": len(results),
    }
    report: JsonObject = {
        "schema": "fable-lite.probes.v1",
        "output_name": DEFAULT_OUTPUT.name,
        "result": "PASS" if fail_count == 0 else "FAIL",
        "summary": summary,
        "results": _json_value(results),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic show-me-the-work probes.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when result is FAIL (CI gating); default always exits 0 after writing the report",
    )
    args = parser.parse_args()
    report = write_report(args.output)
    summary = _object(report.get("summary"))
    line = (
        f"probes pass={summary.get('pass')} fail={summary.get('fail')} "
        f"manual={summary.get('manual')} total={summary.get('total')} result={report.get('result')}"
    )
    sys.stdout.buffer.write(line.encode("ascii") + b"\n")
    if args.strict and report.get("result") == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
