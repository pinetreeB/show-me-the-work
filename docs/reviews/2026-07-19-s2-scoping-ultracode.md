# v2.2.0 이후 잔여 백로그 스코핑 보고서 (2026-07-19)

기준: main `b81af04` (v2.2.0 Quiet Opt-in). 전 과정 조사 전용(Read/Grep/Glob/Bash 읽기 전용) — **추적 파일 수정 0건**, tmp\ 프로브는 실행 후 전부 삭제, `git status --porcelain` 확인 결과 `.codex/config.toml`(조사 시작 전부터 존재하던 무관 변경) 외 신규 변경 없음. 이 문서(및 R1 게이트가 요구한 `.fable-lite/contracts/` 계약 파일)가 유일한 신규 산출물.

방법: 3개 과제를 각각 investigate → deepen → verify(적대적 재검증) 3단계로 독립 조사(9개 에이전트, 총 39.3만 토큰·393회 도구 호출). 아래는 검증에서 살아남거나 검증 과정에서 수정된 최종본이며, 가장 중요한 3건(§2 resume_turn 엄격비교, §3 OK_SIGNALS 한국어 토큰 부재, §1 destructive_guard.py의 scorecard 미배선)은 오케스트레이터가 직접 재열람해 3중 확인했다.

---

## 과제 1 — Scorecard 확장 갭 분석

### 1-1. 현재 실제로 집계하는 것 (닫힌 유한 스키마)

- `SessionIdentity`(core/scorecard.py:67-74) = `{host, session_id, agent}` **뿐**. root/project 필드 없음.
- `ReasonCode`(core/scorecard.py:24-31) 정확히 **6개**: `stop.provenance_incomplete` / `stop.investigation_markers` / `stop.verification_missing` / `pretool.goals_missing` / `pretool.intent_missing` / `pretool.contract_missing`. `_parse_enum`(core/scorecard.py:307-315)이 이 6개 밖 문자열을 전부 거부 — 의도된 닫힌 스키마.
- `GateAction`은 `block/recover/cap_allow` 3종(core/scorecard.py:34-37) 뿐.
- 실제로 이벤트를 기록하는 호출부는 **3곳뿐**: Stop 게이트(core/verify_state.py:527-554), N2 goals/intent(core/gate_counters.py:207-233), R1 계약(core/contract.py:420-445). 전부 **단일 에이전트 시대(N1/N2/R1) 게이트**의 산물.

### 1-2. 갭 목록 (파일:라인 근거, 오케스트레이터 직접 재확인 완료)

| # | 갭 | 근거 | 심각도 |
|---|---|---|---|
| G1 | **R2 파기차단(destructive gate) — 완전 무관측** | `core/destructive_guard.py:evaluate_r2_destructive_gate`(567-623)에 `reason_code` 필드 자체가 없고, **scorecard/ReasonCode/GateTransition을 파일 전체에서 단 한 번도 import하지 않음**(오케스트레이터 직접 grep 재확인: 0건). 07-16 실사고의 직접 처방인 R2가 하나도 집계되지 않는다. | 높음 |
| G2 | **`turn_not_started` — 완전한 무(無)이벤트** | `core/verify_state.py:295-306`의 allow 반환에 `reason_code` 키 자체가 없어 block도 recover도 cap_allow도 만들지 않는다(구조적으로 scorecard가 볼 수 없음). `_gate_state`가 합성 상태를 만들어줘도 scorecard로 가는 경로가 원래 없음. | 높음 |
| G3 | `scope_too_large`가 `STOP_PROVENANCE_INCOMPLETE`로 뭉개짐(전용 코드 없음) — `core/verify_state.py:308-323`, `core/provenance_types.py:44` | 중간 |
| G4 | F1 owners 정산 degraded/attribution_capacity_exceeded 무관측 — `core/ledger_v2.py:187-357`가 scorecard를 한 번도 참조하지 않음 | 중간 |
| G5 | `peer_activity`(관측 제외)·invocation window/lease 무관측 — `core/provenance_types.py:58,133-143` | 낮음 |
| G6 | quick 모드 정상 통과분 비관측 — `adapters/claude_code/stop.py:34-35`. **단, 이는 v2.2 A4의 의도된 사양이지 갭이 아님**(정상 통과 구간을 무겁게 만들지 않는다는 설계 목적과 일치). | 사양(갭 아님) |
| G7 | **"scorecard 표시가 A1 supervision과 배선돼 있다"는 가정은 거짓** — 표시는 별도 env var `FABLE_LITE_SCORECARD`(core/verify_state.py:40,473 / adapters/claude_code/stop.py:75)로만 게이팅되고, `supervision`(config.json)과는 코드상 아무 연결이 없다. opt-in이 이중화된 상태. | 낮음(정리 권고) |

