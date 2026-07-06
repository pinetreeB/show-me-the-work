from __future__ import annotations

import json
from pathlib import Path

from core.classify import classify_prompt
from core.compliance import check_investigation_compliance
from core.contract import evaluate_pretool_contract
from core.ledger import load_ledger, record_event
from core.scope_guard import evaluate_scope
from core.verify_state import evaluate_stop


def test_classify_prompt_routes_korean_debug_and_page_requests() -> None:
    debug_result = classify_prompt({"prompt": "버그 고쳐줘 안돼 에러가 나요"})
    page_result = classify_prompt({"prompt": "관리자 페이지 만들어줘"})
    debug_packs = debug_result["packs"]
    page_packs = page_result["packs"]

    assert debug_result["mode"] == "deep"
    assert isinstance(debug_packs, list)
    assert "investigation" in debug_packs
    assert page_result["mode"] == "normal"
    assert isinstance(page_packs, list)
    assert "verification-grounding" in page_packs


def test_classify_prompt_requires_goals_for_multi_story_work() -> None:
    result = classify_prompt({"prompt": "로그인 고치고 결제 페이지도 만들어줘"})

    assert result["needs_goals"] is True
    assert result["mode"] == "deep"


def test_classify_prompt_covers_korean_negative_forms_without_standalone_particle_multi_story() -> None:
    spaced = classify_prompt({"prompt": "왜 안 돼"})
    conjugated = classify_prompt({"prompt": "안되는데요"})
    single_story = classify_prompt({"prompt": "버튼 눌러도 반응이 없어요"})
    packs = single_story["packs"]

    assert spaced["mode"] == "deep"
    assert conjugated["mode"] == "deep"
    assert single_story["needs_goals"] is False
    assert isinstance(packs, list)
    assert "completion" not in packs


def test_investigation_compliance_requires_three_hypotheses_rejection_and_evidence() -> None:
    compliant = check_investigation_compliance(
        {
            "text": "\n".join(
                [
                    "가설 1: 라우터 문제",
                    "가설 2: 상태 초기화 문제",
                    "가설 3: API 응답 문제",
                    "기각: 라우터 문제는 재현되지 않음",
                    "증거: pytest tests/test_app.py 통과",
                ]
            )
        }
    )
    incomplete = check_investigation_compliance({"text": "가설 1: 하나뿐\n증거: 로그"})

    assert compliant["compliant"] is True
    assert compliant["hypothesis_count"] == 3
    assert incomplete["compliant"] is False
    missing = incomplete["missing"]
    assert isinstance(missing, list)
    assert "hypotheses" in missing
    assert "rejection" in missing


def test_investigation_compliance_accepts_english_markers() -> None:
    result = check_investigation_compliance(
        {
            "text": "\n".join(
                [
                    "Hypothesis 1: router regression",
                    "Hypothesis 2: state initialization",
                    "Hypothesis 3: API response shape",
                    "Rejected: Hypothesis 1 because route logs are clean",
                    "Evidence: tests/test_app.py passed",
                ]
            )
        }
    )

    assert result["compliant"] is True
    assert result["hypothesis_count"] == 3


def test_ledger_records_only_under_project_fable_lite_directory(tmp_path: Path) -> None:
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "버그 고쳐줘",
        }
    )
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "change",
            "path": "app.py",
            "kind": "code",
        }
    )
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "verification",
            "command": "python -m pytest",
            "success": True,
            "evidence": "1 passed",
        }
    )

    ledger = load_ledger({"project_root": str(tmp_path)})
    assert (tmp_path / ".fable-lite" / "ledger.json").exists()
    assert ledger["task_mode"] == "deep"
    assert ledger["changed_files_seen"] == ["app.py"]
    results = ledger["verification_results"]
    assert isinstance(results, list)
    first_result = results[0]
    assert isinstance(first_result, dict)
    assert first_result["success"] is True


def test_ledger_preserves_corrupted_json_as_backup_before_regenerating(tmp_path: Path) -> None:
    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir()
    ledger_file = state_dir / "ledger.json"
    ledger_file.write_text("{broken", encoding="utf-8")

    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "버그 고쳐줘",
        }
    )
    ledger = load_ledger({"project_root": str(tmp_path)})

    assert (state_dir / "ledger.json.bak").read_text(encoding="utf-8") == "{broken"
    assert ledger["prompt"] == "버그 고쳐줘"


