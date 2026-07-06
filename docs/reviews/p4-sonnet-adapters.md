# P4 교차 리뷰 — core/ · adapters/claude_code/ · tests/

**리뷰어**: Claude Code (Sonnet 5, 우하 pane) · **관점**: Claude Code 실사용자
**범위**: `core/`, `adapters/claude_code/`, `tests/`, `goals/`, `.claude-plugin/`, `eval/`. `packs/`는 리뷰어 본인의 P3 산출물이라 검토 대상에서 제외.
**방법**: 코드 직독 + `pytest` 실제 실행 + 실제 Claude Code 훅 페이로드 형태(`tool_input`/`tool_response` 중첩)를 재현한 수동 실행 + `grep` 대조. 모든 주장의 원자료는 맨 아래 "실행 증거" 참고.

**총평**: `core/`의 판정 로직 자체(순수 함수 단위)는 견고하고 단위 테스트도 대응이 잘 되어 있다. 하지만 `adapters/claude_code/`가 Claude Code의 실제 훅 페이로드 스키마와 어긋나는 지점이 여러 곳 있고, 이 어긋남이 R1·N3·S4·N1 네 가지 게이트 중 셋 이상을 실사용에서 무력화한다. 문제는 로직이 아니라 "어댑터가 core에 무엇을 넘기는가" 지점에 집중돼 있다.

## 요약

| # | 심각도 | 한 줄 요약 | 위치 |
|---|--------|-----------|------|
| C1 | Critical | PreToolUse/PostToolUse가 `tool_input`/`tool_response` 중첩을 안 읽어 R1·N3·ledger 변경기록이 실사용에서 전부 무력화 | `pre_tool_use.py:21-22`, `post_tool_use.py:22-25,52-53,81,89-90` |
| C2 | Critical | (C1을 고친 뒤에 노출) `scope_guard._under()`가 절대경로 vs 상대경로를 순수 문자열 비교 — 정상 대상 파일까지 오탐 전환 | `scope_guard.py:17-20` |
| C3 | Critical | N1(`compliance.py`)을 호출하는 훅이 전혀 없음 — AC3가 파이프라인에 존재하지 않음 | grep 0건 (하단 참고) |
| H1 | High | "왜 안 돼"류 "안 되다" 활용형이 `DEBUG_PATTERNS`에서 대부분 누락 | `classify.py:10-24` |
| H2 | High | `MULTI_STORY_PATTERNS`의 "도"/"또"가 조사 수준이라 무관한 문장도 다중 스토리로 오분류 | `classify.py:40-53` |
| H3 | High | PreToolUse matcher에 Bash/PowerShell이 없어 쉘발 대량삭제·마이그레이션이 R1을 완전히 우회 | `hooks.json:15` |
| H4 | High | AC9(fail-open)을 실제로 유발해 검증하는 테스트가 없음 | `tests/` 전체 |
| H5 | High | AC2 goals 넛지 메시지가 어댑터 출력 레벨에서 미검증 | `user_prompt_submit.py:16-34` |
| M1 | Medium | `hooks.json`에 `timeout` 미설정(기본값 의존) | `hooks.json` 전체 |
| M2 | Medium | 고위험 키워드 목록이 `classify.py`/`contract.py`에 중복 유지되며 이미 비대칭("삭제" vs "대량삭제", 무수식 "delete") | `classify.py:60-61`, `contract.py:14-31` |
| M3 | Medium | `scope_guard`가 대소문자 무시 비교를 안 함(Windows는 대소문자 무관 파일시스템) | `scope_guard.py:17-20` |
| M4 | Medium | 정당한 근본원인 수정(사이드파일)이 항상 경고 대상 — S3 지시와 N3 전제의 설계 긴장 | `scope_guard.py:29-41` |
| L1 | 정보 | AC6 충족 확인(core 6개 모듈 전부 stdlib + 내부 상대 import만) | `core/*.py` |
| L2 | 정보 | AC7/AC11(E1/E2)은 계획대로 미구현 — 결함 아님 | `eval/probes-design.md` |
| L3 | 정보 | AC12 형식상 green(10/10) 확인. 단 그린이 C1~C3의 실사용 결함까지 보증하진 않음 | 실행 증거 참고 |

---

