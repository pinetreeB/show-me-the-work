# smtw 마찰 3종 설계 — Qwen(우하) 독립 비평

- **일자**: 2026-08-03
- **비평자**: Qwen Code (우하 pane, 구현 보조)
- **대상**: `tmp/friction-fix/qwen-analysis.md`(본인 이전 세션) + `tmp/friction-fix/design-draft.md`(좌상 종합)의 채택 설계
- **검증 방법**: 코드 서명(file:line) 대조 + **venv python 실호출 재현**(파서 주입·계약 테스트 기분선) + 설계 전제(F2/F3)를 독립적으로 재검증
- **재현 스크립트**: `tmp/friction-fix/_parse_repro.py`(기존), `_adversarial_r2.py`·`_pwsh_bypass.py`(신규 주입)

> **핵심 요지**: 설계의 **F2는 정확히 재현됐다**(cmd/c `echo>file` 차단, Get-ChildItem\|Select-String allow). 그러나 **F3 전제는 재현되지 않았다** — .NET `WriteAllText` here-string은 현재 파서가 **allow**한다. 그리고 ②-1 화이트리스트의 "그 외 fail-closed" 전제는 현재 파서가 **비탐지 명령에 대해 fail-open**이라는 점(코드 구조상)과 정면 충돌한다. 트랙①은 ①-1이 **마찰을 잘못 진단**하고 있다. 트랙③만 요구사항과 계약이 정합적이다.

---

## 트랙② R2 파서 — 화이트리스트+리다이렉트 정밀화

### 2.1 F2 재현 (설계 일치 확인)

venv python 실호출로 설계 F2를 그대로 재현했다.

```
cmd.exe /c "echo test > docs\reviews\test.txt"
  parse = truncate_redirect blocked[parse_unable_wrapped]  → GATE block(command_parse_unavailable)  ✅
Get-ChildItem -Recurse . | Select-String -Pattern 'foo'
  parse = ()  → GATE allow  ✅  (현행 v2.6.2에서 이미 해소됨)
```

설계의 "오차단은 `echo>newfile` 1건, 파이프라인은 이미 allow" 판단은 옳다.

### 2.2 ⚠️ F3 전제 비재현 — .NET WriteAllText는 현재 파서가 allow

설계는 "jarvis-brain quarantine 4건 중 3건은 13KB here-string 파일쓰기 — **현 fail-closed가 실제 mutation을 잡은 것(오차단 아님)**"이라며 here-string/AST 파서(안 1)를 **보류**했다. 이 전제를 독립 주입해 재검증했다:

```
pwsh -NoProfile -Command @"...[IO.File]::WriteAllText('C:/x/out.txt','line1')..."@
  parse = (NON-DESTRUCTIVE -> ALLOW)          ❌ 설계 전제와 반대
powershell -NoProfile -Command "[System.IO.File]::WriteAllText('out.txt','x')"
  parse = (NON-DESTRUCTIVE -> ALLOW)          ❌
powershell -NoProfile -Command "Invoke-Expression 'Remove-Item out.txt'"
  parse = (NON-DESTRUCTIVE -> ALLOW)          ❌
powershell -NoProfile -Command "Copy-Item a b" / "Move-Item a b" / "New-Item -Force out.txt"
  parse = (NON-DESTRUCTIVE -> ALLOW)          ❌
powershell -NoProfile -Command "Add-Content out.txt data" / "Clear-Content out.txt" / "sc out.txt x"
  parse = (NON-DESTRUCTIVE -> ALLOW)          ❌
```

**근거**: `_DETECTORS`(`core/destructive_guard.py:1255-1261`)는 `[tee, truncate_redirect, git, remove, truncate_cmdlet, find_exec]` 6종뿐. `WriteAllText`/`iex`/`Add-Content`/`New-Item`/`Copy-Item`/`Move-Item`/`Clear-Content`/`sc`는 어느 명령집합(`REMOVE_COMMAND_NAMES`·`TRUNCATE_COMMAND_NAMES`·`GIT_*`)에도 없다 → **비탐지 = allow**. `_detect_wrapper_indirect`(`:525`)도 `pwsh`가 `SHELL_COMMAND_NAMES`에 없어 here-string 분기(`:553`)를 타지 않고, `@"..."@`를 리터럴 토큰으로 삼아 재귀 파싱해도 파괴 형상이 없음 → allow.

