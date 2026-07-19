# Scorecard v-next 설계 갈림길 판정 보고서 (2026-07-19)

**작성자**: Antigravity (Gemini 3.1 Pro High)
**대상 설계안**:
- Codex안 (C:\Users\rotat\exchange-yeongjin\from-yeongjin\20260719-scoping\codex-scoping.md)
- ultracode안 (C:\Users\rotat\fable-lite\tmp\ultracode-scoping-20260719.md)
- Antigravity 1차 브리프 (tmp\agy-scoping-20260719.md)

---

## 결론: 구체적 하이브리드 채택

세 제안을 종합 분석한 결과, **Codex의 "별도 저널(coordination.jsonl) 분리" 스키마**와 **ultracode의 "R2 Identity 세탁 방지 요건 및 닫힌 CLI Root Wrapper"**를 결합한 하이브리드 설계를 최종 채택합니다.

## 1. 기존 스키마/저널 호환성 및 구현 리스크

- **Codex안 (승)**: `gates.jsonl`과 기존 `ReasonCode`를 불변으로 유지하고, R2/peer 충돌 등 비-게이트 이벤트를 `.fable-lite/scorecard/coordination.jsonl`로 완전히 분리합니다. 
- **ultracode안 (기각)**: 기존 `GateTransition`을 재사용해 `ReasonCode` 7개를 추가하고 `OBSERVE` 액션을 도입하려 했습니다. 그러나 R2 차단은 본질적으로 Stop/PreTool 게이트의 전이(transition)나 해결(resolves) 개념과 맞지 않아 `GateTransition`을 과적재(overload)하게 됩니다. 엄격한 `_parse_enum` 호환성 및 하위 버전 호환(Backward compatibility) 측면에서도 Codex의 별도 저널 신설이 구현 리스크가 현저히 낮습니다.

## 2. 안티게이밍 및 R2 Identity 세탁 방지

- **ultracode안 (승)**: R2 파기 차단(Destructive gate)이 `resolve_active_invocation()`보다 먼저 실행된다는 불변성(R2-first invariant)을 정확히 포착했습니다. 만약 R2 차단을 **원시(미해석) identity** 상태로 즉시 기록할 경우, 이후 세션 복구로 결정된 실제 identity와 갈라져 "차단 기록 세탁(Split-brain laundering)"이 발생합니다.
- **채택 요건 (`ultracode-scoping-20260719.md:50`)**: R2 판정 자체는 원시 identity로 Fail-closed를 유지하되, **Scorecard I/O(기록)는 반드시 `resolve_active_invocation()`이 확정한 최종 identity를 주입받아 수행**해야 합니다. 또한 기록 실패가 R2 차단을 해제하지 않도록 Fire-and-forget 원칙을 강제해야 합니다.

## 3. 루트 교차 뷰 (Root-level cross view) 모델링

- **공통 합의 (채택)**: Antigravity 1차 브리프에서 제안한 "루트 단위 집계"의 필요성은 모두 동의했으나, 이를 Core 도메인인 `SessionIdentity` 필드에 추가하는 것은 기존 모델을 오염시킵니다.
- **채택 설계 (`codex-scoping.md:90` / `ultracode-scoping-20260719.md:42`)**: Core 스키마는 현재의 `host:session_id:agent`를 유지합니다. 교차 뷰는 오직 **CLI 렌더러 수준의 Wrapper (`RootedGroup`)**에서 다중 에이전트의 저널 파일을 읽기 시간에 조인(Read-time join)하여 `--view agents/root/coordination` 형태로만 노출합니다.

## 4. v2.2 Quiet 정책 정합

- **공통 합의 (채택)**: Scorecard 표시는 항상 Opt-in이어야 합니다. v-next에서 추가되는 어떠한 새로운 R2, peer 충돌, 교차 뷰 통계도 `Stop` 허용 메시지에 자동(Push)으로 노출되어서는 안 됩니다. 명시적인 CLI 호출(`python -m fable_lite scorecard`) 시에만 렌더링하여 v2.2 메시지 다이어트를 훼손하지 않습니다.

---

## 최종 구현 체크리스트 (Action Item)

1. **coordination.jsonl 신설**: `core/scorecard_store.py` 외부에 독립적인 append-only 기록기 구현.
2. **R2 8개 지점 배선 (`core/destructive_guard.py:583-622`)**: 8곳의 `_block` 지점을 4개 카테고리로 맵핑. 단, 기록(I/O)은 R2 판정 직후가 아닌 최종 identity 해석 이후로 지연 또는 콜백 주입 처리.
3. **CLI 뷰 확장**: `fable_lite/scorecard.py`에 `--view` (sessions/agents/coordination/root) 플래그 추가 및 다중 저널 취합 로직(wrapper) 추가.