### 1-3. root 단위 교차 뷰가 불가능한 이유

1. 스키마에 root 차원 자체가 없음(SessionIdentity 3필드뿐).
2. CLI `--root`(fable_lite/scorecard.py:26-38)가 단일 경로 전제, 다중 root 병합 옵션 없음.
3. **더 근본적으로, "동일 루트"라는 전제가 어댑터마다 다르게 계산됨(실측)** — claude_code(latch 3단계) / codex_cli(`adapters/codex_cli/common.py:45-55`, containment 판정이 역방향이라 하위 디렉토리를 상위로 오인) / antigravity(`resolve()` 없이 원문자열) 세 알고리즘이 서로 달라, 물리적으로 동일한 작업 디렉토리도 세 호스트가 다른 `project_root` 문자열을 만들 수 있고 — SessionIdentity에 root 필드가 없어 이 분열 자체를 사후 감지할 방법도 없다.

### 1-4. 설계 제안 (적대적 검증·수정 반영 완성본)

**원칙: 새 이벤트 타입이 아니라 기존 GateTransition 재사용 + 최소 구조 추가.** `_parse_enum`의 닫힌 스키마 보장(core/scorecard.py:307-315)을 유지한 채 확장한다.

- **ReasonCode 확장**: `STOP_TURN_NOT_STARTED`(G2) / `STOP_SCOPE_TOO_LARGE`(G3, 현재 STOP_PROVENANCE_INCOMPLETE와 분리) / `STOP_PEER_ACTIVITY_EXCLUDED`(G5) / R2용 4개(`DESTRUCTIVE_ATTRIBUTION_DEGRADED` / `DESTRUCTIVE_PEER_UNSETTLED` / `DESTRUCTIVE_STATE_DIR_PROTECTED` / `DESTRUCTIVE_UNRESOLVABLE_TARGET`). **⚠️구현 체크리스트**: destructive_guard.py의 `_block()` 호출부는 정확히 **8곳**(583/591/596/598/607/614/618/620/622행) — 검증 과정에서 최초 설계안이 `attribution_health_unavailable`(596행) 1곳을 누락했던 것이 실제로 발견됨. 구현 시 8곳 전부가 4개 카테고리 중 하나에 배정되는지 표로 대조 후 착수할 것.
- **신규 GateAction `OBSERVE`**: 차단도 회복도 아닌 상태 전용(`resolves=()`, `resolution=NONE`). `_resolved_blocks`가 이미 `action is not RECOVER`면 스킵하므로 **코드 변경 없이 자동으로 무해**. `ScorecardAggregate`/`ReasonAggregate`에 `observed_events` 카운터 필드 1개만 추가.
- **root는 core 도메인 모델에 넣지 않는다**: SessionIdentity에 root를 추가하면 기존 저널·CLI 그룹핑 호환이 깨진다. 대신 CLI 로더(fable_lite/scorecard.py)에서 다중 저널을 읽을 때만 붙이는 얇은 wrapper(`RootedGroup`)로 처리 — core는 root 개념을 몰라도 됨.
- **CLI**: 신규 서브커맨드 대신 기존 `scorecard`에 플래그 추가(`--root` 반복 가능화, `--discover-roots`, `--by-reason`, `--gate destructive`, `--conflicts`). `DESTRUCTIVE_*` 코드가 기록되면 `_row_json`의 `by_reason` 동적 순회(core/scorecard.py:186-191)가 **코드 변경 없이** 자동 표시.
- **표시 opt-in**: "기록은 항상, push 표시는 항상 opt-in" 원칙 유지 — 신규 카테고리도 `render_stop_line`에 한 줄 추가하는 정도로 끝내고 새로운 자동 표시 트리거는 만들지 않는다. G7(이중 스위치) 정리는 이번 확장의 필수 전제조건은 아니므로 별도 트랙.

