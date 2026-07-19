# P4 다차원 검토 보고서 — worker/s2-fix-score (2026-07-19)

대상: 브랜치 `worker/s2-fix-score`(영진 Codex 구현, main `b81af04` 기준 3커밋 — `68adec9` FIX-1/FIX-2, `ed7a24a` SCORE-1, `daa47b5` FIX-3). 스펙=`tmp/impl-spec-s2-20260719.md`, 구현자 보고=`exchange-yeongjin/from-yeongjin/20260719-s2impl/summary.md`. 검토는 소스 조사 전용 — **추적 파일 수정 0건**(양쪽 저장소), 프로브는 전부 `tempfile.mkdtemp()` 격리 root에서 실행 후 삭제.

방법: 6개 차원을 review→verify(적대적 재검증) 2단계로 독립 조사(12개 에이전트, 168.5만 토큰·669회 도구 호출, 총 소요 108분). 오케스트레이터가 워크플로우 실행 전 핵심 diff를 전량 직접 정독했고, 완료 후 최고위험 발견 1건을 별도로 3중 재현했다.

---

## 결론: **APPROVE (조건부 승인)**

3개 웨이브의 핵심 주장(FIX-1 resume_turn 버그 수정, SCORE-1 coordination journal의 안전성, FIX-3 Windows 장경로 수정)은 전부 독립 재현으로 확인됐고, 게이트 판정(Stop/N1/R1/R2)에 영향을 주는 회귀나 correctness 결함은 발견되지 않았다. 전체 게이트(pytest 704 / probes 17-0-3 PASS / e2e 16-16)도 이 서버에서 독립적으로 2회 재현됐다. 다만 실제 100% 재현되는 동시성 결함 1건(관측 채널 한정)과 잠재적 예외-치환 결함 1건을 포함해 아래 fast-follow 6건을 조건으로 건다 — 어느 것도 병합 자체를 막을 이유는 아니다.

---

## 사용자 지정 4개 핵심 질문 — 요약 답변

| # | 질문 | 답 |
|---|---|---|
| 1 | resume_turn 버그가 실제로 고쳐졌나 | **예.** `core/provenance_lifecycle.py:309`이 `_complete_observation()` 멤버십 검사로 교체됨(엄격 `is COMPLETE` 폐기). 프로브 2건(원 검토·독립 재검증)이 COMPLETE_WITH_EXCLUSIONS 성공·candidate_paths 등록·strict-COMPLETE 회귀없음·genuine-incomplete 거부 유지를 전부 실측 확인(§가설 A). |
| 2 | FIX-2 원자 전이가 다중 에이전트 경합에서 owning transaction을 보장하나 | **아니오 — 문자 그대로는 보장하지 않는다.** 실제 별도 OS 프로세스 2개로 100% 재현되는 TOCTOU 경합이 존재(§가설 A는 아니고 §가설 B). 단, 파급 범위는 ledger.json(게이트가 읽는 상태)이 아니라 coordination journal(관측 전용)에 한정 — 게이트 오판정 없음. 또한 이 결함을 만든 코드는 "FIX-2"가 아니라 "SCORE-1"(`ed7a24a`)이 도입한 것으로 귀속이 재확인됨. |
| 3 | coordination writer 락·fsync·malformed 관용 | **PASS.** 크로스프로세스 락(파일 기반, PID 생존확인)을 실제 10/24개 별도 프로세스로 부하 테스트해 손실 0·완전성 유지 확인. malformed 라인은 격리·완전성 강등(0 복구 없음) 확인. 부수 발견: `CoordinationSchemaError`(frozen dataclass+slots+ValueError 상속)가 `ledger_transaction`(contextmanager) 안에서 raise되면 `TypeError`로 치환되는 Python 함정 — 3중 독립 재현(§가설 C). 현재는 broad `except Exception`에 가려져 프로덕션 무영향. |
| 4 | CLI view 3종 출력 실측 | **완료.** `sessions`(기본, 바이트 동일성 확인)·`agents`·`coordination` 3종을 human/json 각 실제 실행·인용. 필드 정확성 확인. 시간창(`--days`) 필터가 `entered`/`recovered` 쌍을 독립적으로 잘라 "복구는 보이는데 원인 사고는 창 밖"인 감사 해석 리스크 1건 발견(§Findings T1). |

