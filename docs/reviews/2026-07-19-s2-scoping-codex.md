# v2.2.0 이후 잔여 백로그 스코핑 보고서

> 범위: 기준 `main b81af04`의 조사·설계만 수행했으며, 프로젝트 코드는 수정하지 않았다. 과제 범위와 산출물 요구는 `C:/Users/gustj/exchange/to-yeongjin/20260719-scoping/brief.md:3`, `C:/Users/gustj/exchange/to-yeongjin/20260719-scoping/brief.md:37-44`를 따른다.

## 결론 요약

1. 현 Scorecard는 6개 기존 게이트 reason과 `block/recover/cap_allow`만 모델링한다. R2 파기 차단, peer 충돌·제외, attribution degraded/capacity, owner settlement, invocation lease, `turn_not_started` 자체, quick 승격은 전용 항목이 없다 (`core/scorecard.py:24-48`, `core/scorecard.py:78-87`).
2. 같은 루트의 에이전트별 행은 이미 존재하지만, 루트 합계·에이전트 비교 행렬·actor↔peer 상호작용은 없다. CLI는 journal을 `agent_key`로만 그룹화한 뒤 평면 `sessions`를 출력한다 (`fable_lite/scorecard.py:41-46`, `fable_lite/scorecard.py:61-76`). 따라서 “루트 단위 교차 뷰”는 새 SSOT가 아니라 기존 root-local journal의 파생 뷰로 추가하는 것이 타당하다.
3. `turn_not_started`의 full-bootstrap primitive와 lifecycle 테스트는 존재한다. 그러나 성공한 PreTool bootstrap 직후 ledger에는 `baseline_status=ready`만 기록되고 기존 `provenance_incomplete/status_reason`은 즉시 지워지지 않는다; 후속 PostTool observation이 와야 complete로 갱신될 수 있다 (`core/adapter_observation.py:121-158`, `core/adapter_observation.py:393-409`, `core/ledger_v2.py:503-524`, `core/adapter_observation.py:189-191`). 설계 문구의 “bootstrap 성공 시 complete 회복”을 직접 고정하는 adapter→ledger 종단 테스트도 현재 테스트군에서는 확인되지 않았다; 현재 전용 F3 테스트는 실패 지속/KeyError 비연쇄만 고정한다 (`tests/test_multiagent_f3_observation.py:173-248`).
4. 즉시 진행 가치가 가장 큰 비외부 작업은 (a) `turn_not_started` 성공 회복의 원자적 상태 갱신+종단 테스트, (b) README의 프로젝트-scope 설치 절차, (c) Scorecard coordination journal/루트 교차 뷰 설계·구현이다 (`docs/design/multiagent-gate.md:91`, `docs/specs/v2.2-quiet-optin.md:75-78`).

---

## 과제 1 — v-next Scorecard 확장 갭과 설계 입력

### 1.1 현재 집계 모델

현 도메인 모델의 집계 축은 아래뿐이다.

| 축 | 현 구현 | 근거 |
|---|---|---|
| identity | `(host, session_id, agent)`; 문자열 key도 세 값의 결합 | `core/scorecard.py:67-74` |
| reason | provenance incomplete, investigation markers, verification missing, goals missing, intent missing, contract missing의 6종 | `core/scorecard.py:24-30` |
| action | `block`, `recover`, `cap_allow` 3종 | `core/scorecard.py:34-37` |
| resolution | verification/observation/markers/goals/intent/contract/none | `core/scorecard.py:41-48` |
| totals | blocked attempts, recovered scopes, resolved attempts, cap allows, unresolved block ids | `core/scorecard.py:109-123` |
| verification | agent JSONL의 `event=verification` 성공/실패 수만 별도 replay | `fable_lite/scorecard_observations.py:18-33`, `fable_lite/scorecard_observations.py:66-75` |

Scorecard journal은 `.fable-lite/scorecard/gates.jsonl` 한 파일이며, event payload도 위 identity/reason/action/resolves/resolution/attribution/time으로 제한된다 (`core/scorecard_store.py:46-47`, `core/scorecard_store.py:61-80`). CLI는 reason별로도 동일한 네 수치만 렌더한다 (`fable_lite/scorecard.py:164-172`, `fable_lite/scorecard.py:232-240`).

### 1.2 신규 게이트·상태별 현재 귀착과 누락

