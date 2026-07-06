# GitHub 리서치: Fable 5 행동능력 모사/재현 오픈소스 후보

조사 시점: 2026-07-06 KST.  
범위: `fivetaku/fablize`와 `Anil-matcha/awesome-claude-fable-5`는 이미 알려진 대상으로 보고, 후보 발굴 목록에서는 제외했습니다. 다만 `fivetaku/fablize`는 비교 기준선으로만 사용했습니다.

## 요약 결론

GitHub에는 “하위 Claude 모델을 Fable 5처럼 만든다”는 저장소가 이미 여러 갈래로 생겼습니다. 하지만 코드 수준으로 보면 대부분은 모델 능력 자체를 복제하지 않고, Fable에서 관찰된 작업 규율을 외부 절차로 강제합니다.

가장 실사용 참고 가치가 높은 축은 4가지입니다.

1. **hard gate형**: `why-was-fable-banned`처럼 spec이 통과하기 전 edit 자체를 막는 방식.
2. **completion/verification gate형**: `fable-ish`, `fable-harness`, `fablize`처럼 ledger와 Stop hook으로 검증 없는 완료를 막는 방식.
3. **skill/rules pack형**: `mrtooher/fable-mode`, `op-fable`처럼 stage map, failable check, self-review를 프롬프트 규칙으로 주입하는 방식.
4. **orchestration/ledger형**: `Rylaa/fable5-orchestrator`처럼 모델별 delegation 전략과 Requirements Ledger를 hook으로 보강하는 방식.

반대로 “Fable 5의 가중치 수준 판단력, self-driven implication depth, out-of-spec defect discovery, 장기 문맥 coherence”를 그대로 재현한 공개 코드는 찾지 못했습니다. 이를 주장하는 저장소도 결국 prompt/style injection 또는 gate discipline으로 귀결됐습니다.

## 검색 경로와 실패 각도

사용한 검색식/경로:

- GitHub repository search: `"fable 5" claude`, `fablize`, `"mythos class" claude`, `"Claude Code" hooks verification gate`, `"Claude Code" completion gate`, `"Claude Code" discipline harness`, `awesome-claude-code`.
- awesome/마켓플레이스류 목록: `hesreallyhim/awesome-claude-code`, `ccplugins/awesome-claude-code-plugins`, `rohitg00/awesome-claude-code-toolkit`.
- GitHub API: `/repos/{owner}/{repo}`와 `/commits/{default_branch}`로 stars, pushed_at, 최신 커밋 시각을 확인.

발굴 실패/제외 각도:

- F# 언어/프레임워크인 “Fable” 관련 저장소가 대량으로 섞였습니다. 예: `Fable.Expect`, `Fable.Mocha`, WebGL/Fable 앱 예제. Claude Fable 5와 무관해서 제외했습니다.
- prompt leak/prompt vault류는 많지만, 하네스/검증/상태 관리가 없는 단순 프롬프트 저장소는 제외했습니다. 예: system prompt leak 모음.
- jailbreak/offensive 보안 취지의 Fable 관련 repo는 Fable-lite 일반 작업 규율과 목적이 다르거나 재사용 리스크가 커서 제외했습니다. 예: `fable-jailbreak`, `mythosharness`류.
- `planning-with-files`, `GateGuard`, `cc-discipline`, `obey`, `Bouncer`, `weft`, `agent-completion-gate`, `menhera-loop` 등은 규율/완료 gate 생태계로는 유의미하지만, 직접 “Fable 5 행동능력 모사”를 목표로 한 저장소는 아니어서 인접 후보로만 기록했습니다.

근거 목록:

- awesome list: https://github.com/hesreallyhim/awesome-claude-code
- awesome toolkit: https://github.com/rohitg00/awesome-claude-code-toolkit
- Claude Code plugins list: https://github.com/ccplugins/awesome-claude-code-plugins
- GitHub topic `verification-gate`: https://github.com/topics/verification-gate
- GitHub topic `claude-fable`: https://github.com/topics/claude-fable
- `mrtooher/fable-mode` 커뮤니티 eval 주장 출처(Reddit, repo 재현 파일 아님): https://www.reddit.com/r/ClaudeAI/comments/1u4iktp/built_a_claude_skill_that_mimics_fable_5s_agentic/

## 후보 분류표

| 구분 | Repo | URL | Stars | 최신 커밋일(UTC) | 접근법 | 검증 실험/테스트 | 판정 |
|---|---|---:|---:|---:|---|---|---|
| 기준선 | fivetaku/fablize | https://github.com/fivetaku/fablize | 768 | 2026-07-06 | 검증된 절차만 조건부 pack/router/hook으로 주입. deep-only Stop gate, evidence ledger, early-stop guard. | README상 19 A/B + 26 실작업 세션, 약 1,500 tool calls 자체 측정. | 비교 기준. 후보에서는 제외. |
| 심층 후보 | mrtooher/fable-mode | https://github.com/mrtooher/fable-mode | 600 | 2026-07-05 | skill/rules pack. stage map, subagent delegation, failable check, self-review. Sonnet/Haiku variant 제공. | repo 내 자동 테스트 없음. EXAMPLE.md는 worked example. Reddit상 eval 주장은 있으나 repo 재현 파일은 확인 못함. | 프롬프트 규율형 후보 중 stars/활성도 최상. |
| 심층 후보 | Miguok/fable-harness | https://github.com/Miguok/fable-harness | 132 | 2026-07-05 | 상시 protocol 주입, per-turn nudge, Stop verification gate, adversarial subagents, model routing. | `tests/test_verify_gate.py` 10개 gate 케이스. 외부 벤치 없음. | Claude Code용 “행동 바닥” 구현으로 실용적. |
| 심층 후보 | SihyeonJeon/why-was-fable-banned | https://github.com/SihyeonJeon/why-was-fable-banned | 45 | 2026-06-30 | `.wfb/spec.json` hard PreToolUse edit gate, done gate, forbidden path, state files, Codex worktree accept. | README 자체 보고: gate tests 35/35, SWE-bench combined 29/38→31/38, toy tasks 10/10 양쪽. | hard gate 설계 참고 가치가 가장 큼. |
| 심층 후보 | chrisryugj/fable-ish | https://github.com/chrisryugj/fable-ish | 38 | 2026-06-22 | Claude Code plugin port. task classifier, risk block, evidence ledger, Stop completion gate. | `tests/test_hooks.py` 존재. 공개 A/B 벤치 없음. | fablize와 가장 유사한 completion gate 계열. |
| 심층 후보 | HalalifyMusic/fable-mode | https://github.com/HalalifyMusic/fable-mode | 12 | 2026-06-21 | leaked Fable prompt + execution playbook + UserPromptSubmit injection + non-blocking test-after-edit hook. | CI/tests 있음. README before/after demo 중심, 재현 벤치 부족. | 직접 Fable-style 지향이나 IP/운영 리스크 큼. |
| 보조 후보 | Rylaa/fable5-orchestrator | https://github.com/Rylaa/fable5-orchestrator | 7 | 2026-07-03 | Fable/Opus model-aware orchestration profile, Requirements Ledger, spawn/close guard hooks. | pytest 기반 hook tests. 품질 벤치 없음. | 직접 모사보다 orchestration 비용/품질 균형 참고. |
| 보조 후보 | dilitS/op-fable | https://github.com/dilitS/op-fable | 21 | 2026-06-16 | 정적 `SKILL.md`/rules pack. multi-pass reasoning, stage map, TDD, verification, self-critique. | 코드/hook/test 없음. | 가장 가볍지만 강제력 없음. |
| 인접 생태계 | OthmanAdi/planning-with-files | https://github.com/OthmanAdi/planning-with-files | 24860 | 2026-07-06 | persistent file planning/completion discipline. Fable-specific 아님. | 자체 프로젝트 활성도 높음. | Fable-lite의 file-state 설계 참고. |