**해석**: quarantine 4건이 **block된 사실은** 기록이 존재하므로 참이다. 그러나 quarantine은 **모든 block에 방아쇠**(`_apply_quarantine_backup`, `:1630`)라, 그 block은 13KB 스크립트의 **다른 문법 요소**(`>` 리다이렉트·`Set-Content`·`Remove-Item`·동적 `$()`)가 유발했을 가능성이 크고, `WriteAllText` 자체가 "이해돼서" 잡힌 게 아니다. 설계가 "fail-closed가 실제 mutation을 잡고 있음"으로 일반화한 것은 **"스크립트가 차단됐다"와 "mutation이 탐지됐다"를 혼동**한 것이다.

**비평 결론**: F3의 안 1 보류 근거는 성립하지 않는다. 현재 fail-closed는 .NET mutation에 대해 **실제로 열려 있다**. 안 1을 "보류"로 둘지라도, 최소한 **`.NET mutation 메서드 + PowerShell 비파괴-오인 명령`에 대한 탐지기 추가 또는 결함 문서화**를 함께 내려야 한다. 이건 "오차단이 아니라 오차단"이 아니라, **"실제 미탐지(우회)"**다.

### 2.3 ⚠️ ①-1 화이트리스트 전제 오류 — 파서는 비탐지 명령에 대해 fail-open

설계 ②-1은 "opaque wrapper 페이로드가 [리터럴 인자만·dynamic 0]으로 완전 파싱되고 전 세그먼트가 명백 비파괴(**echo·dir·Get-ChildItem·Select-String·Get-Content·Test-Path 등 고정 목록**)일 때만 allow, 그 외는 기존 fail-closed"를 제안한다.

그런데 위 2.2 주입이 증명하듯 **현재 파서는 이미 그 목록을 훨씬 넘는 명령(이미 allow)을 허용한다**: `copy`·`move`·`xcopy`·`Add-Content`·`New-Item`·`Clear-Content`·`sc`·`iex`·`WriteAllText`. 즉 파서의 암묵적 기본값은 **fail-closed가 아니라 fail-open**이다(탐지기가 "파괴 형상"을 못 찾으면 allow).

따라서 ②-1의 "이 명령만 화이트리스트, 나머지 fail-closed"는 **얇은 오버레이로는 달성 불가**하다:
- (a) 화이트리스트를 얹으면 이미 allow인 명령에 허용이 기계적으로 중복될 뿐, **실제 우회 표면(copy/move/Add-Content/WriteAllText)은 그대로 열린다**.
- (b) "그 외 전부 fail-closed"를 만들려면 cmd/powershell의 **전체 명령을 허용목록으로 역전**해야 하는데, 이는 수천 cmdlet을 아는 불가능한 표면이고, 누락 시 정상 작업이 대량 마찰된다.

**비평 결론**: ②-1은 "배타적 화이트리스트"로 설계하면 **실현 불가능하거나 실질 무효**. 단순히 `echo>newfile` 오차단을 줄이려면 ②-2(리다이렉트)가 정확한 해결이고, 화이트리스트는 ②-2와 분리해 **"이미 허용된 위험 명령을 닫는 방향"이 아니라면** 채택하지 말 것.

### 2.4 ②-2 리다이렉트 truncate/create 구분 — 조건부 가능, 우회 세부

주입 결과:
```
echo test > existing.txt          → block(parse_unable_wrapped)   (기존 파일 덮어쓰기 차단)
echo test >| newfile.txt          → block(parse_unable_wrapped)   (강제 오버라이드 차단 — 좋음)
echo test > C:\Windows\...\x      → block(parse_unable_wrapped)   (루트 밖 차단)
echo test > ../escape.txt         → block(parse_unable_wrapped)   (부모 경로 차단)
echo test >> newfile.txt          → ALLOW                          (append——이미 파일 생성 허용)
```