### 1-5. 안티게이밍 분석 (검증에서 1건 추가 발견)

핵심 원칙(docs/design/multiagent-gate.md:25 "귀속은 주장이 아니라 관측으로만 성립")에 비추어 4가지는 안전하다고 확인됨: ①reason_code/action은 여전히 게이트 함수 내부에서만 생성(에이전트가 조작 불가) ②OBSERVE는 block/recover 매칭에 영향 없음(표시 잡음일 뿐) ③다중 root CLI 병합은 읽기 전용(기존 단일-root에도 내재된 "저널 파일=신뢰" 전제의 확장일 뿐, 새 위협 아님) ④8→4 압축은 원본 `reason` 자유텍스트가 어댑터 응답에 그대로 노출되므로 은폐가 아님.

**⚠️적대적 재검증에서 신규 발견한 우회 시나리오**: R2는 `resolve_active_invocation()`(어댑터에서 R2 **이후**에 실행되는, R2-first invariant가 요구하는 의도된 순서) 이전의 **원시(미해석) identity**로 호출된다. 그런데 `_active_invocation`(core/adapter_observation.py:478-502)의 세션 복구 로직이 `session_id`를 재기입할 수 있어, R2를 그 원시 identity 그대로 scorecard에 기록하면 **같은 실제 세션의 R2 이력이 Stop/N2/R1과 다른 `agent_key`로 갈라진다** — 차단 자체는 유지되지만 감사 기록이 세탁되는(그 세션의 성적이 실제보다 깨끗해 보이는) 결과. **필수 요건: R2의 scorecard 기록은 반드시 `resolve_active_invocation()` 이후의 identity를 사용해야 한다**(판정 자체는 원시 identity로 fail-closed 유지, 기록만 재해석 후 identity로 별도 수행). 아울러 scorecard 기록(I/O)은 R2의 `block`/`allow` 반환 **확정 후**의 fire-and-forget이어야 하며, 기록 실패가 차단 판정에 역으로 영향을 주는 경로(예: 저널 디렉토리를 읽기전용으로 만들어 R2를 우회하려는 시도)를 절대 만들지 않아야 한다.

---

## 과제 2 — `turn_not_started` 회복 경로 실측

### Q1. "후속 full bootstrap 성공 시 complete 회복" 경로가 실제로 있는가?

**있다 — 그러나 전용 로직이 아니라 두 메커니즘의 우연한 조합이며, 검증 과정에서 그중 하나에 실제 버그가 발견됐다.**

프로브 실측(`core.ledger_v2.apply_v2_event`를 실제 어댑터 호출 모양 그대로 직접 실행, 오케스트레이터가 핵심 지점 재확인 완료):

```
(a) 첫 이벤트(baseline 없음): baseline_status=missing, provenance_status_reason=turn_not_started
(b) 두번째 이벤트(baseline_status=ready, reason 필드 안 보냄): reason은 그대로 "turn_not_started" 잔존
(c) reason="" 명시 전달 시에만 지워짐
(e) 실제 adapter_observation._record_invocation과 동일한 payload 모양 + mutation_capable=True:
    baseline_status=ready인데도 provenance_incomplete=True, reason=turn_not_started 잔존
    → Stop 판정: block("stop.provenance_incomplete")
```