## 후보별 코드 수준 분석

### 1. mrtooher/fable-mode

Repo: https://github.com/mrtooher/fable-mode  
핵심 파일:

- README: https://github.com/mrtooher/fable-mode/blob/main/README.md
- skill: https://github.com/mrtooher/fable-mode/blob/main/SKILL.md
- Sonnet variant: https://github.com/mrtooher/fable-mode/blob/main/fable-sonnet/SKILL.md
- example: https://github.com/mrtooher/fable-mode/blob/main/EXAMPLE.md

접근법:

- hook이나 별도 실행 엔진이 아니라 **Claude skill**입니다.
- 핵심 루프는 stage map → delegation 가능성 판단 → failable verification check → skeptical self-review입니다.
- 모델 tier별 강도를 다르게 설명합니다. Frontier/Fable/Mythos급은 ceremony를 줄이고, Sonnet/Haiku급은 verification을 더 강하게 요구합니다.
- `fable-sonnet`, `fable-haiku` 변형은 Agent tool이 있을 때 특정 모델로 subagent를 띄우는 절차를 적습니다.

fablize와 비교:

- fablize는 hook/ledger/router로 관찰 가능한 state를 기록하고 Stop에서 차단합니다.
- `mrtooher/fable-mode`는 강제력이 없습니다. 모델이 skill을 무시하면 막을 수 없습니다.
- 대신 매우 간단하고 IP 리스크가 낮습니다. “Fable-style procedure”를 텍스트 규칙으로 정리한 참고 자료로 좋습니다.

Fable-lite에 이식 가능한 요소:

- stage별 failable check 정의.
- 모델 tier별 ceremony budget.
- subagent는 “분리 가능한 stage”에만 쓰고, coherent thought는 쪼개지 않는 규칙.
- “검증 불가능한 stage는 unverified로 명시”하는 보고 규칙.

이식 불가능/약한 요소:

- 실제 완료 차단, 상태 추적, 검증 명령 판정은 없습니다.
- EXAMPLE.md는 설득용 사례이지 자동 재현 실험은 아닙니다.

### 2. Miguok/fable-harness

Repo: https://github.com/Miguok/fable-harness  
핵심 파일:

- README: https://github.com/Miguok/fable-harness/blob/main/README.md
- protocol: https://github.com/Miguok/fable-harness/blob/main/.claude/hooks/fable_protocol.md
- Stop gate: https://github.com/Miguok/fable-harness/blob/main/.claude/hooks/verify_gate.py
- tests: https://github.com/Miguok/fable-harness/blob/main/tests/test_verify_gate.py
- CLAUDE rules/model routing: https://github.com/Miguok/fable-harness/blob/main/CLAUDE.md

접근법:

- README는 “Fable의 procedure만 이식하고 innate judgment는 이식하지 못한다”고 명시합니다.
- 구성은 behavior protocol, per-turn nudge, Stop verification gate, adversarial review skill, skeptic/red-team/simplifier subagents, model routing입니다.
- `.claude/hooks/verify_gate.py`는 transcript를 읽어 마지막 user prompt 이후 코드 파일 편집이 있었는지 확인하고, 테스트 명령을 못 찾으면 `{"decision":"block"}`를 출력합니다.
- `stop_hook_active=true`면 두 번째 종료 시도는 통과시켜 무한 루프를 방지합니다.
- fail-open 원칙입니다. gate 자체 오류가 session을 망가뜨리지 않도록 예외를 삼킵니다.

검증:

- `tests/test_verify_gate.py`가 10개 케이스를 명시합니다. 예: code edit without test blocks, pytest after edit allows, docs-only allows, corrupt transcript fail-open, non-test command must still block.
- 외부 품질 벤치마크나 Fable/Opus A/B 수치는 확인하지 못했습니다.