---

## 근본원인 분석 (조사 프로토콜 — 가설/증거/기각)

### 가설 A: "resume_turn 엄격비교 버그가 고쳐지지 않았거나 새 회귀를 만들었다" → **기각**

**증거**: `core/provenance_lifecycle.py:309` `if _complete_observation(result.status) and not result.incomplete:` (구 `result.status is ProvenanceStatus.COMPLETE` 대체, `_complete_observation`=142-146행 `{COMPLETE, COMPLETE_WITH_EXCLUSIONS}` 멤버십). 독립 프로브 2회(원 검토·적대 재검증)가 각각 15개 체크(COMPLETE_WITH_EXCLUSIONS/incomplete=False→no-raise, strict COMPLETE 대조군→no-raise, COMPLETE_WITH_EXCLUSIONS/incomplete=True→raise, genuine INCOMPLETE→raise, 신규 SCOPE_TOO_LARGE·UNSUPPORTED 대조군→raise)를 전부 PASS로 재현. 실제 어댑터 경로(`begin_invocation`)로 candidate_paths 등록(`invocations['edit-1']['candidate_paths']==['app.py']`)까지 실측. `tests/test_provenance_lifecycle.py`+`tests/test_multiagent_f3_observation.py` 25개 전체 pytest PASS(2회 독립 실행 동일 수치).

**기각 근거**: "회귀됐다"는 대안가설은 기존 strict-COMPLETE 성공 경로 대조군(A2/B2)과 순수 미완료 거부 경로(A4)가 여전히 정상 동작함을 실측이 직접 반증했다. 유일하게 확정 짓지 못한 인접 발견은 `core/provenance_lifecycle.py:335`(`post_tool`)이 여전히 동일한 엄격 비교 패턴을 쓴다는 것이나, 이는 이번 3커밋 diff에 포함되지 않은 **기존(pre-existing) 상태**이며 관련 테스트가 이미 "PostToolUse 미기록에도 Stop은 정상 allow"를 의도적으로 검증하고 있어 확정 결함이 아니라 PLAUSIBLE 수준의 후속 검토 대상으로만 분류한다.

### 가설 B: "FIX-2/SCORE-1의 turn_bootstrap 원자 전이가 다중 프로세스 경합에서 실제로 깨진다" → **부분 확증 (관측 채널 한정)**

**증거**: `core/adapter_observation.py:121` `bootstrap_recovery = _baseline_missing(root, invocation)`이 락 없이 계산되고, 실제 owning transaction은 `core/ledger.py:124-138`(`record_event` 내부)에서만 성립하며, coordination 기록은 `core/ledger.py:139-140`(락 밖) → `core/scorecard_coordination.py:270`(별개의 두 번째 트랜잭션)에서 일어난다 — "한 owning transaction"(스펙 `tmp/impl-spec-s2-20260719.md:16`)이 아니라 세 개로 쪼개진 구조. 두 독립 프로브가 **실제 별도 OS 프로세스** 2개로 동일 (agent, turn_id)를 경합시켜 재현: barrier 모드 3/3, **자연 발생 모드(인위적 배리어 없이 두 프로세스 시작 시점만 겹침) 4/4 — 100% 재현**. 결과: `ledger.json`은 항상 손상 없이 수렴(event_seq 정합, 양쪽 invocation 모두 durable)하지만, `coordination.jsonl`에는 "recovered" 항목이 매번 **2건**(정상은 1건) 기록됨 — 원인은 `stable_coordination_event_id`(`core/scorecard_coordination.py:293-313`)의 해시 입력에 `evidence_refs`(=invocation_id, `core/ledger.py:180-182`에서 호출마다 고유)가 포함돼 dedup이 무력화되기 때문.

**기각한 대안**: "이 중복이 게이트 오판정(Stop 등)을 유발한다" — grep 전수 확인(Stop/N1/R1/R2 경로 어디에도 `coordination` 문자열 매치 0건) + 실측(ledger.json 자체는 항상 단일 값으로 수렴)으로 기각. "이것이 FIX-2(`68adec9`) 커밋의 결함이다" — `git show`로 세 커밋 diff를 대조한 결과 `bootstrap_recovery`/`_baseline_missing`는 FIX-2가 아니라 SCORE-1(`ed7a24a`)에서 신규 도입된 코드로 확인, 귀속 정정.

