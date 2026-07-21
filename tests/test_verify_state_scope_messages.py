from __future__ import annotations

from pathlib import Path

from core.provenance_types import ProvenanceReason, ProvenanceStatus
from core.state_layout import PROVENANCE_CONFIG_NAME, STATE_DIR_NAME
from core.verify_state import evaluate_without_io


BYTE_TOP_PATHS = [{"path": "src", "bytes": 500, "entries": 3}]
ENTRY_TOP_PATHS = [{"path": "assets", "bytes": 12, "entries": 40}]


def _state(
    reason: str | None,
    *,
    mutation_capable: bool,
    top_paths: object = None,
    breach_path: object = None,
) -> dict[str, object]:
    state: dict[str, object] = {
        "provenance_status": ProvenanceStatus.SCOPE_TOO_LARGE.value,
        "provenance_incomplete": False,
        "provenance_mutation_capable": mutation_capable,
    }
    if reason is not None:
        state["provenance_status_reason"] = reason
    if top_paths is not None:
        state["provenance_budget_top_paths"] = top_paths
    if breach_path is not None:
        state["provenance_budget_breach_path"] = breach_path
    return state


def _payload(root: Path) -> dict[str, object]:
    return {"project_root": str(root)}


def _assert_no_narrow_root_language(message: str) -> None:
    assert "루트를 좁히" not in message
    assert "Narrow the project root" not in message


def test_byte_limit_block_message_includes_top_paths_breach_and_config_guide(
    tmp_path: Path,
) -> None:
    state = _state(
        ProvenanceReason.BYTE_LIMIT.value,
        mutation_capable=True,
        top_paths=BYTE_TOP_PATHS,
        breach_path="src/big.bin",
    )

    decision = evaluate_without_io(state, _payload(tmp_path))

    assert decision["decision"] == "block"
    message = decision["reason"]
    assert isinstance(message, str)
    assert "바이트 예산 초과" in message
    assert "byte budget exceeded" in message
    assert "부분 관측" in message
    assert "src (entries=3, bytes=500)" in message
    assert "예산 초과 지점: src/big.bin" in message
    assert f"{STATE_DIR_NAME}/{PROVENANCE_CONFIG_NAME}" in message
    assert '"exclude"' in message
    assert "저장 후 다음 턴부터 반영" in message
    _assert_no_narrow_root_language(message)


def test_entry_limit_block_message_includes_top_paths_breach_and_config_guide(
    tmp_path: Path,
) -> None:
    state = _state(
        ProvenanceReason.ENTRY_LIMIT.value,
        mutation_capable=True,
        top_paths=ENTRY_TOP_PATHS,
        breach_path="assets/way-too-many/file.png",
    )

    decision = evaluate_without_io(state, _payload(tmp_path))

    assert decision["decision"] == "block"
    message = decision["reason"]
    assert isinstance(message, str)
    assert "파일 개수 예산 초과" in message
    assert "entry-count budget exceeded" in message
    assert "부분 관측" in message
    assert "assets (entries=40, bytes=12)" in message
    assert "예산 초과 지점: assets/way-too-many/file.png" in message
    assert f"{STATE_DIR_NAME}/{PROVENANCE_CONFIG_NAME}" in message
    _assert_no_narrow_root_language(message)


def test_deadline_block_message_frames_top_paths_as_hint_not_cause(tmp_path: Path) -> None:
    state = _state(
        ProvenanceReason.DEADLINE.value,
        mutation_capable=True,
        breach_path="scan/last/hit.py",
    )

    decision = evaluate_without_io(state, _payload(tmp_path))

    assert decision["decision"] == "block"
    message = decision["reason"]
    assert isinstance(message, str)
    assert "관측 시간 초과" in message
    assert "참고용 힌트" in message
    assert "scan/last/hit.py" in message
    # The deadline branch must never borrow the byte/entry causal framing.
    assert "바이트 예산 초과" not in message
    assert "파일 개수 예산 초과" not in message
    _assert_no_narrow_root_language(message)


def test_missing_reason_block_message_falls_back_to_general_config_guide(
    tmp_path: Path,
) -> None:
    state = _state(None, mutation_capable=True)

    decision = evaluate_without_io(state, _payload(tmp_path))

    assert decision["decision"] == "block"
    message = decision["reason"]
    assert isinstance(message, str)
    assert f"{STATE_DIR_NAME}/{PROVENANCE_CONFIG_NAME}" in message
    assert "바이트 예산 초과" not in message
    assert "파일 개수 예산 초과" not in message
    assert "관측 시간 초과" not in message
    _assert_no_narrow_root_language(message)