`core/ledger_v2.py:493-540`의 `_update_turn_after_event`는 `provenance_status_reason`을 "payload에 그 키가 있을 때만" 갱신하는 범용 필드 덮어쓰기(518-525)이지, 전용 recovery 로직이 아니다. 실제 어댑터의 정상 도구 호출 1주기(PreToolUse→도구→PostToolUse)에서는 PostToolUse(`_record_status`, core/adapter_observation.py:440-463)가 매번 이 필드를 새로 채우므로 보통은 우연히 정리되지만, PostToolUse가 예외로 fail-open하면(adapters/claude_code/post_tool_use.py:186-187) 이 어긋남이 그대로 남을 수 있다.

**신규 확정 버그(오케스트레이터 직접 재확인 완료, `core/provenance_lifecycle.py:308-313`)**:
```python
result = self.start_turn(agent, turn_id, mutation_capable)
if (result.status is ProvenanceStatus.COMPLETE and not result.incomplete):
    return
raise TurnBootstrapError(...)
```
`resume_turn`의 이 부트스트랩 재시도 판정이 `is ProvenanceStatus.COMPLETE` **엄격 동일성 비교**를 쓴다 — 같은 파일의 `_complete_observation()`(142-146행, COMPLETE와 COMPLETE_WITH_EXCLUSIONS를 둘 다 "완료"로 인정하는 기존 헬퍼)을 쓰지 않는다. 결과: 동료 에이전트의 기록된 쓰기를 봐주는 F3 구제 로직(`adjust_snapshot_for_peer_activity`)이 성공해 `COMPLETE_WITH_EXCLUSIONS/incomplete=False`를 반환해도, 이 지점은 이를 **부트스트랩 실패로 오판**하고 예외를 던진다 — **F3가 가장 필요한 바로 그 상황(첫 전체 부트스트랩이 동료 쓰기와 경합하는 4-pane 부팅 창)에서 정작 무력화된다.** 두 독립 프로브(조사 담당·검증 담당이 각자 재작성)로 100% 재현: 실제 어댑터 호출 경로(`begin_invocation`)로 재현하면 `baseline_status=ready`(실제로는 baseline이 저장 안 됐는데도)로 잘못 표시되고, 해당 도구 호출의 `candidate_paths` 등록(`lifecycle.begin_invocation`) 자체가 통째로 누락됨. 관련 테스트(`tests/test_provenance_lifecycle.py:194`)는 엄격 COMPLETE 케이스만 검증하고 COMPLETE_WITH_EXCLUSIONS 조합 커버리지는 0건(grep 확인).

발동 조건(좁게 확정): 이 턴의 저장된 baseline이 없고 **동시에** 프로젝트 전체 workspace-current 스냅샷도 아직 하나도 커밋되지 않은 경우(`self._state.current is None`) — 완전 신규 프로젝트에서 여러 세션이 동시에 처음 부팅하거나 스토어 오류 후 등.

### Q2. 관측 재시도/백오프(2026-07-16 문서 보류 항목 #3) 구현됐는가

**구현 안 됨.** `core/provenance_capture.py:35` 부근 `for attempt in range(2):` 그대로 2회 고정, 지연/백오프 없음(오케스트레이터 직접 확인). 문서(docs/reviews/2026-07-16-observation-error-third-path.md:49) 자체가 "본편(v-next)으로 보류"라고 명시.

한편, **4-pane 부팅 창 시각상관을 줄이는 별도 전용 대응(F3 peer_activity)은 실제로 구현·배선돼 있다**(`core/provenance_lifecycle.py:68-131`, `_observe()`에 연결 확인, `tests/test_multiagent_f3_observation.py:83-136`) — 다만 위 resume_turn 버그 때문에 정확히 가장 필요한 "첫 부트스트랩 경합" 시나리오의 한 갈래에서는 구제되지 않는다.