- **`>>`는 이미 새 파일을 생성한다**(append는 `_APPEND_REDIRECT_RE`로 skip, 비파괴 취급). 따라서 "`>` create 허용"은 **새 우회 벡터를 추가하지 않는다** — `>>`로 이미 같은 파일 생성이 가능. 다만 현재 `>`(차단)와 `>>`(허용)가 **같은 새 파일 생성인데 비일관**하다는 점을 설계가 명시해야 한다.
- **`>|`는 반드시 유지 차단**해야 한다(주입에서 차단됨, `_REDIRECT_RE`가 `>|` 매치). ②-2가 "미존재 파일 create"를 허용할 때 `>|`를 실수로 포함하면 안 됨.
- **루트 밖/부모 경로 차단은 현재 "opaque wrapper" 때문에 우연히 차단**된다(전용 루트 검사가 아님). ②-2가 "루트 내부 + 미존재"를 허용하려면 **`_canonicalize_lexical_target` 루트 포함 검사를 반드시 추가**해야 한다. 이를 빼면 `../escape.txt`·절대경로 유출이 난다.
- **연쇄**: `echo test > newfile.txt & del old.txt` → `parse_destructive_commands`가 truncate+remove **둘 다** 반환 → 전체 차단. ②-2를 "파괴 명령이 truncate-create 하나뿐일 때만 allow"로 구현하면 안전하지만, **"하나의 파괴 명령만 보면" 안 되는 미묘한 요구**가 있다. 구현 시 전 파괴 명령을 순회해야 한다.
- **TOCTOU**: 파일 존재 검사(`os.path.exists`)는 실제 실행 전에 다른 pane이 파일을 만들면 create→truncate로 전이. 설계가 "TOCTOU 불확실은 fail-closed"라 했지만, 이는 검사와 실행 사이의 **경쟁을 막지 못한다**. 잔여 리스크로 명시하거나(낮은 확률), dry-run/가드 재확인 정책을 문서화.

### 2.5 트랙② 계약 대조

- `test_destructive_guard_new_02.py::test_all_parser_failures_have_an_explicit_coordination_label` — `parse_unable_*` 전부 → `command_parse_unavailable` 매핑 고정. 설계의 "라벨을 바꾸지 말고 파싱 범위만 정밀화"는 이 계약과 일치. 기분선 47 passed.
- `test_dynamic_parser_denial` — 동적 표현식(`$()`, `Join-Path $PWD`) fail-closed 고정. ②-1의 "dynamic 0" 요건과 일치. ②-2가 동적 리다이렉트(`> $file`)를 허용하면 이 계약 깨짐.

### 2.6 트랙② 결론

| 항목 | 판정 | 근거 |
|---|---|---|
| ②-1 엄격 화이트리스트("그 외 fail-closed") | **NO-GO** | 파서가 비탐지 명령에 fail-open이라 배타적 화이트리스트는 실현 불가/무효. 이미 copy·move·Add-Content·WriteAllText 허용 |
| ②-2 리다이렉트 create/truncate | **조건부 GO** | 루트 포함 검사 추가 + `>|` 차단 유지 + 전 파괴 명령 순회 + TOCTOU 잔여 리스크 명시. `>>` 허용과의 비일관성 정리 |
| here-string/AST 파서(안 1) | **재검토로 상향** | F3 전제(현 fail-closed가 mutation 포착)가 비재현. .NET WriteAllText는 현재 allow — 최소 탐지기 추가 또는 결함 문서화 |
| 라벨 미변경(`command_parse_unavailable`) | **GO** | 계약 테스트와 일치, 유지 필수 |

---

## 트랙① 같은 턴 재-block 방지 vs 카운터 구조

### 3.1 카운터 구조 확인 (`core/gate_counters.py`)