fablize와 비교:

- fablize는 verified-only 원칙 때문에 normal mode hard block을 줄이고 deep-only gate로 보수화했습니다.
- fable-harness는 “항상 규율 바닥을 깔기”에 가깝고 adversarial subagents까지 포함합니다.
- 근거 실험은 fablize보다 약하지만, 코드 구조는 간단합니다.

Fable-lite에 이식 가능한 요소:

- transcript 기반 Stop gate.
- test command regex의 다중 생태계 인식.
- second stop pass-through.
- adversarial review를 “큰 결론에만” 요구하는 비용 절감 규칙.

이식 불가능/약한 요소:

- 테스트가 실행됐는지만 감지하지, 해당 테스트가 변경 파일/행동을 실제로 커버했는지는 깊게 증명하지 못합니다.
- adversarial review가 실제로 실행됐는지는 hook이 강제하지 않습니다.

### 3. SihyeonJeon/why-was-fable-banned

Repo: https://github.com/SihyeonJeon/why-was-fable-banned  
핵심 파일:

- README: https://github.com/SihyeonJeon/why-was-fable-banned/blob/main/README.md
- gate engine: https://github.com/SihyeonJeon/why-was-fable-banned/blob/main/gates/wfb_gate.py
- PreToolUse hook: https://github.com/SihyeonJeon/why-was-fable-banned/blob/main/adapters/hooks/pre_tool_use.py
- benchmark doc: https://github.com/SihyeonJeon/why-was-fable-banned/blob/main/bench/BENCHMARK.md
- tests: https://github.com/SihyeonJeon/why-was-fable-banned/blob/main/tests/test_wfb_gate.py

접근법:

- 이름은 장난스럽지만 구현은 가장 강한 hard gate입니다.
- `.wfb/spec.json`에 restated goal, non-goals, must_read, rejected alternatives, risks, acceptance criteria 등이 들어가야 합니다.
- `adapters/hooks/pre_tool_use.py`는 Edit/Write/apply_patch 계열을 가로채고, active task가 있으면 spec gate 통과 전 실제 구현 파일 edit를 차단합니다.
- `.wfb/spec.json` 작성은 허용하되, spec과 구현 파일을 같은 tool call에서 같이 수정하는 것은 차단합니다.
- gate state 자체(`.wfb/GRADE`, `ACTIVE`, `edits.txt`, `STATE`, sessions)는 모델이 수정하지 못하게 막습니다.
- done gate는 acceptance evidence를 요구하고, fake markers(`assumed`, `would pass`, `pending`, `todo` 등)를 evidence로 인정하지 않습니다.

검증:

- README는 자체 벤치로 `Adversarial / edge gate tests 35/35 pass`, SWE-bench Verified 22/28→23/28, SWE-bench Pro 7/10→8/10, combined 29/38→31/38, 토큰 2~3배를 보고합니다.
- `bench/BENCHMARK.md`는 toy hidden grader에서는 naked/gated 모두 10/10이라 품질 lift가 없었다고 정직하게 기록합니다.
- 따라서 수치는 **저장소 자체 보고**이며, 제가 독립 재현한 값은 아닙니다.

fablize와 비교:

- fablize는 “검증된 절차만 주입하고, deep task에서 검증 없는 완료를 막는다”는 보수형입니다.
- WFB는 “spec 없이는 edit 자체를 못 한다”는 강제형입니다.
- Fable-lite 프로젝트에서 hard gate를 채택하면 품질/감사성은 올라가지만 friction과 token 비용이 커질 가능성이 높습니다.

Fable-lite에 이식 가능한 요소:

- `.wfb/spec.json` 같은 machine-readable task contract.
- PreToolUse edit-before-spec 차단.
- forbidden paths와 acceptance evidence.
- LIGHT/STANDARD/HEAVY 자동 grade.
- Codex headless용 throwaway worktree accept 패턴.

