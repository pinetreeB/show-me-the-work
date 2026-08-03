# smtw friction-fix 독립 재검증·설계 비평·구현 계획

- 검토 기준: `main` @ `4d436a77cc3f9979b40d25cd37f8b13887beb86f` (2026-08-03)
- 입력: `tmp/friction-fix/design-draft.md`, `tmp/friction-fix/qwen-analysis.md`
- 범위: 소스·테스트 수정 없음. F1~F8을 코드, 기존 테스트, 현재 ledger 및 보존된 quarantine에서 독립 재검증하고 후속 구현 계획만 작성한다.
- 비간섭: 시작 전 변경 `.codex/config.toml`과 미추적 `'+$_.Value)`는 건드리지 않았다. 조사 중 smtw가 차단 명령을 `.fable-lite/quarantine/`에 자동 보관한 것은 훅 자체의 관측 부수효과라 삭제하지 않았다.

## 1. 결론과 채택안 교정

초안의 큰 방향은 맞지만 그대로 구현하면 안 된다. 다음 네 결함을 먼저 고쳐야 한다.

1. 트랙①-1의 “recover 뒤 같은 turn latch”는 관측된 goals→contract→Stop의 **이종 게이트 연쇄**를 줄이지 못한다. 오히려 checkpoint를 잠깐 만들었다 삭제하거나 계약을 recover 뒤 변조하는 우회를 만든다. 현행은 살아 있는 checkpoint를 매번 확인하고, recover 뒤 동종 reblock은 재현되지 않았다(`core/gate_counters.py:26-33,72-100`; `tests/test_v261_goals_cli.py:300-320`).
2. F5의 “39개 turn이 모두 closed”는 틀렸다. invocation 587개 중 209개는 `status`와 안전한 lease 증거가 모두 없어 현행 backfill이 모두 `open/unsafe`로 분류한다(`core/ledger_migration.py:94-167,184-208`). 일괄 closed 변환은 복구가 아니라 보호 약화다.
3. aging/GC는 이미 존재한다. v2 reducer는 timestamp가 있는 stale turn을 24시간 기준으로 lazy GC한다(`core/ledger_v2.py:32-34,167-210,782-806`; `tests/test_multiagent_f3.py:160-196`). 문제는 legacy turn 26개에 timestamp가 없고 invalid ledger가 기존 저장 경로를 막는다는 점이다.
4. `>` 대상이 현재 없다는 사전 검사만으로 신규 파일 쓰기를 허용하면 안 된다. PreToolUse와 셸 실행 사이의 TOCTOU 때문에 symlink/junction 교체와 선점 생성 경합을 원자적으로 막을 수 없다. gate가 exclusive-create 실행 자체를 소유하기 전까지 redirection은 계속 block해야 한다.

따라서 채택안은 다음과 같이 교정한다.

- 트랙①: 사전 안내 통합과 엄격한 proven-read-only goals 예외만 채택한다. recover latch는 폐기한다.
- 트랙②: opaque wrapper strict whitelist는 채택하되 일반 redirection 신규-file 예외는 폐기한다.
- 트랙③: doctor 정직화, 기존 안전 status backfill의 명시 CLI화, 기존 lazy GC 유지, legacy 정리는 digest 고정 plan/apply maintenance로 제한한다. manifest journal은 최소 필드 + byte cap으로 compact한다.
- 릴리스는 새 CLI/진단 JSON/정책 확장을 포함하므로 `v2.6.x` patch보다 `v2.7.0` minor가 적절하다.

## 2. 경쟁 가설과 수정 전 증거

가설 1: F1·F2 마찰의 주원인은 같은 gate의 재차단이 아니라 같은 turn에서 서로 다른 gate가 순차 노출되는 구조와 opaque wrapper의 보수적 fail-closed다.

가설 2: F4·F5·F7은 단일 “GC 부재” 문제가 아니다. legacy invocation status와 turn timestamp 결손이 schema validation, safe backfill, 기존 lazy GC를 차례로 멈춘 복합 상태다.

가설 3: F8의 5~10초 timeout에서 8MB ledger는 증폭 요인이지만 단일 원인은 아니다. JSON parse/validation, 반복 load, process start, ledger/layout lock을 phase별로 분리 계측해야 한다.

기각:

- “ledger JSON 문법이 깨졌다”는 기각한다. JSON parse는 성공하고 v2 schema의 invocation `status` 필수 계약에서 실패한다(`core/ledger_schema.py:243-250,560-584`).
- “현재 v2.6.2가 `Get-ChildItem | Select-String`을 막는다”는 기각한다. 직접 parser 결과는 빈 destructive set이다.
- “jarvis 13KB 3건이 모두 PowerShell here-string 오차단”은 기각한다. 3건은 실제 mutation이지만 PowerShell here-string 1건과 Python writer 2건이다.
- “39 active turn의 invocation이 전부 closed”는 기각한다. 209개는 status-less이며 현 안전 분류상 open/unsafe다.
- “8MB JSON load만으로 10초 timeout이 설명된다”는 기각한다. 현재 실측 `json.loads`는 77~119ms, `load_ledger`는 188~242ms였다.

증거:

- material-erp 동일 `turn_id=4c16f051...`에서 goals block 2회→goals recover→contract block→contract recover→Stop block 2회→cap_allow를 확인했다. recover 뒤 같은 reason 재block은 없었다.
- 현재 ledger는 7,967,923 bytes, `event_seq=18996`, active turn 39, invocation 587, explicit open 0, status-less 209다. 최상위 크기는 `manifest_event_journal=3,355,490 bytes`, `active_turns=3,162,236 bytes`다.
- read-only backfill 분류는 changed 209, unsafe 209, 결과 open 209 / closed 378이다. 현 `migrate_v2_invocation_statuses`는 unsafe가 있으면 저장 전에 거부한다.
- identity는 exact 37, synthetic 2다. 환경 힌트가 없으면 `ambiguous_identity`, 존재하지 않는 명시 identity는 `wrong_identity`다(`goals/goals.py:96-113,188-239`).
- kmc-os-v2 legacy ledger는 50,466 bytes이며 `schema_version`과 `active_turns`가 없다. 2026-07-22~24 로그의 `turn_bootstrap_pending` 1004건 모두 baseline missing/turn_not_started지만 mutation-capable은 876건뿐이다.
- jarvis-brain quarantine 13,163/13,214/13,348 byte 3건은 각각 `WriteAllText`, `Path.write_text`, `open(...,'w')`/ `Path.write_text` mutation이다. 46 byte `cmd.exe /c "echo test > ..."`만 신규 파일 false positive다.
- Codex hook timeout은 10초(`adapters/codex_cli/hooks.json`), 일반 예외는 fail-open(`adapters/codex_cli/pre_tool_use.py:8-12,112-117`)이다. 한 hook에서 R2→identity resolve→contract→begin invocation이 직렬 실행된다.
- 관련 회귀 기준 6개 파일을 새로 실행해 `64 passed in 8.37s`를 확인했다.

## 3. F1~F8 독립 판정

| 사실 | 판정 | 재검증 결과 |
|---|---|---|
| F1 | 부분 확인 | guarded tool/goals/R1 조건과 2+2+Stop 이종 연쇄는 확인했다. “recover 후 동종 재차단” 증거는 없고 현행은 checkpoint 실재 여부를 다시 검사한다(`core/contract.py:740-780`; `core/gate_counters.py:26-33,72-100`). |
| F2 | 확인 | `cmd.exe /c "echo test > ..."`는 `truncate_redirect/resolved=false/parse_unable_wrapped`, `Get-ChildItem | Select-String`은 allow다. opaque wrapper와 fail-closed는 `core/destructive_guard.py:75-84,525-550,1683-1700`에 있다. |
| F3 | 부분 수정 | “실제 mutation 3건 + 신규-file false positive 1건”은 맞다. “세 건 모두 here-string”은 틀렸다. |
| F4 | 확인·확장 | 첫 위반은 `antigravity:default:antigravity...status`지만 총 209개 status 누락이다. doctor는 IO/JSON/schema를 동일 `ledger_health=error`와 “unreadable or invalid”로 합친다(`smtw/doctor.py:139-143,222-247`). |
| F5 | 반증 후 재정의 | 크기는 확인했으나 all-closed는 반증했다. 30 turn은 누락이 없고 9 turn에 209개 unsafe invocation이 있다. journal은 count 256 cap만 있고 byte cap/요약이 없다(`core/provenance_manifest.py:42-43,980-991`). |
| F6 | 부분 확인 | pending/missing/turn_not_started 1004는 확인했다. mutation-capable은 876이라 “1004 전부 mutation”은 과장이다. 명시 state migration 미실행만을 유일 원인으로 단정할 수 없다. |
| F7 | 확인 | stale exact identity 37개가 ambiguity를 만들고 새 명시 identity는 wrong_identity다. 해결은 goals 전용 heuristic이 아니라 active-turn lifecycle 정리여야 한다. |
| F8 | 관측 확인·인과 미확정 | 10초 deadline과 fail-open은 확인했다. 8MB ledger가 약 0.2초 load 비용을 만들지만 5~10초의 단일 원인이라는 증거는 없다. W4 phase timing이 먼저다. |

