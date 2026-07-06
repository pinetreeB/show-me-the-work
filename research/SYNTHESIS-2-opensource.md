# 리서치 종합 2 — Fable 재현 오픈소스 생태계 전수 조사

> 2026-07-06 · 4-pane 병렬 발굴 (좌상 gh 검색+README / 우상 Codex GitHub 코드분석 / 좌하 agy HuggingFace / 우하 agy-Opus 큐레이션·파생 추적)
> 원본: `codex-github-attempts.md`(코드 수준 상세) · `agy-huggingface-attempts.md` · `opus-curation-derivatives.md`

## 1. 한 줄 결론

"Fable을 하위 모델로"는 이미 **하나의 오픈소스 장르**다(직접 시도 14개+ 발굴). 그러나 코드를 열어보면 전부 **절차의 외부 강제**로 귀결되며, 가중치 수준 능력(자발적 함의 추적·스펙 밖 결함 발견)을 재현한 공개 코드는 **존재하지 않는다**. 경쟁 지형에서 비어 있는 자리는 4개: **멀티에이전트 지원 · 팩 준수 검증 · 플랫폼 독립 · 한국어**.

## 2. 발굴 전수 목록 (접근법 8분류)

| 분류 | 프로젝트 (★) | 핵심 메커니즘 | 강제력 | 근거 실험 |
|------|-------------|--------------|--------|----------|
| **A. 정적 규칙 팩** | dilitS/op-fable(21), mrtooher/fable-mode(600) | SKILL.md 규칙 — stage map·failable check·모델 티어별 ceremony | 없음 (모델이 무시 가능) | 예제 수준 |
| **B. 스타일 이식** | elon-choo/fablever(24), rennf93/opus-fable-playbook(17) | 공식 가이드 증류 output style / 독트린+드리프트 훅 | 약~중 | **둘 다 정량 A/B 공개** — fablever(scope 위반 0% vs 42%, 품질 동률), playbook(24 probe golden transcript 채점) |
| **B-위험** | HalalifyMusic/fable-mode(12) | **유출 시스템 프롬프트 주입** | 약 | ⚠️ Anthropic IP — 배제 |
| **C. 완료/검증 게이트** | **fivetaku/fablize(768)**, chrisryugj/fable-ish(38), Miguok/fable-harness(132) | PostToolUse ledger + Stop hook — 검증 없는 완료 차단 | **강 (결정론)** | fablize만 통제 실험 |
| **D. 하드 스펙 게이트** | SihyeonJeon/why-was-fable-banned(45) | `.wfb/spec.json` 통과 전 **edit 자체 차단**(PreToolUse) + 가짜 증거 마커 거부 | **최강** | 자체 보고 SWE-bench 29/38→31/38, 토큰 2~3배 |
| **E. 멀티모델 융합** | duolahypercho/fusion-fable(436) | 동일 프롬프트 → Opus·GPT-5.5·Gemini 독립 실행 → Opus 심판·종합 ("독립 후 종합") | 구조적 | 없음 (설계 논증) |
| **F. 스킬 증류** | tomicz/fable-5-train-opus-skills(202) | Fable 5가 은퇴 전 프로젝트 스킬 라이브러리 저술 → 하위 모델 사용 | 간접 | 없음 (Tessl +17점과 정합) |
| **G. 오케스트레이션 원장** | Rylaa/fable5-orchestrator(7), revfactory/harness | Requirements Ledger·모델별 위임 프로파일 / 에이전트 팀 자동 생성 | 중 | harness 자체 A/B 49.5→79.3 |
| **H. 가중치 증류** | HF: Qwable 시리즈, Glint-Research/Fable-5-traces(실재 검증됨 — 4,665 세션, AGPL) | Fable 트레이스 SFT | — | ⚠️ **Anthropic ToS 위반 파생물 — fable-lite 배제 확정** |

기타: apoorvjain25/frontier(41★) — 21 craft standards+수렴 루프+taste gate(모델 불문, B와 C의 중간) / jaredboynton/unifable — fablize 포크(모델 호환성 확장, 독자 검증 없음) / OthmanAdi/planning-with-files(24,860★) — Fable 특화는 아니나 파일 상태 규율의 대표 레퍼런스.

## 3. 코드 수준 핵심 발견 (Codex 정독 결과)

1. **강제력 스펙트럼이 곧 설계 축**: 정적 팩(A)은 "하위 모델이 가장 먼저 무시하는 계층"이고, Stop/PreToolUse 훅(C·D)만이 모델 의지와 무관하게 작동 — 우하 Opus 1인칭 평가와 정확히 일치.
2. **WFB의 하드 게이트는 품질을 올리지만 토큰 2~3배** — 전 작업 적용은 과도, high-risk(인증·마이그레이션·결제)에만 선택 적용이 현실적.
3. **아무도 안 하는 것**: ledger/spec의 **의미적 충실도** 검증(얕은 spec도 형식 통과), 팩 준수 확인("3+ 가설 세웠는가"), 테스트가 변경을 실제 커버하는지 판정 — 전부 미해결 문제.
4. **fablize가 normal-mode 차단을 줄인 것(deep-only)과 fable-ish가 normal도 차단하는 것**은 동일 문제에 대한 상반된 베팅 — friction vs 규율의 트레이드오프.
5. **golden transcript 평가 루프**(playbook)와 **taste gate**(frontier)는 fablize에 없는 독자 기법 — fable-lite 평가 체계에 참고 가치.

## 4. fable-lite 설계 우선순위 (Codex 권고 + 교차 확인)

1. **기본값**: fablize/fable-ish식 observed ledger + Stop gate (검증 없는 완료 차단)
2. **high-risk 한정**: WFB식 spec-before-edit 하드 게이트 (LIGHT/STANDARD/HEAVY 자동 등급)
3. **프롬프트 계층**: mrtooher/op-fable식 stage map — "훅은 빼먹으면 못 끝내게, 스킬은 무엇을 할지"의 역할 분담
4. **장기·멀티에이전트 한정**: Rylaa식 Requirements Ledger (proportional ceremony)
5. **평가**: playbook식 golden transcript 루프 + fablever식 정직한 A/B 문화
6. **배제 확정**: 유출 프롬프트(IP), 가중치 증류(ToS), 스타일 모방 단독(fablize 실험상 효과 미미)

## 5. 미해결 질문 (스펙 단계로 이월)

- fable-lite의 정체: fablize 확장(포크)인가, 신규 통합 하네스인가?
- 타겟 플랫폼: Claude Code 전용인가, wmux 4-pane(Codex·agy 포함) 범용인가? (빈 니치는 범용)
- E(융합)·F(스킬 증류)는 하네스와 별개 축 — 통합할 것인가, 범위 밖인가?
