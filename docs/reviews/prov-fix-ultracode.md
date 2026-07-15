# provenance 오탐 수정 — ultracode 4관점 적대 교차검토

- **대상 범위**: `70caee5..d888c9b` (8커밋, 27파일 — core/·fable_lite/·tests/)
- **설계 SSOT**: `tmp/prov-fix/design-final.md` (3-AI 합의) · `design-codex.md` (§4 D1~D5·§5 불변식·§6 파일계획·§7 테스트) · `design-agy.md` (적대 공격체크리스트)
- **구현 보고**: `tmp/prov-fix/impl-report-codex.md` — *"독립 HEAVY reviewer APPROVE, blocking/non-blocking finding 0"* 주장
- **검토 방식**: ultracode 멀티에이전트(20에이전트, ~188만 토큰). 6 finder 레인(정확성 shell/remote/covers, 회귀, 동시성, 테스트품질) → finding별 적대 검증(실제 분류기 실행·실게이트 구동으로 grounding) → 종합. 우하 pane(구현보조)이 핵심 Critical 4건을 직접 분류기 재실행으로 재확인.
- **일자**: 2026-07-15

---

## 종합 판정

> **impl-report의 "finding 0" 주장은 성립하지 않는다.** 이 교차검토는 **CONFIRMED Critical 3건 + High 2건**을 포함, 실제 게이트 우회(bypass) 또는 게이트 파손을 재현했다. 독립 HEAVY reviewer가 놓친 결함이다. 릴리스/푸시 전 **최소 Critical·High(5건) 수정 필수**.

핵심은 이 하네스의 위협모델과 정확히 맞물린다 — **"합리화로 게이트를 우회하려는 하위 모델"**. Critical 3건 모두 에이전트가 의도적으로 문자 하나(`#`) 또는 loopback 별칭(`127.1`)을 삽입하면 파괴적 로컬 변경이 검증 없이 Stop=allow로 세탁된다.

### 심각도 집계 (검증 후)

| 관점 | Critical | High | Medium | 기각(REFUTED) |
|------|:--------:|:----:|:------:|:-------------:|
| ① 정확성 (shell/remote/covers) | **3** | **1** | 2 | — |
| ② 회귀 | — | — | 1 | 1 |
| ③ 동시성 | — | **1** | 1 | 1 |
| ④ 테스트 품질 | — | — | 2 | 1 |
| **합계** | **3** | **2** | **5** (또는 PLAUSIBLE 1) | **3** |

---

## ① 정확성 (Correctness)

### 🔴 Critical — COR-SHELL-1 · `#` 주석 삼킴이 변경 명령을 PROVEN_READ_ONLY로 세탁 (Stop 우회) — **CONFIRMED**

- **위치**: `core/shell_command.py:308` (근본원인 `:264` `lexer.commenters = "#"`)
- **결함**: `_command_tokens`가 shlex를 `commenters="#"`로 돌려 **단어 중간의 `#`도 주석으로 처리** → `#` 뒤의 `;`/`&&`와 후속 변경 명령을 전부 버림. 그런데 bash는 단어 중간 `#`을 리터럴로 취급하고 뒤 명령을 실행한다. `_is_proven_read_only`의 원시 가드(`:308`)는 `\n \r \` $( > <`는 막지만 `#`은 막지 않는다.
- **재현(우하 직접 실행, HEAD d888c9b)**:
  ```
  classify_shell_effect('cat x#; rm -rf src')  -> proven_read_only ()
  classify_shell_effect('cat foo;rm -rf x')    -> local_or_unknown ()   # 대조(# 없음): 정상
  bash -c 'echo a#; echo PWNED_RAN'  -> 'a#' 와 'PWNED_RAN' 모두 출력 (뒤 명령 실행)
  ```
- **우회 사슬**: read_only 분류 → `adapter_observation._mutation_capable`(`:419`) False → `provenance_mutation_capable` 미설정 → `verify_state.evaluate_without_io`의 두 백스톱 차단(`:232` scope_too_large, `:247` incomplete)이 모두 `provenance_mutation_capable is True`를 요구하므로 **건너뜀** → 관측된 변경 0 → `:279` `not changed` → **allow**. 실제로는 `rm -rf src`가 실행됨. **불변식 #2·#5 위반**, agy F1(scope-bomb + 은닉변경) 방어를 정면 무력화.
- **수정**: `_command_tokens`에서 `lexer.commenters = ""`로 설정(단어 중간 `#`을 리터럴 유지 → 뒤의 `;`/`&&`/`|`가 연산자로 노출되어 LOCAL_OR_UNKNOWN 강등). 이 한 줄이 COR-SHELL-1·COR-SHELL-2를 동시에 닫음. (대안: `:308`·`:289` 가드에 `#` 추가.) **shlex 주석 처리에 의존하지 말 것 — POSIX 셸 단어경계 규칙과 불일치.**

