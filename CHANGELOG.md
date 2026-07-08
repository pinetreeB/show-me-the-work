# Changelog

## [1.1.3] - 2026-07-08

### Changed — N1 markers only on turns that change files (user feedback)

- The N1 investigation-marker gate now applies only to turns that actually modified files. Answer-only turns ("why doesn't this work?") reply in plain prose — no `가설/증거/기각` block demanded. Rationale: the pack's purpose is "investigate before you modify"; forcing markers on a turn that modifies nothing is friction, not discipline.
- Ledger change/verification records are now reset per prompt (turn-scoped). Without this, one early edit kept `changed=True` for every later question turn in the session — an adversarial review (Critical-1) caught that the relaxation would have been ineffective before it shipped.
- Packs (ko/en) document the exemption, and still recommend recording markers for investigation-only turns so a later fix turn doesn't retro-invent its investigation.
- Known accepted limitation (documented, backlog): file edits made via raw shell commands are not counted as changes, so such turns bypass N1 — fable-lite's threat model is carelessness, not an adversarial model deliberately dodging its own gates.
- pytest 101 (4 new: exemption x2, per-turn reset, changed-turn still blocks), probes 15/15.

## [1.1.2] - 2026-07-08

### Changed — report style guidance (user feedback)

- Investigation packs (ko/en) gained a "Report style: body for humans, markers for the gate" section: the final report must lead with a plain-language body (no code identifiers/paths/line numbers; readers may be non-developers), with the marker block compressed to one line each at the bottom. Rationale: the N1 gate only parses marker existence, but models were letting the marker format drag the whole report into a technical document — forcing non-developer users to ask follow-up questions.
- N1 block message now carries the same hint ("markers are one compact line each at the bottom; keep the body in plain language"), so a blocked model rewrites toward a readable report instead of a more technical one.
- Gate logic unchanged: pytest 97, probes 15/15.

## [1.1.1] - 2026-07-08

### Fixed — briefing false positives (real-world defect)

- Worker boot/role-injection prompts ("[부팅] ... read MEMORY.md and X.md ... then stand by") were classified as multi-story work: the two memory file paths alone tripped `needs_goals`, so the PreToolUse gate blocked the worker's first Bash/Edit calls twice and wasted 1-2 minutes per boot (observed live on a Sonnet worker pane).
- New briefing detection: a boot marker ("[부팅]", "세션 부팅", ...) or a stand-by closing ("대기하라", "standby", ...) suppresses `needs_goals`/multi-story/artifact signals — but only when no imperative action follows. Imperative-suffix matching keeps rule phrases ("...만 수정", "sentinel 파일 생성.") from counting as actions, while "[부팅] ... auth.py 고쳐줘" still gates (adversarial-review bypass guard).
- `is_debug` and `risk_flags` are intentionally NOT suppressed inside briefings: a boot prompt carrying a dangerous command still hits the R1 contract gate.
- Path mention counting no longer counts version strings (`v1.1.0`) or bare domains (`google.com`) as file paths.
- Word-boundary matching for multi-story terms: `and` no longer matches inside `commands`/`sandbox`; `여러분`, `12개월`, `multiply` no longer false-positive.

### Evidence

- Reproduced both reported boot prompts before the fix (`needs_goals=True`, spurious verification pack), confirmed suppression after (`needs_goals=False`, no packs), and confirmed the enumeration regression probe (PRB03) still blocks real multi-story prompts.
- pytest 92 (10 new classification tests), deterministic probes 15/15, live-payload hook E2E: boot prompt → Bash allowed; multi-story prompt → Edit blocked.

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
