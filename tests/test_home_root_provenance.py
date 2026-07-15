from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.adapter_observation import (
    CanonicalInvocation,
    begin_invocation,
    finish_turn,
    observe_post_tool,
    reconcile_turn,
    start_turn,
)
from core.project_root import HOME_ROOT_ADVISORY, is_user_home_root
from core.provenance_lifecycle import ProvenanceLifecycle
from core.provenance_types import ProvenanceReason, ProvenanceStatus
from core.verify_state import evaluate_without_io


def _use_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setattr(
        "core.project_root.os.path.expanduser",
        lambda value: str(home) if value == "~" else value,
    )
    monkeypatch.delenv("USERPROFILE", raising=False)


def _invocation(phase: str = "turn_start") -> CanonicalInvocation:
    return CanonicalInvocation(
        host="codex",
        agent="codex",
        session_id="session",
        turn_id="turn",
        invocation_id="invocation",
        phase=phase,
        tool_family_hint="edit",
        candidate_paths=("app.py",),
        command_hint="",
        success=True,
        evidence="",
    )


def _unsafe_state(status: ProvenanceStatus) -> dict[str, object]:
    return {
        "provenance_status": status.value,
        "provenance_status_reason": ProvenanceReason.BYTE_LIMIT.value,
        "provenance_incomplete": status is ProvenanceStatus.INCOMPLETE,
        "provenance_mutation_capable": True,
    }


def test_home_root_detection_matches_only_the_exact_normalized_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "UserHome"
    child = home / "project"
    sibling = tmp_path / "project"
    child.mkdir(parents=True)
    sibling.mkdir()
    _use_home(monkeypatch, home)

    assert is_user_home_root(home) is True
    assert is_user_home_root(child) is False
    assert is_user_home_root(sibling) is False
    if os.name == "nt":
        assert is_user_home_root(Path(str(home).swapcase())) is True


def test_home_root_lifecycle_starts_unsupported_without_scanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _use_home(monkeypatch, home)
    lifecycle = ProvenanceLifecycle(home)

    with patch.object(lifecycle, "_scan", side_effect=AssertionError("home scan attempted")):
        result = lifecycle.start_turn("codex", "turn-home", mutation_capable=True)

    assert result.status is ProvenanceStatus.UNSUPPORTED
    assert result.status_reason is ProvenanceReason.HOME_ROOT
    assert result.incomplete is False
    assert result.clean_claim is False
    assert result.snapshot is None
    assert lifecycle.active_turns == ()
    assert lifecycle.turn_baseline_path("codex", "turn-home").exists() is False


def test_home_child_keeps_normal_lifecycle_scanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = home / "project"
    project.mkdir(parents=True)
    (project / "app.py").write_text("value = 1\n", encoding="utf-8")
    _use_home(monkeypatch, home)
    lifecycle = ProvenanceLifecycle(project)

    with patch.object(lifecycle, "_scan", wraps=lifecycle._scan) as scan:
        result = lifecycle.start_turn("codex", "turn-project")

    assert scan.call_count >= 1
    assert result.status is ProvenanceStatus.COMPLETE
    assert result.snapshot is not None


@pytest.mark.parametrize(
    "status",
    (ProvenanceStatus.SCOPE_TOO_LARGE, ProvenanceStatus.INCOMPLETE),
)
def test_home_root_provenance_failure_is_actionable_advisory_allow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: ProvenanceStatus,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _use_home(monkeypatch, home)

    result = evaluate_without_io(_unsafe_state(status), {"project_root": str(home)})

    assert result == {"decision": "allow", "message": HOME_ROOT_ADVISORY}


@pytest.mark.parametrize(
    "status",
    (ProvenanceStatus.SCOPE_TOO_LARGE, ProvenanceStatus.INCOMPLETE),
)
def test_home_child_keeps_existing_provenance_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: ProvenanceStatus,
) -> None:
    home = tmp_path / "home"
    project = home / "project"
    project.mkdir(parents=True)
    _use_home(monkeypatch, home)

    result = evaluate_without_io(_unsafe_state(status), {"project_root": str(project)})

    assert result["decision"] == "block"
    assert result["reason_code"] == "stop.provenance_incomplete"


@pytest.mark.parametrize(
    "observe, phase",
    (
        (start_turn, "turn_start"),
        (begin_invocation, "pre_tool"),
        (observe_post_tool, "post_tool"),
        (reconcile_turn, "stop"),
        (finish_turn, "stop"),
    ),
)
def test_shared_adapter_observation_never_resumes_scanning_at_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    observe: object,
    phase: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _use_home(monkeypatch, home)

    with (
        patch(
            "core.adapter_observation.ProvenanceLifecycle",
            side_effect=AssertionError("home lifecycle resumed"),
        ),
        patch("core.adapter_observation._record_invocation"),
        patch("core.adapter_observation._record_status"),
    ):
        report = observe(home, _invocation(phase))  # type: ignore[operator]

    assert report.status is ProvenanceStatus.UNSUPPORTED
    assert report.status_reason is ProvenanceReason.HOME_ROOT
    assert report.incomplete is False