- `goals_blocks`/`intent_blocks`는 **턴 스코프 카운터**(`_set_counter` → `active_turn(ledger, payload)`에 저장, `:183-196`). `active_turn`(`core/verification_covers.py:30`)은 `agent_key`(host:session:agent)로 턴을 찾고, `turn_id` 불일치 시 None.
- `block_goals_once`(`:72`): `_goals_present` True → RECOVER+allow; 아니면 `blocks>=MAX(2)` → cap_allow; 아니면 증가+BLOCK.
- **불변식**(`test_v262_block_persistence_invariant.py`): "block ⟺ 영속된 카운터 증가". 기분선 41 passed.

### 3.2 ⚠️ ①-1이 마찰을 잘못 진단 — "같은 턴 recover 후 재-block"은 사실상 발생하지 않음

설계 ①-1은 "goals_checkpoint/contract recover 발생 시 같은 turn_id 내 동종 게이트 재차단 금지"를 제안한다. 그러나:

- `block_goals_once`는 `_goals_present`가 True면 **즉시 RECOVER+allow**(`_goals_present`는 체크포인트 파일 존재에 결정적). **recover가 기록된 시점에 체크포인트가 존재하므로**, 같은 턴의 다음 invocation도 `_goals_present` True → **이미 allow**다. 동종 게이트 재-block은 **구조적으로 일어나지 않는다**.
- 동종 게이트 재-block이 유일하게 일어나는 두 경우:
  - **(a) identity 불일치** — 체크포인트가 활성 턴과 다른 identity(namespaced)에 만들어짐 → `_goals_present`(identity-aware)가 False → block. 이 block은 **정당**.
  - **(b) 턴 도중 체크포인트 삭제/변형** → `_goals_present` False → block. 이 block도 **정당**(보호 목적).
- 즉 ①-1이 "고치려는" 재-block은 **모두 정당한 block**이다. ①-1로 억제하면 **체크포인트 무결성 보호를 약화**한다. 설계가 "체크포인트 무결성 검증은 유지"라고 했지만, "체크포인트가 사라져도 재차단하지 않는다"는 곧 **무결성 검증을 포기**하는 것과 동일.
- **실재 마찰(F1)은 동종 게이트가 아니라 게이트 간 연쇄**(goals→contract→stop)다. 이는 ①-2(정보 통합)가 1회 상호작용으로 해결하는 것이 정확하다.

### 3.3 카운터 구조와의 충돌 검증

- ①-1이 "recovered this turn" 플래그를 추가하면 **새 턴 필드**이지 기존 `goals_blocks` 수정이 아니다. `test_v262_block_persistence_invariant`는 "block ⟺ 영속 증가"만 고정 — ①-1의 억제 block은 **블록 결정을 내지 않으므로**(allow) 증가도 없어 **불변식은 유지**. 단, 플래그를 영속하지 않고 fail-open하면 다음 워커가 플래그를 못 봐 재-block(안전) — 무해.
- **정면 충돌은 행동 의미론에서**: ①-1은 (a)(b)의 정당한 block을 억제 → **안전 의미론과 충돌**(카운터 숫자와가 아니라, 게이트의 존재 이유와).
- ①-2(정보 통합): 카운터 무변경, 안전성 무저하. **충돌 없음**.
- ①-3(read-only 제외): 카운터 무변경(게이트 스킵이지 카운터 변화 아님) → 불변식 유지. 그러나 `executable_command_positions`(advisory, `_parse_destructive_segment` 파서 재사용)를 **인가 게이트로 재사용**하면 파괴 명령을 read-only로 오분류해 우회할 수 있다. 이는 설계의 미결 Q2(결합도·순환 위험) — **적대 검증 필수**.

### 3.4 트랙① 결론

| 항목 | 판정 | 근거 |
|---|---|---|
| ①-1 같은 턴 재-block 방지 | **NO-GO** | 마찰을 오진. 고치려는 재-block은 identity 불일치/턴 중 삭제로 **전부 정당** — 억제 시 무결성 보호 약화. 실재 연쇄 마찰은 ①-2가 해결 |
| ①-2 block 메시지 정보 통합 | **GO** | 카운터 무변경, 안전성 무저하, 연쇄를 1회 상호작용으로. 가장 안전한 우선 조치 |
| ①-3 read-only 명령 게이트 제외 | **조건부 GO** | 게이트 스킵(카운터 무변경)이라 불변식 안전. 단 advisory 파서를 인가로 재사용하지 말 것, 적대 주입 검증 필수 |
| 🚫 stop 완화 / 전역 fail-open / MAX_GOALS 축소 | **NO-GO(유지)** | 설계와 일치. `test_lost_cache_commit_*`·process invariant 계약(기분선 41) |

