# SMTW 내부 통일 설계 의견서

- 작성 기준: `worker/v25-state01`의 현재 워킹트리와 `tmp/smtw-unification-design-draft.md`
- 범위: 설계 검토만 수행한다. 현재 STATE-01 소스 변경은 수정하거나 커밋하지 않는다.
- 결론 요약: 상태 트리는 **원본 보존 staging-copy 방식(c)** 으로만 전환하되, 전환 전후에 트리 하나만 권위 있게 선택한다. Python import rename은 같은 v3 릴리스의 **별도 PR**로 분리하고 한 메이저 동안 `fable_lite` shim을 둔다. 새 환경변수는 값의 truthiness가 아니라 **키 존재 여부**로 legacy보다 우선한다. malformed config는 “상위 config가 명시되었는가”를 기준으로 fail-closed 여부를 나눈다.

## 먼저 고정할 불변식

1. 한 프로젝트에서 어느 순간에도 권위 있는 런타임 상태 트리는 하나뿐이다. 파일별로 `.smtw/`와 `.fable-lite/`를 섞어 읽지 않는다.
2. `.smtw.toml`은 추적 가능한 공유 설정 파일이고 `.smtw/`는 추적하지 않는 런타임 디렉터리다. 이름이 비슷해도 수명·보호 정책을 섞지 않는다.
3. 상태 경로 선택 함수는 마이그레이션 같은 쓰기 부작용을 일으키지 않는다. 이전은 활성 프로젝트임이 확정된 뒤 명시적인 준비 단계에서만 수행한다.
4. 새 형식이 유효하게 publish된 뒤에는 legacy 트리를 자동 fallback으로 다시 살리지 않는다. 이는 rollback이 아니라 두 세대의 split-brain이기 때문이다.
5. v3 동안 legacy 이름을 “읽을 수 있음”과 “새 이름으로만 새 상태를 씀”을 구분한다. 기존 프로젝트는 이전 전까지 legacy 트리 전체를 권위 있게 계속 사용할 수 있다.

## Q1. `.fable-lite/`에서 `.smtw/`로 상태 디렉터리 이전

### 입장

수정한 (c), 즉 **원본 보존 + 격리 staging copy + 내용 검증 + 단일 publish**를 채택한다. 단, 첫 훅에서 무조건 자동 복사하지 않는다. 기존 프로젝트는 안전한 quiescence를 증명할 수 없으면 `.fable-lite/` 전체를 계속 사용하고, 설치/doctor의 명시적 migrate 또는 제한된 안전 조건을 만족한 준비 단계에서만 이전한다.

| 안 | 원자성·crash-safety | 동시 세션 | 하위호환·rollback | 판단 |
|---|---|---|---|---|
| (a) 새 경로 우선, 파일별 legacy fallback | 개별 파일은 원자 write가 가능해도 트리 세대는 원자적이지 않다. 새 ledger 하나가 생기면 오래된 snapshot/goal과 혼합될 수 있다. | 두 세션이 서로 다른 트리에 쓰는 split-brain을 막지 못한다. | fallback이 오래 남고 어느 세대가 권위 있는지 설명하기 어렵다. | **기각** |
| (b) 1회 rename/move | 같은 볼륨·대상 부재 조건의 성공한 rename은 좋은 cutover 경계가 될 수 있지만, Windows의 열린 lock handle과 디렉터리 교체 제약이 있다. 전원 손실 durability도 별도 문제다. | lock을 잡은 채 자기 부모 디렉터리를 rename하기 어렵고, lock을 놓으면 경쟁 창이 생긴다. 이미 실행 중인 구버전은 옛 inode/경로에 계속 쓸 수 있다. | 원본이 사라져 즉시 rollback할 수 없고, legacy `.fable-lite/config.json`까지 이동해 다음 Claude 세션을 비활성화할 수 있다. | 온라인 자동 이전으로는 **기각**. 완전 중지된 별도 관리 명령에만 제한 가능 |
| (c) 원본 보존 copy | 임시 sibling 트리에 완전히 복사·검증한 뒤 대상 부재 rename으로 publish한다. publish 전 crash는 원본, publish 후에는 marker가 있는 새 트리가 권위 있다. | v3 migrator끼리는 layout lock으로 한 명만 publish한다. 구버전 writer 문제는 quiescence/재시작 정책으로 차단해야 한다. | 원본이 cutover 시점 snapshot으로 남는다. 다만 새 트리에 쓰기 시작한 뒤 원본으로 자동 복귀하는 것은 안전한 rollback이 아니다. | **채택** |