### Q3. `turn_not_started`가 운영자에게 실행가능하게 노출되는가

- **Stop 메시지**: read-only 경로(core/verify_state.py:295-306)에서는 `turn_not_started`라는 단어가 문자 그대로 노출됨. 그러나 mutation_capable=True 경로(오케스트레이터가 320줄 이후까지 직접 재확인)에서는 별도 분기 없이 일반 `STOP_PROVENANCE_INCOMPLETE` 차단과 완전히 동일한 문구("provenance 관측이 불완전하여...")로 떨어져 `turn_not_started`라는 단어 자체가 사라진다.
- **scorecard 실측(실제 커맨드 실행, `python -m fable_lite scorecard --root . --days 1`)**: 실제 출력·`--json` 출력 어디에도 `turn_not_started`/`degraded`/`R2` 문구 없음(구조적으로 나올 수 없는 스키마 — 과제1 G2와 동일 근본원인).

### 갭 요약 및 규모

| # | 발견 | 성격 | 규모 |
|---|---|---|---|
| 1 | 회복이 전용 로직 아닌 범용 필드 덮어쓰기, PostToolUse 예외 시 잔존 가능 | 확정 갭 | M |
| 2 | 관측 재시도/백오프 미구현(의도된 보류) | 확정 갭(의도됨) | L(멀티에이전트 본편 스코프) |
| 3 | scorecard가 구조적으로 turn_not_started 집계 불가(=과제1 G2) | 확정 갭 | M |
| 4 | mutation_capable+missing은 일반 incomplete 차단과 동일 코드 | **갭 아님**(설계·구현 일치, 문서 §5-3 그대로) | — |
| **5** | **`resume_turn`의 엄격 COMPLETE 비교가 COMPLETE_WITH_EXCLUSIONS를 실패로 오판 → F3가 가장 필요한 자리에서 무력화, 테스트 커버리지 0** | **신규 확정 갭(버그)** | **S**(`is ProvenanceStatus.COMPLETE` → `_complete_observation(result.status)` 한 줄 교체 + 회귀테스트 1~2건. 단 `except TurnBootstrapError`가 `lifecycle.begin_invocation`을 건너뛰는 하위 처리까지 함께 손봐야 해 S~M 사이) |

---

## 과제 3 — 외부 게이트 없이 진행 가능한 백로그 (전수 스캔 + 우선순위, 검증·수정 반영 최종본)

### 3-1. 이미 해소된 것으로 확인됨 (참고용, 재작업 불필요)

CONFIG 자가면제 우회(prov-fix 체인 전체 R1~R4+reverify), scope_too_large 메시지 홈루트 문구/provenance-config 안내, SOFT_EXCLUDES 캐시 경계 항목, `MULTI_STORY_PATTERNS` "하고" 과매칭, 스크립트 재실행 정규식 판정, v1-readiness B1/B2/H1/H2/H3, pytest collect 이슈, TEST-COV 공백 테스트, check.py git-fallback 억제, **OK_WORD_RE 콜론/공백 브리틀니스**(주의: memory·1차 스캔이 "미해소"로 오판했으나 검증에서 `\bok\b` 도입 커밋 + `test_text_indicates_success_accepts_ok_word_boundary_cases_from_p8` 회귀테스트로 **이미 해소**됨을 확인 — 아래 우선순위에서 이 근거는 폐기하고 최신 근거로 교체함), RP-2 문서정정.