---

## 트랙③ aging+doctor 정직화 vs test_* 계약

### 4.1 doctor 판정 구분 (③-1) — GO, 단 테스트 메시지 계약 갱신 필요

- `smtw/doctor.py:142` `errors.append("ledger is unreadable or invalid")` — `ledger_health=="error"`일 때.
- **충돌**: `test_doctor_03a_unsupported_schema_is_error`가 `"ledger is unreadable or invalid" in snapshot["errors"]` **부분 문자열을 고정**한다(schema 0/3/99). ③-1이 invalid/unreadable/unsupported를 메시지로 분리하면 **이 테스트는 반드시 갱신** 대상.
- 단, `test_doctor_03a_malformed_json_ledger_is_error`·`non_object_ledger_is_error`·`agrees_with_runtime_loader_rejection`는 `ledger_health=="error"`·`exit_code==1`만 단언 — 메시지 갱신 영향 없음.
- **안전성**: 설계대로 `exit_code` 1 유지, stop fail-closed 유지 → doctor "정직 개선"만. **GO** (테스트 메시지 단언 갱신 + 계약 대조 명시).

### 4.2 legacy 스키마 정합 migrate (③-3) — GO, 명시적 migrate로만

- `test_v2_schema_contract.py::test_save_ledger_rejects_invalid_v2_shape_before_writing`는 save 경계에서 invalid v2 거부를 고정. ③-3이 **status 누락 invocation에 `status`를 추가**해 유효 v2로 만들면 save 통과 → **충돌 없음**.
- `test_load_ledger_reads_v1_fixture_without_writing_or_migrating`는 load 시 자동 마이그레이션 금지를 고정. ③-3이 **명시적 `smtw migrate`로만** 실행하면 충돌 없음(설계의 "전역 자동 마이그레이션 금지 원칙"과 일치).
- **GO** — 단 마이그레이션은 쓰기=데이터 변형이므로 백업·롤백 선행(설계와 일치).

### 4.3 active_turns aging (③-2) — 조건부 GO, 테스트 계약 충돌 없음

- aging은 closed 턴을 active_turns→closed_turns로 이동. 계약 테스트는 격리 픽스처를 쓰므로 **직접 충돌 없음**(기분선 41 green).
- 위험은 **운영 동시성**(4-pane 중 다른 pane이 아직 쓰는 턴 오삭제) + **비가역**(데이터 정리). 보수 임계·dry-run·백업·opt-out 필수(설계와 일치).
- **stop fail-closed는 aging과 무관하게 유지** — aging이 완료 게이트 불변식을 건드리지 않도록 명시.

### 4.4 identity aging (③-4) — 조건부 GO, identity 계약 검증 필요

- `_goals_present`(`gate_counters.py`)와 `_single_active_exact_identity`(`contract.py:110`)는 **active_turns의 identity 키에 의존**한다. aging으로 죽은 identity를 active_turns에서 제거하면 이 로직의 입력이 바뀐다.
- F7(죽은 identity 37개) 해소를 위해 identity aging을 하려면, **goals/contract의 identity-파생 테스트**를 먼저 대조해야 한다. **조건부 GO** — 계약 검증 전까지는 도입 금지.

### 4.5 journal GC + 훅 성능 (③-5) — 조건부 GO

- 8MB ledger(manifest_event_journal 3.3MB)의 pre-Tool 훅 타임아웃(F8)은 유력. GC는 `scorecard_journal_offset`·`manifest_generation` 정합을 깨지 않게 해야 한다. **조건부 GO** — 저널 오프셋 계약 확인 후.

### 4.6 트랙③ 결론

