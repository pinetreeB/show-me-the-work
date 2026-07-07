# Changelog

## [1.1.0] - 2026-07-07

### Added — Intent Gate ("알" gate)

- Ambiguity scoring (`core/ambiguity.py`): signal-count score (0-4) with a conservative threshold of 2 and hard never-flag guards (questions, explicit paths, existing goals/intent, "그냥 해"). Scoring→threshold-gating methodology adapted from [Yeachan-Heo/gajae-code](https://github.com/Yeachan-Heo/gajae-code) (MIT).
- Intent-interview packs (`packs/intent-interview.ko.md` / `.en.md`): at most 3 questions, one at a time, multiple-choice first; explicit-assumption path (`--assumed`) when the user says "just do it".
- `fable_lite intent set/show/clear` CLI writing `.fable-lite/intent.json`; a new prompt auto-clears the previous intent.
- PreToolUse edit-lock: when a prompt is flagged ambiguous, Edit/Write/patch tools are blocked until `intent.json` exists (2-block cap, fail-open; Bash/read tools stay allowed so investigation can proceed).
- Self-locating launcher `fable-lite-cli.py` + absolute-path command injection: hooks compute their own install location and embed a copy-paste-runnable command into block reasons and injected context, so recovery works in plugin installs without `pip` and without relying on model path-hunting.

### Evidence

- Ambiguity corpus (41 real-style Korean prompts, adversarially authored by a different model): accuracy 100% — 0 false positives, 0 false negatives after a targeted fix round (initial run: FP 0, FN 7).
- Live nested-session E2E: ambiguous prompt → questions first and Edit genuinely blocked; clear prompt → zero added friction; "그냥 알아서" → assumption declared and recorded with `--assumed`; after the launcher fix, the model copy-executed the injected command verbatim and produced a standard-schema `intent.json` with no workarounds.

### Fixed

- BLOCKER (found in live E2E): `python -m fable_lite` failed with `ModuleNotFoundError` from arbitrary project directories under plugin installs — resolved via the self-locating launcher plus injected absolute-path commands.
- Verification OK-signal matching now uses word-boundary regex (`\bok\b`, case-insensitive): recognizes leading `ok ...` and `OK: ...`, with a regression test proving `broken` does not false-positive.

### Known Limitations

- Value-dump verification output with no pass/ok token (e.g. `add(2,3) = 5`) is conservatively treated as unverified — the safe direction (gate gets stricter, never looser).
- `확인질문 N:` marker adherence varies turn-to-turn; the gate is deterministic on `intent.json` file existence, markers are guidance.

## [1.0.0] - 2026-07-07

### Stabilized

- Core discipline engine: Korean-first prompt classification, investigation compliance markers, evidence-gated Stop checks, scope warnings, high-risk contract gating, ledger persistence, and goals checkpoints.
- Three adapter surfaces: Claude Code, Codex CLI, and Antigravity thin wrappers around the shared core behavior.
- Orchestrator CLI: `python -m fable_lite check` and `python -m fable_lite brief`, including task-card based delegation checks.
- Evaluation surface: deterministic probe runner, smoke harness, A/B methodology documents, and summarized review reports.

### Evidence

- Controlled A/B evaluation: fable-lite ON won 5/5 tasks against OFF in the blind-judged report.
- Live 3-CLI observation: Claude Code, Codex CLI, and Antigravity adapter paths were exercised as practical hook surfaces.
- Natural-compliance experiment: pack-only behavior failed to reliably enforce investigation markers, while the Stop gate recovered runs with concrete evidence.

### Known Limitations

- Verification recognition is centered on Python, JavaScript, shell, and common build/test runner patterns. Other ecosystems may need additional adapter policy.
- Evaluation sample sizes are intentionally small; the reports support release readiness, not statistical generalization.
- Sonamu Bot deployment assumes a host with Python available on `PATH` because hooks are stdlib Python scripts.
- fable-lite reproduces procedural discipline only. It does not claim to reproduce Fable 5 model-weight capabilities.

## [0.6.x] - 2026-07

- Added orchestrator `check` and `brief` commands, including task-card integration.
- Added Codex CLI and Antigravity adapters alongside the Claude Code adapter.
- Added deterministic probe runner and JSON result output.

## [0.5.x] - 2026-07

- Hardened adapter payload parsing, fail-open behavior, N1 investigation compliance wiring, and Stop gate sharing.
- Added regression tests for realistic nested tool payloads and malformed hook inputs.

## [0.4.x] - 2026-07

- Added compliance, scope, and high-risk contract modules.
- Added bilingual investigation marker parsing and pack alignment checks.

## [0.3.x] - 2026-07

- Added ledger, verification state, and goals checkpoint CLI behavior.
- Added tests for Stop blocking limits and verification recording.

## [0.2.x] - 2026-07

- Added Korean prompt classification patterns and initial pack routing.
- Added Claude Code hook scaffold and plugin manifest wiring.

## [0.1.x] - 2026-07

- Project bootstrap, research notes, initial architecture, and v1 contract drafts.
