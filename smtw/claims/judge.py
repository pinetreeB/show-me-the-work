"""Manual, terminal-only subscription judge; every test injects the executor."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from .judge_contract import PROMPT, SCHEMA, cli_schema, validate_contract
from .judge_scope import JudgeScope, load_scope
from .advisory import load_sent_items, read_json, verify_judgments
from .packets import display_path, sensitive_path, hard_excluded, sent_sources, redact
from .judge_scope import work_source_basis

MODEL = "claude-sonnet-5-5"
HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".ai-limits-widget" / "limits-cache.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def child_environment(parent):
    # Iterate names and filter BEFORE looking up any value.
    names = list(parent)
    if any(key.upper().startswith("ANTHROPIC_")
           or key.upper() in {"CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"}
           for key in names):
        raise ValueError("provider override environment name present; subscription execution refused")
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR",
               "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH",
               "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_COLLATE",
               "LC_NUMERIC", "LC_TIME", "LC_MONETARY", "LC_PAPER", "LC_NAME", "LC_ADDRESS",
               "LC_TELEPHONE", "LC_MEASUREMENT", "LC_IDENTIFICATION", "TERM", "TZ"}
    env = {key: parent[key] for key in names if key.upper() in allowed}
    # Match claude-p without reading either configuration directory.
    env["CLAUDE_CONFIG_DIR"] = str(Path.home() / ".claude-personal")
    env["CLAUDE_CODE_PLUGIN_CACHE_DIR"] = str(Path.home() / ".claude" / "plugins")
    return env


def read_usage(path: Path = CACHE) -> dict:
    try:
        service = read_json(path)["services"]["claude_personal"]
        stamp = service["as_of"]
        if (service.get("error") or type(stamp) not in {int, float}
                or not 0 < stamp < 100_000_000_000_000):
            return {"known": False}
        # The personal widget stores epoch milliseconds; tolerate older seconds
        # caches too, but never treat an implausible future timestamp as fresh.
        seconds = stamp / 1000 if stamp >= 100_000_000_000 else stamp
        if not -60 <= time.time() - seconds <= 600:
            return {"known": False}
        values = {}
        for row in service["rows"]:
            label = row["label"]
            if label in {"5시간", "5h", "five_hour"}:
                values["five_hour"] = row["used"]
            elif label in {"주간 전체", "weekly", "seven_day"}:
                values["weekly"] = row["used"]
        if set(values) != {"five_hour", "weekly"} or any(
                type(v) not in {int, float} or not math.isfinite(v) or not 0 <= v <= 100
                for v in values.values()):
            return {"known": False}
        return {"known": True, **values}
    except (OSError, ValueError, KeyError, TypeError):
        return {"known": False}


def usage_gate(usage: dict, allow_unknown: bool):
    if not usage.get("known"):
        if not allow_unknown:
            raise ValueError("usage unknown; --allow-unknown-usage is required")
        return
    if usage["five_hour"] >= 40 or usage["weekly"] >= 55:
        raise ValueError("personal subscription usage threshold reached")


def validate_scope(manifest: dict, sent: dict, scope: JudgeScope):
    if manifest.get("scope_sha256", scope.sha256) != scope.sha256:
        raise ValueError("scope changed since packet generation")
    if "sent_sources" in manifest and manifest["sent_sources"] != sent_sources(sent):
        raise ValueError("sent_sources differs from packet excerpts")
    cases = scope.cases
    item_cases = manifest.get("item_cases", {})
    if not isinstance(item_cases, dict):
        raise ValueError("invalid case mapping")
    for key, item in sent.items():
        case = item_cases.get(key, manifest.get("case_id"))
        if case not in cases:
            raise ValueError("case outside the fixed Phase 2 allowlist")
        for field in ("claim", "before", "after"):
            value = item.get(field, "")
            if redact(value)[0] != value:
                raise ValueError("unredacted pattern in report input")
        for excerpt in item["excerpts"]:
            if redact(excerpt["text"], preserve_source_lines=True)[0] != excerpt["text"]:
                raise ValueError("unredacted pattern in excerpt input")
            path = display_path(excerpt["path"]).replace("\\", "/").casefold()
            parts = path.split("/")
            basis = work_source_basis(case, excerpt["path"], scope, visible=True)
            approved = (excerpt.get("source") == "transcript_read"
                        and excerpt.get("scope_basis") == basis) if basis else any(
                            path.startswith(prefix.casefold()) for prefix in cases[case])
            if (".." in parts or hard_excluded(path) or not approved):
                raise ValueError("excerpt outside approved source roots")


def resolve_executable() -> str:
    """Find native Windows Claude by metadata only; never run help/version."""
    if os.name == "nt":
        candidates = []
        native = shutil.which("claude.exe")
        if native:
            candidates.append(Path(native))
        # npm's Windows shim installs this native binary beside its wrapper.
        shim = shutil.which("claude")
        if shim:
            candidates.append(Path(shim).parent / "node_modules" / "@anthropic-ai"
                              / "claude-code" / "bin" / "claude.exe")
        for path in candidates:
            if path.is_absolute() and path.is_file() and path.name.casefold() == "claude.exe":
                return str(path.resolve())
    launcher = shutil.which("claude-p")
    if not launcher:
        raise ValueError("personal subscription executable is unavailable")
    return launcher


def command(schema: str, executable: str, *, lean_context: bool = False) -> list[str]:
    if type(lean_context) is not bool:
        raise ValueError("lean-context must be a boolean")
    name = Path(executable).name.casefold()
    if name not in {"claude-p", "claude-p.cmd", "claude-p.exe", "claude.exe"}:
        raise ValueError("only the personal launcher or native claude.exe is allowed")
    if name == "claude.exe" and not Path(executable).is_absolute():
        raise ValueError("native executable requires a resolved absolute path")
    # Batch CALL splits literal newlines. Compact JSON also preserves empty args
    # and schema quotes through the legacy two-shim Windows chain.
    compact = cli_schema(schema)
    argv = [executable, "--model", MODEL, "-p", "--tools", "",
            "--no-session-persistence", "--output-format", "json",
            "--disable-slash-commands", "--strict-mcp-config", "--json-schema", compact]
    # MCP/slash isolation is mandatory; lean only changes prompt delivery and cwd.
    if lean_context:
        argv += ["--system-prompt-file", str(PROMPT.resolve())]
    return argv


def _launch(argv: list[str], prompt: str, env: dict, *, cwd: str | None = None) -> subprocess.CompletedProcess:
    """The sole real execution point. Tests must never enter this function."""
    return subprocess.run(argv, input=prompt, env=env, capture_output=True,
                          encoding="utf-8", errors="strict", timeout=300, cwd=cwd)


def _is_error_response(value: dict) -> bool:
    # Malformed truthy error flags and boolean status values fail closed.
    return (bool(value.get("is_error"))
            or type(value.get("api_error_status")) in {int, bool}
            or value.get("subtype") != "success")


def _reported_error(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(value, dict) or not _is_error_response(value):
        return {}
    result = value.get("result", "")
    # Only the CLI's bounded cause is eligible for the receipt. Never inspect
    # environment values or persist exception/stderr text as the cause.
    reason = redact(result[:200])[0][:200] if isinstance(result, str) else ""
    status = value.get("api_error_status")
    return {"error": "judge_reported_error", "error_reason": reason,
            "api_error_status": status if type(status) is int else None}


def _token_usage(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except ValueError:
        value = {}
    usage = value.get("usage") if isinstance(value, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    # Copy only numeric counters, never arbitrary response metadata.
    return {key: usage[key] if type(usage.get(key)) is int and usage[key] >= 0 else None
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                        "cache_creation_input_tokens")}


def _judgments(raw: str):
    value = json.loads(raw)
    if isinstance(value, dict):
        if _is_error_response(value):
            raise ValueError("judge reported an error")
        value = value.get("structured_output", value.get("result", value))
        if isinstance(value, str):
            value = json.loads(value)
    if isinstance(value, dict) and "judgments" in value:
        if set(value) != {"judgments"}:
            raise ValueError("unexpected fields in judgments wrapper")
        value = value["judgments"]
    return value


def judge(folder: Path, *, scope_file: Path, output_root: Path, max_calls: int = 4, allow_unknown_usage: bool = False,
          executor=None, tty=None, usage_reader=None, parent_env=None,
          executable=None, lean_context: bool = False) -> dict:
    if type(max_calls) is not int or not 1 <= max_calls <= 8:
        raise ValueError("max-calls must be between 1 and 8")
    if not (tty or (lambda: sys.stdin.isatty() and sys.stdout.isatty()))():
        raise ValueError("a visible terminal on stdin and stdout is required")
    env = child_environment(os.environ if parent_env is None else parent_env)
    sent = load_sent_items(folder)  # Validate every hash and file before any call.
    manifest = read_json(folder / "manifest.json")
    scope = load_scope(scope_file)
    validate_scope(manifest, sent, scope)
    validate_contract(manifest)
    if not sent or not 1 <= len(manifest["packets"]) <= max_calls:
        raise ValueError("empty packet folder or max-calls exceeded")
    schema = SCHEMA.read_text(encoding="utf-8")
    instructions = PROMPT.read_text(encoding="utf-8")
    executable = executable or resolve_executable()
    argv = command(schema, executable, lean_context=lean_context)
    usage_reader = usage_reader or read_usage
    before = usage_reader()
    usage_gate(before, allow_unknown_usage)
    if not before.get("known"):
        print("Warning: usage is unknown; explicit operator override applied.", file=sys.stderr)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    destination = output_root / "judge-runs" / run_id
    destination.mkdir(parents=True)
    results = []
    for index, entry in enumerate(manifest["packets"], 1):
        before = usage_reader()
        usage_gate(before, allow_unknown_usage)
        data = (folder / entry["file"]).read_bytes()
        if sha(data) != entry["sha256"] or len(data.decode("utf-8")) > 30000:
            raise ValueError("packet changed after preflight")
        validate_contract(manifest)
        if load_scope(scope_file).sha256 != scope.sha256:
            raise ValueError("scope changed after preflight")
        subset = {key: sent[key] for key in entry["item_ids"]}
        validate_scope(manifest, sent, scope)  # Scan again immediately before every send.
        prompt = instructions + data.decode("utf-8")
        started = time.monotonic()
        raw, exit_code, error = "", None, None
        error_details = {}
        try:
            execute = executor or _launch
            if lean_context:
                # A new empty system TEMP child per call; no process-wide chdir.
                # The context also cleans up after errors and timeouts.
                with tempfile.TemporaryDirectory(prefix="smtw-judge-") as cwd:
                    completed = execute(argv, prompt, env, cwd=cwd)
            else:
                completed = execute(argv, prompt, env)
            raw = completed.stdout
            exit_code = completed.returncode
            error_details = _reported_error(raw)
            if error_details:
                error = error_details["error"]
            elif exit_code != 0:
                error = "judge_nonzero_exit"
        except Exception as exc:
            # Never log exception text or stderr: either can contain environment data.
            error = type(exc).__name__
        after = usage_reader()
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "packet_sha256": entry["sha256"], "item_ids": entry["item_ids"],
            "sent_sources": sent_sources(subset),
            "model": MODEL, "argv": argv, **manifest["judge_contract"], "exit_code": exit_code,
            "scope_sha256": scope.sha256, "lean_context": lean_context,
            "usage": _token_usage(raw),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "output_sha256": sha(raw.encode("utf-8")), "usage_before": before,
            "usage_after": after, "error": error, "run_id": run_id,
            "execution": "injected" if executor else "personal_subscription",
            **error_details,
        }
        with (output_root / "calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        (destination / f"raw-{index:03d}.json").write_bytes(raw.encode("utf-8"))
        if error:
            raise ValueError("judge execution failed; see call receipt")
        rows = _judgments(raw)
        subset = {key: sent[key] for key in entry["item_ids"]}
        verified = verify_judgments(rows, subset)
        (destination / f"judgments-{index:03d}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        (destination / f"verified-{index:03d}.json").write_text(
            json.dumps(verified, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(verified)
    return {"run": str(destination), "calls": len(results),
            "execution": "injected" if executor else "personal_subscription",
            "unknown_usage_allowed": allow_unknown_usage}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--scope-file", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=4)
    parser.add_argument("--allow-unknown-usage", action="store_true")
    parser.add_argument("--lean-context", action="store_true",
                        help="opt in to Phase 2 lean context with an empty temporary cwd")
    args = parser.parse_args(argv)
    try:
        result = judge(args.packets, scope_file=args.scope_file, output_root=args.out_root,
                       max_calls=args.max_calls,
                       allow_unknown_usage=args.allow_unknown_usage,
                       lean_context=args.lean_context)
    except (OSError, ValueError, KeyError, TypeError):
        print("Judge refused or failed; check packet/usage/TTY and receipts.", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