## 1. hooks.json ↔ Claude Code 훅 스키마 호환성

### 잘 된 부분
이벤트명(`UserPromptSubmit`/`PreToolUse`/`PostToolUse`/`Stop`), `matcher`+`hooks` 배열 중첩 구조, `"type": "command"` 형식은 실제 플러그인 훅 스키마와 일치한다. `${CLAUDE_PLUGIN_ROOT}`는 Claude Code가 셸에 넘기기 전에 자체적으로 치환하는 토큰이므로(셸 문법이 아니라 정적 문자열 치환) cmd.exe/PowerShell/bash 어느 쪽이 명령을 실행하든 Windows에서도 문제없이 동작한다 — 경로 앞뒤의 `"..."`도 공백 포함 경로 대응으로 적절하다. `PostToolUse` matcher에 `"PowerShell"`을 포함시킨 것도 이 환경(Windows, PowerShell 병용)에 맞는 정확한 선택이다.

### C1. (Critical) `tool_input`/`tool_response` 중첩을 안 읽음 — R1·N3·ledger 전부 무력화

`pre_tool_use.py:21-22`, `post_tool_use.py:22-25,52-53,81,89-90`가 `payload.get("file_path")`/`"file_paths"`/`"prompt"`/`"command"`/`"output"`/`"success"`/`"exit_code"`를 **최상위 키**로 읽는다. 그러나 실제 Claude Code의 PreToolUse/PostToolUse 페이로드에서 이 값들은 전부 `tool_input`(Edit/Write/MultiEdit → `file_path`, Bash/PowerShell → `command`)과 `tool_response`(실행 결과) 아래에 중첩된다. 최상위에 남는 건 `tool_name`(`pre_tool_use.py:20`에서는 올바르게 최상위로 읽고 있음), `cwd`, `session_id`, `transcript_path` 정도다. `prompt`는 애초에 `UserPromptSubmit` 이벤트 전용 필드라 PreToolUse/PostToolUse에는 존재하지 않는다.

**직접 실행으로 확인**: 실제 스키마를 재현한 페이로드(`tool_input.file_path = "migrations/001_init.sql"`, 내용에 `DROP TABLE users;`)로 `pre_tool_use.py`를 호출하면:

```
$ python adapters/claude_code/pre_tool_use.py < realistic_pretooluse.json
{}
```

R1이 완전히 통과된다(차단이면 `{"decision":"block", ...}`가 나와야 한다). 같은 방식으로 `settings.py`를 수정한 realistic PostToolUse 페이로드를 넣으면:

```
$ python adapters/claude_code/post_tool_use.py < realistic_posttooluse.json
{"systemMessage": "fable-lite ledger: recorded 0 change(s)."}
```

실제로 파일을 고쳤는데 "0 change(s)"다. `.fable-lite/ledger.json`도 생성되지 않는다(실행 증거 참고). `changed_files_seen`이 항상 비면 `verify_state.py:56`의 `not changed` 조건이 항상 참이 되어 **S4(Stop 게이트)의 changed+unverified 차단 경로도 함께 죽는다** — 필드 매핑 버그 하나가 R1(AC8) + N3(AC4) + S4의 ledger 경로(AC1/AC9) 세 갈래를 동시에 무력화한다.

기존 테스트(`test_high_risk_contract_blocks_edit_until_valid_contract_exists`, `test_claude_code_adapters_are_thin_fail_open_wrappers`)가 전부 통과하는 이유는 테스트 자신도 `file_paths`/`prompt`를 최상위 키로 손수 구성해서 넘기기 때문이다 — 같은 잘못된 가정을 테스트와 구현이 공유하고 있어 그린이 실제 동작을 보증하지 못하는 전형적인 사례다.

**제안**: `common.py`에 `tool_input(payload)` / `tool_response(payload)` 헬퍼를 추가해 `payload.get("tool_input", {})` / `payload.get("tool_response", {})`에서 `file_path`/`command`/실행결과를 꺼내도록 세 어댑터를 수정. 정확한 키 이름은 실제 훅을 한 번 붙여 stdin을 파일로 덤프해 재확인 후 진행 권장(Bash/PowerShell `tool_response`의 정확한 stdout/exit_code 키는 이 리뷰에서 단정하지 않음).