| 항목 | 판정 | 근거 |
|---|---|---|
| ③-1 doctor 판정 구분 | **GO** | 안전성 유지, 단 `test_doctor_03a_unsupported_schema_is_error` 메시지 단언 갱신 필요 |
| ③-2 active_turns aging | **조건부 GO** | 테스트 계약 충돌 없음, 운영 동시성·비가역 대책(백업·dry-run·임계) 필수 |
| ③-3 legacy 스키마 정합 | **GO** | 명시적 migrate로만(자동 금지 원칙 유지), save 검증 통과 |
| ③-4 identity aging | **조건부 GO** | `_goals_present`·`_single_active_exact_identity` identity 의존 대조 후 |
| ③-5 journal GC + 훅 성능 | **조건부 GO** | journal offset·manifest_generation 정합 확인 후 |

---

## 결론 — 설계 항목별 GO/조건부GO/NO-GO

| 트랙 | 설계 항목 | 판정 | 핵심 근거 |
|---|---|---|---|
| ② | ②-1 엄격 화이트리스트("그 외 fail-closed") | **NO-GO** | 파서가 비탐지 명령에 fail-open — copy/move/Add-Content/WriteAllText 이미 allow. 배타적 화이트리스트 실현 불가/무효 |
| ② | ②-2 리다이렉트 create/truncate 구분 | **조건부 GO** | 루트 포함 검사 추가 + `>|` 차단 유지 + 전 파괴 명령 순회 + TOCTOU 명시 + `>>` 허용과 정리 |
| ② | here-string/AST 파서(안 1) | **재검토 상향** | F3 전제 비재현 — .NET WriteAllText는 현재 **allow**. 최소 탐지기 추가 또는 결함 문서화 |
| ② | 라벨 `command_parse_unavailable` 미변경 | **GO** | `test_destructive_guard_new_02` 계약과 일치 |
| ① | ①-1 같은 턴 재-block 방지 | **NO-GO** | 마찰 오진. 재-block은 identity 불일치/턴 중 삭제로 전부 정당 — 억제 시 무결성 약화. 실재 연쇄는 ①-2가 해결 |
| ① | ①-2 block 메시지 정보 통합 | **GO** | 카운터 무변경·안전 무저하·연쇄 1회화. 우선 조치 |
| ① | ①-3 read-only 명령 제외 | **조건부 GO** | advisory 파서를 인가로 재사용 금지, 적대 주입 검증 필수 |
| ① | stop 완화/전역 fail-open/MAX 축소 | **NO-GO(유지)** | 설계와 일치, 계약 테스트(41)가 고정 |
| ③ | ③-1 doctor 판정 구분 | **GO** | `test_doctor_03a_unsupported_schema_is_error` 메시지 단언 갱신 필요 |
| ③ | ③-2 active_turns aging | **조건부 GO** | 테스트 계약 직접 충돌 없음, 동시성·비가역 대책(백업·dry-run·임계) 필수 |
| ③ | ③-3 legacy 스키마 정합 | **GO** | 명시적 migrate로만, save 검증 통과 |
| ③ | ③-4 identity aging | **조건부 GO** | identity-파생 로직 대조 후 |
| ③ | ③-5 journal GC + 훅 성능 | **조건부 GO** | journal offset·manifest_generation 정합 확인 후 |

**요약**: 신속 GO(①-2, ③-1, ③-3) 3건은 저위험·안전성 불변식 유지로 즉시 채택 가능. **①-1과 ②-1은 설계 결함으로 재설계 필요**(각각 마찰 오진·fail-open 전제 오류). **F3 기반의 안 1 보류는 전제가 무너져 재검토**해야 한다. 모든 완화는 좌상 승인 + 계약 테스트(RED 대조) + 롤백 확보 선행.

**공통 실행 증거**: venv 재현(트랙② 파서 주입 2종 + ②-1/②-2 우회 주입 20건 + PowerShell/.NET mutation 주입 19건) + 계약 테스트 기분선(doctor·block·schema·scorecard 41 passed, destructive_guard 47 passed).