이식 불가능/약한 요소:

- spec의 의미적 품질은 deterministic gate만으로 보장되지 않습니다. repo도 “trivial command passes” 한계를 테스트에 명시합니다.
- hard gate는 간단한 작업에는 과도합니다.

### 4. chrisryugj/fable-ish

Repo: https://github.com/chrisryugj/fable-ish  
핵심 파일:

- README: https://github.com/chrisryugj/fable-ish/blob/main/README.md
- plugin manifest: https://github.com/chrisryugj/fable-ish/blob/main/.claude-plugin/plugin.json
- hooks: https://github.com/chrisryugj/fable-ish/blob/main/hooks/hooks.json
- Stop hook: https://github.com/chrisryugj/fable-ish/blob/main/hooks/stop_gate.py
- Stop decision helper: https://github.com/chrisryugj/fable-ish/blob/main/scripts/verify_state.py

접근법:

- README는 “목표 설정 → 근거 수집 → 작업 단위 정의 → 탈출 기준 설정 → 구현 → 검증 → 반례 탐색 → 기준 재조정”을 Fable loop로 정의합니다.
- UserPromptSubmit에서 task를 `quick` / `normal` / `deep` / `blocked`로 분류합니다.
- PreToolUse/PermissionRequest는 위험 명령과 secret 파일 조작을 차단합니다.
- PostToolUse는 변경 파일, 검증 명령, 검증 결과, coverage 관계를 JSON ledger에 기록합니다.
- Stop hook은 파일 변경 후 검증이 없거나 “하겠습니다” 식으로 말만 하고 tool call 없이 끝내려는 경우를 차단합니다.
- `scripts/verify_state.py`는 `normal` task의 changed+unverified도 차단하고, `deep` task는 검증 증거가 없으면 더 엄격히 차단합니다. 최대 차단은 2회입니다.

검증:

- README의 개발 검증 명령은 `python3 tests/test_hooks.py`와 py_compile/json tool 수준입니다.
- 공개된 Fable/Opus A/B 품질 벤치마크는 확인하지 못했습니다.

fablize와 비교:

- fablize와 가장 비슷한 “completion gate + ledger” 계열입니다.
- fablize가 normal mode hard block을 줄인 것과 달리 fable-ish는 normal changed+unverified도 block합니다.
- fable-ish는 secret/destructive command guard까지 포함해 안전 guardrail 성격이 더 강합니다.

Fable-lite에 이식 가능한 요소:

- task mode classification.
- PostToolUse evidence ledger.
- “말만 하고 끝내기” 탐지.
- 위험 명령은 hook에서 1차 차단, hard security는 native permissions로 보강하라는 설계 노트.

이식 불가능/약한 요소:

- task classification은 regex/heuristic입니다. 모드 오분류 가능성이 큽니다.
- hook은 보안 경계가 아니며 shell 우회가 가능합니다. repo도 이를 한계로 명시합니다.

### 5. HalalifyMusic/fable-mode

Repo: https://github.com/HalalifyMusic/fable-mode  
핵심 파일:

- README: https://github.com/HalalifyMusic/fable-mode
- trigger hook: https://github.com/HalalifyMusic/fable-mode/blob/main/hooks/fable-trigger.py
- test hook: https://github.com/HalalifyMusic/fable-mode/blob/main/hooks/test-after-edit.py
- tests: https://github.com/HalalifyMusic/fable-mode/tree/main/tests
- playbook: https://github.com/HalalifyMusic/fable-mode/blob/main/FABLE_PLAYBOOK.md

접근법:

- README가 “Run Claude Fable 5 on Opus 4.8”을 직접 표방합니다.
- 구성은 leaked Fable system prompt, FABLE_PLAYBOOK, UserPromptSubmit trigger, PostToolUse test-after-edit, grounding skill/agent입니다.
- `hooks/fable-trigger.py`는 `use fable`, `fable mode`, `load fable` 문구 또는 `xhigh/max/ultracode` effort일 때 playbook을 context로 주입합니다.
- `hooks/test-after-edit.py`는 Edit/Write/MultiEdit 후 프로젝트 루트에서 테스트 명령을 찾아 실행하고 pass/fail을 additionalContext로 돌려줍니다.
- 테스트 hook은 non-blocking입니다. 실패해도 edit 자체를 되돌리거나 Stop을 차단하지 않습니다.

검증:

- repo에 `tests/test_fable_trigger.py`, `tests/test_install.py`, `tests/test_test_after_edit.py`가 있고 CI가 있습니다.
- README는 before/after 이미지를 제시하지만, 공개 재현 가능한 품질 벤치마크로 보기는 어렵습니다.

중요 리스크:

- README가 `fable-system.md`를 “Anthropic IP”라고 직접 명시합니다. Fable-lite 프로젝트에서 그대로 재사용하면 IP/약관/운영 리스크가 큽니다.
- test-after-edit hook은 사용자 머신에서 테스트 명령을 자동 실행합니다. 환경에 따라 비용/시간/부작용 리스크가 있습니다.

fablize와 비교:

- fablize는 prompt/style mimicry를 검증되지 않은 아이디어로 보고 제외했습니다.
- HalalifyMusic/fable-mode는 prompt/style mimicry까지 적극 사용합니다.
- 따라서 “비슷해 보이게 만들기”에는 강하지만, 안전하고 방어 가능한 Fable-lite 설계 기준으로는 그대로 채택하기 어렵습니다.

Fable-lite에 이식 가능한 요소:

- effort/trigger 조건부 playbook injection.
- test-after-edit의 project test autodiscovery.
- grounding verifier agent 컨셉.

이식 불가능/위험 요소:

- leaked prompt bundle.
- non-blocking test hook만으로는 completion discipline이 약합니다.

### 6. Rylaa/fable5-orchestrator

Repo: https://github.com/Rylaa/fable5-orchestrator  
핵심 파일:

- README: https://github.com/Rylaa/fable5-orchestrator
- spawn guard: https://github.com/Rylaa/fable5-orchestrator/blob/main/scripts/ledger_guard_spawn.py
- stop guard: https://github.com/Rylaa/fable5-orchestrator/blob/main/scripts/ledger_guard_stop.py
- tests: https://github.com/Rylaa/fable5-orchestrator/tree/main/tests

접근법:

- 목적은 “하위 모델을 Fable처럼”이라기보다, Fable/Opus별로 orchestration profile을 달리하는 것입니다.
- Fable profile은 token-frugal delegation과 ledger-first를 강하게 요구합니다.
- Opus profile은 latency-lean, inline-first, proportional ledger 전략입니다.
- `ledger_guard_spawn.py`는 Agent/Task/Workflow prompt가 길고 `.workflow/LEDGER.md`가 없으면 delegation을 차단합니다. threshold는 Fable 1500자, Opus 4000자 기본입니다.
- `ledger_guard_stop.py`는 `.workflow/LEDGER.md`에 `- [ ]` open item이 남아 있으면 Stop을 1회 차단합니다.

검증:

- pytest로 spawn guard, stop guard, cache cleanup, profile injection을 테스트합니다.
- 품질 벤치마크는 확인하지 못했습니다.

fablize와 비교:

- fablize는 task-specific verified discipline pack입니다.
- Rylaa는 multi-agent orchestration의 detail loss를 막는 ledger guard입니다.
- Fable-lite에서 장기 과제/멀티패스 작업의 상태 파일 설계에 참고할 가치가 있습니다.

Fable-lite에 이식 가능한 요소:

- Requirements Ledger checkbox format.
- delegation 전 ledger 존재 차단.
- Stop 시 open ledger item 차단.
- model-aware threshold/proportional ceremony.

이식 불가능/약한 요소:

- ledger fidelity는 검사하지 못합니다. repo도 “shallow ledger passes”를 한계로 명시합니다.
- Fable 행동능력 전체 모사가 아니라 orchestration discipline입니다.

### 7. dilitS/op-fable

Repo: https://github.com/dilitS/op-fable  
핵심 파일:

- README: https://github.com/dilitS/op-fable
- skill: https://github.com/dilitS/op-fable/blob/main/SKILL.md

접근법:

- `README.md`와 `SKILL.md`만 있는 정적 규칙 팩입니다.
- multi-pass reasoning, stage maps, scope discipline, pre-read, iterative construction, TDD, failable checks, self-critique, two-strike error recovery, parallel delegation, context efficiency 등을 적습니다.

검증:

- hook, test, state file, benchmark가 없습니다.

fablize와 비교:

- fablize가 “관찰된 state로 완료를 차단”한다면, op-fable은 “모델에게 좋은 습관을 읽게 하는” 수준입니다.
- 간단히 가져다 쓰기에는 쉽지만, 하위 모델에서 가장 먼저 무시될 가능성이 있는 계층입니다.

Fable-lite에 이식 가능한 요소:

- 문장화된 운영 규칙과 failure mode 목록.
- “두 번 실패하면 reassess” 같은 error recovery 규칙.

이식 불가능/약한 요소:

- 강제력 0.
- 실험 근거 0.

## fablize와 접근법 비교

기준선 `fivetaku/fablize`: https://github.com/fivetaku/fablize

fablize의 핵심 특성:

- README가 transferable/non-transferable을 명확히 분리합니다.
- shipped 항목은 verification grounding, multi-story evidence gate, investigation protocol, early-stop hook, per-task router입니다.
- non-transferable로 out-of-spec defect discovery, open-ended creative detail, self-driven propagation depth를 명시합니다.
- `hooks/gate_stop.py`와 `scripts/gate/verify_state.py`는 observed ledger 기반으로 Stop을 판단합니다.
- 최신 코드 기준 gate는 deep-only입니다. normal mode hard block은 측정상 noise가 커서 줄인 것으로 설명됩니다.

비교 표:

| 항목 | fablize | fable-ish | fable-harness | WFB | mrtooher/fable-mode | Halalify fable-mode | Rylaa orchestrator |
|---|---|---|---|---|---|---|---|
| 상시 규칙 | 선택적 always-on/router | skill+hooks | protocol+nudge | contract injection | skill | playbook injection | SessionStart profile |
| 조건부 pack | 강함 | task mode별 | 약함 | grade별 LIGHT/STANDARD/HEAVY | task 복잡도별 | trigger/effort별 | model profile별 |
| Hook gate | Stop, PostTool, Prompt | Prompt/Pre/Post/Stop | Stop 중심 | PreToolUse hard gate | 없음 | Prompt/PostTool, non-blocking | PreToolUse/Stop |
| 상태 파일 | ledger/shadow | JSON ledger | transcript 기반 | `.wfb/spec.json`, ACTIVE, STATE, edits | 없음 | temp marker | `.workflow/LEDGER.md`, temp cache |
| 멀티패스 | packs/goals | skill loop | adversarial review | spec/acceptance loop | stage map loop | playbook | orchestration profile |
| 실험 근거 | 자체 A/B와 세션 관찰 | 부족 | unit tests 중심 | 자체 SWE-bench/bench 문서 | 예제 중심 | demo/CI 중심 | hook tests 중심 |
| 철학 | 검증된 절차만 보수적으로 ship | 검증 없는 완료 차단 | disciplined floor | edit-before-spec hard enforcement | prompt discipline | style+prompt injection | detail loss 방지 |

## Fable-lite에 대한 가설 분류

### 하위 모델에서 프롬프트/하네스 규율로 재현 가능