### H3. (High) PreToolUse matcher가 Bash/PowerShell을 안 잡음

`hooks.json:15`의 PreToolUse matcher는 `"Edit|Write|MultiEdit|NotebookEdit"`뿐이다. R1의 `HIGH_RISK_TERMS`(`contract.py:14-31`)에는 `"delete"`, `"truncate"`, `"db"`, `"sql"`, `"대량삭제"`처럼 파일 편집보다 **쉘 명령으로 실행될 가능성이 훨씬 높은** 용어가 다수 포함돼 있다(`rm -rf`, `Remove-Item -Recurse`, `psql -c "DROP TABLE"`, `alembic upgrade` 등). 지금 구조로는 이런 명령이 전부 R1을 원천적으로 우회한다 — Edit 계열 도구만 지켜보고 있기 때문이다.
**제안**: PostToolUse처럼 PreToolUse matcher에도 `Bash|PowerShell`을 추가하고, `contract.py`의 `_high_risk`/`evaluate_pretool_contract`가 `tool_name in {Bash, PowerShell}`일 때 `file_paths` 대신 `command` 문자열을 검사하도록 분기 추가.

### M1. (Medium) `timeout` 미설정

4개 훅 명령 어디에도 `"timeout"` 키가 없다(`hooks.json` 전체). Claude Code 기본값에 의존하게 되는데, 이 프로젝트 자체의 설계 원칙 1번("fail-open: 게이트 자체 오류는 세션을 절대 죽이지 않는다")과의 정합성을 생각하면, 로컬 stdlib 파일 I/O만 하는 훅이 비정상적으로 오래 걸리는 경우(예: `.fable-lite/ledger.json`이 백신 스캔·다른 프로세스에 잠긴 경우)에도 세션이 그만큼 멈춘다. 실패가 아니라 "응답 없음"은 현재 fail-open 처리 범위 밖이다.
**제안**: 4개 훅 모두에 짧은 명시적 `timeout`(5~10초 선)을 추가.

---

## 2. classify.py 한국어 패턴 재현율

과제가 예시로 든 세 문장과 추가 케이스를 `classify_prompt()`에 직접 통과시킨 결과(실행 증거의 원자료 그대로):

| 입력 | mode | packs | risk | 판정 |
|------|------|-------|------|------|
| "왜 안 돼" | quick | `[]` | `[]` | ❌ 미탐지 |
| "이거 고쳐" | deep | `[investigation]` | `[]` | ✅ |
| "화면 만들어" | normal | `[verification-grounding]` | `[]` | ✅ |
| "안되는데요" | quick | `[]` | `[]` | ❌ 미탐지 |
| "로그인이 왜 안되지" | quick | `[]` | `[]` | ❌ 미탐지 |
| "버튼 눌러도 반응이 없어요" | normal | `[completion]` | `[]` | ⚠️ 오탐(단일 버그 리포트인데 다중 스토리 취급) |
| "이 죽은 코드 삭제해줘" | deep | `[]` | `[삭제]` | ⚠️ 과잉(사소한 삭제도 고위험 취급) |

### H1. "안 되다" 활용형 대량 누락

`DEBUG_PATTERNS`(`classify.py:10-24`)는 "안돼"·"안됨" 두 표면형만 갖고 있다. 한국어 "안 되다"는 되+어→돼(축약), 되+었→됐, 되+지→되지, 되+는→되는 등으로 활용마다 표면형이 바뀐다:
- 공백 문제: "안 돼"(띄어쓰기)는 "안돼"(붙여쓰기)와 부분 문자열이 달라 매칭 실패 — "왜 안 돼"가 정확히 이 경우다.
- 어간 활용 문제: "안되는데요"·"안되나요"·"안됐어요"·"안될까요"·"안되지" 전부 "안돼"/"안됨" 어느 쪽과도 부분 문자열이 안 맞는다.

반대로 `ARTIFACT_PATTERNS`는 "페이지"·"화면"·"차트"처럼 활용이 없는 명사 위주라 이 문제가 없다 — 두 리스트의 견고성이 설계상 비대칭이다.
**제안**: 어간 `"안되"`를 추가하고(공백 없는 활용형 대부분을 커버) `"안 돼"`(공백 포함)를 별도 패턴으로 추가. 근본적으로는 `안\s*(되|돼|됨)` 형태의 정규식으로 전환하는 편이 확장에 유리하다.

