import json
from pathlib import Path
import time

import pytest

from smtw.claims import judge as runner
from tests.claims_judge_support import make_scope
from tests.test_claims_judge import fake_judge, invoke, packet_folder


@pytest.fixture(autouse=True)
def never_launch_model(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("REAL MODEL LAUNCH FORBIDDEN")
    monkeypatch.setattr(runner, "_launch", forbidden)
    monkeypatch.setattr(runner.subprocess, "run", forbidden)


def native(tmp_path):
    # command() only needs an absolute claude.exe path; nothing is executed.
    return str(tmp_path / "bin" / "claude.exe")


@pytest.mark.parametrize("value", ["", "   ", "relative-config", None, 0])
def test_config_directory_refuses_empty_relative_and_non_paths(value):
    with pytest.raises(ValueError):
        runner.config_directory(value)


def test_config_directory_refuses_missing_and_file(tmp_path):
    with pytest.raises(ValueError):
        runner.config_directory(tmp_path / "missing")
    file = tmp_path / "file.txt"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        runner.config_directory(file)


def test_explicit_config_directory_reaches_child_environment(tmp_path):
    config = tmp_path / "synthetic-config"
    config.mkdir()
    env = runner.child_environment({"PATH": "synthetic-path"}, config)
    assert env["CLAUDE_CONFIG_DIR"] == str(config)
    assert env["CLAUDE_CODE_PLUGIN_CACHE_DIR"] == str(Path.home() / ".claude" / "plugins")
    with pytest.raises(ValueError):
        runner.child_environment({"PATH": "synthetic-path"}, "")


@pytest.mark.parametrize("value", ["", "relative-config"])
def test_invalid_config_directory_refuses_before_executor(tmp_path, value):
    def fail(*args, **kwargs):
        pytest.fail("executor called with invalid config directory")
    with pytest.raises(ValueError):
        invoke(packet_folder(tmp_path), tmp_path, executor=fail, config_dir=value,
               executable=native(tmp_path))
    assert not (tmp_path / "out/calls.jsonl").exists()


def test_claude_p_launcher_refused_with_explicit_config(tmp_path):
    config = tmp_path / "synthetic-config"
    config.mkdir()
    def fail(*args, **kwargs):
        pytest.fail("executor called through the personal launcher")
    with pytest.raises(ValueError, match="claude-p"):
        invoke(packet_folder(tmp_path), tmp_path, executor=fail, config_dir=config)


def test_explicit_config_is_used_and_recorded(tmp_path):
    config = tmp_path / "synthetic-config"
    config.mkdir()
    seen = []
    def executor(argv, prompt, env, **kwargs):
        seen.append(env["CLAUDE_CONFIG_DIR"])
        return fake_judge(argv, prompt, env)
    result = invoke(packet_folder(tmp_path), tmp_path, executor=executor, config_dir=config,
                    usage_service="claude", executable=native(tmp_path))
    assert seen == [str(config)]
    assert result["config_dir_name"] == "synthetic-config" and result["usage_service"] == "claude"
    record = json.loads((tmp_path / "out/calls.jsonl").read_text(encoding="utf-8"))
    assert record["config_dir_name"] == "synthetic-config" and record["usage_service"] == "claude"


def test_unknown_usage_service_refused(tmp_path):
    with pytest.raises(ValueError):
        invoke(packet_folder(tmp_path), tmp_path, usage_service="other")
    with pytest.raises(ValueError):
        runner.read_usage(tmp_path / "usage.json", "other")


def test_cli_requires_config_dir_and_usage_service():
    base = ["--packets", "synthetic", "--scope-file", "synthetic", "--out-root", "synthetic"]
    for extra in ([], ["--config-dir", "synthetic"], ["--usage-service", "claude"],
                  ["--config-dir", "synthetic", "--usage-service", "other"]):
        with pytest.raises(SystemExit) as error:
            runner.main(base + extra)
        assert error.value.code == 2


def write_usage(path, service, rows):
    path.write_text(json.dumps({"services": {service: {
        "as_of": time.time(), "error": None, "rows": rows}}}), encoding="utf-8")


def test_service_without_total_weekly_keeps_five_hour_only(tmp_path):
    path = tmp_path / "usage.json"
    write_usage(path, "claude", [{"label": "5시간", "used": 4}, {"label": "주간 Fable", "used": 38}])
    assert runner.read_usage(path, "claude") == {"known": False, "five_hour": 4}
    write_usage(path, "claude", [{"label": "5시간", "used": 4}, {"label": "주간 전체", "used": 38}])
    assert runner.read_usage(path, "claude") == {"known": True, "five_hour": 4, "weekly": 38}
    with pytest.raises(KeyError):
        runner.read_json(path)["services"]["claude_personal"]
    assert runner.read_usage(path) == {"known": False}


@pytest.mark.parametrize("usage", [{"known": False, "five_hour": 40},
                                   {"known": False, "weekly": 55}])
def test_unknown_override_still_enforces_known_limits(usage):
    with pytest.raises(ValueError, match="threshold"):
        runner.usage_gate(usage, allow_unknown=True)
    runner.usage_gate({"known": False, "five_hour": 39}, allow_unknown=True)
    with pytest.raises(ValueError, match="unknown"):
        runner.usage_gate({"known": False, "five_hour": 1}, allow_unknown=False)


@pytest.mark.parametrize("rows,kept", [
    ([{"label": "5시간", "used": 45}, {"label": "주간 전체", "used": -1}], {"five_hour": 45}),
    ([{"label": "5시간", "used": 45}, {"label": "주간 전체", "used": float("nan")}], {"five_hour": 45}),
    ([{"label": "5시간", "used": -1}, {"label": "주간 전체", "used": 60}], {"weekly": 60}),
    ([{"label": "5시간", "used": 1}, {"label": "주간 전체", "used": 101}], {"five_hour": 1}),
])
def test_mixed_valid_and_invalid_rows_refused_even_with_override(tmp_path, monkeypatch, rows, kept):
    cache = tmp_path / "usage.json"
    write_usage(cache, "claude", rows)
    assert runner.read_usage(cache, "claude") == {"known": False, "invalid": True, **kept}
    monkeypatch.setattr(runner, "CACHE", cache)
    config = tmp_path / "synthetic-config"
    config.mkdir()
    def fail(*args, **kwargs):
        pytest.fail("executor called with malformed usage data")
    with pytest.raises(ValueError):
        runner.judge(packet_folder(tmp_path), scope_file=make_scope(tmp_path), output_root=tmp_path / "out",
                     executor=fail, tty=lambda: True, parent_env={}, executable=native(tmp_path),
                     config_dir=config, usage_service="claude", allow_unknown_usage=True)
    assert not (tmp_path / "out/calls.jsonl").exists()


@pytest.mark.parametrize("rows,kept", [
    ([{"label": "5시간", "used": 45}, {"label": "5시간", "used": 4}, {"label": "주간 전체", "used": 10}],
     {"five_hour": 45, "weekly": 10}),
    ([{"label": "5시간", "used": -1}, {"label": "5h", "used": 4}, {"label": "주간 전체", "used": 10}],
     {"weekly": 10}),
    ([{"label": "5시간", "used": 4}, {"label": "weekly", "used": 60}, {"label": "seven_day", "used": 10}],
     {"five_hour": 4, "weekly": 60}),
])
def test_duplicate_usage_labels_are_malformed(tmp_path, rows, kept):
    cache = tmp_path / "usage.json"
    write_usage(cache, "claude", rows)
    usage = runner.read_usage(cache, "claude")
    assert usage == {"known": False, "invalid": True, **kept}
    with pytest.raises(ValueError):
        runner.usage_gate(usage, allow_unknown=True)