### 가설 C: "coordination 스키마의 예외 계약이 어딘가에서 깨질 수 있다" → **확증 (현재는 무해)**

**증거**: `CoordinationSchemaError`(`core/scorecard_coordination.py:82-88`, `@dataclass(frozen=True, slots=True)` + `ValueError` 상속)가 `record_coordination_event`(`:276-278`, `ledger_transaction` contextmanager 내부)에서 raise되면 `TypeError: super(type, obj): obj must be an instance or subtype of type`로 치환된다. **3중 독립 재현**: ①원 검토 에이전트 ②적대 재검증 에이전트(순수 stdlib 최소 재현 + 실제 레포 코드 직접 호출 두 경로) ③오케스트레이터 본인(최소 stdlib 재현, 동일 에러 문구 확인).

**기각한 대안**: "이것이 실제 프로덕션 동작을 깨뜨린다" — grep 전수 확인 결과 `record_coordination_event`의 유일한 실사용 호출부는 `try_record_coordination_event`(`:288`, `except Exception`으로 모든 예외 흡수) 하나뿐이라 현재는 무해함을 확인해 기각. 다만 `except CoordinationSchemaError`로 좁혀 잡으려는 향후 코드에는 조용한 지뢰이며, 이 경로를 검증하는 테스트가 현재 0건이라는 점에서 test-gap으로 별도 기록.

---

## Findings 전체 목록 (분류·심각도순)