### H2. `MULTI_STORY_PATTERNS`의 "도"/"또"가 지나치게 일반적

`MULTI_STORY_PATTERNS`(`classify.py:40-53`)의 `"도"`는 한국어에서 가장 흔한 보조사 중 하나라("~해도", "~눌러도", "~많아도") 다중 스토리와 무관한 문장에도 광범위하게 매칭된다. 위 표의 "버튼 눌러도 반응이 없어요"가 실사례다 — 단일 버그 리포트인데 "눌러도"의 "도" 때문에 `packs=["completion"]`으로 분류됐다("또"도 "그래도"·"안 그래도" 등에 흔히 등장해 동일한 문제를 갖는다). AC2(N2)가 과잉 트리거되면 사소한 단일 질문마다 "2개 이상 스토리인가요" 확인이 반복돼 게이트 신뢰도가 떨어진다.
**제안**: 1글자 조사 단독 패턴(`"도"`, `"또"`)을 제거하거나, 최소한의 문맥 결합(`"그리고 또"`, `"~하고 ~도"` 등)으로 좁힐 것.

### M2. 고위험 키워드 목록 중복·비대칭

`classify.py:54-68`의 `HIGH_RISK_PATTERNS`와 `contract.py:14-31`의 `HIGH_RISK_TERMS`는 사실상 같은 목적의 리스트를 두 파일에 독립적으로 유지하고 있고, 이미 서로 달라져 있다: `classify.py`는 `"대량삭제"`와 바닥 `"삭제"`를 함께 갖고 있어(위 표의 "이 죽은 코드 삭제해줘" → `risk=["삭제"]`) 사소한 삭제도 고위험으로 잡히는 반면, `contract.py`는 한국어 쪽엔 `"대량삭제"`만(올바른 스코프) 두고 영어 쪽엔 수식어 없는 바닥 `"delete"`를 그대로 둬서 같은 비대칭이 언어를 바꿔 재발한다. 스펙 R1의 의도("대량삭제" 신호에만 한정)에 비해 두 리스트 다 부분적으로 과잉이다.
**제안**: 두 리스트를 공유 모듈(예: `core/risk_terms.py`)로 합치고, `"삭제"`/`"delete"` 단독 항목은 제거하거나 `"대량"`/`"mass"`/`"전체"`류 수식어와 결합한 패턴으로 한정.

---

## 3. scope_guard.py 오탐(및 오탐보다 심각한 미탐) 시나리오

> **정확성 메모**: `scope_guard.py`는 PostToolUse(사후) 훅이고 반환값도 `"warn"`/`"allow"`뿐이라, 문자 그대로 "차단"은 할 수 없다 — systemMessage/additionalContext로 경고만 주입한다(코드·스펙 문서 둘 다 "경고 주입"이라 표현). 아래는 정확히는 "허위 경고가 주입되는 시나리오"이며, 비용은 작업 중단이 아니라 컨텍스트 노이즈·게이트 신뢰도 저하 쪽이다.

가장 심각한 오탐/미탐 건(C2)은 위 Critical 요약에 이미 기술했다: `_under()`(`scope_guard.py:17-20`)가 베이스네임 추출이나 절대/상대 정규화 없이 순수 문자열 접두사 비교만 해서, C1이 고쳐진 뒤 실제 Claude Code의 절대경로 `changed_files`가 들어오면 **사용자가 정확히 이름을 지목한 파일조차 범위 이탈로 오탐**된다. 바로 아래 `_prompt_mentions()`(`scope_guard.py:23-26`, 요청 파일명이 없을 때의 폴백 경로)는 `PurePath(path).name`으로 베이스네임을 뽑아 비교해 이 문제가 없다 — 더 자주 타는 주경로가 덜 자주 타는 폴백 경로보다 약하게 구현된 역설이다. 기존 테스트(`test_scope_guard_warns_when_changed_file_is_outside_requested_scope`)는 `changed_files`와 `requested_paths` 양쪽 다 상대경로인 이상화된 케이스만 써서 이 불일치를 잡아내지 못한다.