**중요 정정**: memory가 언급한 "CON-2·COR-1·PERF-2·REG-1·RP-2"는 CHANGELOG.md에 그 라벨 자체로는 존재하지 않는다 — `docs/reviews/session-scorecard-ultracode*.md` 내부 결함 코드다. CHANGELOG.md의 실제 Known Limitations(60-66행)는 5개 항목(Stop fail-open·원격 미관측·10k reconciliation 지연·promise-only 미구현·Antigravity 미확인)이며, 이 중 **"Antigravity 1.1.1 미확인" 문구는 저장소 자신의 최신 문서(p9-agy-live-hooks.md, 1.1.2 재판정 성공)와 이미 불일치** 상태로 CHANGELOG/README에 방치돼 있다.

### 3-2. 최종 우선순위 (효용/규모/리스크, 오류 수정 반영)

**Tier 1 — 지금 바로 (고효용·저비용·저리스크)**

| 순위 | 항목 | 규모/리스크 | 근거 |
|---|---|---|---|
| 1 | **`core/provenance_policy.py`의 SOFT_EXCLUDES에 빌드 산출물(`.next/**` 등) 추가** | S/Low | `docs/reviews/2026-07-16-project-root-build-artifact-byte-limit.md:149`가 저장소 스스로 **"★우선순위 높음"** 표기(1·2차 스캔 둘 다 누락했다가 검증에서 발견). Next.js 프로젝트는 사실상 100% 재현. |
| 2 | v2.2 D절 "프로젝트 scope 설치" README 미문서화 | S-M/Low | `docs/specs/v2.2-quiet-optin.md:75-78` D절이 요구하는 "완전 무비용 설치 경로" 안내가 README/README.ko 어디에도 없음(grep 0건) — 진짜 0비용 옵트인의 존재 자체를 사용자가 못 찾는 상태 |
| 3 | OK_SIGNALS 한국어 성공 토큰("통과"/"성공") 부재 | S/Low | **근거 교체**: `docs/reviews/2026-07-16-project-root-build-artifact-byte-limit.md:144-145`("ALL PASS"조차 미매치, 최신 재현) — p8/v1.2 인용분은 이미 해소된 사례라 근거 부적절함이 검증에서 밝혀짐 |
| 4 | CHANGELOG/README Antigravity "1.1.1 미확인" 문구 정정 | S/Low | 순수 문서, 저장소 자체 최신 사실과의 불일치 해소 |
| 5 | COR-STORE-1: `core/provenance_store.py`(73,107행 bare `os.replace`)에 `core/ledger_storage.py`의 기존 `_replace_with_one_retry` 재사용 | S/Low-Medium | 이미 검증된 패턴 재사용, Windows 동시 쓰기 실패 시 허위 incomplete 방지 |
| 6 | **§과제2의 resume_turn 엄격비교 버그 수정** | S~M | 이번 조사의 최고 실체적 발견 — F3 구제 로직이 가장 필요한 자리에서 무력화되는 실제 버그 |

**Tier 2 — 여유 있을 때**

7. antigravity 설치 스크립트 부재(codex_cli/install.py 패턴 재사용) — S/Low
8. `scope_too_large` 메시지 status_reason별 분기(오진단 유발 실측 사례 있음) — S-M/Low
9. CON-2 aging deep-tail(`scorecard_cache.py` evicted_keys 이력도 64개로 잘려 128+세션 후 오라벨) — S-M/Low(scorecard는 fail-open 관측 전용이라 게이트 판정엔 무영향)
10. 관측 재시도/백오프 파라미터 튜닝(멀티에이전트 본편 착수 전 뗄 수 있는 부분 완화) — S-M/Low-Medium
11. COR-1 자매 케이스 — S/Low
12. 동일 세션 다중 루트 관측 미비 — M/Low
13. **과제1의 scorecard 확장 설계안**(G1~G7) 구현 착수 — M(스키마 확장은 재사용 위주로 설계돼 있어 상대적으로 저리스크이나 R2 배선은 identity 해석 순서 요건 때문에 신중한 구현 필요)

**Tier 3 — 계획 잡고 진행**