### 🔴 Critical — COR-SHELL-2 · 동일 `#` 삼킴이 로컬+원격 혼합 명령을 PROVEN_REMOTE_ONLY로 세탁, 로컬 변경 면죄 — **CONFIRMED**

- **위치**: `core/shell_command.py:289`
- **결함**: 같은 근본원인. `#`으로 로컬 변경을 숨긴 ssh 명령이 PROVEN_REMOTE_ONLY로 분류되어 로컬 `rm`/`sed -i`가 **로컬 provenance 의무 없이** 원격 검증만으로 청산 가능(design D2 "ssh ... && echo x > local.txt는 local-or-unknown" 규칙 무력화).
- **재현**:
  ```
  classify_shell_effect('ssh -F none -o StrictHostKeyChecking=yes host.example.com true#; rm -rf src')
    -> proven_remote_only ('ssh://host.example.com:22',)
  ```
  bash에서 `true#; rm ...`의 로컬 세그먼트가 실제 실행됨. `#` 제거 시 `... true && rm -rf src`는 올바르게 local_or_unknown.
- **수정**: COR-SHELL-1과 동일(`commenters=""`). 패치 후 두 케이스 모두 재검증.

### 🔴 Critical — COR-REMOTE-1 · `_is_loopback_host`가 loopback 별칭을 놓쳐 로컬 ssh/scp 변경을 PROVEN_REMOTE_ONLY로 분류 (F4 "ssh localhost 면죄" 방어 무력화) — **CONFIRMED**

- **위치**: `core/shell_command.py:375`
- **결함**: `_is_loopback_host`가 리터럴 `localhost`와 `ipaddress.ip_address`가 파싱하는 엄격형 IP만 인식. 설계 브리핑·design-agy §F4가 열거한 별칭 — `localhost.`(후행점 FQDN)·`127.1`/`127.0.1`(inet_aton 축약)·`0.0.0.0`·십진 `2130706433` — 을 전부 놓침. 이 별칭 대상 ssh/scp가 로컬 머신을 변경하는데 PROVEN_REMOTE_ONLY로 분류됨.
- **재현(우하 직접 실행)**:
  ```
  _is_loopback_host: 'localhost'->True | 'localhost.'->False | '127.1'->False |
                     '127.0.1'->False | '0.0.0.0'->False | '2130706433'->False | '::1'->True
  classify_shell_effect('ssh -F none -o StrictHostKeyChecking=yes 127.1 "sed -i s/x/y/ app.py"')
    -> proven_remote_only ('ssh://127.1:22',)
  ```
- **우회 사슬**: incomplete/scope-too-large provenance(이 PR이 겨냥하는 바로 그 조건)에서 로컬 편집을 loopback ssh로 수행 → `_mutation_capable` False → 백스톱 차단 건너뜀 → 로컬 편집 미관측 → 팬텀 원격 epoch `ssh://127.1:22`만 "변경"으로 잡히고, 에이전트가 `ssh ... 127.1 pytest -q`(is_verification_command=True)로 자가 청산 → `has_successful_verification` True → **allow**. `_is_loopback_host('127.1')`가 True였다면(정상) LOCAL_OR_UNKNOWN → mutation_capable → 차단.
- **수정**: `_is_loopback_host`에서 정규화 후 판정 — 후행점 strip, `{localhost, localhost.}` 포함, inet_aton/십진/축약형 파싱(`socket.inet_aton` 또는 명시적 `127.` / `0.0.0.0` / 십진-in-127.0.0.0/8 검사), **판정 불가 시 LOCAL_OR_UNKNOWN 강등(fail-closed)**. 기존 테스트(`test_remote_mutation.py:275`)는 localhost/127.0.0.1/::1만 커버 — 별칭 케이스 미커버.

### 🟠 High — COR-COVERS-1 · check.py의 git-fallback 억제가 scan-제외 경로의 현재 턴 변경을 은닉 (soft-exclude + 에이전트 편집 가능 config = 자가면제) — **CONFIRMED**