### M3. 대소문자 무시 비교 누락 (Windows)

`_under()`는 `.lower()`를 호출하지 않는다(`_prompt_mentions()`는 호출함). Windows는 파일시스템이 대소문자를 구분하지 않으므로, 실제 경로의 케이스가 사용자가 프롬프트에 타이핑한 케이스와 다르면(`App.py` vs `app.py`) `_under()` 쪽만 동일 파일을 다른 파일로 오판할 수 있다.
**제안**: `_under()`에도 동일하게 `.lower()` 적용.

### M4. 정당한 근본원인 수정과 N3 전제의 설계 긴장

`evaluate_scope`(`scope_guard.py:29-41`)는 요청에서 뽑힌 `requested_paths` 아래 있는 파일만 "정상"으로 본다. 그런데 investigation 팩(S3)은 정확히 "인과사슬을 추적해 근본 원인을 고치라"고 지시한다 — 사용자가 `auth.py`만 지목했어도 근본 원인이 `session.py`에 있으면 그쪽을 고치는 게 옳은 행동이다. 현재 scope_guard는 프롬프트에 파일명이 있든 없든(`_under` 경로든 `_prompt_mentions` 경로든) 이런 정당한 사이드파일 수정을 전부 경고 대상으로 잡는다. "경고"일 뿐 작업을 막지는 않지만, 반복되면 경고를 무시하는 습관을 만들 위험이 있다.
**제안**: 즉각 수정보다는 systemMessage에 "이 경고는 참고용이며 근본원인 수정이면 무시 가능"이라는 안내를 덧붙이는 정도로 완화 가능. 정밀한 해법(동일 디렉터리/모듈 파일은 관용 등)은 v2 검토 사항으로 제안.

---

## 4. tests/ 10개 ↔ AC 12개 매트릭스

| AC | 내용(요약) | 단위 테스트 | 통합/어댑터 경로 검증 | 비고 |
|----|-----------|:---:|:---:|------|
| AC1 | 산출물 미관측 완료 시도 → Stop 차단(S1+S4) | 부분 | ✗ | `mode="deep"`만 테스트됨. 순수 아티팩트 요청은 `mode="normal"`이 되는데(`classify.py`), `verify_state.py:56`은 quick만 예외 처리해 normal도 걸려야 함 — 그 경로가 테스트되지 않음 |
| AC2 | 2+ 스토리 → goals 플랜/명시 확인(N2) | ✅ | ✗ | `needs_goals` 플래그와 `goals.py` CLI는 각각 테스트됨. `user_prompt_submit.py`가 실제로 넛지 메시지를 `additionalContext`에 넣는지는 미검증(H5) |
| AC3 | 가설 1개만 → 준수 게이트 경고/차단(S3+N1) | ✅(순수함수만) | ✗✗ | 호출하는 훅이 없음(C3). 통합 경로 자체가 존재하지 않아 테스트로 잡을 수 있는 상태가 아님 |
| AC4 | 범위 밖 수정 → PostToolUse 경고(N3) | ✅(이상화 케이스만) | ✗✗ | C1(필드 매핑)+C2(경로 비교)가 겹쳐, 현재는 사실상 항상 미탐(C1 탓) → C1만 고치면 항상 오탐(C2 탓)으로 뒤집힘 |
| AC5 | 한국어 라우팅("버그 고쳐줘"·"페이지 만들어줘") | ✅ | — | 스펙 예시 문구 자체는 직접 커버. 예시 밖 활용형 재현율은 H1/H2 참고 |
| AC6 | core 0 CC-import, 단위테스트 통과 | ✅ | — | 6개 core 모듈 전부 stdlib + 내부 상대 import(`.ledger`)만 사용 확인(직접 열람) |
| AC7 | golden 프로브 ≥12 (E1) | — | — | 계획대로 미구현(`eval/probes-design.md`만 존재, ARCHITECTURE.md상 좌하 agy 담당·추후 구현) — 결함 아님 |
| AC8 | high-risk 수정 spec 없으면 차단(R1) | ✅(이상화 케이스만) | ✗✗ | C1과 동일 원인으로 실사용 시 R1이 발동하지 않음(실행 증거로 직접 확인) |
| AC9 | 게이트 오류 시 fail-open | — | ✗ | try/except 래퍼는 4개 어댑터 전부에 있으나, 실패를 강제로 유발해 fail-open 경로를 실제로 타는지 검증하는 테스트가 없음(H4) |
| AC10 | 게이트 메시지 한국어 우선+영어 병기 | — | — | 코드상 관행으로는 준수(직접 열람 확인) — 자동 검증 테스트는 없음 |
| AC11 | 게이트별 독립 on/off 토글(E2) | — | — | 토글 메커니즘 자체가 `core`/`adapters` 어디에도 없음(grep 0건). AC7과 마찬가지로 계획된 이후 단계 — 다만 나중에 붙일 때 4개 어댑터를 전부 수정해야 하니 지금 스텁만 넣어두는 것도 고려할 만함 |
| AC12 | 전체 훅 단위 테스트 스위트 green | ✅(형식) | — | `pytest` 10 passed 확인. 다만 그린이 C1~C3가 가리키는 실사용 결함까지 보증하지는 않음 |