14. PERF-2 벤치 대표성 확장 — M/Low
15. PERF-1 계측 확장 — **정정**: 대상 파일은 `core/agent_log.py`가 아니라 `eval/provenance_bench_scorecard.py`(벤치 mock)이며, 저장소 자체가 "라이브 결함 아님·P3 백로그"로 3라운드 연속 확정 — 리스크를 Medium에서 **Low로 하향**
16. REG-1 cap dedup 시맨틱 — M/Medium(게이트 락 구역 공유, 설계결정 선행 필요)
17. e1c 교차파일 import 재실행 휴리스틱 — M-L/Medium-High(검증인식 로직 확장은 신규 우회 벡터 위험 있어 적대검증 필수)

**Tier 4 — 별도 프로젝트로 분리 (설계 재수렴 필수)**

18. 무변경 턴 차단 비용 재설계 — L/High
19. 멀티에이전트 협업 쓰기 귀속/조정 본편 — L/High. **정정**: `docs/design/multiagent-gate.md`는 "착수 흔적"이 아니라 이미 **rev3(2라운드 교차검토 완료, F0-F5 acceptance matrix 확정)** 수준의 성숙한 스펙 — 실행 리소스 배정만 남은 상태.

---

## 종합 결론 — 크로스 태스크 최우선 5건

1. **`core/provenance_policy.py` SOFT_EXCLUDES 빌드 산출물 추가** — 저장소 스스로 "★우선순위 높음" 표기, 1·2차 스캔 모두 놓쳤던 항목(S/Low)
2. **`core/provenance_lifecycle.py:308-313` resume_turn 엄격비교 버그** — 과제2 최대 발견, F3 무력화(S~M)
3. **destructive_guard.py(R2) scorecard 미배선 해소** — 07-16 실사고 직접 처방이 감사 기록에서 완전히 사라져 있는 상태(과제1 G1, M — 단 R2-first invariant·identity 해석 순서 요건 준수 필수)
4. **v2.2 D절 프로젝트 scope 설치 문서화** — 이미 배포된 기능의 진입점 자체가 안 보이는 상태(S-M/Low)
5. **OK_SIGNALS 한국어 성공 토큰 보강** — 한국어 프로젝트에서 검증 성공을 구조적으로 인식 못 하는 반복 재현 마찰(S/Low)

## 부록 — 조사 절차 검증 기록

- 3개 과제 × 3단계(investigate/deepen/verify) = 9개 에이전트, 전원 완료(에러 0). Task1·Task2는 최종 "승인"/"조건부승인"(1건 사소한 매핑 누락 수정), Task3은 "조건부승인"(2건 오류 수정 + 1건 누락 항목 추가).
- 모든 단계에서 `git status --porcelain` 확인 결과 `.codex/config.toml`(조사 시작 전부터 존재) 외 tmp\ 바깥 변경 없음. 생성된 프로브 스크립트(`tmp/ultracode-probe-turn-recovery.py`, `tmp/ultracode-probe-turn-recovery-gap2.py`, `tmp/ultracode-probe-verify-turn-recovery.py`)는 전부 실행 후 삭제 확인.
- 오케스트레이터가 최고 위험도 3건(resume_turn 엄격비교, OK_SIGNALS 한국어 토큰 부재, destructive_guard.py scorecard 미배선)을 원문 재열람으로 직접 3중 확인.
- 이 보고서 작성 자체가 R1(계약) 게이트에 의해 최초 차단되어(`.fable-lite/contract.json`은 다른 작업자의 미검증 계약이라 존치, 대신 오케스트레이터 전용 네임스페이스 계약 `.fable-lite/contracts/claude_code-46cb7c83-777c-4d1a-963b-32b4cca72d29-claude-dcccb97df5d1c557.json`를 신규 작성해 통과) — 이 하네스 자신이 자기 세션에도 동일하게 적용됨을 실측으로 확인.