def test_stop_gate_blocks_changed_unverified_work_at_most_twice(tmp_path: Path) -> None:
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "버그 고쳐줘",
        }
    )
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "change",
            "path": "app.py",
            "kind": "code",
        }
    )

    first = evaluate_stop({"project_root": str(tmp_path)})
    second = evaluate_stop({"project_root": str(tmp_path)})
    third = evaluate_stop({"project_root": str(tmp_path)})

    assert first["decision"] == "block"
    assert second["decision"] == "block"
    assert third["decision"] == "allow"
    message = third["message"]
    assert isinstance(message, str)
    assert "최대 2회" in message


def test_stop_block_counter_resets_on_new_prompt(tmp_path: Path) -> None:
    record_event({"project_root": str(tmp_path), "event": "prompt", "task_mode": "deep", "prompt": "첫 작업"})
    record_event({"project_root": str(tmp_path), "event": "change", "path": "app.py", "kind": "code"})
    assert evaluate_stop({"project_root": str(tmp_path)})["decision"] == "block"
    assert evaluate_stop({"project_root": str(tmp_path)})["decision"] == "block"

    record_event({"project_root": str(tmp_path), "event": "prompt", "task_mode": "deep", "prompt": "새 작업"})
    ledger = load_ledger({"project_root": str(tmp_path)})

    assert ledger["stop_blocks"] == 0


def test_scope_guard_warns_when_changed_file_is_outside_requested_scope() -> None:
    result = evaluate_scope(
        {
            "prompt": "app.py만 수정해줘",
            "requested_paths": ["app.py"],
            "changed_files": ["app.py", "settings.py"],
        }
    )

    assert result["decision"] == "warn"
    assert result["out_of_scope"] == ["settings.py"]


def test_scope_guard_allows_casefolded_absolute_path_under_requested_relative_path(tmp_path: Path) -> None:
    changed = tmp_path / "APP.py"
    result = evaluate_scope(
        {
            "project_root": str(tmp_path),
            "prompt": "app.py만 수정해줘",
            "requested_paths": ["app.py"],
            "changed_files": [str(changed)],
        }
    )

    assert result["decision"] == "allow"


def test_high_risk_contract_blocks_edit_until_valid_contract_exists(tmp_path: Path) -> None:
    blocked = evaluate_pretool_contract(
        {
            "project_root": str(tmp_path),
            "tool_name": "Edit",
            "file_paths": ["migrations/001_init.sql"],
            "prompt": "DB 마이그 수정",
        }
    )

    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir()
    (state_dir / "contract.json").write_text(
        json.dumps(
            {
                "restated_goal": "DB 마이그레이션 수정",
                "acceptance": ["python -m pytest tests/test_migration.py"],
                "evidence": ["test will be run before done"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    allowed = evaluate_pretool_contract(
        {
            "project_root": str(tmp_path),
            "tool_name": "Edit",
            "file_paths": ["migrations/001_init.sql"],
            "prompt": "DB 마이그 수정",
        }
    )

    assert blocked["decision"] == "block"
    assert allowed["decision"] == "allow"


def test_high_risk_contract_blocks_shell_commands_without_valid_contract(tmp_path: Path) -> None:
    result = evaluate_pretool_contract(
        {
            "project_root": str(tmp_path),
            "tool_name": "Bash",
            "command": "python manage.py migrate && psql -c 'DROP TABLE users'",
        }
    )

    assert result["decision"] == "block"


def test_classify_enumeration_is_multi_story_but_progressive_is_not() -> None:
    # 회귀 고정: "하고" 제거(발견B) 후에도 쉼표 열거형은 다중 스토리로 잡혀야 한다 (v2 러너 P-N2 프로브)
    enumeration = classify_prompt(
        {"prompt": "사용자 프로필 페이지를 만들고, API 연동도 하고, 데이터베이스 스키마도 업데이트해줘."}
    )
    assert enumeration["needs_goals"] is True

    # 진행형("~하고 있어")은 단일 수정 요청 — 다중 스토리 오분류 금지 (발견B)
    progressive = classify_prompt({"prompt": "add 함수가 뺄셈을 하고 있어 고쳐줘"})
    assert progressive["needs_goals"] is False