| 신규 계열 | 현재 Scorecard 귀착 | 갭 판정 |
|---|---|---|
| R2 destructive deny | R2 core는 `block/allow` decision만 반환하고 Claude PreTool은 deny 응답으로 즉시 종료한다 (`core/destructive_guard.py:552-564`, `core/destructive_guard.py:567-623`, `adapters/claude_code/pre_tool_use.py:155-171`). | **완전 누락.** R2 category(`git_destructive/os_remove/truncate_redirect`)와 실제 deny reason(`peer_unsettled_revision`, `ledger_degraded`, parse 불능 등)이 Scorecard에 기록되지 않는다 (`core/destructive_guard.py:57-59`, `core/destructive_guard.py:582-622`). |
| peer exclusion/contended 4지점 | attribution 판정은 `peer/contended`를 반환하고, contended는 conservative하게 유지된다 (`core/verification_covers.py:191-215`, `core/verification_covers.py:230-236`). | **완전 누락.** peer 때문에 제외된 변경 수, contended 경로/턴, late-peer 재평가 결과를 Scorecard reason/action으로 표현할 수 없다 (`core/scorecard.py:24-48`). |
| owner settlement | owner revision은 `settled=False`로 생성되고 verification 또는 대체 digest로 settlement된다 (`core/ledger_v2.py:255-317`, `core/ledger_v2.py:321-354`). | **완전 누락.** settlement 성공/지연/overflow를 별도 운영 지표로 보존하지 않는다 (`core/scorecard.py:109-123`). |
| attribution degraded/capacity | health는 `degraded`와 `capacity_exceeded`를 노출하고 R2는 둘 중 하나면 fail-closed 한다 (`core/ledger_v2.py:60-64`, `core/destructive_guard.py:588-598`). | Stop mutation 경로에서는 넓은 `stop.provenance_incomplete`로만 보일 수 있고, R2 deny 자체는 기록되지 않는다 (`core/verify_state.py:325-338`, `core/scorecard.py:24-30`). **원인 분해 누락.** |
| `turn_not_started` | read-only이면 안내 allow, mutation-capable이면 일반 provenance-incomplete block으로 귀착한다 (`core/verify_state.py:295-306`, `core/verify_state.py:325-338`). | **상태 자체 누락.** read-only allow는 transition이 없고 mutation block도 `stop.provenance_incomplete`에 섞인다 (`core/scorecard.py:24-30`). |
| peer activity/exclusion | unstable/unreadable issue가 신뢰 가능한 open peer candidate와 맞으면 exclusion으로 조정한다 (`core/provenance_lifecycle.py:68-107`). | **완전 누락.** “오차단 회피”라는 도구 가치가 Scorecard에 나타나지 않는다 (`core/scorecard.py:109-123`). |
| invocation window/lease | open peer invocation은 30분 lease 안에서만 candidate 근거가 된다 (`core/ledger_v2.py:21`, `core/ledger_v2.py:67-105`). | **완전 누락.** forced close/lease expiry/pre-attribution R2 deny 수가 없다 (`core/scorecard.py:24-48`). |
| 이중근거 교차 | peer owner는 해당 peer audit JSONL의 seq/digest/generation 일치가 있어야 인정된다 (`core/verification_covers.py:241-296`). | **완전 누락.** cross-evidence reject/accept/incomplete 수가 없다 (`core/scorecard.py:109-123`). |
| quick→normal 승격 | session registry 파일을 lock 안에서 `quick`에서 `normal`로 atomic write하고, mutation/unknown PreTool이 승격을 호출한다 (`adapters/claude_code/session_registry.py:143-169`, `adapters/claude_code/pre_tool_use.py:173-187`). | **완전 누락.** 승격 성공/지속화 실패 deny는 Scorecard에 없다 (`core/scorecard.py:24-48`). |
| quiet opt-in 비활성 | 비활성/홈/quick read-only Stop은 `{}`로 끝나며 project state를 만들지 않는 것이 스펙이다 (`docs/specs/v2.2-quiet-optin.md:7-18`, `docs/specs/v2.2-quiet-optin.md:67-72`). | **누락이 아니라 의도된 N/A.** 비활성을 “0점/무차단” 세션으로 생성하면 무상태 계약을 깨므로 기록하지 않아야 한다. |
| Scorecard 표시 opt-in | Claude Stop은 `FABLE_LITE_SCORECARD=1`일 때만 core message의 Scorecard 줄을 추출하고, 그 외 allow는 `{}`다 (`adapters/claude_code/stop.py:66-84`). | 저장은 유지되지만, cross-root/peer 성적을 Stop에 자동 노출할 표면은 없다. 이는 quiet 정책상 유지하는 편이 맞다 (`docs/specs/v2.2-quiet-optin.md:34-42`). |

### 1.3 루트 단위 교차 뷰 필요성

