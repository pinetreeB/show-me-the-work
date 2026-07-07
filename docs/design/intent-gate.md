# 의도 게이트 (Intent Gate, "알" 게이트) — v1.1 설계 (동결)

> 2026-07-07. 배경: Fable의 "알잘딱" 중 "잘·딱"(검증·완주)은 v1.0.0이 강제하지만, "알"(모호한 지시에서 의도 파악)은 가중치 능력이라 이식 불가. 이 게이트는 "알"을 **능력이 아니라 절차로 대체**한다 — 모호하면 추측으로 일하지 말고, 초장에 1~3문답으로 의도를 확정시킨 뒤에만 수정을 허용.

## 동작 흐름

```
UserPromptSubmit: 모호성 판정(core/ambiguity) → 모호하면 intent-interview 팩 주입 + ledger에 intent_required=true
  ↓
모델: 작업 대신 확인 질문 1~3개 먼저 (1문1답) → 사용자 답변 →
      python -m fable_lite intent set --goal "..." --scope "..." [--assumed]  (.fable-lite/intent.json 생성)
  ↓
PreToolUse: intent_required인데 intent.json 없으면 Edit/Write/patch 차단 (상한 2회, fail-open)
  ↓
intent.json 존재 → 게이트 열림, 이후 scope_guard의 requested_paths 보강에도 intent.scope 활용
```

## 모호성 판정 (core/ambiguity.py) — ⚠️ 과탐 금지가 제1원칙

**모호 신호** (2개 이상 동시 충족 시에만 flag — N2 "하고" 과탐의 교훈):
- 대상 부재: 수정 동사(고쳐/바꿔/만들어/추가/해줘)가 있는데 requested_paths 추출 0 + 구체 명사 목적어 없음
- 대명사 지시: "이거/저거/그거/여기" 가 유일한 목적어
- 위임 표현: "알아서", "적당히", "어떻게 좀", "느낌대로", "니가 판단해서"
- 초단문: 15자 미만 + 수정 동사 포함

**절대 flag 금지**: quick 모드(질문·조회), 파일명/경로 명시, goals.json 이미 존재(플랜 승인됨), intent.json 이미 존재, 프롬프트에 "그냥 해"/"묻지 말고" 포함(명시적 스킵).

## intent.json 스키마 (.fable-lite/intent.json)

```json
{"goal": "확정된 목표 한 줄", "scope": ["대상 파일/영역"], "non_goals": ["안 하는 것"],
 "assumed": false, "confirmed_at_prompt": "원 프롬프트 발췌"}
```
- `assumed: true` = 사용자가 답 안 해서 모델이 가정 명시 후 진행한 경우 (허용하되 기록)

## CLI (fable_lite intent 서브커맨드)

- `intent set --root . --goal "..." [--scope "a/**,b.py"] [--non-goal "..."] [--assumed]` → intent.json 기록
- `intent show --root .` / `intent clear --root .` (새 과제 시작 시 UserPromptSubmit이 자동 clear — 프롬프트가 바뀌면 이전 의도는 무효)

## 팩 (packs/intent-interview.ko.md / .en.md)

- 원칙: 질문 최대 3개·1문1답·객관식 우선(사용자가 비개발자여도 답하기 쉽게)·이미 명확한 부분은 묻지 않음
- 마커 계약: 질문은 `확인질문 N:` 접두. 답 받으면 즉시 `intent set` 실행(말로 "확인했습니다" 금지 — CLI 실행이 곧 확정)
- 사용자가 "그냥 해"라고 하면: 가정을 `가정:` 접두로 선언 + `--assumed`로 기록 후 진행
- 메타발언 금지(E1 교훈)

## 게이트 (pre_tool_use)

- 조건: ledger.intent_required=true AND intent.json 부재 AND 도구가 Edit/Write/MultiEdit/NotebookEdit/apply_patch
- 동작: block + 한국어 사유("의도가 확정되지 않았습니다. 확인질문에 답을 받거나 fable_lite intent set으로 가정을 기록한 뒤 수정하세요")
- 상한: 2회 후 통과(기존 stop 카운터와 동일 철학, 별도 intent_blocks 카운터), fail-open
- Bash는 차단하지 않음(조사·재현은 의도 확정 전에도 허용 — 오히려 권장)

## 하지 않는 것

- LLM 호출로 모호성 판정(결정론 위반) ❌ / quick 질의 차단 ❌ / 3문 초과 인터뷰(피로) ❌
- 사용자 /deep-interview 스킬 의존 ❌ (방법론만 팩에 내장, fable-lite는 자립)

## 영역 배타 (병렬)

| 작업자 | 영역 |
|--------|------|
| Codex | core/ambiguity.py·ledger intent 필드·adapters 3종 배선·fable_lite intent CLI·tests |
| Sonnet | packs/intent-interview.ko/en.md + 완료 후 라이브 E2E(중첩 세션 실검증) |
| agy | 과탐 측정: 실전형 프롬프트 코퍼스 30개+로 ambiguity 오탐/미탐률 검증 |
