from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias, cast
from unittest.mock import patch

from core.adapter_observation import CanonicalInvocation, ObservationReport, start_turn
from core.agent_log import agent_log_path
from core.ledger import record_event
from core.ledger_storage import ledger_path
from core.ledger_schema import JsonObject as LedgerJsonObject
from core.provenance_types import ProvenanceReason, ProvenanceStatus
import fable_lite.check as check_module


ROOT = Path(__file__).resolve().parents[1]

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def run_cli(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-m", "fable_lite", *args],
        cwd=cwd or ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": python_path},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def git(project: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr


def init_repo(project: Path) -> None:
    git(project, "init")
    git(project, "config", "user.email", "test@example.com")
    git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    git(project, "add", ".")
    git(project, "commit", "-m", "init")


def read_json(path: Path) -> dict[str, JsonValue]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def write_card(
    path: Path,
    *,
    owner: str = "codex",
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    verify: str = "python -m pytest tests/",
    done_artifact: str = "tmp/.done-card",
    sentinel: str | None = None,
) -> None:
    allowed: list[JsonValue] = list(allowed_paths or ["app.py"])
    forbidden: list[JsonValue] = list(forbidden_paths or [])
    payload: dict[str, JsonValue] = {
        "slug": "card",
        "owner": owner,
        "allowed_paths": allowed,
        "forbidden_paths": forbidden,
        "verify": verify,
        "done_artifact": done_artifact,
        "risk": "L1",
    }
    if sentinel is not None:
        payload["sentinel"] = sentinel
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8", newline="\n")


def write_project_file(project: Path, relative: str, text: str = "changed\n") -> None:
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def record_card_work(project: Path, changed_path: str, verify: str, *, success: bool = True) -> None:
    base = {"project_root": str(project), "agent": "codex"}
    record_event({**base, "event": "prompt", "task_mode": "deep", "prompt": "카드 작업"})
    record_event({**base, "event": "change", "path": changed_path, "kind": "artifact"})
    record_event({**base, "event": "verification", "command": verify, "success": success, "evidence": "1 passed"})


def start_observed_turn(
    project: Path,
    *,
    session_id: str = "session",
    prompt: str = "dist/current.js 수정해줘",
) -> CanonicalInvocation:
    invocation = CanonicalInvocation(
        "codex_cli",
        "codex",
        session_id,
        f"turn-{session_id}",
        f"start-{session_id}",
        "turn_start",
        "other",
        (),
        "",
        True,
        "",
    )
    observation = start_turn(project, invocation)
    _ = record_event(
        {
            "project_root": str(project),
            "event": "prompt",
            "host": invocation.host,
            "agent": invocation.agent,
            "session_id": invocation.session_id,
            "turn_id": invocation.turn_id,
            "baseline_snapshot_id": observation.baseline_snapshot_id,
            "current_snapshot_id": observation.snapshot_id,
            "provenance_incomplete": observation.incomplete,
            "provenance_status": observation.status.value,
            "provenance_status_reason": observation.status_reason,
            "task_mode": "normal",
            "prompt": prompt,
        }
    )
    return invocation


def test_check_reports_green_when_changed_files_have_successful_verification(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8", newline="\n")
    record_event({"project_root": str(tmp_path), "event": "prompt", "task_mode": "deep", "prompt": "app.py 수정", "agent": "codex"})
    record_event({"project_root": str(tmp_path), "event": "change", "path": "app.py", "kind": "code", "agent": "codex"})
    record_event({"project_root": str(tmp_path), "event": "verification", "command": "python -m pytest", "success": True, "evidence": "1 passed", "agent": "codex"})

    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    assert result.returncode == 0
    assert "GREEN" in result.stdout
    assert "app.py" in result.stdout


def test_check_reports_red_for_unverified_scope_drift_r1_and_missing_sentinel(tmp_path: Path) -> None:
    init_repo(tmp_path)
    marker = tmp_path / "marker.txt"
    marker.write_text("start\n", encoding="utf-8", newline="\n")
    (tmp_path / "settings.py").write_text("DROP TABLE users;\n", encoding="utf-8", newline="\n")
    record_event({"project_root": str(tmp_path), "event": "prompt", "task_mode": "deep", "prompt": "app.py만 수정하고 완료 후 빈 파일 tmp/.done-x sentinel 생성", "agent": "codex"})
    record_event({"project_root": str(tmp_path), "event": "change", "path": "settings.py", "kind": "code", "agent": "codex"})

    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex", "--since-file", str(marker)])

    assert result.returncode == 1
    assert "RED" in result.stdout
    assert "미검증 변경" in result.stdout
    assert "settings.py" in result.stdout
    assert "범위" in result.stdout
    assert "R1" in result.stdout
    assert "sentinel" in result.stdout


def test_record_event_writes_agent_jsonl_without_breaking_legacy_ledger(tmp_path: Path) -> None:
    record_event({"project_root": str(tmp_path), "event": "change", "path": "app.py", "kind": "code", "agent": "codex"})

    ledger = read_json(ledger_path(str(tmp_path)))
    agent_log = agent_log_path(str(tmp_path), "codex")
    lines = agent_log.read_text(encoding="utf-8").splitlines()

    assert ledger["agent"] == "codex"
    assert ledger["changed_files_seen"] == ["app.py"]
    assert len(lines) == 1
    assert json.loads(lines[0])["agent"] == "codex"


def test_brief_prints_target_specific_delegation_rules() -> None:
    result = run_cli(
        ["brief", "--paths", "core/**,tests/**", "--verify-cmd", "python -m pytest tests/", "--sentinel", "tmp/.done-x", "--target", "agy"]
    )

    assert result.returncode == 0
    assert "allowed_paths" in result.stdout
    assert "core/**" in result.stdout
    assert "python -m pytest tests/" in result.stdout
    assert "tmp/.done-x" in result.stdout
    assert "사후 check" in result.stdout
    assert "상세 규율" in result.stdout


def test_check_with_card_reports_red_for_forbidden_touch_and_missing_verify(tmp_path: Path) -> None:
    init_repo(tmp_path)
    card = tmp_path / "card.json"
    write_card(card, allowed_paths=["app.py"], forbidden_paths=["secrets/**"], verify="python -m pytest tests/", done_artifact="tmp/.done-card")
    write_project_file(tmp_path, "secrets/token.txt")
    write_project_file(tmp_path, "tmp/.done-card", "")
    record_card_work(tmp_path, "secrets/token.txt", "python -m pytest other/")

    result = run_cli(["check", "--card", str(card)], cwd=tmp_path)

    assert result.returncode == 1
    assert all(token in result.stdout for token in ("RED", "forbidden", "secrets/token.txt", "verify", "python -m pytest tests/"))


def test_check_with_card_matches_forbidden_case_insensitively(tmp_path: Path) -> None:
    init_repo(tmp_path)
    card = tmp_path / "card.json"
    verify = "python -m pytest tests/"
    write_card(card, allowed_paths=["**"], forbidden_paths=["secrets/**"], verify=verify, done_artifact="tmp/.done-card")
    write_project_file(tmp_path, "Secrets/token.txt")
    write_project_file(tmp_path, "tmp/.done-card", "")
    record_card_work(tmp_path, "Secrets/token.txt", verify)

    result = run_cli(["check", "--card", str(card)], cwd=tmp_path)

    assert result.returncode == 1
    assert "forbidden" in result.stdout
    assert "Secrets/token.txt" in result.stdout


def test_check_with_card_enforces_allowed_glob_without_broadening(tmp_path: Path) -> None:
    init_repo(tmp_path)
    card = tmp_path / "card.json"
    verify = "python -m pytest tests/"
    write_card(card, allowed_paths=["src/*.py"], verify=verify, done_artifact="tmp/.done-card")
    write_project_file(tmp_path, "src/secrets.txt")
    write_project_file(tmp_path, "tmp/.done-card", "")
    record_card_work(tmp_path, "src/secrets.txt", verify)

    result = run_cli(["check", "--card", str(card)], cwd=tmp_path)

    assert result.returncode == 1
    assert "src/secrets.txt" in result.stdout
    assert "allowed_paths" in result.stdout


def test_check_with_card_rejects_pre_card_legacy_verify(tmp_path: Path) -> None:
    init_repo(tmp_path)
    verify = "python -m pytest tests/"
    record_event({"project_root": str(tmp_path), "event": "verification", "command": verify, "success": True, "evidence": "old pass"})
    card = tmp_path / "card.json"
    write_card(card, allowed_paths=["app.py"], verify=verify, done_artifact="tmp/.done-card")
    write_project_file(tmp_path, "app.py", "print('ok')\n")
    write_project_file(tmp_path, "tmp/.done-card", "")

    result = run_cli(["check", "--card", str(card)], cwd=tmp_path)

    assert result.returncode == 1
    assert "verify" in result.stdout
    assert verify in result.stdout


def test_check_with_card_reports_red_for_missing_required_fields(tmp_path: Path) -> None:
    init_repo(tmp_path)
    card = tmp_path / "card.json"
    card.write_text(json.dumps({"owner": "codex", "forbidden_paths": []}), encoding="utf-8", newline="\n")

    result = run_cli(["check", "--card", str(card)], cwd=tmp_path)

    assert result.returncode == 1
    assert all(token in result.stdout for token in ("RED", "작업카드 오류", "allowed_paths", "verify", "done_artifact"))


def test_brief_with_card_uses_card_fields(tmp_path: Path) -> None:
    card = tmp_path / "card.json"
    write_card(
        card,
        owner="antigravity",
        allowed_paths=["fable_lite/**", "tests/**"],
        forbidden_paths=["core/**"],
        verify="python -m pytest tests/test_fable_lite_cli.py",
        done_artifact="tmp/.done-card",
        sentinel="C:/Users/rotat/.claude/tmp/.done-card",
    )

    result = run_cli(["brief", "--card", str(card)])

    assert result.returncode == 0
    assert all(
        token in result.stdout
        for token in ("fable_lite/**", "core/**", "python -m pytest tests/test_fable_lite_cli.py", "tmp/.done-card", "C:/Users/rotat/.claude/tmp/.done-card", "antigravity")
    )


def test_check_uses_active_turn_delta_instead_of_preexisting_repo_dirty(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    write_project_file(tmp_path, "build/preexisting.js", "before\n")
    write_project_file(tmp_path, "dist/preexisting.js", "before\n")
    write_project_file(tmp_path, "uv.lock", "version = 1\n")
    _ = start_observed_turn(tmp_path)

    before = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    assert before.returncode == 0
    assert "changed: 0" in before.stdout
    assert "build/preexisting.js" not in before.stdout
    assert "dist/preexisting.js" not in before.stdout
    assert "uv.lock" not in before.stdout

    write_project_file(tmp_path, "dist/current.js", "current turn\n")
    after = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    assert after.returncode == 1
    assert "changed: 1" in after.stdout
    assert "dist/current.js" in after.stdout
    assert "build/preexisting.js" not in after.stdout
    assert "dist/preexisting.js" not in after.stdout
    assert "uv.lock" not in after.stdout


def test_check_observes_rewrite_of_path_dirty_before_turn(tmp_path: Path) -> None:
    # Given: a generated path is already dirty before the active turn begins.
    init_repo(tmp_path)
    write_project_file(tmp_path, "dist/preexisting.js", "before\n")
    _ = start_observed_turn(tmp_path)

    # When: the same pre-existing dirty path is rewritten during the turn.
    write_project_file(tmp_path, "dist/preexisting.js", "rewritten during turn\n")
    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    # Then: pre-turn dirtiness cannot hide the new revision.
    assert result.returncode == 1
    assert "dist/preexisting.js" in result.stdout


def test_check_fresh_reconciliation_observes_change_after_last_tool_event(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    write_project_file(tmp_path, "app.py", "before\n")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "add app")
    _ = start_observed_turn(tmp_path, prompt="app.py 수정해줘")

    write_project_file(tmp_path, "app.py", "after external change\n")
    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    assert result.returncode == 1
    assert "changed: 1" in result.stdout
    assert "app.py" in result.stdout


def test_check_git_backstop_observes_changes_excluded_at_turn_baseline(
    tmp_path: Path,
) -> None:
    # Given: tracked files are excluded by user config or a soft-excluded directory at turn start.
    init_repo(tmp_path)
    write_project_file(tmp_path, "src/core.py", "safe\n")
    write_project_file(tmp_path, "src/deleted.py", "delete me\n")
    write_project_file(tmp_path, "node_modules/dep/index.js", "safe dependency\n")
    write_project_file(
        tmp_path,
        ".fable-lite/provenance-config.json",
        json.dumps({"version": 1, "exclude": ["src/**"]}),
    )
    git(
        tmp_path,
        "add",
        "-f",
        "src/core.py",
        "src/deleted.py",
        "node_modules/dep/index.js",
    )
    git(tmp_path, "commit", "-m", "add excluded tracked files")
    _ = start_observed_turn(tmp_path, prompt="excluded files 수정해줘")

    # When: both baseline-excluded tracked files are changed during the active turn.
    write_project_file(tmp_path, "src/core.py", "backdoor\n")
    (tmp_path / "src" / "deleted.py").unlink()
    write_project_file(tmp_path, "node_modules/dep/index.js", "backdoor dependency\n")
    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    # Then: the independent Git backstop keeps both current-turn changes visible.
    assert result.returncode == 1
    assert "src/core.py" in result.stdout
    assert "src/deleted.py" in result.stdout
    assert "node_modules/dep/index.js" in result.stdout


def test_check_blocks_committed_config_exclusion_of_tracked_source(
    tmp_path: Path,
) -> None:
    # Given: config excludes a tracked source before the observed turn begins.
    init_repo(tmp_path)
    write_project_file(tmp_path, "src/core.py", "safe\n")
    write_project_file(
        tmp_path,
        ".fable-lite/provenance-config.json",
        json.dumps({"version": 1, "exclude": ["src/**"]}),
    )
    git(
        tmp_path,
        "add",
        "-f",
        "src/core.py",
        ".fable-lite/provenance-config.json",
    )
    git(tmp_path, "commit", "-m", "add excluded tracked source")
    _ = start_observed_turn(tmp_path, prompt="src/core.py 수정해줘")

    # When: the agent changes both config and source, then launders Git status by committing.
    write_project_file(
        tmp_path,
        ".fable-lite/provenance-config.json",
        json.dumps({"version": 1, "exclude": ["src/**", "vendor/**"]}),
    )
    write_project_file(tmp_path, "src/core.py", "backdoor\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "launder config and source changes")
    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    # Then: snapshot evidence blocks the turn even though Git status is clean.
    assert result.returncode == 1, result.stdout
    assert "src/core.py" in result.stdout
    assert ".fable-lite/provenance-config.json" in result.stdout


def test_check_ignores_untracked_config_and_soft_exclusion_noise(
    tmp_path: Path,
) -> None:
    # Given: an observed turn whose config suppresses vendored and dependency noise.
    init_repo(tmp_path)
    write_project_file(
        tmp_path,
        ".fable-lite/provenance-config.json",
        json.dumps({"version": 1, "exclude": ["vendor/**"]}),
    )
    _ = start_observed_turn(tmp_path, prompt="프로젝트 상태를 읽어줘")

    # When: only untracked files under config and soft exclusions appear.
    write_project_file(tmp_path, "vendor/cache.bin", "untracked config noise\n")
    write_project_file(tmp_path, "node_modules/pkg/index.js", "untracked soft noise\n")
    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    # Then: the read-only turn remains clean and excludes both noise paths.
    assert result.returncode == 0, result.stdout
    assert "changed: 0" in result.stdout
    assert "vendor/cache.bin" not in result.stdout
    assert "node_modules/pkg/index.js" not in result.stdout


def test_check_ignores_tracked_file_deleted_before_read_only_turn(
    tmp_path: Path,
) -> None:
    # Given: a tracked file was already deleted before the observed turn starts.
    init_repo(tmp_path)
    write_project_file(tmp_path, "src/gone.py", "old\n")
    git(tmp_path, "add", "src/gone.py")
    git(tmp_path, "commit", "-m", "add file deleted before turn")
    (tmp_path / "src/gone.py").unlink()
    _ = start_observed_turn(tmp_path, prompt="프로젝트 상태를 읽어줘")

    # When: the agent performs no mutation and check reconciles the read-only turn.
    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    # Then: the pre-turn deletion is not attributed to this turn.
    assert result.returncode == 0, result.stdout
    assert "changed: 0" in result.stdout
    assert "src/gone.py" not in result.stdout


def test_check_ignores_index_only_removal_of_excluded_tracked_file(
    tmp_path: Path,
) -> None:
    # Given: an excluded tracked file is present in the observed turn baseline.
    init_repo(tmp_path)
    write_project_file(tmp_path, "src/core.py", "stable\n")
    write_project_file(
        tmp_path,
        ".fable-lite/provenance-config.json",
        json.dumps({"version": 1, "exclude": ["src/**"]}),
    )
    git(
        tmp_path,
        "add",
        "-f",
        "src/core.py",
        ".fable-lite/provenance-config.json",
    )
    git(tmp_path, "commit", "-m", "add excluded tracked file")
    _ = start_observed_turn(tmp_path, prompt="프로젝트 상태를 읽어줘")

    # When: only the Git index changes while the working-tree bytes remain identical.
    git(tmp_path, "rm", "--cached", "src/core.py")
    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    # Then: provenance does not report a filesystem deletion that never occurred.
    assert (tmp_path / "src/core.py").read_text(encoding="utf-8") == "stable\n"
    assert result.returncode == 0, result.stdout
    assert "changed: 0" in result.stdout
    assert "src/core.py" not in result.stdout


def test_check_ignores_intent_to_add_for_preexisting_excluded_file(
    tmp_path: Path,
) -> None:
    # Given: an excluded untracked file already exists before the observed turn.
    init_repo(tmp_path)
    write_project_file(tmp_path, "src/core.py", "stable\n")
    write_project_file(
        tmp_path,
        ".fable-lite/provenance-config.json",
        json.dumps({"version": 1, "exclude": ["src/**"]}),
    )
    _ = start_observed_turn(tmp_path, prompt="프로젝트 상태를 읽어줘")

    # When: Git records only intent-to-add without changing the file bytes.
    git(tmp_path, "add", "-N", "src/core.py")
    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    # Then: the index-only operation does not become a filesystem CREATE.
    assert (tmp_path / "src/core.py").read_text(encoding="utf-8") == "stable\n"
    assert result.returncode == 0, result.stdout
    assert "changed: 0" in result.stdout
    assert "src/core.py" not in result.stdout


def test_check_reports_provenance_finding_for_ambiguous_active_turns(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    for session in ("one", "two"):
        _ = record_event(
            {
                "project_root": str(tmp_path),
                "event": "prompt",
                "host": "codex_cli",
                "agent": "codex",
                "session_id": session,
                "turn_id": f"turn-{session}",
                "prompt": "app.py 수정해줘",
            }
        )
        _ = record_event(
            {
                "project_root": str(tmp_path),
                "event": "change",
                "host": "codex_cli",
                "agent": "codex",
                "session_id": session,
                "paths": [
                    {
                        "change_id": f"change-{session}",
                        "path": f"{session}.py",
                        "kind": "code",
                        "before": None,
                        "after": session,
                        "requires_verification": True,
                    }
                ],
            }
        )

    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    assert result.returncode == 1
    assert "provenance" in result.stdout.casefold()
    assert "ambiguous" in result.stdout.casefold()


def test_check_without_agent_reports_ambiguous_active_turns(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    for agent in ("alpha", "beta"):
        _ = record_event(
            {
                "project_root": str(tmp_path),
                "event": "prompt",
                "host": "codex_cli",
                "agent": agent,
                "session_id": agent,
                "turn_id": f"turn-{agent}",
                "prompt": "read only",
            }
        )

    result = run_cli(["check", "--root", str(tmp_path)])

    assert result.returncode == 1
    assert "provenance" in result.stdout.casefold()
    assert "ambiguous" in result.stdout.casefold()


def _scope_too_large_remote_ledger(*, verified: bool) -> dict[str, JsonValue]:
    target_id = "ssh://deploy@host:22"
    results: list[JsonValue] = []
    if verified:
        results.append(
            {
                "success": True,
                "seq": 5,
                "covers": {
                    "through_seq": 4,
                    "remote_target_ids": [target_id],
                },
            }
        )
    return {
        "agent": "codex",
        "active_turns": {
            "codex_cli:session:codex": {
                "agent": "codex",
                "turn_id": "turn-session",
                "path_revisions": {},
                "provenance_mutation_capable": False,
                "provenance_remote_mutation": True,
                "last_remote_mutation_seq": 4,
                "remote_mutation_epochs": {target_id: 4},
                "verification_results": results,
            }
        },
    }


def test_check_scope_too_large_blocks_unverified_remote_only_turn(
    tmp_path: Path,
) -> None:
    ledger = _scope_too_large_remote_ledger(verified=False)
    report = ObservationReport(
        "snapshot:large",
        "snapshot:base",
        (),
        False,
        True,
        ProvenanceStatus.SCOPE_TOO_LARGE,
        ProvenanceReason.ENTRY_LIMIT,
    )

    with (
        patch.object(check_module, "reconcile_turn", return_value=report),
        patch.object(check_module, "load_agent_ledger", return_value=ledger),
    ):
        _, paths, messages = check_module._reconcile_active_turn(
            tmp_path, "codex", cast(LedgerJsonObject, ledger)
        )

    assert paths is None
    assert any("remote" in message for message in messages)


def test_check_scope_too_large_allows_fresh_same_target_remote_verification(
    tmp_path: Path,
) -> None:
    ledger = _scope_too_large_remote_ledger(verified=True)
    report = ObservationReport(
        "snapshot:large",
        "snapshot:base",
        (),
        False,
        True,
        ProvenanceStatus.SCOPE_TOO_LARGE,
        ProvenanceReason.ENTRY_LIMIT,
    )

    with (
        patch.object(check_module, "reconcile_turn", return_value=report),
        patch.object(check_module, "load_agent_ledger", return_value=ledger),
    ):
        _, paths, messages = check_module._reconcile_active_turn(
            tmp_path, "codex", cast(LedgerJsonObject, ledger)
        )

    assert paths == []
    assert messages == []