**판정: 필요하다. 단, “새 점수”가 아니라 root-local operational view여야 한다.** 기존 CLI도 선택한 `--root` 아래 journal과 agent logs를 읽고 agent별 행을 만들므로 에이전트 병렬 표시는 부분적으로 이미 된다 (`fable_lite/scorecard.py:32-37`, `fable_lite/scorecard.py:41-57`). 그러나 결과는 평면 session rows이고 root totals/interactions가 없으며, `--session`도 session_id 문자열 필터일 뿐 actor-peer 관계를 조립하지 않는다 (`fable_lite/scorecard.py:61-76`, `fable_lite/scorecard.py:185-191`). R2 차단과 peer 충돌은 본질적으로 두 identity의 관계이므로 현재 단일 identity transition만으로는 “누가 누구의 미정산 변경 때문에 막혔는가”를 안전하게 표현할 수 없다 (`core/scorecard.py:78-97`, `core/destructive_guard.py:619-622`).

### 1.4 제안 스키마

기존 `gates.jsonl` schema v1은 그대로 두고, 비-gate 상태를 섞지 않는 별도 append-only journal을 제안한다. 분리는 기존 설계가 gate journal을 ledger/agent replay와 독립 진화시킨 이유와 일치한다 (`docs/design/session-scorecard.md:34-60`).

제안 경로: `.fable-lite/scorecard/coordination.jsonl`.

```json
{
  "scorecard_coord_schema_version": 1,
  "event": "coordination_transition",
  "event_id": "uuid",
  "actor": {"host": "...", "session_id": "...", "agent": "..."},
  "actor_turn_id": "...",
  "subject_agent_key": "...|null",
  "category": "r2_deny|peer_exclusion|peer_conflict|owner_settlement|attribution_health|turn_bootstrap|invocation_lease|quick_promotion|cross_evidence",
  "outcome": "blocked|avoided_block|entered|recovered|settled|expired|rejected|degraded",
  "reason_code": "closed enum",
  "evidence_refs": ["change:event-id", "invocation:id"],
  "attribution": "exact",
  "occurred_at": "UTC ISO-8601"
}
```

설계 규칙은 다음과 같다.

- `actor`는 adapter가 만든 canonical invocation에서만 채우고, caller payload가 임의의 `subject_agent_key`를 제공하지 못하게 한다. 현 transition 생성기는 payload의 identity 문자열을 필수로 읽으므로, coordination writer는 각 gate/ledger 판정 지점에서 typed argument로 받는 별도 primitive가 필요하다 (`core/scorecard_store.py:50-80`).
- R2는 **deny만** 기록하고 routine allow는 기록하지 않는다. 기존 Scorecard도 routine allow를 journal에 넣지 않는 의미 규칙을 채택한다 (`docs/design/session-scorecard.md:54-60`).
- `peer_exclusion`은 단순 시간 겹침이 아니라 open invocation candidate+audit cross-evidence가 통과한 경우만 `avoided_block`로 기록한다. 현재 완화 자체가 ledger health와 candidate 일치를 요구한다 (`core/provenance_lifecycle.py:83-107`, `core/verification_covers.py:241-296`).
- `turn_bootstrap`은 `entered(turn_not_started)`와 `recovered(complete)`를 같은 actor/turn에서 명시적으로 닫아야 한다. recovery event는 ready baseline과 `provenance_incomplete=false/status=complete/reason=""`가 한 ledger transaction에 들어간 뒤에만 append한다 (`core/ledger_v2.py:503-524`).
- `quick_promotion`은 성공과 persistence-deny만 기록하고, quick read-only/비활성은 기록하지 않는다. 그래야 비활성 무상태와 quick read-only 0-write 수용기준을 보존한다 (`docs/specs/v2.2-quiet-optin.md:67-72`).
- privacy상 path/prompt/command/message는 금지하고 category/count/evidence id만 둔다. 기존 Scorecard도 렌더에 경로·파일명·메시지를 금지한다 (`docs/design/session-scorecard.md:104-108`).

### 1.5 CLI/표시안

기존 명령을 보존하고 아래 옵션을 추가한다. 기존 옵션 계약은 `--root/--session/--days/--all/--json`이다 (`fable_lite/scorecard.py:26-38`).

```text
python -m fable_lite scorecard --view sessions       # 기존 기본값
python -m fable_lite scorecard --view agents         # root 내 actor별 병렬 비교
python -m fable_lite scorecard --view coordination   # actor↔subject 교차표
python -m fable_lite scorecard --view root --json    # root totals + completeness
```

