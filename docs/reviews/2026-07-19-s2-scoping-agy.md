# 스코핑 브리프 — v2.2.0 이후 잔여 백로그 규명 보고서

**작성자**: Antigravity (Gemini 3.1 Pro High)
**기준**: main `b81af04` (v2.2.0)

---

## 과제 1 — v-next Scorecard 확장 갭 분석 + 설계 입력

### 1. Scorecard 집계 누락 항목 (갭 분석)
현재 `core/scorecard.py`의 `ReasonCode`에는 신규 멀티에이전트 게이트와 v2.2 상태가 반영되어 있지 않습니다.
- **누락 카테고리**: R2 destructive deny, peer exclusion (경합 배제), owners settlement, turn_not_started (현재 `STOP_PROVENANCE_INCOMPLETE`로 처리됨), 이중근거 교차 실패, invocation window/lease 초과 차단, v2.2 quick→normal 원자 승격 기록.
- **근거 (`core/scorecard.py:24-30`)**: `ReasonCode` Enum에는 `STOP_PROVENANCE_INCOMPLETE`, `STOP_INVESTIGATION_MARKERS` 등 기존 6개 사유만 존재합니다.

### 2. 루트 단위 교차 뷰 (Root-level cross view) 설계 제안
다중 에이전트가 동일 워킹트리를 병행 편집하는 환경에서는 에이전트 간 경합(peer exclusion, R2 deny) 여부를 파악해야 하므로 **루트 단위의 교차 뷰가 필요**합니다.
- **현행 한계 (`core/scorecard.py:67`)**: `SessionIdentity`가 `host:session_id:agent`로 단일 에이전트에 종속되어 있습니다.
- **저장 스키마**: 기존 에이전트별 저널을 유지하되, `root_scorecard.jsonl`를 도입하여 R2 차단/peer 충돌 등 상호작용 관련 이벤트를 통합 기록.
- **CLI 확장 (`python -m fable_lite scorecard`)**: `--root` 옵션 시 현재 워킹트리의 다중 세션 데이터를 통합하여 에이전트별 경합 지표를 가로로 비교하는 표 렌더링.
- **표시 opt-in**: CLI 호출 시나 `--verbose` 옵션에만 렌더링하고 평시 훅에서는 다이어트를 유지.

### 3. 안티게이밍 (우회 표면 리스크)
- Scorecard는 내부 평가 도구이므로 에이전트가 직접 기록(페이로드 위조)하거나 타 에이전트의 차단 사유를 날조(Spoofing)하는 것을 방지해야 합니다.
- 게이트 엔진(verify_state)이 관측한 Ledger `event_seq`에 의존하는 **Append-only** 기록이어야 하며, 멀티세션 스냅샷 교차 검증 로직이 성적표에도 적용되어야 합니다.

---

## 과제 2 — turn_not_started 잔여 갭 규명

### 1. "full bootstrap 성공 시 complete 회복" 경로 갭
- **구현 결함 (`core/ledger_v2.py:518-525`)**: `_update_turn_after_event()`에서, Payload에 의해 `turn["baseline_status"]`가 변경되기 직전(504-506라인 처리와 565-567라인 반환)에 `provenance_status_reason` 판단이 먼저 수행(518-525라인)됩니다. 그 결과 빈 문자열 `""`이 인입되어도 `turn.get("baseline_status") == "missing"`이 True로 평가되어 `provenance_status_reason`은 다시 `"turn_not_started"`로 영구 고착됩니다.
- **테스트 부재 (`tests/test_multiagent_f3.py:217-218, 241`)**: 테스트는 해당 조건에서 차단됨만 Assert할 뿐, 후속 정상 관측(full bootstrap)을 통한 복구(complete 회복)를 테스트하지 않습니다.

### 2. 발생 빈도 축소 (관측 재시도/백오프) 갭
- **근거 (`core/provenance_lifecycle.py:462-471`)**: 4-pane 부팅 등 동시성 시발생하는 unstable_path/unreadable_path 상황에서, 스캔이 `incomplete`일 경우 백오프 없이 즉각 `_mark_incomplete`를 반환합니다. 보류된 재시도/백오프 로직이 본편에서 처리되지 않아 동시 다중 쓰기 시 turn_not_started가 구조적으로 지속 발생합니다.

### 3. 운영자 가시성 갭
- **근거 (`core/verify_state.py:330-339`)**: mutation-capable 턴일 경우, `STOP_PROVENANCE_INCOMPLETE`라는 범용 사유로 차단되며 "provenance 관측이 불완전하여 clean을 주장할 수 없습니다"라는 공용 메시지만 출력됩니다. "turn_not_started"라는 명확한 원인이 은닉되어 운영자 조치를 방해합니다.

---

## 과제 3 — 기타 진행 가능 건 열거 (외부 게이트 없음)

| 백로그 항목 | 규모 | 리스크 | 추천 우선순위 | 상세 내용 및 근거 |
|---|---|---|---|---|
| **v2.2 스펙 D절 이행 (설치 문서화)** | S | 낮음 | **최상** (P1) | `docs/specs/v2.2-quiet-optin.md` D절에 지정된 `config.json`을 통한 명시적 활성화 절차가 `README.ko.md`에 누락되어 있습니다(현재 "설치하면 알아서 작동" 기재). v2.2부터 Opt-in이므로 사용자 혼선을 막기 위해 즉각 수정해야 합니다. |
| **CON-2 aging deep-tail 과소집계 잔여 처리** | S | 낮음 | **상** (P2) | `docs/reviews/session-scorecard-ultracode-r3.md`에 확인된 128+ 세션 경과 시 발생하는 Scorecard 블록 유실 한계. `CHANGELOG.md` Known Limitations에 명시하거나, unbounded seen-set 도입으로 해소. |
| **v2.0.1 잔여 (RP-2 문서 정정, COR-1 자매 등)** | S | 낮음 | **중** (P3) | 코드 본질의 게이트 무결성과 무관한 소규모 유지보수 건. 안정화 기간에 함께 처리하기 좋습니다. |