**해석**: 10개 테스트는 core 순수 로직(classify/compliance/ledger/scope_guard/contract/verify_state)의 단위 커버리지로는 준수하다. 그러나 **"어댑터가 실제 Claude Code 페이로드를 올바르게 파싱해 core에 넘기는가"를 검증하는 테스트가 하나도 없다** — `test_adapters.py`가 존재하긴 하지만 이상화된(실스키마가 아닌) 페이로드로만 어댑터를 호출한다. C1/C2/C3와 AC3/AC4/AC8 갭은 전부 이 한 가지 테스트 설계 공백에서 갈라져 나온 것이다.

---

## 실행 증거

**pytest (10 passed)**
```
collected 10 items
tests/test_adapters.py::test_claude_code_adapters_are_thin_fail_open_wrappers PASSED
tests/test_adapters.py::test_plugin_manifest_and_hooks_json_exist PASSED
tests/test_core_contracts.py::test_classify_prompt_routes_korean_debug_and_page_requests PASSED
tests/test_core_contracts.py::test_classify_prompt_requires_goals_for_multi_story_work PASSED
tests/test_core_contracts.py::test_investigation_compliance_requires_three_hypotheses_rejection_and_evidence PASSED
tests/test_core_contracts.py::test_ledger_records_only_under_project_fable_lite_directory PASSED
tests/test_core_contracts.py::test_stop_gate_blocks_changed_unverified_work_at_most_twice PASSED
tests/test_core_contracts.py::test_scope_guard_warns_when_changed_file_is_outside_requested_scope PASSED
tests/test_core_contracts.py::test_high_risk_contract_blocks_edit_until_valid_contract_exists PASSED
tests/test_goals_cli.py::test_goals_cli_creates_and_verifies_checkpoint PASSED
10 passed in 0.82s
```

**실제 Claude Code 페이로드 형태(`tool_input`/`tool_response` 중첩)로 재현한 수동 실행**

PreToolUse — Edit로 `migrations/001_init.sql`에 `DROP TABLE users;` 삽입 시도(R1이 반드시 차단해야 하는 교과서적 케이스):
```
$ python adapters/claude_code/pre_tool_use.py < realistic_pretooluse.json
{}
```
→ 차단되지 않음(정상이면 `{"decision":"block", ...}`).

PostToolUse — Edit로 `settings.py` 실제 수정 후:
```
$ python adapters/claude_code/post_tool_use.py < realistic_posttooluse.json
{"systemMessage": "fable-lite ledger: recorded 0 change(s)."}
```
→ 0건 기록. `.fable-lite/ledger.json` 생성 자체가 되지 않음(Glob으로 확인, 프로젝트 루트에 잔존 파일 없음).

**grep** (`transcript_path|check_investigation_compliance|tool_input|tool_response`, `adapters/` 전체 대상): `No matches found`.

**classify_prompt 직접 호출** (dimension 2 표의 원자료): `python -c "from core.classify import classify_prompt; ..."` 실행 결과가 표에 정리된 mode/packs/risk 그대로.

---

이 문서는 `core/adapters/tests/goals/eval`의 P3 산출물을 대상으로 하며, `packs/`(리뷰어 본인 산출물)는 검토 대상에서 제외했다.