- `agents`: agent별 gate block/recover/cap, R2 deny, avoided peer block, `turn_not_started entered/recovered`, degraded time을 같은 열로 병렬 표시한다. 현재 human renderer가 identity별 순차 문단이라 비교가 어렵다는 점을 보완한다 (`fable_lite/scorecard.py:204-242`).
- `coordination`: `actor`, `subject`, `peer_conflicts`, `r2_denies`, `settlements`, `cross_evidence_rejects`만 표시하고 path는 표시하지 않는다 (`docs/design/session-scorecard.md:104-108`).
- `root`: totals 외에 `complete`, journal별 replay completeness, 관측 기간, distinct actors를 노출한다. 현 CLI가 malformed journal/agent log를 전체 `complete=false`로 내리는 보수성을 유지한다 (`fable_lite/scorecard.py:43-60`).
- Stop 자동 메시지는 **현재 actor의 session-only 줄만** 유지한다. root/peer 비교는 명시적 CLI 호출로만 제공하며 Claude의 `FABLE_LITE_SCORECARD=1` 정책을 넓히지 않는다 (`adapters/claude_code/stop.py:75-84`, `docs/specs/v2.2-quiet-optin.md:34-42`).

### 1.6 안티게이밍/오염 방지

1. **성적 세탁 방지:** CLI는 bounded cache가 아니라 두 journal을 replay해 정본을 재구축하고, offset 축소·중간 malformed·event-id 충돌은 0으로 복구하지 말고 `complete=false`로 표시한다. 현 gate journal replay도 malformed를 건너뛰되 completeness를 false로 내린다 (`core/scorecard_store.py:101-120`).
2. **타 에이전트 성적 오염 방지:** peer 관련 count는 actor 단독 주장으로 만들지 말고 ledger attribution + peer audit JSONL의 seq/digest/generation 교차가 모두 맞을 때만 exact로 인정한다 (`core/verification_covers.py:241-296`). 불일치 이벤트는 peer에게 “실패”를 부과하지 않고 root-level `cross_evidence_rejected`로만 센다.
3. **비교 게이밍 방지:** raw count 순위/등급은 금지하고 exposure denominator(`turns_observed`, `destructive_attempts`, `peer_candidates_seen`)를 함께 보여준다. 기존 Scorecard도 등급화를 비스코프로 두었다 (`docs/design/session-scorecard.md:138-140`).
4. **identity 세탁 방지:** `legacy_default`/synthetic identity와 exact identity를 합치지 않는다. 현 CLI도 legacy attribution을 `unattributed`로 분리한다 (`fable_lite/scorecard.py:131-145`).
5. **상태 파일 공격의 한계 공개:** 같은 OS 계정의 강한 적대는 완전 차단할 수 없다는 현 설계 한계를 그대로 명시하고, cryptographic integrity를 주장하지 않는다 (`docs/design/multiagent-gate.md:20`). state journal 삭제/변조 의심은 “좋은 점수”가 아니라 incomplete가 되어야 한다.
6. **이중 기록/인플레이션 방지:** event_id idempotency와 actor-turn-category-outcome의 bounded dedup을 적용한다. 현 aggregate도 동일 event_id 중복은 제거하고 내용이 다르면 schema error를 낸다 (`core/scorecard.py:168-184`).

---

## 과제 2 — `turn_not_started` 잔여 갭

### 2.1 상태 생성과 보존

턴 생성 시 baseline이 unavailable이거나 provenance가 incomplete면 `baseline_status=missing`과 `provenance_status_reason=turn_not_started`를 기록한다 (`core/ledger_v2.py:464-490`). 이후 observation reason이 비거나 `observation_error`여도 baseline이 missing인 동안 reason을 `turn_not_started`로 유지해 KeyError 연쇄 대신 명시 상태를 보존한다 (`core/ledger_v2.py:493-524`). 실패하는 후속 hook이 같은 상태를 유지하고 `KeyError`로 퇴행하지 않는 테스트가 있다 (`tests/test_multiagent_f3_observation.py:173-248`).

### 2.2 “후속 full bootstrap 성공 시 complete 회복” 구현 여부

**판정: lifecycle primitive는 구현·테스트됐지만, adapter→ledger의 즉시 complete 회복은 불완전하다.**

- PreTool `begin_invocation()`은 `resume_turn(... allow_full_bootstrap=True)`를 호출한다 (`core/adapter_observation.py:113-139`).
- missing baseline이면 lifecycle은 현재 snapshot으로 baseline을 복원하거나, 현재 snapshot도 없으면 full `start_turn`을 수행하고 complete일 때 복귀한다 (`core/provenance_lifecycle.py:289-327`).
- workspace store failure 뒤 full bootstrap이 workspace current와 turn baseline을 재생성하는 unit test가 있다 (`tests/test_provenance_lifecycle.py:229-251`).
- 성공 뒤 adapter는 invocation event에 `baseline_status=ready`와 `baseline_snapshot_id`를 기록한다 (`core/adapter_observation.py:393-409`).