## 4. 트랙별 비평과 누락된 `test_*` 불변식

### 4.1 트랙①: gate 완화

①-1 recover latch는 폐기한다. 다음 우회가 생긴다.

- goals file을 잠깐 만든 뒤 recover를 기록하고 삭제
- contract digest가 맞을 때 recover를 만든 뒤 파일 변조
- 다른 identity의 checkpoint 바꿔치기
- turn 중 state authority가 `.fable-lite`↔`.smtw`로 전환
- scorecard recover만 남고 실제 checkpoint write가 유실

반드시 보존하거나 추가할 계약:

- `test_goals_recovery_does_not_latch_after_checkpoint_deletion`
- `test_contract_recovery_does_not_latch_after_digest_change`
- `test_recover_transition_never_substitutes_for_live_checkpoint_validation`
- `test_goals_block_requires_persisted_increment`
- `test_intent_block_requires_persisted_increment`
- `test_lost_cache_commit_invalidates_next_transition_summary`
- `test_lost_cache_commit_omits_scorecard_on_next_routine_allow`

①-2는 채택하되 첫 block 메시지가 필요한 goals, 고위험 contract, mutation 뒤 verification/Stop 조건을 **예고**만 해야 한다. 예고가 counter나 recover를 기록하면 안 된다.

①-3은 채택한다. R2 command-position parser를 재사용하지 말고 이미 존재하는 `core/shell_command.py:220-232,308-368`의 `ShellEffect.PROVEN_READ_ONLY`를 단일 SSOT로 공개한다. R2는 항상 먼저 실행하고 goals 예외만 적용한다.

추가 계약:

- `test_proven_read_only_shell_skips_goals_without_consuming_block_counter`
- `test_read_only_exception_requires_every_segment_to_be_proven_read_only`
- `test_read_only_exception_rejects_redirection_profile_script_and_dynamic_wrapper`
- `test_pretool_hint_lists_future_gates_without_recording_recover`
- `test_stop_decision_is_unchanged_by_pretool_read_only_exception`

### 4.2 트랙②: opaque wrapper whitelist 공격 모델

화이트리스트는 명령 이름 포함 검사가 아니라 wrapper mode, 전체 payload grammar, 모든 segment와 인자를 완전히 해석한 경우에만 allow해야 한다.