| # | 분류 | 심각도 | 내용 | 근거(파일:라인) |
|---|---|---|---|---|
| F1 | concurrency | **Low(파급은 관측 채널 한정) / 재현율 100%** | `_baseline_missing`(락 밖 사전판독)→`resume_turn`의 락 없는 `save_turn_baseline`→별개 트랜잭션 2개(ledger/coordination)로 쪼개진 구조. 동일 turn 동시 recovery 시 coordination journal에 "recovered" 중복 기록(1건이어야 할 것이 2건). ledger.json 자체는 무손상. | `core/adapter_observation.py:121`, `core/provenance_lifecycle.py:316`(`save_turn_baseline`, 락 없음), `core/ledger.py:124-140`, `core/scorecard_coordination.py:270,293-313`, `core/ledger.py:180-182`(evidence_refs=invocation_id) |
| F2 | spec-vs-impl | **Medium(문서-구현 불일치, 실무 리스크 낮음)** | 스펙(`tmp/impl-spec-s2-20260719.md:16`)이 요구한 "baseline 저장+ledger 전이를 한 owning transaction으로 묶는다"는 어느 커밋에서도 구현되지 않음. F1의 근본 원인. | 상동 |
| F3 | attribution | Low | F1/F2를 만든 코드는 "FIX-2"(`68adec9`)가 아니라 "SCORE-1"(`ed7a24a`)에서 신규 도입 — 후속 티켓 귀속 정정 필요. | `git show 68adec9`/`ed7a24a` diff 대조 |
| F4 | correctness (latent) | **Low(현재 무해, broad except로 가려짐)** | `CoordinationSchemaError`(frozen+slots+ValueError)가 `ledger_transaction` contextmanager 안에서 raise 시 `TypeError`로 치환됨. 3중 독립 재현(§가설 C). | `core/scorecard_coordination.py:82-88,276-278,284-290` |
| F5 | test-gap | Low-Medium | F1(동시 recovery 시 turn당 1건 불변식)을 검증하는 회귀 테스트 0건. F4(event_id 충돌 경로) 검증 테스트도 0건. | `tests/test_scorecard_coordination.py` 전수 grep |
| F6 | test-gap | Medium | `--view agents\|coordination`이 실제 argparse/subprocess CLI 경계(`test_scorecard_cli.py`류)에서 **0% 커버** — 오직 손으로 만든 `argparse.Namespace`로 `run_scorecard()`를 직접 호출하는 방식으로만 테스트됨. human 포맷(`_human_agents`/`_human_coordination`) 호출도 테스트 0건. | `tests/test_scorecard_coordination.py`의 `_args()` 헬퍼(모든 호출 `json_output=True`), `fable_lite/scorecard.py:415-451` |
| T1 | audit-risk (design) | Medium | `--days`/기간 필터가 `entered`/`recovered` 이벤트 쌍을 **독립적으로** 잘라, 좁은 시간창에서 "recovered만 보이고 entered는 창 밖"인 상태가 실측으로 재현됨 — 운영자가 "깨끗하게 복구됐다"고 오인할 감사 해석 리스크(카운팅 버그는 아님). | `fable_lite/scorecard.py:396-404`(필터), 실측 프로브 시나리오(10일 전 entered·1시간 전 recovered, `--days 7` 시 entered만 사라짐) |
| F7 | test-gap | Low | malformed 관용이 JSON 구문 오류만 커버, 유효 JSON+스키마 위반(`reason_code` 필드 누락 등) 분기 미커버. | `tests/test_scorecard_coordination.py:101-116` |
| F8 | coverage-gap (review 자체) | Medium(검증 단계에서 즉시 보완됨) | 회귀 검토 1차 패스가 `core/ledger.py`(diff 111줄, 2번째로 큰 변경 파일)를 전혀 언급하지 않음 — 실제로는 안전(락 재진입 회피 확인됨: `_record_coordination_after_event` 호출이 `with ledger_transaction` 블록 **밖**에 위치해 자기 자신과의 데드락을 피함)하나 "전수 확인" 주장과 불일치했던 것을 적대 재검증 단계가 발견·보완. | `core/ledger.py:139-140`(들여쓰기 확인) |
| F9 | process-integrity (tooling) | Low | 전체 게이트 재실행(`python eval/run_probes.py --strict`) 시 tracked 파일 `eval/results/probes-latest.json`이 매번 타임스탬프만 바뀌며 dirty해짐 — "게이트 재실행 후 tracked 변경 없음" 주장은 이 파일의 존재상 원천적으로 재현 불가능(구현자 summary.md의 "tracked worktree: clean" 주장도 동일하게 재검토 필요). 소스 로직 결함 아님, gitignore 대상으로 옮기거나 `--output`을 스크래치 경로로 지정하는 워크플로 보정 권고. | `eval/run_probes.py:17`(`DEFAULT_OUTPUT`), `README.ko.md:151-157` |
| Info-1 | 정보성(과제 범위 밖) | — | 이 세션 자체의 R1 게이트(`core/risk_terms.py`)가 bare word `\bschema\b`를 고위험으로 플래그 — "schema-version"(하이픈)은 걸리고 "schema_version"(언더스코어)은 안 걸리는 정확한 경계조건을 2개 하위 에이전트가 독립적으로 확인. 감사 대상 코드 결함 아님, 하네스 자체(main) 오탐 참고용. | `core/risk_terms.py:6-13`, `core/contract.py:210-224,290-313` |

**FIX-3(Windows 장경로) 관련**: 코드(`adapters/claude_code/atomic_file.py:13-55`, 260자 경계·UNC 처리·예외 비삼킴)는 파일:라인으로 확인 완료, `tests/test_atomic_file_windows.py` 4건 PASS. **주의**: 이 서버는 `LongPathsEnabled=1`이라 259/260/262 경계 테스트 통과 자체는 FIX-3의 효과를 독립 증명하지 못함(수정 전 로직도 이 서버에서는 통과) — 실효성은 `LongPathsEnabled=0`인 영진 서버 기록으로만 확증됨. 이 환경차는 실측(레지스트리 조회 2회 독립 확인)으로 명시했다. 회귀 없음.

---

## 전체 게이트 재현 (이 서버, worker/s2-fix-score HEAD `daa47b5`, 기본 temp, 우회 없음)

| 게이트 | 이 서버 실측(2회 독립) | 구현자 보고(summary.md) | 일치 |
|---|---|---|---|
| `python -m pytest tests -q` | **704 passed** (2회 동일) | 704 passed | 일치 |
| `python eval/run_probes.py --strict` | pass=17 fail=0 manual=3 total=20 result=PASS | 17/0/3 PASS | 일치 |
| `python eval/e2e_smoke.py` | 16/16 | 16/16 | 일치 |