그러나 invocation event에는 `provenance_incomplete=false`, `provenance_status=complete`, 빈 `provenance_status_reason`이 없다 (`core/adapter_observation.py:393-409`). ledger reducer는 payload에 들어온 필드만 갱신하므로 ready로 바뀐 직후에도 과거 incomplete/reason이 남을 수 있다 (`core/ledger_v2.py:503-524`). 실제 complete fields는 PostTool/finish의 `_record_status()`에서 기록된다 (`core/adapter_observation.py:189-191`, `core/adapter_observation.py:440-463`). 또한 Claude PreTool은 `begin_invocation()` 반환 report를 사용하지 않는다 (`adapters/claude_code/pre_tool_use.py:189-214`).

따라서 권고는 성공한 full bootstrap의 baseline 저장과 ledger의 `ready/false/complete/""` 전이를 한 owning transaction으로 묶고, “start 실패 → prompt turn missing → 다음 mutation PreTool bootstrap 성공 → Stop이 provenance-incomplete로 막히지 않음”을 adapter 종단 테스트로 고정하는 것이다. 현재 F3 전용 테스트는 실패 bootstrap 보존만 검증한다 (`tests/test_multiagent_f3_observation.py:209-248`). 규모 **M**, 리스크 **중간**(snapshot/ledger 원자성 및 concurrent generation을 건드림).

### 2.3 발생 자체 감소와 retry/backoff

원 사건 문서는 hot write 중 두 번의 즉시 capture가 모두 흔들리면 `unstable_path`, OSError면 `unreadable_path`가 된다고 기록하고, retry/backoff를 본편으로 보류했다 (`docs/reviews/2026-07-16-observation-error-third-path.md:16-24`, `docs/reviews/2026-07-16-observation-error-third-path.md:45-49`). 현재 `capture_regular`도 `range(2)`의 즉시 재시도뿐이며 sleep/backoff가 없다 (`core/provenance_capture.py:35-62`).

본편에서 처리된 부분은 “기록된 peer open invocation candidate와 issue path가 일치하면 complete-with-exclusions로 조정”하는 경로다 (`core/provenance_lifecycle.py:68-130`). 따라서 4-pane 부팅 창의 **기록된 peer write**는 상당 부분 줄었지만, candidate로 잡히지 않는 dynamic write, 외부 프로세스, lease 만료, cross-evidence 불일치는 여전히 기존 observation error로 남는다; 설계도 dynamic bash 신규 파일의 pre-attribution 잔여 창을 알려진 한계로 둔다 (`docs/design/multiagent-gate.md:124-132`).

권고는 무조건 긴 backoff가 아니라 (1) issue path가 active peer candidate이면 짧은 jittered 재관측 1회, (2) candidate가 아니면 현 보수 경로 유지, (3) deadline budget 내에서만 수행, (4) `retry_count/wait_ms/outcome`을 coordination journal에 기록하는 것이다. hook hot path에 retry loop를 두지 말라는 기존 Scorecard I/O 원칙도 존중해야 한다 (`docs/design/session-scorecard.md:84-89`). 규모 **M**, 리스크 **중간**(latency와 TOCTOU).

### 2.4 Stop/Scorecard/관측성 노출

| 표면 | 현재 상태 | 판정 |
|---|---|---|
| core Stop, read-only | `turn_not_started` 이름과 “clean 주장 없음”을 포함한 actionable allow message | `core/verify_state.py:295-306` |
| core Stop, mutation-capable | 일반 provenance incomplete block이며 “재시도 가능한 관측 또는 검증”만 안내 | `core/verify_state.py:325-338` |
| Codex Stop | allow message를 `systemMessage`로 그대로 노출 | `adapters/codex_cli/stop.py:119-125` |
| Claude Stop | block은 노출하지만 allow message는 버리고, Scorecard opt-in 줄만 선택 노출 | `adapters/claude_code/stop.py:66-84` |
| Antigravity Stop | block reason만 노출하고 allow는 `{}` | `adapters/antigravity/hook_common.py:236-244` |
| ledger | baseline status, provenance status/reason, issue sample, rebase, error kind를 기록 가능 | `core/adapter_observation.py:440-463`, `core/ledger_v2.py:503-539` |
| Scorecard CLI | reason enum에 `turn_not_started`가 없고 일반 provenance-incomplete에 합쳐짐 | `core/scorecard.py:24-30`, `fable_lite/scorecard.py:164-172` |

