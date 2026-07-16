# 사건 기록: observation_error — 제3의 차단 경로 규명 (2026-07-16)

> 홈 루트(07-15 A안 해소)·byte_limit(07-16 B안 해소)에 이은 세 번째 차단 계열.
> 성격: 게이트 설계상 정직한 동작이나, **다중 에이전트 협업 환경에서 구조적 오탐**이 된다. v-next 멀티에이전트 게이트 본편의 1급 입력.

## 쉬운 요약

여러 AI가 같은 폴더에서 동시에 일하면, 한 AI가 "지금 폴더 상태를 사진 찍는" 동안 다른 AI가 파일을 고치고 있을 수 있다. 사진이 흔들리면(찍는 중에 피사체가 움직이면) 도구는 정직하게 "제대로 못 봤다"고 선언하고, 그 턴을 차단한다. 문제는 흔들림의 원인이 **협업 중인 동료 AI**라는 것 — 차단당한 AI가 스스로 할 수 있는 일이 없다.

## 증상

- Stop 게이트: "provenance 관측이 불완전하여 clean을 주장할 수 없습니다" (`STOP_PROVENANCE_INCOMPLETE`, reason=`observation_error`)
- ledger: `provenance_status=incomplete`, `provenance_status_reason=observation_error`
- 발생 규모 실측(fable-lite 루트 agents/*.jsonl): claude 471건·codex 1,939건. **단, 대부분(07-13: 1,472건·07-14: 930건)은 v2.0.2가 고친 구 오탐 계열** — 07-15 이후 잔존은 한 자릿수/일.

## 인과사슬 (실측 확정)

1. 에이전트 A가 스캔하는 동안 에이전트 B가 파일을 활발히 씀
2. `capture_regular`(core/provenance_capture.py:35)가 2회 재시도 후에도 stat이 계속 변하면 `ScanIssue(path, "unstable_path")` 반환 (Windows에서 배타 잠금이면 OSError → `"unreadable_path"`)
3. issues가 있는 스냅샷은 `Snapshot.incomplete=True`이지만 `status_reason`은 NONE (provenance_types.py:144)
4. `provenance_lifecycle._observe`(:273-282)가 reason NONE인 incomplete를 **`OBSERVATION_ERROR`로 매핑**
5. mutation-capable 턴이면 Stop 차단 (verify_state.py:305 부근)

**연쇄 효과(2차 피해)**: `start_turn`은 관측이 COMPLETE가 아니면 **턴을 등록하지 않고 반환**(provenance_lifecycle.py:124) → 같은 턴의 후속 훅(post_tool/finish)이 `KeyError` → adapter의 광역 except가 다시 `_incomplete_report()`=observation_error. 즉 부팅 순간 한 번 흔들리면 그 턴 전체가 오류로 도배된다(이벤트 카운트가 부풀려지는 이유).

## 재현 (결정론)

- 스크립트: 격리 루트에 파일 400개 + writer 프로세스 3개가 계속 재작성 + 관측자 1개 `start_turn` 15회 → **15/15 전부 `incomplete/observation_error`** (rebase_count 0 = generation 경합 아님, capture 불안정 경로 확정)
- 대조: 동일 루트 단독(쓰기 없음) 스캔 → complete, 1,102 entries, issues 0
- 시각 상관: 07-16 부팅 차단(22:01:11Z) ±10초 안에 **타 에이전트 3개의 관측 이벤트**(ERP 세션 claude·타 claude·antigravity) — 4-pane 부팅 창과 정확히 일치

## 경쟁 가설과 판정

- 가설 1(채택): 동시 쓰기 → capture 불안정(issues) → reason NONE → observation_error. 증거: hot-write 재현 15/15·단독 재현 0/1·시각 상관.
- 가설 2(기각): 스냅샷 커밋 generation 2회 경합(_observe:295) 포기 경로. 증거: 4프로세스 48회 경합 재현 0건·재현 성공분 rebase_count 전부 0. (경로 자체는 존재 — 대형 트리에서는 여전히 가능성 있음, 이번 잔존분의 주범은 아님)
- 가설 3(부분 채택): 훅 광역 except(KeyError 등) 폴백 — 독립 원인이 아니라 가설 1의 **연쇄 증폭기**(미등록 턴 KeyError).
- 기각: 구조적 스캔 결함 — 단독 스캔 complete로 반증. 07-13/14 대량분은 별개(수정 완료된 구 계열).

## 조치 (이번 커밋)

**관측성 수정만** — 게이트 판정 로직 무변경:
- `ObservationReport`에 `issue_sample`(최대 5개 {path, reason})·`rebase_count`·`error_kind` 추가, observation 이벤트에 조건부 기록(`provenance_issue_sample`·`provenance_rebase_count`·`provenance_error_kind`)
- 효과: 다음부터는 이벤트만 봐도 "무엇이 흔들렸나(어느 파일)·왜(불안정/잠금/예외종류)·경합 여부(rebase)"가 즉시 판독된다. 이번 조사가 코드 정독+재현 2회를 요구했던 관측 공백의 제거.

## 보류 (v-next 멀티에이전트 게이트 본편으로)

1. **협업 에이전트의 쓰기를 구분·귀속**: 흔들림의 원인이 "관측된 동료 에이전트의 기록된 변경"이면 차단 대신 조정(대기·재시도·귀속)하는 설계 — 멀티에이전트 본편의 핵심 요구.
2. start_turn 미등록 턴의 KeyError 연쇄를 명시 상태("turn_not_started")로 분리.
3. 관측 재시도/백오프(현재 capture 2회) — 쓰기가 계속되는 한 재시도만으로는 부족, 본편에서 조정 프로토콜과 함께.

## 관련

- 재현 스크립트: 세션 스크래치패드 `repro_hotwrite2.py`(15/15)·`repro_contention.py`(경합 0/48) — 소멸성, 본 문서의 재현 절차로 재작성 가능
- [[2026-07-16-multiagent-repair-destroys-concurrent-work]] — 같은 뿌리(동일 루트 다중 에이전트, 소유권·귀속 부재)
- core/provenance_capture.py:35-62 · core/provenance_lifecycle.py:262-298 · core/adapter_observation.py(_incomplete_report·_issue_sample)
