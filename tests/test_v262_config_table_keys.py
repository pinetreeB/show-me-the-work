"""v2.6.2 CONFIG-03 — [tool] table-relative dotted key 인식 (RED-first).

INV-07: canonical config 선언 가능성이 있으면 legacy fallback 금지.
`[tool]` 섹션 아래 `smtw.supervision = false`는 tool.smtw.supervision의 유효한
TOML 표현이다. parse-error tolerant scanner가 current table path를 추적하지
않으면 이 선언을 ABSENT로 오판해 legacy config로 fallback한다.
"""
from __future__ import annotations

import json
from pathlib import Path

from adapters.claude_code.project_config import (
    ConfigLoadState,
    ConfigSource,
    load_project_config,
)


def _write_pyproject(root: Path, text: str) -> None:
    (root / "pyproject.toml").write_text(text, encoding="utf-8")


def _enable_legacy(root: Path) -> None:
    legacy = root / ".fable-lite"
    legacy.mkdir()
    (legacy / "config.json").write_text(
        json.dumps({"schema_version": 1, "supervision": True}), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# RED: table-relative dotted key 선언 → DECLARED_INVALID (legacy fallback 없음)
# ---------------------------------------------------------------------------


def test_config_03_tool_table_relative_dotted_key_is_declared_invalid(
    tmp_path: Path,
) -> None:
    _write_pyproject(
        tmp_path,
        "[tool]\n"
        "smtw.schema_version = 1\n"
        "smtw.supervision = false\n"
        "\n"
        "broken =\n",
    )
    _enable_legacy(tmp_path)

    loaded = load_project_config(tmp_path)

    # 수정 전: scanner가 table context를 몰라 ABSENT → legacy fallback(RED).
    assert loaded.state is ConfigLoadState.DECLARED_INVALID
    assert loaded.source is ConfigSource.PYPROJECT


def test_config_03_tool_table_relative_quoted_key_is_declared_invalid(
    tmp_path: Path,
) -> None:
    _write_pyproject(
        tmp_path,
        "[tool]\n"
        '"smtw".supervision = false\n'
        "broken =\n",
    )
    _enable_legacy(tmp_path)

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.DECLARED_INVALID
    assert loaded.source is ConfigSource.PYPROJECT


def test_config_03_tool_smtw_subtable_header_alone_stays_absent(
    tmp_path: Path,
) -> None:
    # 기존 corpus 계약: [tool.smtw.x] 서브테이블 헤더 alone은 config 선언 증거가
    # 아니다(실제 선언이면 [tool.smtw] 헤더/dotted key가 감지된다) → fallback 허용.
    _write_pyproject(
        tmp_path,
        "[tool.smtw.extra]\n"
        "x = 1\n"
        "broken =\n",
    )
    _enable_legacy(tmp_path)

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.VALID
    assert loaded.source is ConfigSource.LEGACY


def test_config_03_tool_smtw_array_table_stays_absent(tmp_path: Path) -> None:
    # 기존 corpus 계약: [[tool.smtw]] array-table은 config table 선언이 아니다.
    _write_pyproject(
        tmp_path,
        "[[tool.smtw]]\n"
        "broken =\n",
    )
    _enable_legacy(tmp_path)

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.VALID
    assert loaded.source is ConfigSource.LEGACY


def test_config_03_key_under_tool_smtw_table_is_declared_invalid(
    tmp_path: Path,
) -> None:
    # [tool.smtw] 아래 직접 키는 canonical 선언이다(table context 추적).
    _write_pyproject(
        tmp_path,
        "[tool.smtw]\n"
        "supervision = false\n"
        "broken =\n",
    )
    _enable_legacy(tmp_path)

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.DECLARED_INVALID
    assert loaded.source is ConfigSource.PYPROJECT


# ---------------------------------------------------------------------------
# 회귀 가드: 기존 감지 경로 + 오탐 방지
# ---------------------------------------------------------------------------


def test_config_03_canonical_table_header_still_declared_invalid(
    tmp_path: Path,
) -> None:
    _write_pyproject(tmp_path, "[tool.smtw]\nsupervision = false\nbroken =\n")
    _enable_legacy(tmp_path)

    assert load_project_config(tmp_path).state is ConfigLoadState.DECLARED_INVALID


def test_config_03_absolute_dotted_key_still_declared_invalid(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "tool.smtw.supervision = false\nbroken =\n")
    _enable_legacy(tmp_path)

    assert load_project_config(tmp_path).state is ConfigLoadState.DECLARED_INVALID


def test_config_03_unrelated_table_relative_key_stays_absent(tmp_path: Path) -> None:
    # [other] 아래 smtw.* 는 tool.smtw 선언이 아니다 → ABSENT → legacy fallback.
    _write_pyproject(tmp_path, "[other]\nsmtw.supervision = false\nbroken =\n")
    _enable_legacy(tmp_path)

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.VALID
    assert loaded.source is ConfigSource.LEGACY


def test_config_03_comment_and_string_mentions_are_not_declarations(
    tmp_path: Path,
) -> None:
    _write_pyproject(
        tmp_path,
        "[tool]\n"
        "# smtw.supervision = false\n"
        'note = "smtw.supervision"\n'
        "broken =\n",
    )
    _enable_legacy(tmp_path)

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.VALID
    assert loaded.source is ConfigSource.LEGACY


def test_config_03_valid_tool_table_relative_keys_parse_as_valid(
    tmp_path: Path,
) -> None:
    # malformed가 아니면 tomllib가 [tool] + dotted key를 정상 파싱한다.
    _write_pyproject(
        tmp_path,
        "[tool]\n"
        "smtw.schema_version = 1\n"
        "smtw.supervision = true\n",
    )

    loaded = load_project_config(tmp_path)

    assert loaded.state is ConfigLoadState.VALID
    assert loaded.source is ConfigSource.PYPROJECT
    assert loaded.values is not None
    assert loaded.values["supervision"] is True