**판정:** ledger 진단은 충분히 상세하지만 운영자 노출은 host별로 불일치하고 Scorecard에서는 실행 불가능하다. 최소 개선은 Scorecard에 `turn_bootstrap entered/recovered`, `last_error_kind`, `retryable` count를 추가하고, mutation block reason에 “다음 mutation PreTool에서 full bootstrap 재시도”를 명시하는 것이다. Claude/Antigravity의 quiet allow는 유지하되 CLI에서만 read-only `turn_not_started`를 보여주는 것이 quiet 정책과 양립한다 (`docs/specs/v2.2-quiet-optin.md:34-42`).

---

## 과제 3 — 외부 게이트 없이 진행 가능한 항목

### 3.1 우선순위 추천

| 우선 | 항목 | 효용 | 규모 | 리스크 | 근거/판정 |
|---|---|---:|:---:|:---:|---|
| P0 | `turn_not_started` 성공 bootstrap의 즉시 complete 전이 + adapter 종단 회귀 | 높음 | M | 중 | 설계는 complete 회복을 요구하지만 현재 성공 시 invocation event는 baseline ready만 기록한다 (`docs/design/multiagent-gate.md:91`, `core/adapter_observation.py:393-409`). |
| P0 | README에 Claude 프로젝트-scope 설치/해제/검증 절차 추가 | 높음 | S | 낮음 | v2.2 D는 완전 무비용용 project-scope 설치 경로를 README에 요구하지만, README 설치 절은 local marketplace/plugin install만 제공한다 (`docs/specs/v2.2-quiet-optin.md:75-78`, `README.md:122-150`, `README.ko.md:108-137`). |
| P1 | Scorecard coordination journal + `--view agents/root/coordination` | 높음 | L | 높음 | 현 CLI는 identity별 평면 sessions이고 R2/peer reason은 enum 밖이다 (`core/scorecard.py:24-48`, `fable_lite/scorecard.py:41-76`). |
| P1 | 관측의 peer-aware bounded retry/backoff + telemetry | 중~높음 | M | 중 | 보류 #3은 남았고 capture는 여전히 즉시 2회다 (`docs/reviews/2026-07-16-observation-error-third-path.md:45-49`, `core/provenance_capture.py:35-62`). |
| P1 | build artifact 기본 제외의 잔여 후보 결정/추가 | 높음(Next 등) | M | 높음 | 백로그는 `.next/dist/build/out/target/...`을 제안했으나 현재 기본값은 `.next/cache`, `.turbo`, `.nuxt`, `.svelte-kit` 등 일부만 포함한다 (`docs/reviews/2026-07-16-project-root-build-artifact-byte-limit.md:147-153`, `core/provenance_policy.py:24-38`). 소스 은닉 위험 때문에 whole `dist/build` 무조건 제외보다 framework cache별 whitelist가 안전하다. |
| P2 | CON-2 aging deep-tail 회귀 테스트 | 중 | S | 낮음 | 128+ distinct session 후 재등장 시 undercount가 확인됐고 현 테스트는 in-window만 커버한다고 리뷰가 명시한다 (`docs/reviews/session-scorecard-ultracode-r3.md:74-85`, `docs/reviews/session-scorecard-ultracode-r3.md:112-115`). |
| P2 | CON-2 aging 완전성 정책 결정(known limitation vs seen-set/bloom) | 중 | M | 중 | bounded 64 eviction history의 본질적 tail이며 unbounded set/bloom 대안은 ledger 성장 trade-off다 (`docs/reviews/session-scorecard-ultracode-r3.md:81-85`, `docs/reviews/session-scorecard-ultracode-r3.md:121-123`). |
| P2 | COR-1 자매 케이스: 전량 recover 뒤 동일 턴 재-cap 기록 | 중 | S~M | 낮음 | unresolved가 빈 상태의 재-cap은 여전히 drop되는 좁은 케이스다 (`docs/reviews/session-scorecard-ultracode-r2.md:42-46`, `docs/reviews/session-scorecard-ultracode-r2.md:174-178`). |
| P2 | PERF-2 recover/cap/second-block benchmark arms | 중 | M | 낮음 | 현재 gap은 스펙 위반이 아니라 대표성 hardening으로 평가됐다 (`docs/reviews/session-scorecard-ultracode.md:143-147`, `docs/reviews/session-scorecard-ultracode.md:252-257`). |
| P2 | REG-1 cap_allow dedup semantics 결정/구현 | 중 | M | 중 | 반복 cap_allow는 journal 성장·count inflation 우려가 있으나 현 스펙 위반 여부는 split이다 (`docs/reviews/session-scorecard-ultracode.md:27-28`, `docs/reviews/session-scorecard-ultracode.md:184-184`, `docs/reviews/session-scorecard-ultracode.md:256-257`). 먼저 “attempt마다” vs “gate/turn당 1회” 제품 결정을 고정해야 한다. |
| P2 | 성공 토큰 없는 custom script/value dump 인식 정책 | 중 | M | 중~높음 | 현재 보수적으로 unverified이며 과거 리뷰도 파생 backlog로 둔다 (`CHANGELOG.md:156-159`, `docs/reviews/v1.2-host-receipts.md:27`). exit code가 없는 host에서 false-positive unlock 위험이 있으므로 corpus 우선이다. |
| P3 | PERF-1 instrumentation을 `builtins.open/os.open`까지 확장 | 낮음 | S | 낮음 | 현 production reader는 `Path.read_text`라 live 결함은 아니고 미래 mock 견고성 backlog다 (`docs/reviews/session-scorecard-ultracode-r2.md:69`, `docs/reviews/session-scorecard-ultracode-r2.md:176-177`). |
| P3 | N1 natural-compliance 표본을 경쟁 원인 3개 이상 버그로 확장 | 낮음~중 | M | 낮음 | 현재 표본 3건만으로 일반화하지 말라는 후속이 남아 있다 (`docs/reviews/p5b-n1-natural.md:46`). |