- task를 quick/normal/deep/heavy로 분류하고 ceremony budget을 조절.
- stage map과 explicit done criteria.
- failable check가 없는 stage를 unverified로 표시.
- PostToolUse ledger로 변경 파일과 검증 명령을 기록.
- Stop hook으로 changed+unverified completion을 차단.
- PreToolUse로 spec 없는 edit, forbidden path, spec+implementation 동시 수정 차단.
- Requirements Ledger로 explicit/implicit 요구사항을 checkbox로 유지.
- long-running task에서 work log와 continuation re-read를 강제.
- “두 번 실패하면 같은 접근 반복 금지” 같은 retry spiral 차단.
- model-aware delegation: 하위 모델에는 verification/check step을 더 강하게, 상위 모델에는 ceremony를 줄이는 방식.

### 모델 가중치 수준이라 재현 불가 또는 약하게만 가능

- out-of-spec defect를 스스로 발견하는 능력.
- 애매한 요구에서 “한 단계 더 깊은 함의”를 자발적으로 파고드는 능력.
- 장기 문맥에서 무엇이 중요한지 압축/유지하는 판단력 자체.
- spec/ledger의 의미적 fidelity. 얕은 ledger/spec도 형식상 통과할 수 있습니다.
- 시각/디자인 taste, open-ended creative detail.
- high-risk architecture에서 tradeoff를 정확히 판정하는 능력.
- test command가 실제 변경 행위를 커버하는지 의미적으로 판단하는 능력.

## Fable-lite 설계에 바로 참고할 우선순위

1. **WFB의 hard PreToolUse gate를 선택적으로 채택**  
   모든 작업에 적용하면 과도하므로, auth/migration/payment/security/DB 같은 high-risk에서만 spec-before-edit를 켜는 것이 현실적입니다.

2. **fablize/fable-ish식 observed ledger + Stop gate를 기본값으로 채택**  
   일반 구현 작업에서는 edit는 허용하되, 검증 없는 completion을 막는 쪽이 friction 대비 효율이 좋습니다.

3. **mrtooher/op-fable의 stage map 문구를 skill layer로 채택**  
   hook만으로는 모델이 어떤 check를 만들어야 하는지 알기 어렵습니다. prompt layer는 “무엇을 해야 하는지”, hook layer는 “빼먹으면 못 끝내게” 역할을 나누는 것이 좋습니다.

4. **Rylaa의 Requirements Ledger를 장기/멀티에이전트 작업에만 적용**  
   작은 작업에는 inline checklist, 큰 작업에는 파일 ledger를 쓰는 proportional ceremony가 적절합니다.

5. **Halalify식 leaked prompt 접근은 배제**  
   Fable-lite가 공개/재사용 가능한 프로젝트라면 Anthropic IP로 보이는 system prompt를 포함하지 않는 편이 안전합니다. 대신 공개적으로 관찰 가능한 절차만 재작성해야 합니다.

## 최종 판단

새로 발굴한 후보 중 실제 Fable-lite 구현 참고 우선순위는 다음입니다.

1. `SihyeonJeon/why-was-fable-banned`: hard spec/evidence gate 설계 참고.
2. `chrisryugj/fable-ish`: Claude Code plugin + ledger + Stop gate 구조 참고.
3. `Miguok/fable-harness`: 간단한 Stop verification gate와 adversarial review 참고.
4. `mrtooher/fable-mode`: skill/rules layer 문구와 model-tier ceremony calibration 참고.
5. `Rylaa/fable5-orchestrator`: Requirements Ledger와 model-aware orchestration 참고.

`HalalifyMusic/fable-mode`는 직접 Fable mimic을 표방하지만, leaked prompt/IP 리스크와 non-blocking hook 한계 때문에 핵심 설계로 삼기보다는 “무엇을 피해야 하는가”의 사례로 보는 편이 안전합니다.

`dilitS/op-fable`은 가장 가벼운 규칙 팩이지만, Fable-lite의 핵심 차별점이 될 기계적 enforcement는 없습니다.