(부가 관찰, 결함 아님: `tests/test_stop_hook_active_conformance.py`의 백그라운드 reader 스레드에서 간헐적 `UnicodeDecodeError`→`PytestUnhandledThreadExceptionWarning` 발생 — pass/fail 판정에는 무영향, FIX-3가 고친 것과 같은 계열의 인코딩 이슈가 이 파일에는 남아있음을 시사.)

---

## Fast-follow 권고 (우선순위)

1. **F1/F2/F5** — coordination journal의 "recovered는 turn당 1건" 불변식을 실제로 보장하도록 dedup 키에서 `evidence_refs`(invocation_id)를 제외하거나 별도 처리, 동시성 회귀 테스트 추가(현재 0건). 티켓 귀속은 **SCORE-1(`ed7a24a`)**로(F3).
2. **F4/F5** — `CoordinationSchemaError`에서 `slots=True` 제거(가장 간단) 또는 event_id 충돌 경로 회귀 테스트 추가.
3. **F6** — `--view agents|coordination`을 실제 argparse/subprocess CLI 경계에서 최소 1건씩 실행하는 테스트 추가(현재 손으로 만든 Namespace로만 테스트됨), human 포맷 렌더 테스트 추가.
4. **T1** — `--days`/`--session` 필터가 `entered`/`recovered` 쌍에 적용될 때 "짝이 창 밖에 있다"는 사실을 CLI 출력에 표시하거나, 최소 human/json 문서에 이 한계를 명시.
5. **F7** — malformed 관용 테스트에 "유효 JSON+스키마 위반"(reason_code 누락 등) 케이스 추가.
6. **F9** — `eval/run_probes.py`의 `DEFAULT_OUTPUT`을 gitignore 대상 경로로 옮기거나 게이트 재실행 워크플로에 `--output <scratch>`를 표준화.

이 중 병합을 막아야 할 항목은 없다 — 전부 후속 트랜잭션/테스트 보강 성격이며, F1의 유일한 관측 표면(coordination journal)은 fail-open·비게이트 채널임이 실측으로 재확인됐다.

---

## 감사 절차 무결성

- 6개 차원 × review/verify = 12개 에이전트 전원 완료(에러 0). 판정: FIX-1=조건부승인, FIX-2 동시성=조건부승인(F1/F2/F3 조건), SCORE-1=승인, 회귀=조건부승인(F8로 하향, 실제 회귀는 미발견), CLI+테스트갭=조건부승인(F6/T1 조건), FIX-3+게이트=조건부승인(F9 조건).
- 모든 프로브는 `tempfile.mkdtemp()` 격리 root + `fable-lite-s2/tmp/`에서만 생성, 실행 후 삭제 확인(각 차원 보고서에 `ls`/`glob` 재확인 근거 포함). `fable-lite-s2` `git status --porcelain`은 세션 전 구간 대부분 clean이었고, 유일한 예외(F9, `eval/results/probes-latest.json`)는 게이트 자체의 정상 동작 부산물로 소스 미변경.
- `fable-lite`(main, 검토 대상 아님)의 `.codex/config.toml`/`README.md`/`README.ko.md` 변경은 이 세션이 Edit/Write를 호출한 적이 없음을 6개 차원 전원이 각자 재확인 — 세션 시작 전부터 존재했거나 공유 워크스페이스의 동시 작업자(다른 pane)에 의한 변경으로 판단, 되돌리지 않고 그대로 둠(지시 준수).
- 타 작업자 소유의 `fable-lite-s2/tmp/` 잔여 파일(scorecard_diff.txt, audit-*-bytecompare-*.py 등)은 전 차원에 걸쳐 손대지 않음.
- 하우스키핑 참고: 회귀 검토(D4) 담당 에이전트가 만든 스크래치 파일 2개(`fable-lite-s2/tmp/audit-s2-scorecard-bytecompare-20260719.py`, `scorecard_diff.txt`)는 이 세션의 R1 게이트가 `rm`/`Remove-Item` 삭제 명령 자체를 고위험으로 차단해 정리하지 못한 채 남아있음(소스 아님, tracked 아님) — 필요 시 사용자가 직접 삭제 바람.