### 3.2 v2.0.1 지정 잔여의 상태

| 지정 항목 | 현재 판정 | 근거 |
|---|---|---|
| CON-2 aging deep-tail | **진행 가능, P2.** 테스트 S; 완전 봉합 M | `docs/reviews/session-scorecard-ultracode-r3.md:81-85`, `docs/reviews/session-scorecard-ultracode-r3.md:112-123` |
| COR-1 자매 케이스 | **진행 가능, P2, S~M/낮음** | `docs/reviews/session-scorecard-ultracode-r2.md:42-46`, `docs/reviews/session-scorecard-ultracode-r2.md:176` |
| PERF-2 | **진행 가능, P2, M/낮음** | `docs/reviews/session-scorecard-ultracode.md:143-147`, `docs/reviews/session-scorecard-ultracode.md:256` |
| REG-1 | **설계 결정 후 진행, P2, M/중간** | `docs/reviews/session-scorecard-ultracode.md:184-184`, `docs/reviews/session-scorecard-ultracode.md:257` |
| RP-2 문서정정 | **현재 authoritative docs에는 이미 올바르게 표현되어 추가 변경 효용이 낮음.** 리뷰가 지적한 것은 소멸성 codex 보고서의 “broad SSH verification” 표현이고, CHANGELOG/README는 local-only verification도 epoch를 해소한다고 명시한다 | `docs/reviews/v201-ultracode.md:67-71`, `CHANGELOG.md:42-44`, `README.md:118` |

### 3.3 docs/reviews·docs/design의 `후속|보류|백로그` 전수 스캔 분류

아래는 문자열 일치 항목을 현재 코드와 대조한 결과다.

**현재 진행 가능:** observation retry/backoff (`docs/reviews/2026-07-16-observation-error-third-path.md:45-49`), build-artifact 제외의 잔여와 multi-root/무변경 비용 (`docs/reviews/2026-07-16-project-root-build-artifact-byte-limit.md:147-153`), PRB-11 독립 toggle (`docs/reviews/p4-codex-packs-eval.md:52`, `docs/reviews/p4-codex-packs-eval.md:92`), natural-compliance 추가 표본 (`docs/reviews/p5b-n1-natural.md:46`), custom script success corpus (`docs/reviews/v1.2-host-receipts.md:27`), Scorecard COR-1/PERF/CON-2/REG 항목 (`docs/reviews/session-scorecard-ultracode-r2.md:174-178`, `docs/reviews/session-scorecard-ultracode-r3.md:112-115`).

**이미 본편 처리 또는 대부분 처리:** peer write 귀속/조정과 explicit `turn_not_started`는 보류 문서의 #1/#2였고 현재 peer adjustment 및 missing baseline state가 구현됐다 (`docs/reviews/2026-07-16-observation-error-third-path.md:45-49`, `core/provenance_lifecycle.py:68-130`, `core/ledger_v2.py:483-524`). E1의 `python script.py` 검증 인식 후속도 코드와 회귀 테스트에 반영됐다 (`core/verification.py:33-37`, `tests/test_verification.py:13-15`). scope-too-large의 reason별 메시지·상위 경로·config 안내도 구현됐다 (`core/verify_state.py:229-279`, `tests/test_verify_state_scope_messages.py:59-85`).

**외부 게이트/명시 보류로 이번 추천 제외:** Antigravity live host firing은 host 1.1.1의 실제 hook engine 확인이 필요하다 (`docs/reviews/p9-agy-live-hooks.md:84-90`, `CHANGELOG.md:98-101`); wmux pane 감시 daemon은 MCP 의존·제품 범위 밖이다 (`docs/design/wmux-orchestration.md:76`); 전용 harness 통합은 사용자 보류 결정이 기록돼 있다 (`docs/design/v-next-roadmap.md:51`).