Python의 `os.rename`/`os.replace` 계약도 교차 파일시스템 이동을 허용하지 않고 플랫폼별 대상 처리 차이가 있으므로, source와 staging과 target은 반드시 같은 프로젝트 부모 아래에 둔다. `copytree`는 재귀 복사일 뿐 트랜잭션이 아니므로 직접 target에 복사하지 않는다. 참고: [Python `os.rename`/`os.replace`](https://docs.python.org/3/library/os.html#os.rename), [Python `shutil.copytree`](https://docs.python.org/3/library/shutil.html#shutil.copytree).

### 실제 코드에서 확인한 이유

- `core/ledger_storage.py:12-17`의 `state_dir()`가 `.fable-lite`를 직접 반환하고, `core/ledger_storage.py:20-68`의 writer는 destination 디렉터리 안 임시 파일과 `os.replace`를 사용한다. 이는 **한 파일의 process-crash atomicity**에는 유용하지만 상태 트리 세대 전환이나 전원 손실 durability를 보장하지 않는다.
- `core/agent_log.py:149-176`에는 `O_CREAT|O_EXCL`, owner token, PID와 `fsync`를 사용한 락 획득이 있고, `core/agent_log.py:195-210`에는 owner가 맞을 때만 해제하는 로직이 있다. Windows PID 생존 확인은 `core/agent_log.py:112-122`에 있다.
- 현재 `ledger_transaction()`은 `core/agent_log.py:213-260`에서 `.fable-lite/ledger.lock`을 직접 사용한다. 이를 그대로 새 layout lock으로 호출하면 오히려 옛 디렉터리를 만들고 락 네임스페이스가 갈린다.
- `core/provenance_store.py:50-59,207-234`의 snapshot과 manifest, ledger는 복구 프로토콜을 공유한다. 따라서 파일별 fallback은 동일 transaction generation을 깨뜨린다.
- goals는 `goals/goals.py:76-83`, intent는 `core/intent.py:50-82`에서 별도 쓰기 경로를 가진다. ledger lock 하나만 잡아서는 이 writer들과 이미 실행 중인 구버전을 모두 멈출 수 없다.
- 현재 STATE-01의 legacy activation config인 `.fable-lite/config.json`은 런타임 상태 copy 대상이 아니다. 이 파일은 config fallback 수명이 끝날 때까지 옛 위치에 남겨야 한다.

### 구현 구조

1. `core/state_layout.py`
   - `STATE_DIR_NAME = ".smtw"`, `LEGACY_STATE_DIR_NAME = ".fable-lite"`와 staging/marker/protected-name 상수를 둔다.
   - `inspect_state_layout(root)`는 `EMPTY`, `LEGACY`, `NATIVE`, `MIGRATED`, `MIGRATING`, `CONFLICT`를 **읽기 전용**으로 판정한다.
   - `state_dir(root)`의 기존 public facade는 유지하되 이 모듈로 위임한다. 기존 프로젝트는 migrate 전 트리 전체가 legacy, 새 프로젝트는 native다.
   - native `.smtw/`도 최초 생성 때 layout metadata를 먼저 원자적으로 만든다. marker 없는 임의 `.smtw/`와 legacy가 동시에 있으면 자동 선택하지 않고 conflict로 본다.
2. `core/file_lock.py`
   - `agent_log`의 owner-token/PID/stale-recovery 구현을 명시적 lock path를 받는 공용 primitive로 추출한다.
   - 프로젝트 루트의 안정된 sibling lock(예: `.smtw-migration.lock`)을 layout 전환에 사용한다. 이는 양쪽 상태 디렉터리 밖에 있어 rename과 무관하며, guard/provenance/.gitignore 정책에 포함하고 정상 해제 시 제거한다.
   - 획득 순서는 항상 `layout lock -> legacy ledger lock`으로 고정한다. 반대 순서를 허용하지 않는다.
3. `core/state_migration.py`
   - `prepare_state_layout(root, activation)`와 명시적 `migrate_state(root)`를 둔다. 단순 `state_dir()` 호출에서는 절대 실행하지 않는다.
   - staging은 `root/.smtw.migrating-<pid>-<nonce>`처럼 같은 부모에 생성한다.
   - `.fable-lite/config.json`, lock, 이전 staging/guard/temp 파일은 제외한다. 알려지지 않은 일반 runtime 파일과 backup/quarantine은 유실 방지를 위해 기본 포함한다.
   - symlink, junction/reparse point, special file, root 밖으로 resolve되는 항목은 따라가지 않고 실패한다. Windows casefold 뒤 동일한 두 상대 키가 생기면 실패한다.
4. 모든 consumer는 선택된 **트리 하나**를 주입받는다.
   - ledger: `core/ledger_storage.py`, `core/agent_log.py`
   - attribution/snapshot: `core/provenance_store.py`, `core/provenance_policy.py`
   - contract/goals/intent: `core/contract.py`, `goals/goals.py`, `core/intent.py`
   - scorecard/coordination/audit: `core/scorecard_store.py`, `core/scorecard_coordination.py`, `core/verification_covers.py`
   - adapter runtime log: `adapters/codex_cli/stop.py`

### 마이그레이션 안전 방식

권장 state machine은 다음과 같다.

1. activation/root/home 판정을 먼저 끝낸다. exact home 또는 비활성 프로젝트에서는 lock·staging·target을 하나도 만들지 않는다.
2. layout lock을 획득하고 상태를 다시 읽는다. 이미 유효한 target이 있으면 idempotent 성공, invalid target이면 conflict다.
3. open invocation/active turn/lock owner를 검사한다. 안전한 quiescence를 입증하지 못하면 이전하지 않고 legacy 트리를 그대로 사용한다. 큰 트리로 훅 latency 예산을 넘을 때도 명시적 명령으로 미룬다.
4. legacy ledger lock을 획득한다. source의 canonical manifest를 만든다: project-relative key, entry type, size, SHA-256, 총 파일 수·bytes. Windows 비교 키는 casefold한다.
5. 새 staging으로 복사한다. source와 staging의 manifest를 비교하고, source manifest를 다시 계산해 copy 도중 변화가 없었는지 확인한다.
6. staging 안에 migration marker를 atomic write한다. marker에는 schema, migration id, canonical source, digest/count/bytes, 시작·완료 시각, tool version을 기록한다.
7. 모든 파일 handle을 닫고 target 부재를 재확인한 뒤 staging 디렉터리를 `.smtw`로 rename한다. 기존 target은 절대 overwrite하지 않는다.
8. publish된 marker를 다시 읽어 검증한 뒤에만 `.smtw/`를 권위 상태로 선택한다. legacy 원본은 그대로 둔다.

Crash 복구 규칙은 단순해야 한다.

- publish 전 crash: `.fable-lite/`가 계속 권위 있다. orphan staging은 owner 사망·충분한 age·root/migration-id 일치를 확인한 경우에만 정리한다.
- target은 없고 staging만 있음: 다른 migrator가 이어 쓰지 않는다. 새 staging으로 처음부터 다시 수행한다.
- 유효 marker가 있는 target: `.smtw/`가 영구적으로 권위 있다. 이후 legacy 변화는 경고 대상이지 merge/fallback 근거가 아니다.
- marker가 없거나 digest가 맞지 않는 target: `CONFLICT`/degraded를 명시한다. 임의로 old 또는 new를 선택하지 않는다.
- 이전 실패 자체는 훅 프로세스를 죽이지 않는다. publish 전이고 legacy가 유효하면 full gate를 legacy에서 계속 수행한다. 다만 destructive R2 경로는 애매한 상태를 완화 근거로 사용하지 않고 fail-closed를 유지한다.

### 멀티세션과 홈 세션

- 같은 v3 코드의 동시 migrator는 stable layout lock, lock 후 재검사, target-no-overwrite로 한 winner만 허용할 수 있다.
- v2 writer는 새 layout lock을 모른다. 기존 ledger lock은 ledger/provenance copy의 안정화에는 재사용할 수 있지만 goals, 수동 contract write, 이미 열린 handle까지 막지 못한다. 따라서 **혼합 버전이 활동 중인 상태의 완전 자동 migration은 세 안 모두 안전하지 않다.** 설치 시 세션 재시작을 요구하고, active/open lease가 있으면 migrate를 보류해야 한다.
- Claude bootstrap은 exact home에서 state access 전에 hard-off되고(`adapters/claude_code/bootstrap.py:93-99,176-177`), home 판정 helper는 `adapters/claude_code/activation_policy.py:62-65`에 있다. 공용 migration API도 호출자에게 `activation`을 요구해 exact home에서 디스크 흔적을 만들지 않아야 한다. home 아래 별도 프로젝트 root는 정상 대상으로 본다.

### 보호 정책

- `core/destructive_guard.py:19,672-707`의 단일 state-name 보호를 `.smtw`, 보존된 `.fable-lite`, migration staging/lock 모두를 lexical·resolved 기준으로 보호하도록 바꾼다.
- `core/provenance_policy.py:13-22,42,142-147`의 exclude/config 특례도 두 runtime 디렉터리를 모두 제외한다. 반면 root의 `.smtw.toml`은 공유 config이므로 provenance에서 일반 추적 파일로 취급한다.
- legacy 원본은 호환 기간 내 삭제하지 않는다. 삭제는 별도 opt-in cleanup 명령과 backup 확인을 요구한다.

### 예상 diff 규모와 리스크

- 단순 `rg` 기준 production 영역(`core`, `adapters`, `fable_lite`, `goals`, `contrib`, `scripts`)에 `.fable-lite`가 약 33행/18파일이고, 이 중 동작 경로가 약 21행, 안내·메시지가 약 12행이다. 따라서 “리터럴 15곳”만 치환하면 부족하다. tests는 약 149행/45파일, eval은 약 17행/7파일이 관련된다.
- Q1 전체는 약 **60~75파일, 1,200~2,000 LOC**를 예상한다. 새 layout/migration/lock 코드 350~650 LOC, consumer 전환 100~200 LOC, fault/race/platform 테스트 400~800 LOC, fixture·eval·문서가 나머지다.
- P0: 혼합 버전 split-brain, invalid target을 정상으로 선택, legacy config를 runtime과 함께 이동, 보존된 old state 보호 누락.
- P1: ledger 외 writer의 source race, Windows 열린 handle/casefold/junction, 훅 latency, marker publish의 전원 손실 복구.
- P2: orphan staging 정리, migration warning 반복, 큰 backup/quarantine copy 비용.

## Q2. Python 모듈 `fable_lite`에서 `smtw`로 변경

### 입장

제품 v3 통일 범위에는 포함하되 **상태/env 전환과 같은 PR에는 넣지 않고 마지막 별도 PR**로 수행한다. canonical package는 `smtw`, `fable_lite`는 v3 전체에서 compatibility shim으로 유지한다. 배포 distribution 이름은 첫 전환에서 `fable-lite`를 유지하는 것이 안전하다. import package명, console script명, distribution identity는 서로 다른 문제다.

### 실제 규모와 설치 영향

- 현재 package 파일은 11개다.
- 직접 `fable_lite` import는 6개 파일의 7개 statement다: `fable-lite-cli.py:12`, `tests/test_fable_lite_cli.py:15`, `tests/test_p4_core_regressions.py:15`, `tests/test_packaging_entry_points.py:59`, `tests/test_scorecard_coordination.py:46`, `tests/test_verification_covers.py:8-9`.
- `python -m fable_lite` subprocess 호출은 tests/eval에 10곳이고, 생성되는 command 문자열도 `fable_lite/brief.py:60`, `core/contract.py:560` 및 adapter prompt에 있다.
- `pyproject.toml:6`의 distribution 이름은 `fable-lite`, `pyproject.toml:15-17`의 console entries는 `fable_lite.cli`, `pyproject.toml:19-20`은 package include를 지정한다.
- distribution을 동시에 `smtw`로 바꾸면 pip는 upgrade가 아니라 별개 설치로 볼 수 있다. 그러면 구 wheel의 `fable_lite`와 console script 소유권이 남고 uninstall 순서에 따라 새 파일을 지울 수 있다. 같은 distribution identity를 유지하면 정상 upgrade가 구 wheel RECORD를 정리한다.

### 구현 구조

1. `git mv fable_lite smtw`; 내부 import·`python -m`·command 생성은 모두 `smtw`가 canonical이다.
2. `pyproject.toml`은 distribution `fable-lite`를 당분간 유지하고 packages에 `smtw*`와 `fable_lite*`를 포함한다.
3. console script `smtw = "smtw.cli:main"`을 canonical로 추가하고, legacy `fable-lite`도 `smtw.cli:main`을 가리키게 한다.
4. `smtw-cli.py`를 canonical source launcher로 두고 `fable-lite-cli.py`는 얇은 wrapper로 유지한다. `adapters/intent_command.py:6-11`의 launcher 계산도 새 이름을 우선한다.
5. `fable_lite/__main__.py`는 `smtw.cli.main`으로 위임한다. 공개 submodule별 shim은 target을 import한 뒤 `sys.modules[__name__] = target`으로 동일 module object를 보장한다.

단순 `fable_lite = smtw` 변수, `from smtw import *`, 또는 package `__path__` 공유만으로는 충분하지 않다. `fable_lite.cli`와 `smtw.cli`가 별도 module object로 로드되면 singleton, monkeypatch, module-level cache가 갈릴 수 있다. shim 계약 테스트는 `import fable_lite.cli as old; import smtw.cli as new; assert old is new`까지 포함해야 한다.

### 설치·재설치 검증

- checkout 밖의 깨끗한 venv에 wheel 설치 후 `python -P -m smtw`, `python -P -m fable_lite`, `smtw`, `fable-lite`를 모두 실행한다.
- v2.4.1 wheel에서 v3 wheel로 일반 `pip install --upgrade`와 같은 버전 `--force-reinstall`을 각각 검증한다.
- uninstall 뒤 두 package 디렉터리와 console script가 모두 정리되는지 wheel RECORD를 확인한다.
- source checkout이 site-packages를 가리는 것을 막기 위해 smoke는 반드시 임시 디렉터리에서 `python -P`로 수행한다.

### 예상 diff 규모와 리스크

- rename을 포함해 약 **30~40파일**, 실질 추가/수정 **130~240 LOC**와 경로 rename이 예상된다.
- P0: distribution까지 동시에 rename해 설치본 두 개가 공존, shim이 두 module object를 만들어 상태가 갈림.
- P1: subprocess/prompt에 남은 `python -m fable_lite`, Windows console script 잔존, source-tree smoke의 거짓 양성.
- 결론적으로 이번 통일 릴리스에는 넣되 Q1/Q3/Q4가 안정화된 뒤 독립 commit/PR과 wheel upgrade gate로 진행한다.

## Q3. `FABLE_LITE_*`에서 `SMTW_*`로 환경변수 변경

### 입장

v3에서는 `SMTW_*`를 canonical로 쓰고 legacy `FABLE_LITE_*`를 읽기 alias로 유지한다. 둘 다 있으면 **새 키가 존재하는 것 자체**가 우선한다. 새 값이 빈 문자열 또는 `"0"`이어도 legacy 값으로 fallback하지 않는다.

### 실제 코드 조사와 SSOT

초안의 “7종”보다 실제 `os.environ`/`getenv` 소비 의미 키는 8종이다.

1. `FABLE_LITE_AUTO_MIGRATION` — `core/release_gate.py:11,32-34`
2. `FABLE_LITE_TEST_LOCK_WAIT_SECONDS` — `core/agent_log.py:19,263-271`
3. `FABLE_LITE_DESIGN_GATE` — `core/design_gate.py:17,68-72`
4. `FABLE_LITE_SCORECARD` — `core/verify_state.py:40,477-483`, Claude stop adapter
5. `FABLE_LITE_CODEX_REAPER` — `adapters/codex_cli/stop.py:12,47-53`
6. `FABLE_LITE_CODEX_REAPER_LOG`
7. `FABLE_LITE_CODEX_REAPER_DRY_RUN`
8. `FABLE_LITE_CODEX_REAPER_POWERSHELL`

`{FABLE_LITE_ROOT}`는 환경변수가 아니라 installer/hook template token이므로 별도 `{SMTW_ROOT}` 전환 대상으로 분리한다.

`core/runtime_env.py`에 dependency-free helper를 둔다.

```text
resolve_smtw_env(suffix, environ=None) -> {value, source, key}
if "SMTW_" + suffix in environ: use it
elif "FABLE_LITE_" + suffix in environ: use it
else: absent
```

helper는 raw string과 출처만 반환하고 boolean/float/path 해석은 기존 caller가 맡는다. 기존 SCORECARD처럼 core와 adapter의 opt-in/opt-out 기본값이 다른 곳을 rename 과정에서 임의로 통일하면 안 된다. adapter direct-file 실행은 현재 root injection/activation 이후에만 core helper를 import해 inactive 경로의 “core import 0” 계약을 유지한다.

Codex reaper 부모는 양쪽 키를 해석하되 자식에는 canonical `SMTW_CODEX_REAPER_LOG`를 넘긴다. 훅마다 deprecation warning을 출력하지 않고 `smtw doctor`가 실제 선택된 키와 shadow된 legacy 키를 보여준다. installer의 hook JSON은 foreign entry를 덮지 않으므로 v3에는 owned entry만 atomic update하는 명시적 `--upgrade` 경로가 필요하다.

### 예상 diff 규모와 리스크

- production consumer 8개 의미 키, 새 helper, installer/template, tests/docs를 합쳐 약 **15~25파일, 250~450 LOC**를 예상한다.
- P0: `if new_value:` 식 truthiness fallback으로 `SMTW_X="0"`이 legacy `"1"`에 밀림.
- P1: direct-file adapter import 순서 변경, SCORECARD 기존 기본 의미 변화, reaper parent/child가 서로 다른 env 세대를 봄.
- P2: 매 훅 warning으로 quiet contract 훼손, template token을 실제 env로 오인.

## Q4. malformed config의 우선순위와 fallback

### 입장

우선순위는 **`.smtw.toml` > `pyproject.toml`의 `[tool.smtw]` > `.fable-lite/config.json`**으로 확정한다. 높은 우선순위 source가 SMTW config를 명시했는데 malformed/schema-invalid이면 inactive/corrupt이며 낮은 source로 fallback하지 않는다. 높은 source가 SMTW config를 **명시하지 않은 것**이 확정될 때만 다음 source로 내려간다.

### STATE-01 초안의 재사용과 수정점

현재 미커밋 STATE-01의 `adapters/claude_code/project_config.py:19-39` 우선순위 구조와 `tomllib` 기반 loader(`:64-74`)는 그대로 재사용할 수 있다. 다만 `_load_pyproject()`의 현재 `:79-90`, 특히 parse error를 즉시 corrupt로 반환하는 `:80-81`은 아래 tri-state 계약으로 보완해야 한다.

각 source loader는 `ABSENT`, `VALID(normalized_config)`, `DECLARED_INVALID(detail)` 중 하나를 반환한다.

- `.smtw.toml` 파일이 존재: 그 파일 자체가 명시다. read/UTF-8/TOML/schema 오류는 `DECLARED_INVALID`; fallback 금지.
- valid `pyproject.toml`에 `[tool.smtw]` mapping 존재: 명시다. schema/type 오류는 `DECLARED_INVALID`; fallback 금지.
- valid `pyproject.toml`에 `[tool.smtw]` 없음: `ABSENT`; legacy JSON 또는 상위 탐색 허용.
- malformed `pyproject.toml`이지만 canonical `[tool.smtw]` table header가 없음이 lexical하게 확정: 이 parse 오류는 SMTW와 무관하므로 `ABSENT`; fallback 허용.
- malformed `pyproject.toml`에 canonical `[tool.smtw]` table header가 있음: `DECLARED_INVALID`; fallback 금지.
- 파일을 읽지 못했거나 table header 유무를 신뢰성 있게 판정할 수 없음: 보수적으로 `DECLARED_INVALID`; fallback 금지.

malformed pyproject용 marker probe는 단순 substring/regex가 아니라 TOML comment와 single/multiline string을 건너뛰고 실제 table-header line만 인식해야 한다. `[tool.smtw.fake]`, 주석의 `[tool.smtw]`, 문자열 안 텍스트는 명시로 보지 않는다. 이 작은 lexer가 불편하면 더 단순하고 안전한 대안은 “모든 malformed pyproject를 corrupt”로 처리하는 것이며, legacy로 무조건 내려가는 선택은 fail-open이라 채택하지 않는다.

세 경로에서 읽은 raw 형식은 하나의 normalized config dataclass/schema validator로 합친다. `schema_version`, `supervision: true`와 향후 플래그의 의미가 source마다 달라지면 안 된다. pyproject config digest는 가능하면 전체 파일 bytes가 아니라 canonicalized `[tool.smtw]` table만 대상으로 해, unrelated dependency edit가 supervision config 변경으로 기록되지 않게 한다.

### 필수 테스트

- `.smtw.toml` valid/invalid/unreadable 각각의 precedence와 no-fallback.
- valid pyproject: section 있음/없음/non-table/schema-invalid.
- malformed pyproject: SMTW header 전/후 오류, 주석·일반 문자열·multiline 문자열 false positive, `[tool.smtw.fake]`.
- legacy JSON valid/invalid과 두 modern source 모두 absent인 경우.
- 세 source의 동일 input이 동일 normalized config와 digest를 만드는 contract test.

### 예상 diff 규모와 리스크

- STATE-01 초안을 기준으로 loader 보완 약 **30~70 LOC**, tests **60~120 LOC**가 추가될 전망이다. 공용 config SSOT를 Claude 외 core consumer까지 옮기면 총 **8~15파일, 180~350 LOC**를 잡는 편이 안전하다.
- P0: 명시적으로 망가뜨린 상위 config가 legacy opt-in으로 fallback하여 정책 우회.
- P1: unrelated malformed pyproject가 정상 legacy 프로젝트를 비활성화, marker probe의 comment/string 오탐, source별 schema 의미 불일치.
- P2: 전체 pyproject hash로 불필요한 config churn 발생.

## Q5. legacy 호환 수명과 제거 기준

### 입장

v3.x 전체에서 다음 네 호환면을 유지한다.

- `.fable-lite/` 전체-tree legacy mode와 명시적 copy migration
- 8종 `FABLE_LITE_*` read alias
- `.fable-lite/config.json` 최하위 fallback
- `fable_lite` import/`python -m`/console compatibility shim

v3 훅 실행마다 경고하지 않는다. 대신 `smtw doctor`가 현재 state layout, config source, env source, migration marker/version, legacy 잔존과 충돌을 구조적으로 보여준다. 제거는 최소 두 minor에서 사전 공지하고 v4를 earliest boundary로 둔다.

v4에서 runtime fallback을 제거하더라도 legacy state를 발견하고 조용히 빈 `.smtw/`를 만드는 동작은 금지한다. “legacy state 발견; v3 migrator 또는 explicit compatibility command가 필요”라는 진단으로 멈춰 데이터가 사라진 것처럼 보이지 않게 한다. legacy config 역시 명시적 `smtw config migrate` 또는 사용자의 `.smtw.toml` 생성 전에는 삭제하지 않는다.

영구 fallback은 기각한다. 두 state/config/env control plane이 무기한 남으면 precedence gaming과 진단 표면이 계속 커진다. console script 별칭은 운영 편의를 위해 import shim보다 더 오래 둘 수 있지만, canonical 내부 import는 v3부터 `smtw` 하나여야 한다.

### 예상 diff 규모와 리스크

- doctor/deprecation inventory와 docs/tests 약 **10~20파일, 200~400 LOC**를 예상한다.
- P0: v4가 old state를 미탐지하고 빈 새 프로젝트로 시작.
- P1: 호환 제거 시점이 state/env/config/import마다 달라 문서와 실제 계약이 어긋남.
- P2: 정상 훅에 반복 경고를 넣어 quiet contract 훼손.

## Q6. 실패 복구·rollback·관측 가능성

### 입장

rollback을 “old directory가 남아 있음”으로 정의하면 안 된다. old는 **cutover 순간의 보존 snapshot**이고, 새 state에 한 번이라도 write한 뒤에는 자동 rollback이 데이터 손실을 만들 수 있다. 자동 rollback은 새 marker의 ledger sequence/manifest가 cutover 당시와 완전히 같을 때만 허용한다. 그 외에는 quiesced reverse export/검증 또는 수동 merge 절차가 필요하다.

### 구현 구조와 안전 규칙

- migration receipt/marker 필드: schema version, attempt/migration id, source/target canonical path, phase, source digest/count/bytes, started/completed time, tool version, error stage/type/detail.
- publish 전 실패 기록은 legacy 쪽의 안정된 migration audit 또는 별도 receipt에 atomic write한다. 불완전 target 안의 marker만으로 실패를 기록하지 않는다.
- `COPYING`, `VERIFIED`, `PUBLISHED`, `FAILED(stage)`를 구분하되 권위 판정은 간단히 한다: valid published marker가 있으면 new, 없으면 valid old, 둘이 애매하면 conflict.
- 유효 publish 뒤 old에 새 변화가 생기면 “old diverged”를 관측하지만 자동 merge하지 않는다.
- migration OFF, migration deferred, ON-but-failed를 같은 boolean 결과로 뭉개지 않는다. 로그/doctor/receipt에서 서로 다른 reason code를 제공한다.
- audit write 실패 때문에 정상 hook process를 죽이지는 않되, 상태 선택이 애매한 경우 destructive 판단에는 완화 근거로 쓰지 않는다.

### 검증·리허설 행렬

1. fault injection: manifest 전/중/후, 각 file copy, marker atomic write, rename 직전/직후, publish 후 marker reread 실패.
2. concurrency: 동일 root에서 N개 subprocess가 동시에 migrate; 한 publish만 성공하고 나머지는 idempotent 관찰 또는 명시적 busy여야 한다.
3. unlocked source mutation: goals/contract/manual writer가 copy 중 변경될 때 pre/post manifest mismatch로 publish를 거부한다.
4. platform: Windows open handle, long path, casefold collision, junction/reparse point, readonly file, antivirus성 `PermissionError`; POSIX symlink와 mode.
5. home: exact home와 inactive project에서 target/staging/lock/receipt가 모두 0개인지 확인한다.
6. rollback: publish 전 실패는 old 재사용, publish 후 write 0은 검증된 rollback, publish 후 write 1 이상은 자동 rollback 거부.
7. rehearsal: 실제 ledger 사본을 immutable backup으로 보관하고 기존 행/seq/manifest의 hash·count·bytes, 멱등성, source 불변, 실패 stage 진단을 비교한다. B5 마이그레이션 리허설의 fault-injection/보존 패턴을 재사용할 수 있다.

### 예상 diff 규모와 리스크

- Q6 전용 marker/audit/doctor/fault harness는 Q1에 포함해 약 **250~500 LOC** 비중이다.
- P0: publish 뒤 old로 자동 fallback하여 R2/ledger history 소실, invalid marker를 성공으로 간주.
- P1: receipt write 실패가 실제 migration 성공/실패를 가림, source 변화 감지 범위 부족, Windows rename 성공 직후 crash 상태 미분류.
- P2: stale staging 자동 정리가 살아 있는 migrator의 파일을 제거.

## 권장 구현 순서와 PR 경계

1. **Foundation PR**: state/config/env 이름 상수, read-only layout inspector, generic owner lock, 두 runtime 이름의 destructive/provenance 보호. 아직 migration cutover는 하지 않는다.
2. **State migration PR**: staging copy, manifest/marker, explicit migrate, crash/race/home 테스트. legacy 프로젝트는 기본적으로 legacy 전체-tree mode를 유지한다.
3. **Consumer SSOT PR**: ledger/provenance/contracts/goals/intent/scorecard/audit/adapter 경로를 layout facade로 전환하고 eval/fixtures를 갱신한다.
4. **Env PR**: `runtime_env` helper, 8종 alias, installer template upgrade와 doctor 진단.
5. **Config PR**: 현재 STATE-01 초안을 재사용하되 Q4 tri-state malformed 계약과 공용 normalized schema를 반영한다.
6. **Python package rename PR**: `smtw` canonical package, `fable_lite` identity shim, 두 console entry, clean-wheel upgrade/reinstall/uninstall gate.

이 순서는 각 단계에서 기존 경로를 계속 읽을 수 있어 bisect와 rollback이 가능하고, 상태 전환과 packaging 전환이라는 두 큰 실패 영역을 분리한다. 실제 feature flag를 둔다면 새 writer를 두 트리에 나눠 쓰는 flag가 아니라 “migration command 노출/자동 준비 허용”만 제어해야 한다.

## 전체 규모 산정과 승인 조건

- 역사 문서를 무차별 치환하지 않고 production/tests/eval/current docs를 대상으로 할 때 전체는 대략 **60~90파일, 1,500~2,300 LOC + package rename** 규모다. 기존 이름을 의도적으로 보존하는 상수, shim, legacy fixture는 literal-zero 검사에서 예외 allowlist로 관리한다.
- 반드시 먼저 고정할 contract tests:
  - 프로젝트마다 권위 state tree가 정확히 하나
  - exact home/inactive는 write 0
  - 동시 migrator 한 winner, source mutation 시 publish 0
  - valid publish 후 legacy fallback 0
  - `.smtw`, `.fable-lite`, staging 모두 destructive hard-block
  - env는 key-presence precedence
  - explicit malformed modern config는 legacy fallback 0
  - old/new import module identity 동일
- 각 PR은 좁은 회귀 테스트, 전체 pytest, ruff, strict probes, e2e를 통과해야 한다. state migration과 package rename PR에는 각각 Windows subprocess race/crash rehearsal와 clean-wheel upgrade smoke를 별도 필수 gate로 둔다.

## Show-me-the-work 판단 기록

- **가설 1:** “새 경로 우선 + old 파일별 fallback이면 점진 전환이 쉽다.” 실제 ledger/snapshot/manifest가 transaction generation을 공유하고 goals/intent가 별도 writer라 한 파일만 새로 생겨도 세대가 섞인다. **기각했다.**
- **가설 2:** “디렉터리 rename이면 가장 원자적이다.” 성공 시 cutover 경계는 단순하지만 현재 lock이 source 내부에 있고 Windows 열린 handle, 구버전 writer, legacy config 이동, rollback 부재가 겹친다. 온라인 자동 방식으로는 **기각했다.**
- **가설 3:** “원본 보존 copy가 가장 보수적이다.” 직접 target copy는 crash 시 불완전 디렉터리를 남기므로 부족하다. stable lock, sibling staging, pre/post manifest, marker, target-no-overwrite publish를 결합하면 실패 전에는 old, 성공 후에는 new 하나만 권위 있게 만들 수 있다. 이 수정안을 **채택했다.**
- **추가 기각:** 영구 dual-read/write, publish 후 silent fallback, 단순 `fable_lite = smtw` alias, import package와 distribution 동시 rename, malformed modern config의 무조건 legacy fallback.
- **재사용 가능한 증거:** `ledger_storage`의 same-directory temp+replace, `agent_log`의 owner-token/PID/stale-lock, Claude `atomic_file`의 fsync/replace 패턴, B5의 immutable-copy/idempotence/fault-injection rehearsal. 단 adapter 구현을 core에서 직접 import하지 말고 core 공용 primitive로 옮긴다.