def test_byte_limit_advisory_allow_message_includes_same_diagnostics(tmp_path: Path) -> None:
    state = _state(
        ProvenanceReason.BYTE_LIMIT.value,
        mutation_capable=False,
        top_paths=BYTE_TOP_PATHS,
        breach_path="src/big.bin",
    )

    decision = evaluate_without_io(state, _payload(tmp_path))

    assert decision["decision"] == "allow"
    message = decision["message"]
    assert isinstance(message, str)
    assert "바이트 예산 초과" in message
    assert "src (entries=3, bytes=500)" in message
    assert "예산 초과 지점: src/big.bin" in message
    assert f"{STATE_DIR_NAME}/{PROVENANCE_CONFIG_NAME}" in message
    _assert_no_narrow_root_language(message)


def test_entry_limit_advisory_allow_message_includes_same_diagnostics(tmp_path: Path) -> None:
    state = _state(
        ProvenanceReason.ENTRY_LIMIT.value,
        mutation_capable=False,
        top_paths=ENTRY_TOP_PATHS,
        breach_path="assets/way-too-many/file.png",
    )

    decision = evaluate_without_io(state, _payload(tmp_path))

    assert decision["decision"] == "allow"
    message = decision["message"]
    assert isinstance(message, str)
    assert "파일 개수 예산 초과" in message
    assert "assets (entries=40, bytes=12)" in message
    assert "예산 초과 지점: assets/way-too-many/file.png" in message
    _assert_no_narrow_root_language(message)


def test_deadline_advisory_allow_message_frames_top_paths_as_hint_not_cause(
    tmp_path: Path,
) -> None:
    state = _state(
        ProvenanceReason.DEADLINE.value,
        mutation_capable=False,
        breach_path="scan/last/hit.py",
    )

    decision = evaluate_without_io(state, _payload(tmp_path))

    assert decision["decision"] == "allow"
    message = decision["message"]
    assert isinstance(message, str)
    assert "관측 시간 초과" in message
    assert "참고용 힌트" in message
    assert "바이트 예산 초과" not in message
    assert "파일 개수 예산 초과" not in message
    _assert_no_narrow_root_language(message)


def test_missing_reason_advisory_allow_falls_back_to_general_config_guide(
    tmp_path: Path,
) -> None:
    state = _state(None, mutation_capable=False)

    decision = evaluate_without_io(state, _payload(tmp_path))

    assert decision["decision"] == "allow"
    message = decision["message"]
    assert isinstance(message, str)
    assert f"{STATE_DIR_NAME}/{PROVENANCE_CONFIG_NAME}" in message
    _assert_no_narrow_root_language(message)


def test_invalid_top_path_entries_are_filtered_without_crashing(tmp_path: Path) -> None:
    state = _state(
        ProvenanceReason.BYTE_LIMIT.value,
        mutation_capable=True,
        top_paths=[
            {"path": "", "bytes": -1, "entries": "not-an-int"},
            {"path": "ok", "bytes": 10, "entries": 1},
            "not-a-dict",
        ],
        breach_path=12345,
    )

    decision = evaluate_without_io(state, _payload(tmp_path))

    assert decision["decision"] == "block"
    message = decision["reason"]
    assert isinstance(message, str)
    assert "ok (entries=1, bytes=10)" in message
    # The invalid (non-string) breach_path was normalized away entirely.
    assert "예산 초과 지점" not in message
    _assert_no_narrow_root_language(message)


def test_config_guide_example_is_valid_config_schema(tmp_path: Path) -> None:
    """P4 수리 회귀 고정: 안내 예시 JSON은 실제 로더가 수용해야 하고,
    소스 디렉토리 제외 경고를 포함해야 한다."""
    import json
    import re

    from core.provenance_policy import CONFIG_RELATIVE_PATH, load_provenance_config
    from core.verify_state import _CONFIG_GUIDE

    examples = re.findall(r"\{[^{}]*\"exclude\"[^{}]*\}", _CONFIG_GUIDE)
    assert examples, "config guide must contain an example JSON"
    for example in examples:
        parsed = json.loads(example)
        assert parsed.get("version") == 1
        config_path = tmp_path / CONFIG_RELATIVE_PATH
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(example, encoding="utf-8")
        loaded = load_provenance_config(tmp_path)
        assert loaded.exclude, "example exclude must be honored by the loader"
    assert "소스 디렉토리" in _CONFIG_GUIDE
    assert "source directories" in _CONFIG_GUIDE