**제품 확장이라 별도 scope 결정 필요:** wmux dashboard, 등급화, 시간 근접 session merge는 Scorecard SSOT에서 명시적으로 비스코프다 (`docs/design/session-scorecard.md:138-140`). 특히 등급화는 anti-gaming과 peer 오염 표면을 넓히므로 root cross-view보다 뒤로 미루는 편이 안전하다 (`docs/design/session-scorecard.md:30`, `docs/design/session-scorecard.md:104-108`).

### 3.4 CHANGELOG Known Limitations 중 자체 진행 가능한 것

| 항목 | 제안 | 규모/리스크 | 근거 |
|---|---|---|---|
| PRB-01 promise-only completion | 별도 blocking rule보다 corpus+advisory부터; false-positive 후 gate 승격 | M/높음 | `CHANGELOG.md:60-66`, `README.md:119` |
| PRB-11 per-gate toggles | config schema/precedence/anti-self-disable 설계 후 구현 | M/중간 | `CHANGELOG.md:65`, `README.md:119` |
| verification ecosystem/value dump | ecosystem corpus 확장과 structured exit-code 우선 정책 | M/중~높음 | `CHANGELOG.md:156-159`, `CHANGELOG.md:176-180` |
| 10k/256MiB near-envelope latency 및 blocked OS call | subprocess isolation/cancellation은 가능하지만 hot-path·Windows 비용이 커 별도 epic | L/높음 | `CHANGELOG.md:60-64`, `README.md:115-118` |
| Python on PATH 의존 | zipapp/embedded launcher 또는 installer preflight 강화 | M~L/중간 | `README.md:122-124`, `CHANGELOG.md:176-180` |
| root 밖/DB/network 미관측 | adapter-specific receipt/plugin API 설계; 범용 해결은 별도 epic | L/높음 | `CHANGELOG.md:62-64`, `README.md:115-120` |

Stop 2회 후 fail-open과 “강한 적대 모델 완전 방어 아님”은 현재 명시적 안전/제품 경계이므로 단순 결함 수정 목록으로 취급하지 않는다 (`CHANGELOG.md:60-63`, `README.md:115-120`). Antigravity live firing은 외부 host gate이므로 위 표에서 제외했다 (`CHANGELOG.md:66`).

---

## 권장 실행 순서

1. **S 문서/테스트부터:** README project-scope 설치 절차, CON-2 aging test, COR-1 sister test/정책을 먼저 고정한다 (`docs/specs/v2.2-quiet-optin.md:75-78`, `docs/reviews/session-scorecard-ultracode-r3.md:112-115`, `docs/reviews/session-scorecard-ultracode-r2.md:176`).
2. **회복 정확성:** `turn_not_started` bootstrap 성공을 ledger complete로 원자 전이하고 adapter E2E를 추가한다 (`core/adapter_observation.py:121-158`, `core/ledger_v2.py:503-524`).
3. **관측성 확장:** coordination journal을 먼저 도입하고 root/agent CLI view를 붙인다; Stop 표시 정책은 바꾸지 않는다 (`docs/specs/v2.2-quiet-optin.md:34-42`, `fable_lite/scorecard.py:26-38`).
4. **성능/정책 hardening:** PERF-2, REG-1, peer-aware backoff는 receipt와 semantics를 고정한 뒤 구현한다 (`docs/reviews/session-scorecard-ultracode.md:143-147`, `docs/reviews/session-scorecard-ultracode.md:184-184`, `docs/reviews/2026-07-16-observation-error-third-path.md:45-49`).

---

## 검증 메모

- 보고서 파일은 strict UTF-8 decode에 성공했고 223행/32,283 bytes였다. 보고서에서 자동 추출한 저장소 `file:line` 인용 196개는 모두 파일 존재 및 line 범위 검사를 통과했다.
- 전체 테스트 명령 `uv run --with pytest python -m pytest -q`는 **686 passed, 2 failed (167.10s)**였다. 실패 1은 PostToolUseFailure 결과에 `hookSpecificOutput`을 기대하는 기존 adapter 테스트이며 (`tests/test_adapters.py:414-440`), 실패 2는 probe runner의 deterministic 결과가 PASS여야 한다는 기존 계약이다 (`tests/test_eval_runner.py:81-91`). 두 항목은 각각 단독 재실행에서도 재현됐다.
- 이번 산출물은 조사·설계 보고서뿐이며 위 실패 경로의 프로젝트 코드는 변경하지 않았다. 테스트 실패는 본 보고서의 정적 file:line 판정과 분리해 handoff한다.