| # | 공격 벡터 | 예시 계열 | 방어 |
|---|---|---|---|
| 1 | allow 명령 뒤 chain | `dir & del`, `Get-Content; Set-Content`, newline/pipe | wrapper 내부 segment를 전수 분해하고 하나라도 whitelist 밖이면 전체 block |
| 2 | PowerShell 동적 실행 | `Invoke-Expression`, `&`, dot-source, scriptblock create | iex/call operator/dot-source/scriptblock/type invocation 토큰 전부 거부 |
| 3 | PowerShell 식 확장 | `$()`, `${}`, `@()`, splatting, `%{}` | 비리터럴 식 또는 `$`/`@`/scriptblock이 하나라도 있으면 fail-closed |
| 4 | encoded/file/profile mode | `-EncodedCommand`, `-File`, profile autoload | `-NoProfile -NonInteractive -Command` 제한 grammar만 허용 |
| 5 | cmd 변수·지연 확장 | `%VAR%`, `%COMSPEC%`, `!VAR!`, `for /f`, `call` | `%`, `!`, for/call, delayed expansion `/V` 거부 |
| 6 | escape/quote smuggling | cmd caret, PowerShell backtick, CRLF, NUL, unmatched quote | escape 문자·물리 newline/NUL·불균형 quote를 fail-closed |
| 7 | redirection 변형 | `>`, `2>`, `*>`, `Out-File`, `Tee-Object` | redirection/write cmdlet 전부 block. 미존재 target 사전검사 예외 없음 |
| 8 | path 탈출/특수 경로 | `..`, UNC, `\\?\`, ADS, junction, wildcard, device | root containment, reparse point/ADS/device/wildcard/outside absolute 거부. 이 검사만으로 쓰기 allow 금지 |
| 9 | alias/PATH 치환 | `gci`, `cat`, PATH shadowing, `.cmd/.bat` | canonical exact command만 허용하고 alias/script/relative executable 거부 |
| 10 | pipeline stage 오염 | `Get-Content | %{...}`, `dir | cmd /c ...` | 모든 stage와 parameter set을 독립 검증하고 unknown 하나면 전체 block |

필수 RED 계약:

- `test_opaque_whitelist_rejects_encoded_command_and_file_mode`
- `test_opaque_whitelist_rejects_profile_dot_source_and_call_operator`
- `test_opaque_whitelist_rejects_cmd_percent_bang_and_caret_expansion`
- `test_opaque_whitelist_rejects_powershell_subexpression_scriptblock_and_splatting`
- `test_opaque_whitelist_rejects_mixed_pipeline_when_one_stage_is_unknown`
- `test_opaque_whitelist_rejects_all_redirection_without_owned_atomic_execution`
- `test_opaque_whitelist_allows_literal_get_content_select_string_pipeline`
- `test_opaque_whitelist_allows_literal_cmd_dir_and_echo_without_redirection`

기존 `test_reverify_*` corpus와 `test_all_parser_failures_have_an_explicit_coordination_label`도 그대로 통과해야 한다.

### 4.3 트랙③: lifecycle, doctor, 성능

- doctor: `unreadable`(IO/Unicode/JSON)과 `invalid`(schema field)을 분리하고 `error_field`, `needs_migration`, 안전한 다음 명령을 JSON/text 모두 제공한다.
- status backfill: 기존 lease-aware 분류와 digest archive를 재사용한다. 증거 없는 209개를 자동 close하지 않는다.
- turn aging: timestamp가 있는 valid v2 turn에는 현 24시간 lazy GC를 유지한다. timestamp-less legacy turn은 자동 aging하지 않는다. 별도 maintenance plan/apply에서 exact identity, 사유, source digest, backup을 고정한 경우만 정리한다.
- identity: goals만의 별도 age 규칙을 만들지 않고 maintenance 후 `active_turns`를 유일 authority로 둔다.
- journal: ledger에는 `event_id/seq/manifest_generation/commit_state` 최소 필드만 보존하고 count와 serialized byte cap을 모두 둔다. full event는 agent log/pending transaction에 남긴다.
- F8: compact 전후 size와 hook phase를 측정한다. timeout 증가는 원인 수리가 아니므로 금지한다.

추가 계약:

- `test_doctor_distinguishes_unreadable_from_invalid_and_reports_schema_field`
- `test_statusless_invocation_without_lease_evidence_never_auto_closes`
- `test_status_migration_check_is_write_free_and_apply_requires_safe_rows`
- `test_legacy_timestampless_turn_is_not_lazy_aged`
- `test_maintenance_apply_requires_matching_source_digest_and_backup`
- `test_maintenance_never_collects_current_identity_or_open_leased_invocation`
- `test_maintenance_preserves_closed_turn_replay_guard_capacity`
- `test_manifest_journal_compaction_preserves_generation_pending_recovery_and_agent_log_evidence`
- `test_hook_latency_fixture_emits_phase_metrics_without_mutating_fixture`

## 5. W1~W4 구현 계획

### W1. doctor 정직화 + 명시적 invocation status backfill

#### 수정 파일·함수 목록

- `smtw/doctor.py`: `_ledger_snapshot`, `diagnostic_snapshot`, text renderer. parse/IO 실패와 `LedgerSchemaError`를 분리하고 field/detail/needs-migration을 반환한다.
- `core/ledger_migration.py`: 기존 `invocation_status_backfill_required`, `backfill_invocation_statuses`, `migrate_v2_invocation_statuses`를 재사용하고 write-free `inspect_v2_invocation_statuses`만 추가한다.
- `smtw/migrate.py`: `add_migrate_parser`, `run_migrate`에 `--invocation-statuses`와 `--check`를 추가한다. unsafe row가 있으면 apply는 exit 1이고 원본/archive 무변경이다.
- 테스트: `tests/test_v262_doctor_truthfulness.py`, `tests/test_v261_migration02.py`, `tests/test_w3_legacy_invocation_compat.py`, 신규 `tests/test_friction_w1_doctor_migrate.py`.

#### RED-first 테스트 계획

1. malformed JSON은 unreadable, valid JSON의 missing status는 invalid + 정확한 field라는 기대를 먼저 RED로 만든다.
2. `--invocation-statuses --check`가 required/safe/unsafe/source digest를 출력하며 파일 hash가 동일함을 RED로 고정한다.
3. unsafe status-less apply가 원본과 archive index를 바꾸지 않고 실패하는 불변식을 추가한다.
4. 완전한 lease evidence가 있는 stale row만 closed로 backfill되고 digest archive가 생성되는 기존 GREEN 계약을 유지한다.

검증: `python -m pytest -q tests/test_friction_w1_doctor_migrate.py tests/test_v262_doctor_truthfulness.py tests/test_v261_migration02.py tests/test_w3_legacy_invocation_compat.py`

#### 브랜치 전략(worker/*, PR 단위)

- `worker/friction-w1-doctor-migrate`
- doctor/backfill truthfulness 1 PR. RED test commit → production commit → regression commit.
- W3와 병렬 가능. W4는 W1 merge 후 분기한다. 실제 운영 ledger에는 apply하지 않는다.

#### 예상 회귀 지점

- `ledger_health == "error"`를 기대하는 JSON 소비자
- 기존 `smtw migrate` state-directory 의미와 `--check` exit code
- `SMTW_AUTO_MIGRATION=1` 경로와 명시 CLI 중복 실행
- archive digest/index retention과 wheel CLI smoke

### W2. gate preflight + proven-read-only goals 예외

#### 수정 파일·함수 목록

- `core/contract.py`: `evaluate_pretool_contract`에서 R2 통과 후 shared read-only가 true인 shell만 goals block을 생략한다. 상태를 쓰지 않는 `_gate_preflight_requirements`를 추가한다.
- `core/gate_counters.py`: `block_goals_once`의 reason에 advisory를 합치되 counter/persistence 분기는 유지한다. latch는 추가하지 않는다.
- `core/shell_command.py`: W3가 공개한 `is_proven_read_only_command`를 소비하며 W2 parser를 별도 생성하지 않는다.
- 테스트: `tests/test_v262_goals_correctness.py`, `tests/test_v262_block_persistence_invariant.py`, `tests/test_scorecard_review_regressions.py`, 신규 `tests/test_friction_w2_gate_preflight.py`.

#### RED-first 테스트 계획

1. goals-required turn의 literal read-only shell이 현재 block되는 characterization을 RED로 만들고 allow 후 goals counter 불변을 확인한다.
2. mixed pipeline, dynamic wrapper, redirection은 예외가 아니라 기존 goals block을 유지한다.
3. 첫 block reason이 goals/contract/verification을 예고하지만 recover event는 0개임을 확인한다.
4. checkpoint 삭제와 contract digest 변조 뒤 같은 turn에서 다시 block되는 guard를 변경 전에 고정한다.
5. Stop decision과 lost-cache action sequence는 변하지 않아야 한다.

검증: `python -m pytest -q tests/test_friction_w2_gate_preflight.py tests/test_v262_goals_correctness.py tests/test_v262_block_persistence_invariant.py tests/test_scorecard_review_regressions.py`

#### 브랜치 전략(worker/*, PR 단위)

- `worker/friction-w2-gate-preflight`
- gate UX/read-only 1 PR. W3 merge 후 분기해 shared API를 그대로 소비한다.

#### 예상 회귀 지점

- read-only false negative가 goals를 우회
- `smtw goals plan` authoring 예외와 일반 read-only 예외 혼동
- adapter가 기대하는 exact block reason 변경
- counter persistence, recover linkage, Stop fail-closed 약화

### W3. R2 opaque wrapper strict whitelist

#### 수정 파일·함수 목록

- `core/shell_command.py`: `_is_proven_read_only`를 public `is_proven_read_only_command`로 승격하고 `cmd.exe /c`, `pwsh|powershell -NoProfile -NonInteractive -Command` 제한 grammar를 추가한다.
- `core/destructive_guard.py`: `_detect_wrapper_indirect`, `_wrapper_payload_tokens`가 shared parser의 완전 성공 때만 allow하도록 변경한다. opaque unknown은 빈 destructive 결과가 아니라 `parse_unable_wrapped`로 block한다. redirection 예외는 추가하지 않는다.
- 테스트: `tests/test_v261_r2_command_parser.py`, `tests/test_destructive_guard_new_02.py`, `tests/test_multiagent_f4_stabilization_r2_01.py`, `tests/test_remote_mutation.py`, 신규 `tests/test_friction_w3_opaque_whitelist.py`.

#### RED-first 테스트 계획

1. 현 parser가 직접 destructive token을 보지 못하는 encoded/file/dot-source/call-operator/variable-expansion 변형을 expected block으로 먼저 RED 처리한다.
2. literal `cmd /c dir`, `cmd /c echo text`, `Get-Content`, `Get-ChildItem | Select-String` allow를 characterization으로 고정한다.
3. 위 10개 벡터별 최소 2개 변형과 Windows quote/path/case 변형을 corpus에 넣는다.
4. 모든 `parse_unable_*`가 기존 coordination label로 매핑되고 quarantine decision이 약화되지 않음을 확인한다.
5. 독립 adversarial 실주입 30건 이상에서 false negative 0을 합격 기준으로 둔다.

검증: `python -m pytest -q tests/test_friction_w3_opaque_whitelist.py tests/test_v261_r2_command_parser.py tests/test_destructive_guard_new_02.py tests/test_multiagent_f4_stabilization_r2_01.py tests/test_remote_mutation.py`

#### 브랜치 전략(worker/*, PR 단위)

- `worker/friction-w3-r2-opaque-whitelist`
- R2/shared read-only classifier 1 PR. W1과 병렬 가능.
- 코드 리뷰만으로 merge하지 않고 별도 adversarial receipt를 PR artifact로 첨부한다.

#### 예상 회귀 지점

- 치명적 false negative로 dynamic wrapper mutation 통과
- Windows quoting/literal path/`Get-ChildItem -Recurse` false positive
- POSIX corpus와 remote-only mutation classifier 회귀
- parser latency 및 quarantine reason/coordination label 변경

### W4. legacy lifecycle maintenance + journal compact + hook latency

#### 수정 파일·함수 목록

- `core/ledger_v2.py`: `_gc_stale_turns`의 timestamped 24h lazy GC/current-turn preserve를 유지한다. timestamp-less 자동 정리는 금지한다. `_remember_closed_turn` capacity 사전 검사 helper를 노출한다.
- 신규 `core/ledger_maintenance.py`: `plan_maintenance`가 safe-auto와 manual-only legacy 후보, source digest, identity, reason, 예상 bytes를 만든다. `apply_maintenance_plan`은 digest/backup/current identity/open lease를 재확인하고 exact 후보만 적용한다.
- 신규 `smtw/maintenance.py`, `smtw/cli.py`: `maintenance plan`과 `maintenance apply`를 추가한다. doctor에는 repair를 넣지 않는다.
- `core/provenance_manifest.py`: `_append_journal`에서 최소 필드 projection과 count + serialized byte cap을 적용한다.
- `adapters/codex_cli/pre_tool_use.py`: W3 proven-read-only가 true이면 R2/gate 후 identity resolve/provenance begin을 생략한다. mutation/verification/unknown은 기존 경로를 유지한다.
- 신규 `eval/hook_latency_bench.py`: 8MB temp fixture로 phase별 cold/warm p50/p95, bytes, timeout count를 JSON receipt로 출력한다.
- 테스트: `tests/test_multiagent_f3.py`, `tests/test_f2_atomicity.py`, `tests/test_v2_schema_contract.py`, 신규 `tests/test_friction_w4_ledger_maintenance.py`, `tests/test_friction_w4_hook_fastpath.py`.

#### RED-first 테스트 계획

1. timestamp-less legacy turn은 lazy GC가 보존하고 maintenance plan에서 manual-only로 분류돼야 한다.
2. plan 뒤 ledger가 한 byte라도 바뀌면 apply가 거부되고 원본/backup이 보존되는 digest-CAS를 고정한다.
3. current identity, 30분 lease 내 open invocation, unsettled attribution, pending manifest transaction을 후보에서 제외한다.
4. closed-turn replay guard 256 cap을 밀어내는 apply는 거부한다.
5. journal compact가 최소 필드/count/byte cap을 만족하면서 generation/pending/agent-log evidence를 보존한다.
6. proven-read-only Codex hook만 `resolve_active_invocation`과 `begin_invocation`을 건너뛰고 unknown/mutation/verification은 호출한다.
7. 30 cold run에서 timeout 0, p95 < 2.0s, 각 run < 8.0s, compact 후 ledger ≤4.5MB를 목표로 한다. 명시 maintenance temp copy는 ≤1.5MB를 목표로 한다.

검증:

- `python -m pytest -q tests/test_friction_w4_ledger_maintenance.py tests/test_friction_w4_hook_fastpath.py tests/test_multiagent_f3.py tests/test_f2_atomicity.py tests/test_v2_schema_contract.py`
- `python -m eval.hook_latency_bench --fixture tmp/friction-fix/fixture-ledger-8mb.json --runs 30 --json-out tmp/friction-fix/hook-latency-receipt.json`

#### 브랜치 전략(worker/*, PR 단위)

- `worker/friction-w4-ledger-maintenance`
- lifecycle/compact/perf 1 PR. 리뷰 가능 크기를 넘으면 W4a maintenance+journal, W4b hook fast path+benchmark의 2 PR로만 분할한다.
- W1과 W3가 merge된 main에서 분기한다. 운영 ledger apply는 PR 범위 밖이며 별도 승인과 backup 확인 후 수행한다.

#### 예상 회귀 지점

- active turn/identity 오정리와 진행 중 증거 손실
- closed-turn cap 때문에 오래된 replay guard가 밀리는 문제
- manifest recovery/verification이 compact journal을 full event로 오인
- plan/apply 사이 TOCTOU, lock starvation, backup retention
- read-only fast path가 verification 또는 unknown mutation을 건너뜀
- wall-clock CI 변동성. 시간 SLO는 reference Windows receipt로 판정하고 unit test는 호출 경로/byte budget만 결정론적으로 검증

## 6. 병합 순서와 최종 합격 게이트

```text
W1 ---------\
             -> W4