- **위치**: `fable_lite/check.py:144` (+ `:228`)
- **결함**: 활성 턴이 COMPLETE로 reconcile되면 `changed_paths()`가 provenance 턴-delta만 신뢰하고 `git status` 병합을 건너뜀(`if authoritative_paths is None:` 가드). provenance 스캔은 하드코딩 SOFT_EXCLUDES(venv/node_modules/__pycache__/*cache)와 **에이전트 편집 가능한** `.fable-lite/provenance-config.json`의 `exclude` 목록을 따름. 따라서 edit-tool로 강제관측되지 않은 scan-제외 경로의 현재 턴 변경(외부 쓰기·툴 사이 변경·config-제외 경로)이 delta에서 누락되고, 그걸 잡아주던 git 안전망이 이제 사라짐. **70caee5는 무조건 git status를 병합**해 RED로 잡던 것이 GREEN이 됨(회귀).
- **재현(실 CLI)**: `provenance-config.json`에 `{"exclude":["src/**"]}` 작성(`.fable-lite`는 하드제외라 미관측) 후 tracked `src/core.py`를 백도어로 재작성 → `python -m fable_lite check` **exit 0 / GREEN / changed:0** (반면 `git status`는 `M src/core.py`). config 없는 좁은 변형: soft-exclude 디렉토리 하의 tracked `venv/mod.py` 외부 변경도 GREEN. 대조(config·제외 없음): 동일 변경이 RED — 원인 격리 확인.
- **수정**: authoritative 턴-delta 사용 시에도 `git status --porcelain=v1 -uall`을 유지하되 **턴 시작 전 dirty 집합만** 차감(전체 git dirty 차감이 아니라). 또는 turn-delta와 scan-제외 디렉토리 내 git-status 경로를 합집합. 독립적으로, `.fable-lite/provenance-config.json` 쓰기(및 exclude 확장)를 agy D5 고위험 변경으로 취급하고 **에이전트 편집 exclude가 git 백스톱에서 경로를 제거하지 못하게** 할 것.

### 🟡 Medium — COR-REMOTE-2 · `_option_value`가 원격 명령 피연산자까지 스캔 → `-p/-l/-o` 누출 → target_id 불안정 → epoch-covers 불일치 — **CONFIRMED (우하 독립 확인)**

> ⚠️ 워크플로우 검증자가 placeholder(`"test"`)를 반환한 유일한 결함 케이스. 우하 pane이 직접 분류기 실행으로 대체 검증함.

- **위치**: `core/shell_command.py:488`
- **결함**: `_target_from_authority`가 `_option_value`를 **전체 인자 벡터**에 대해 호출 — 이 함수는 `_operands`와 달리 첫 피연산자에서 멈추지 않아, 원격 명령 안의 unquoted 옵션(`ls -l`, `run -p 2222`)을 ssh의 `-l user`/`-p port`로 오독. 같은 논리적 호스트가 원격-인자 텍스트에 따라 다른 target_id를 냄. F4 게이트가 `remote_mutation_epochs`와 covers를 **정확한 target_id로 대조**하므로 epoch·covers 키가 어긋남(실패 클래스 B).
- **재현(우하 직접 실행)**:
  ```
  'ssh ... deploy.example.com ls -l /srv'     -> ('ssh:///srv@deploy.example.com:22',)  # -l 누출→user='/srv'
  'ssh ... deploy.example.com "ls -l /srv"'   -> ('ssh://deploy.example.com:22',)        # quoted: 정상
  'ssh ... deploy.example.com run --flag -p 2222' -> ('ssh://deploy.example.com:2222',)  # -p 누출→port
  ```
- **영향**: 검증된 원격 턴이 target_id 불일치로 **허위 BLOCK**(우회 아님, 오탐/마찰). 방향은 안전.
- **수정**: port/user를 첫 피연산자 앞의 옵션 영역에서만 계산(`_operands` 스캔을 재사용하거나 pre-operand 슬라이스 전달), 피연산자 이후의 `-p/-l/-o` 무시.

### 🟡 Medium — COR-REMOTE-3 · `_has_ambiguous_remote_options` 부분문자열 매치가 원격 페이로드(`make -j`)에 오발화 → 정상 remote-only를 LOCAL_OR_UNKNOWN로 강등(신규 오탐) — **CONFIRMED**

- **위치**: `core/shell_command.py:516`
- **결함**: `_has_ambiguous_remote_options`가 **전체 명령**을 casefold 후 `' -j '`/`'proxyjump='` 등을 부분문자열 스캔. 이 마커가 ssh가 실행하는 원격 명령 안에도 나타나 정상 remote-only 호출이 ambiguous로 오판되어 `:228`에서 LOCAL_OR_UNKNOWN 반환. (오탐 감소가 목적인 변경에서 발생한 신규 오탐 — 안전 방향이나 incomplete provenance 하 정상 원격 작업을 재차단.)
- **재현**: `ssh ... deploy.example.com make -j build` → local_or_unknown (반면 `"ls -l /srv"`는 proven_remote_only). `make -j`는 극히 흔함.
- **수정**: ambiguity 검사를 원시 문자열 스캔이 아닌 **구조적(토큰)** 검사로 — ssh 옵션 영역(첫 피연산자 앞)에만 적용. `-o` 지시자는 이미 권위 있는 `_is_safe_ssh_config` allowlist(`_is_direct_ssh`)에 의존하면 이 belt-and-suspenders 부분문자열이 원격 페이로드에 오발화하지 않음.

---

## ② 회귀 (Regression)

> **핵심 판정(REG 레인 coverage)**: 하드게이트 코어(`verify_state.py`)는 **순 강화(net tightening)**, block→allow 회귀 없음. scope_too_large는 mutation_capable 시 **차단 가드 신설**(agy F1 방어), 원격 검증은 target별 covers로 **엄격화**, `_docs_only`에 `not _remote_epochs` 추가. edit는 항상 local, classifier 프로빙에서 로컬 변경 명령이 read-only/remote-only allowlist로 새는 케이스 없음(단, 아래 check.py·§①의 예외). `is_harness_state_path`는 올바르게 root-level 한정. 기존 테스트 중 block→allow로 뒤집힌 것 없음.

### 🟡 Medium — REG-CHECK-SCOPE-1 · check CLI가 complete 턴에서 git-fallback을 버려 soft-excluded 디렉토리 내 git-tracked 파일의 현재 턴 변경이 BLOCK→ALLOW — **CONFIRMED**

- **위치**: `fable_lite/check.py:228` (근본원인 `:144`)
- **관계**: **COR-COVERS-1과 동일 근본원인**(check.py git-fallback 억제)의 다른 공격면. COR-COVERS-1은 에이전트 편집 config-exclude 각도(High), 본 건은 하드코딩 soft-exclude(node_modules/venv/*cache) 각도(Medium).
- **재현(실 CLI, e2e)**: vendored deps를 커밋하는 JS 레포에서 tracked `node_modules/dep/index.js`를 백도어로 재작성 → HEAD: `changed_files=[]` → **GREEN**. 70caee5: `git status`가 `M node_modules/dep/index.js` → **RED**. 대조로 in-scope `app.py` 변경은 여전히 RED — soft-exclude 스코프가 원인.
- **수정**: COR-COVERS-1과 통합 처리(complete-turn authoritative 경로에서도 `git status`를 union, `is_harness_state_path`만 제외). 최소한 git-vs-delta 불일치를 provenance 경고로 노출.

### ⚪ REFUTED — REG-CODEGRAPH-2 · `.codegraph` 하드제외

- **위치**: `core/provenance_policy.py:97`
- **주장**: root `.codegraph` 무조건 harness-state 제외가 합의 설계를 초과, root-level 사각지대 신설.
- **기각 근거(검증자)**: `.codegraph`는 이 레포 root의 **OMO 소유 gitignore된 디렉토리 정션**(→ `/c/Users/rotat/.omo/codegraph/...`, 레포 밖 캐시)이며 제품 소스가 아님. design-codex §F1이 정확히 이 정션을 F1 대형-루트-스캔 오탐 원인으로 문서화, `d888c9b`가 그 **의도된 수정**. impl-report item 8·line 98이 `.codegraph`를 의도된 harness-state 제외로 명시. 제안된 수정("user config exclude 의존")은 D5(config 정책 이관 금지, agy 자가면제 공격)와 정면 배치. **의도된 동작 — 결함 아님.**

---

## ③ 동시성 (Concurrency)

> **락 코어는 안전**(CON 레인 coverage): `record_event`/`evaluate_stop`/`capture_verification_covers`는 `ledger_transaction` 내 완전 잠금 RMW. 교차-에이전트 키가 달라 "B가 A의 ledger changed 항목 덮어쓰기"라는 원안 prize는 **불가능**. 스냅샷 쓰기는 tempfile+os.replace(torn read 없음). `_winapi.OpenProcess` 팬텀-ID 가드(v2.0.1 수정)는 이 범위에서 보존됨. **진짜 구멍은 baseline 재구성 경로.**

### 🟠 High — COR-RECON-1 · 변경 후 baseline 재구성이 fail-OPEN: 동시 진행된 workspace-current가 변경을 삼킴(clean_claim=True) — **CONFIRMED (e2e 실게이트 재현)**

- **위치**: `core/provenance_lifecycle.py:156`
- **결함**: post-mutation 관측(observe_post_tool/reconcile_turn/finish_turn — 전부 `allow_full_bootstrap=False`로 resume_turn 호출)에서 지속된 턴 baseline이 부재하면, `MissingTurnBaselineError` 핸들러가 **fail-closed 대신** `self._state.current`(공유 workspace-current.json)에서 baseline을 재구성. 동시 에이전트가 이미 이 에이전트의 편집을 workspace-current에 스캔해 넣었다면, 재구성된 baseline이 그 편집을 이미 포함 → `calculate_net_delta=0` → 변경 미기록 → Stop이 미검증 변경을 allow.
- **회귀**: `git diff`상 이 핸들러는 HEAD에서 신설. 70caee5는 baseline 부재 시 `KeyError` → observe_post_tool가 catch → `provenance_incomplete=True` → **차단(fail-closed)**. HEAD는 이 fail-closed 차단을 **fail-open allow로 전환**.
- **재현(e2e, repro_e2e_gate.py)**: A가 `app.py` 편집 → A의 baseline 파일 unlink(COR-STORE-1 조건 또는 `.fable-lite` 접촉) + 동시 에이전트 B가 공유 workspace-current를 A의 새 digest로 진행 → A의 observe_post_tool → `changed_files_seen=[]`, `provenance_incomplete=False`, `evaluate_without_io` → **allow**. 대조(B 진행 없음): `changed_files_seen=['app.py']` → **block**. 동시 진행이 유일 차이. **불변식 5·7 위반.**
- **수정**: post-mutation 관측점에서 `baseline := current` 재구성 금지. `allow_full_bootstrap` 없이 호출된 경로(post_tool/reconcile/finish)에서 baseline 부재는 **fail-closed**(typed reason으로 incomplete 표시 → evaluate_stop 차단)여야 하며, 진행됐을 수 있는 current로 재-baseline 금지.

### 🟡 Medium — COR-STORE-1 · 스냅샷 스토어 쓰기가 재시도 없는 os.replace(ledger와 달리) → 멀티에이전트 경합 시 Windows 쓰기 실패 → 허위 incomplete + COR-RECON-1 전제조건 공급 — **CONFIRMED**

- **위치**: `core/provenance_store.py:104`
- **결함**: `_save`(save_workspace_current)와 `save_turn_baseline_from_current`가 재시도 없는 `os.replace`. ledger 쓰기는 `_replace_with_one_retry`(v2.0.1 락 작업이 겨냥한 Windows sharing-violation을 견딤)를 쓰는데, **가장 경합이 심한** workspace-current.json(매 훅 프로세스가 생성 시 read)에는 그 완화가 없음.
- **재현(우하 확인, Windows 11/CPython 3.12.8, 3/3 결정론)**: 리더가 파일을 ~10ms retry 창 내에 잡았다 놓으면 ledger `atomic_write_text`는 재시도로 **성공**, `save_workspace_current`는 `SnapshotStoreError`로 **실패** → start_turn이 `_mark_incomplete(STORE_WRITE_ERROR)`, 턴 baseline 미지속 → **정상 턴의 허위 fail-closed incomplete**.
- **경계**: 단독으로는 fail-closed(안전). COR-RECON-1로의 escalation(baseline 부재 → fail-open)은 별개 결함 의존. 지속 점유 시 ledger도 실패(재시도가 만능 아님). 70caee5 대비 회귀 아님(당시도 bare os.replace).
- **수정**: 스냅샷 스토어 쓰기를 동일 재시도 헬퍼(`_replace_with_one_retry`/공유 atomic writer)로 라우팅 → 허위 incomplete와 baseline-부재 전제 동시 제거.

### ⚪ REFUTED — COR-LOCK-1 · reconcile_turn의 workspace-current 쓰기가 락 밖(비원자 RMW)

- **위치**: `core/provenance_lifecycle.py:191`
- **주장**: `reconcile_turn`의 `save_workspace_current(finalized)`가 `ledger_transaction` 밖 → 마지막-쓰기-승리로 stale stop-full 스냅샷 지속.
- **기각 근거(검증자, 실증)**: 리터럴 사실(락 밖)은 참이나 **중심 논지 기각** — `LifecycleState.generation`이 매 인스턴스 0으로 리셋(`provenance_lifecycle_types.py:85`)되고 매 훅이 새 단일스레드 인스턴스를 생성하므로 generation CAS는 **프로세스 내 가드일 뿐**, 크로스-프로세스 CAS 제로. 실증: **완전 잠금된** `_commit_if_current` 경로조차 동시 커밋된 파일을 동일하게 드롭("LOCKED commit dropped concurrently-committed b.py? True"). 즉 `:191`이 락 밖이라 추가되는 경합은 없고, 제안된 수정(락으로 감싸기)은 효과 없음. gate delta는 에이전트별 자체 baseline vs 자체 fresh 스캔으로 계산, ledger는 완전 잠금 `record_event` — **fail-safe**(다음 스캔이 over-report). 오allow/오block 재현 불가.
- **비고**: 검증자가 지적한 **크로스-프로세스 원자성 부재**는 별개 관찰거리이나 이 finding의 주장 범위 밖이며 게이트 안전엔 영향 없음(fail-safe). 백로그 참고로만.

---

## ④ 테스트 품질 (Test Quality)

> **테스트 완화·삭제 없음 확인**(TEST 레인 coverage): 10개 변경 테스트 전부 skip/xfail 부재, block→allow로 뒤집힌 단언 없음. `core/classify.py` diff는 **비어 있음**(corpus 테스트는 characterization, 신규 behavior RED 아님 — 설계대로). `.fablize`/`.codegraph`/`is_harness_state_path`는 진짜 신규 behavior RED. 아래는 **커버리지 공백**(현재 동작은 정확, 회귀 가드 부재).

### 🟡 Medium — TEST-COV-DELTA-1 · F3 턴-delta 테스트가 신규 파일 추가만 커버, 사전-dirty 파일의 턴 중 수정(정확한 우회 경계)은 미커버 — **CONFIRMED**

- **위치**: `tests/test_fable_lite_cli.py:297`
- **결함**: `test_check_uses_active_turn_delta_instead_of_preexisting_repo_dirty`는 사전 build/dist/uv.lock dirt 제외(changed:0)와 **신규** `dist/current.js` 검출(changed:1)은 증명하나, **이미 dirty한 사전 경로를 턴 중 덮어쓰는** 케이스(F3 수정과 불변식 6이 방어하는 정확한 경계)를 결코 실행하지 않음.
- **영향**: 현재 동작은 정확(우하 확인: 턴 중 `dist/preexisting.js` 덮어쓰기 → RED/changed:1). 그러나 향후 "턴 시작 시 dirty였던 경로 스킵" 최적화가 들어오면 백도어 덮어쓰기가 changed:0으로 통과해도 **어떤 테스트도 실패하지 않음**(인접 테스트는 git-committed 파일을 써서 이 경계를 지키지 못함).
- **수정**: 동일 테스트에 사전-dirty `dist/preexisting.js`를 턴 중 재작성 후 `returncode==1` & `'dist/preexisting.js' in changed` 단언 추가.

### 🟡 Medium (PLAUSIBLE) — TEST-COV-INV3-1 · 불변식 3(briefing은 provenance allow 권한 없음)의 게이트급 테스트 부재, 신규 corpus 테스트 이름이 검증 내용 과장 — **PLAUSIBLE**

- **위치**: `tests/test_classify_corpus.py:165`
- **결함**: `test_actual_wmux_boot_templates_are_briefings_without_provenance_authority`가 `result['briefing'] is True`·`needs_goals is False`(순수 분류기 출력)만 단언 — Stop/provenance를 전혀 구동하지 않아 이름이 주장하는 "without provenance authority"를 검증하지 않음.
- **검증 판정(PLAUSIBLE)**: 사실 관계(이름 과장·커버리지 공백)는 참이나 **헤드라인 임팩트("녹색 스위트로 불변식3 조용히 회귀")는 재현 불가** — briefing이 게이트와 완전 분리(`briefing`은 `classify.py`·테스트 2곳에만 존재, `verify_state.py`/provenance 모듈에 없음, UserPromptSubmit 훅이 ledger에 미기록). "미검증 변경 → 차단"은 이미 `test_core_contracts.py:295`에서 게이트급 단언됨. **테스트 위생 문제**(부정확한 이름 + 중복 누락 테스트)이지 실증 가능한 구멍 아님.
- **수정(권장)**: 통합 테스트 — briefing=True 프롬프트 기록 + 실제 편집(mutation_capable) + 무검증 → `evaluate_stop == 'block'` 단언.

### ⚪ REFUTED — TEST-COV-F2-CONCURRENCY-1 · agy F2(스냅샷 스토어 삭제로 증거 인멸) + lost-ledger-record 테스트 부재

- **위치**: `tests/test_provenance_lifecycle.py:307`
- **기각 근거(검증자, 실증)**: `classify_shell_effect`는 **경로 무관** — `rm -rf .fable-lite/snapshots`·`rm .fable-lite/snapshots/workspace-current.json`·`rm -rf .fable-lite/snapshots/*` 전부 LOCAL_OR_UNKNOWN(차단 결과) 반환. F2 방어는 별개 코드경로가 아니라 `rm local.txt`와 동일 코드로 처리되며, LOCAL_OR_UNKNOWN→mutation_capable(`test_adapters.py:294`)→차단(`test_verification_covers.py:300`)이 **합성으로 이미 테스트됨**. failure scenario는 가정형("IF 향후 harness-state rm을 면제하면")으로 존재하지 않는 미래 기능. 동시성도 `test_two_observers_dedupe...`·`test_block_counter_atomic.py` 인접 커버. **결함·의미있는 공백 아님.**

---

## 교차 절단 근본원인 (수정 우선순위)

1. **[Critical×2, 단일 수정] shlex 토크나이저 주석 처리** — `core/shell_command.py:264` `lexer.commenters = ""`. COR-SHELL-1·COR-SHELL-2를 동시 종결. **최우선.**
2. **[Critical×1] loopback 별칭 정규화** — `core/shell_command.py:375` `_is_loopback_host`에 inet_aton/십진/후행점/`0.0.0.0` 처리 + fail-closed. COR-REMOTE-1.
3. **[High×1, 회귀] check.py git-fallback 억제** — `fable_lite/check.py:144`. 사전-dirty만 차감하는 git-status union으로 복원. COR-COVERS-1 + REG-CHECK-SCOPE-1(동일 근본, 두 공격면). 에이전트 편집 exclude가 백스톱 무력화 금지(D5).
4. **[High×1] fail-open baseline 재구성** — `core/provenance_lifecycle.py:156`. post-mutation 관측점 baseline 부재를 fail-closed로. COR-RECON-1. (전제 공급하는 COR-STORE-1 재시도도 동반 수정 권장.)
5. **[Medium] remote target_id 파싱** — `_option_value` 피연산자 누출(COR-REMOTE-2) + `_has_ambiguous_remote_options` 원격페이로드 오발화(COR-REMOTE-3). 둘 다 마찰/오탐(안전 방향)이나 remote-only 기능의 실사용성 훼손.
6. **[Medium] 테스트 회귀 가드** — TEST-COV-DELTA-1(사전-dirty 턴중 수정), TEST-COV-INV3-1(briefing 게이트급).

## 방법론 메모 (검토 신뢰도)

- **결함 1건**: 워크플로우 검증자 중 COR-REMOTE-2 담당이 placeholder(`"test"`) 반환 → 우하 pane이 실제 분류기 실행으로 대체 검증(CONFIRMED). 나머지 19에이전트 정상.
- **적대 검증의 가치**: finder가 올린 결함 중 **REFUTED 3건**(REG-CODEGRAPH-2·COR-LOCK-1·TEST-COV-F2)은 실증으로 기각 — 각각 "OMO 정션 의도된 수정"·"크로스-프로세스 CAS 부재라 락 감싸기 무효·fail-safe"·"경로무관 분류기라 합성 테스트로 커버". 그럴듯하지만 틀린 결함을 걸러냄.
- **impl-report 대조**: "독립 HEAVY reviewer APPROVE, finding 0" 주장은 이 다관점 적대 검토에서 **성립하지 않음**. HEAVY reviewer는 `#` 세탁·loopback 별칭·check git-fallback 억제·fail-open 재구성을 놓침.
