from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import TypeAlias, cast

ROOT = Path(__file__).resolve().parents[1]
JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def _git(project: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr


def _init_repo(project: Path) -> None:
    _git(project, "init")
    _git(project, "config", "user.email", "test@example.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")


def _write(project: Path, relative: str, text: str) -> None:
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def _run_design(project: Path) -> tuple[subprocess.CompletedProcess[str], JsonObject]:
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    process = subprocess.run(
        [sys.executable, "-m", "fable_lite", "check", "--root", str(project), "--design"],
        cwd=ROOT,
        env={
            **os.environ,
            "FABLE_LITE_DESIGN_GATE": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": python_path,
        },
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if not process.stdout.strip():
        raise AssertionError(f"expected design JSON output, got: {process.stderr.strip()}")
    decoded = cast(object, json.loads(process.stdout))
    assert isinstance(decoded, dict)
    return process, cast(JsonObject, decoded)


def _violations(payload: JsonObject) -> list[JsonObject]:
    raw = payload["violations"]
    assert isinstance(raw, list)
    assert all(isinstance(item, dict) for item in raw)
    return cast(list[JsonObject], raw)


def test_design_lint_detects_changed_hardcodes_across_supported_surfaces(
    tmp_path: Path,
) -> None:
    # Given: changed CSS, TSX, React Native, and Tailwind files contain forbidden literals.
    _init_repo(tmp_path)
    _write(tmp_path, "src/app.css", ".card { color: #123456; padding: 13px; }\n")
    _write(tmp_path, "src/App.tsx", 'export const App = () => <div style={{ color: "#abcdef", margin: "13px" }} />;\n')
    _write(tmp_path, "src/native.ts", 'const styles = StyleSheet.create({ card: { backgroundColor: "#112233", gap: 13 } });\n')
    _write(tmp_path, "src/panel.html", '<div class="bg-[#123456] p-[13px]">panel</div>\n')

    # When: the manual design check runs while the persistent toggle is off.
    process, payload = _run_design(tmp_path)

    # Then: the CLI reports every surface with stable machine-readable rule identifiers.
    violations = _violations(payload)
    assert process.returncode == 1
    assert payload["passed"] is False
    assert {item["file"] for item in violations} == {
        "src/App.tsx",
        "src/app.css",
        "src/native.ts",
        "src/panel.html",
    }
    assert {item["rule_id"] for item in violations} == {
        "design/raw-color",
        "design/raw-spacing",
        "design/tailwind-arbitrary",
    }
    assert all(isinstance(item["line"], int) and item["line"] > 0 for item in violations)


def test_design_lint_checks_only_lines_changed_from_existing_project(
    tmp_path: Path,
) -> None:
    # Given: a committed legacy violation exists before a compliant line is added.
    _init_repo(tmp_path)
    _write(tmp_path, "src/legacy.css", ".legacy { color: #123456; padding: 13px; }\n")
    _git(tmp_path, "add", "src/legacy.css")
    _git(tmp_path, "commit", "-m", "legacy")
    _write(
        tmp_path,
        "src/legacy.css",
        ".legacy { color: #123456; padding: 13px; }\n.new { color: var(--ink); padding: var(--space-3); }\n",
    )

    # When: design lint evaluates the working-tree delta.
    process, payload = _run_design(tmp_path)

    # Then: unchanged legacy debt does not fail the current turn.
    assert process.returncode == 0
    assert payload["passed"] is True
    assert _violations(payload) == []


def test_design_lint_preserves_design_ops_literal_boundaries(tmp_path: Path) -> None:
    # Given: changed files use only DESIGN-OPS token and literal exceptions.
    _init_repo(tmp_path)
    _write(tmp_path, "design/tokens.css", ":root { --brand: #123456; --space-3: 13px; }\n")
    _write(
        tmp_path,
        "src/safe.css",
        ".safe { margin: 0; width: 50%; color: currentColor; border: 1px solid currentColor; }\n",
    )
    _write(tmp_path, "src/icon.svg", '<svg><path fill="#123456" stroke="#abcdef" /></svg>\n')
    _write(
        tmp_path,
        "src/chart.ts",
        'const chartData = [\n  { label: "A", color: "#123456" },\n];\n',
    )

    # When: design lint evaluates those changed files.
    process, payload = _run_design(tmp_path)

    # Then: token sources, zero, hairlines, percentages, currentColor, SVG, and chart data stay allowed.
    assert process.returncode == 0
    assert payload["passed"] is True
    assert _violations(payload) == []


def test_design_lint_does_not_treat_chart_named_styles_as_chart_data(tmp_path: Path) -> None:
    # Given: a normal UI style uses a raw color behind a chart-shaped variable name.
    _init_repo(tmp_path)
    _write(
        tmp_path,
        "src/chart.tsx",
        'const chartLabelStyle = { color: "#123456" };\n',
    )

    # When: the manual design lint evaluates the changed script.
    process, payload = _run_design(tmp_path)

    # Then: only actual chart data colors are exempt, so the style remains blocked.
    assert process.returncode == 1
    assert [(item["file"], item["rule_id"]) for item in _violations(payload)] == [
        ("src/chart.tsx", "design/raw-color")
    ]


def test_design_lint_honors_reasoned_unexpired_allowlist(tmp_path: Path) -> None:
    # Given: a violation has a path/rule exception with both reason and future expiry.
    _init_repo(tmp_path)
    _write(tmp_path, "src/legacy.tsx", 'export const ink = "#123456";\n')
    _write(
        tmp_path,
        "design/gate.config",
        json.dumps(
            {
                "enabled": False,
                "allowlist": [
                    {
                        "path": "src/legacy.tsx",
                        "rule_id": "design/raw-color",
                        "reason": "third-party migration",
                        "expires": "2099-12-31",
                    }
                ],
            }
        ),
    )

    # When: the one-shot manual check runs despite enabled=false.
    process, payload = _run_design(tmp_path)

    # Then: the active exception suppresses only the named rule.
    assert process.returncode == 0
    assert payload["passed"] is True
    assert _violations(payload) == []


def test_design_lint_rejects_expired_allowlist_entry(tmp_path: Path) -> None:
    # Given: the same violation is covered only by an expired exception.
    _init_repo(tmp_path)
    _write(tmp_path, "src/legacy.tsx", 'export const ink = "#123456";\n')
    _write(
        tmp_path,
        "design/gate.config",
        json.dumps(
            {
                "enabled": False,
                "allowlist": [
                    {
                        "path": "src/legacy.tsx",
                        "rule_id": "design/raw-color",
                        "reason": "migration ended",
                        "expires": "2000-01-01",
                    }
                ],
            }
        ),
    )

    # When: the manual check evaluates the changed file.
    process, payload = _run_design(tmp_path)

    # Then: expiry restores the violation.
    violations = _violations(payload)
    assert process.returncode == 1
    assert payload["passed"] is False
    assert [(item["file"], item["rule_id"]) for item in violations] == [
        ("src/legacy.tsx", "design/raw-color")
    ]