W3 -> W2 ---/
```

- W1과 W3는 병렬 가능하다.
- W2는 W3 shared read-only API merge 뒤 시작한다.
- W4는 W1 진단/backfill surface와 W3 classifier를 전제로 시작한다.
- 각 PR은 RED commit과 GREEN evidence를 보존하며 기존 dirty 파일을 stage하지 않는다.

최종 합격 기준:

1. F1~F8 fixture와 새 `test_*` 계약 전부 GREEN.
2. 전체 `pytest`, `ruff check core smtw adapters tests eval`, Windows/Ubuntu CI, wheel CLI smoke GREEN.
3. W3 adversarial 실주입 30건 이상, false negative 0.
4. 8MB fixture PreToolUse 30회 timeout 0, p95 <2초, 최대 <8초.
5. doctor가 invalid/unreadable을 구분하고 unsafe status backfill을 자동 적용하지 않는다.
6. 운영 `.fable-lite/ledger.json`은 구현 PR에서 수정하지 않는다. 실제 maintenance는 backup + source digest + 별도 승인 후 수행한다.

## 7. 남은 불확실성

- F8 5~10초 중 parse, 반복 load, process start, lock wait 비중은 아직 미측정이다. W4 benchmark RED가 구현 선택보다 먼저다.
- kmc-os-v2 1004건은 과거 누적 로그다. 현재 버전에서 같은 빈도로 재발하는지는 live 재현 없이는 단정하지 않는다.
- 209개 unsafe invocation을 자동 closed로 만들 증거는 현재 ledger에 없다. 즉시 F7을 해소하려면 maintenance plan에서 exact stale identity를 명시 선택해야 하며 코드 merge와 별도 운영 작업이다.
