# show-me-the-work v2.6.1 안전 경계·문서·릴리스 안정화 작업 지시서

> 대상 저장소: [pinetreeB/show-me-the-work](https://github.com/pinetreeB/show-me-the-work)  
> 제품명: **show-me-the-work**  
> canonical CLI / Python package: `smtw`  
> legacy distribution / compatibility surface: `fable-lite`, `fable_lite`  
> 검토 기준: 2026-07-22 `main`, 기준 최신 커밋 `d688304e79ba3a2ce904cb19e5fd829c733254d7`  
> 기준 제품 버전: `2.6.0`  
> 검토한 주요 PR: #6~#18  
> 확인한 CI: PR #18 GitHub Actions run #102, Ubuntu·Windows 성공  
> 목적: 기존 외부 리뷰를 통해 개선된 v2.4~v2.6 상태를 유지하면서, 새로 확인된 R2 우회·상태 migration race·stale turn·quarantine·goals identity 결함과 README·CLI·Release·CI·리뷰 프로세스의 잔여 부채를 **신규 기능 없이** 폐쇄한다.

---

# 0. 최종 판정

현재 `show-me-the-work`는 초기 리뷰 당시보다 확실히 좋아졌다.

이미 다음 수준까지 올라왔다.

- 작은 단위 PR
- regression-first 테스트
- Ubuntu·Windows CI
- 결정론 probe
- 실제 hook-chain E2E
- wheel RECORD 검사
- 깨끗한 가상환경 wheel 설치
- `smtw`·`fable-lite` CLI 양쪽 smoke
- `fable_lite` compatibility shim
- 명시적 `.fable-lite → .smtw` 상태 migration
- runtime env·config precedence 통합
- 이전 리뷰 debt의 다수 실제 폐쇄

하지만 핵심 안전 경계에서 다음 문제가 남아 있다.

```text
R2 shell syntax 우회
상태 migration publish 순간 write 유실
StaleTurn mutation 미등록 허용
quarantine 동시 overwrite
goals identity 복구 UX 실패
```

따라서 현재 판정은 다음과 같다.

```text
개인·샌드박스 사용: GO
단일 에이전트 일반 사용: GO
중요 멀티에이전트 저장소: CONDITIONAL GO
전사 공통 글로벌 훅: v2.6.1 P0 폐쇄 전 NO-GO
```

---

# 1. 목표 릴리스

권장 릴리스:

```text
v2.6.1 — Safety Boundary & Documentation Stabilization

No new features.
Shell parsing, state migration write barrier, stale-turn denial,
quarantine durability, identity-aware goals recovery,
README truthfulness, CLI onboarding, release hygiene.
```

P0가 모두 green이 되기 전에는 다음을 시작하지 않는다.

- 신규 gate
- 신규 task mode
- 신규 agent host
- 신규 scorecard view
- state schema 확장
- 자동 migration
- legacy 제거
- v3/v4 breaking release
- 마케팅 문구 확대

---

# 2. 이미 해결된 영역 — 재구현 금지

다음은 기존 PR·코드·CI에서 해결된 것으로 본다. 이번 트랙에서는 **회귀만 검증**하고 다시 설계하지 않는다.

| ID | 해결된 내용 |
|---|---|
| R2-01 | `&&`, `||`, `;`, `|` 뒤의 파괴 command segment 검사 |
| R2-02-basic | 정확한 토큰 `-f`, `--force` checkout 차단 |
| AUDIT-01 | R2 deny 감사 기록의 장기 ledger-lock 대기 제거 |
| ACT-01 | session root mismatch 프로젝트별 opt-in 재검사 |
| R2-03-basic | peer candidate의 project-relative canonicalization |
| CODEX-01/02 | recovered Codex identity 보정·contract authorship 순서 |
| SCORE-01 | coordination first/last timestamp `min/max` |
| R2-04 | symlink된 state dir lexical 보호 |
| HINT-01-basic | literal `Path.rename/replace/open` 쓰기 탐지 |
| REL-01-basic | stale `uv.lock` 제거·버전 표면 검사 |
| DOC-01-basic | opt-in·2회 block 후 fail-open 문구 정정 |
| PKG-01-basic | `smtw`, `fable-lite` console script와 `version` |
| STATE-Q1 | `.fable-lite → .smtw` explicit copy/verify/publish migration |
| STATE-Q3 | `SMTW_*` canonical + `FABLE_LITE_*` alias·충돌 fail-closed |
| STATE-Q4 | `.smtw.toml` > `pyproject [tool.smtw]` > legacy config |
| MODULE-Q2 | canonical Python package `smtw`, `fable_lite` shim |
| B1-basic | R2 차단 command quarantine 저장·CLI list/show/clear |
| B4-basic | identity별 goals namespace |
| B5-basic | missing invocation status fail-closed·lease-aware backfill |

이번 지시서의 항목은 위 해결을 되돌리는 것이 아니라 **해결 범위 밖의 실제 경계**를 닫는 것이다.

---

# 3. 절대 불변식

## INV-01 — R2 fail-closed를 약화하지 않는다

파괴형 command를 파싱할 수 없으면 기존대로 fail-closed한다.

```text
새 parser 도입
≠
unknown command allow
```

## INV-02 — deny 결정은 보조 I/O와 무관하다

다음 실패가 R2 block을 allow로 바꾸면 안 된다.

- quarantine 실패
- audit 실패
- scorecard 실패
- log 실패
- state receipt 실패

## INV-03 — 한 시점에 하나의 state authority만 존재한다

```text
LEGACY    -> .fable-lite
NATIVE    -> .smtw
MIGRATED  -> .smtw
```

일반 writer와 migration이 authority를 서로 다르게 볼 수 없어야 한다.

## INV-04 — state migration 중 성공한 write를 유실하지 않는다

migration 시작 이후 성공을 반환한 모든 state write는 최종 authoritative tree에 존재해야 한다.

## INV-05 — mutation-capable tool은 현재 turn에 등록돼야 한다

`begin_invocation()`이 `StaleTurn`을 반환한 mutation은 allow하지 않는다.

## INV-06 — quarantine 성공 메시지는 사실이어야 한다

```text
완전 보관
부분 보관
보관 실패
```

를 구분한다.

## INV-07 — 안내한 복구 명령은 실제 gate를 해소해야 한다

N2가 identity별 goals를 요구하면 사용자·AI에게 identity가 완성된 실행 가능한 명령을 제공해야 한다.

## INV-08 — 상위 config가 깨졌으면 하위 config로 조용히 fallback하지 않는다

canonical SMTW 선언이 있었으나 parse할 수 없으면 `DECLARED_INVALID`다.

## INV-09 — 문서는 current code를 설명해야 한다

README가 다음을 잘못 말하면 release gate 실패다.

- state authority
- config precedence
- migration
- fail-open/fail-closed
- CLI command
- compatibility
- host status
- release version

## INV-10 — review finding은 증거 없이 닫지 않는다

각 finding은 다음 중 하나여야 한다.

```text
FIXED
NOT_REPRODUCIBLE_WITH_PROOF
ACCEPTED_RISK
SUPERSEDED
```

---

# 4. 우선순위 요약

| ID | 우선순위 | 항목 | 위험 |
|---|---:|---|---|
| R2-05 | P0 | shell command-position parser | 단일 `&`, newline, `command`, `exec`, control word 우회 |
| R2-05B | P0 | Git clustered short force flags | `-qf`, `-fB`, `-Bf`로 dirty work 폐기 |
| STATE-02 | P0 | 모든 state writer와 migration의 공통 write barrier | migration 성공 후 최신 write 유실 |
| STALE-01 | P0/P1 | mutation-capable `StaleTurn` 즉시 deny | 현재 turn에 등록되지 않은 mutation 허용 |
| QUAR-01 | P1 | quarantine 목적지 원자적 예약 | 동시 block command 서로 overwrite |
| GOALS-02 | P1 | active identity 자동 유도·완전 recovery command | 안내대로 plan해도 N2 미해소 |
| R2-06 | P1/P2 | `tee -a`, output-only tee 오탐 제거 | 정상 command 불필요 차단 |
| CONFIG-02 | P1 | quoted/dotted TOML declaration 검출 | 깨진 상위 config가 legacy로 fallback |
| MIGRATION-02 | P1/P2 | invocation-status archive rotation | mixed-version 두 번째 backfill 영구 실패 |
| ATTR-02 | P1/P2 | logical candidate와 resolved R2 key 분리 | symlink replace attribution 누락 |
| HINT-02 | P2 | shell prefix·`Path as P` AST alias | 상태파일 friction 미탐 |
| QUAR-02 | P2 | truncation metadata·정직한 메시지 | 불완전 백업을 완전 보관이라 주장 |
| DOC-02 | P1 | README·README.ko 전면 정합화 | 사용자가 잘못된 state/config/contract 이해 |
| CLI-02 | P1 | `smtw doctor`, `init`, `status` | first-run·운영 진단 부재 |
| REL-02 | P1 | GitHub Release·tag·main 버전 동기화 | 공개 최신 버전이 2.1.0에 머묾 |
| REL-03 | P2 | ignored local `uv.lock` 처리 | 로컬 ignored 파일로 release check 실패 |
| CI-02 | P1/P2 | Python 3.12~3.14·tool pin | 지원 범위 회귀·비재현 CI |
| GOV-01 | P1 | review conversation merge gate | 자동 리뷰 P1이 남은 채 merge |
| COMPAT-01 | P2 | source checkout version semantics | 설치된 구버전 metadata를 출력 |
| COMPAT-02 | P2 | legacy submodule `python -m` 범위 | package-level 호환과 submodule 실행 혼동 |
| HOST-01 | P2 | Antigravity 실제 host receipt | payload conformance만 있고 live execution 미확인 |

---

# 5. P0 작업

## R2-05 — shell command-position parser

### 현재 확인된 우회

다음은 파괴 command가 실행되지만 현재 parser가 0건으로 판정할 수 있다.

```bash
echo ok & rm peer.py

echo ok
rm peer.py

command rm peer.py
exec rm peer.py

{ rm peer.py; }

if true; then rm peer.py; fi

for x in 1; do rm peer.py; done
```

현재 segment separator는 사실상 다음만 처리한다.

```text
&&
||
;
|
```

단일 `&`와 newline을 command boundary로 다루지 않고, 각 segment의 첫 head가 `command`, `exec`, `then`, `do`, `{`인 경우 실제 command position을 찾지 못할 수 있다.

### 수정 목표

문자열 검색을 계속 덧붙이지 말고 **command-position tokenizer/state machine**을 구현한다.

최소 상태:

```text
EXPECT_COMMAND
IN_ARGUMENTS
AFTER_SEPARATOR
AFTER_CONTROL_WORD
IN_SINGLE_QUOTE
IN_DOUBLE_QUOTE
ESCAPED
```

### command boundary

지원:

```text
&
&&
||
;
|
\n
\r\n
```

quote·escape·substitution 안의 연산자는 boundary로 취급하지 않는다.

### command-position prefix

실제 command 이전에 올 수 있는 prefix:

```text
VAR=value
env
command
exec
sudo
doas
nohup
nice
ionice
stdbuf
timeout
setsid
```

각 wrapper의 option·value 계약을 명시한다.

### shell control word

최소 다음 뒤에는 새 command position이 열린다.

```text
then
do
else
elif
{
(
!
```

`fi`, `done`, `}` 등 종료 토큰은 command가 아니다.

### nested shell

기존 지원을 유지한다.

```bash
bash -c "echo ok; rm peer.py"
sh -c "rm peer.py"
pwsh -Command "Remove-Item peer.py"
cmd /c "del peer.py"
```

### 금지 접근

- command string에 `"rm"`이 있으면 무조건 block
- 모든 unknown shell을 block
- Python·Node script 문자열 속 데이터까지 command로 오탐
- quote parser를 regex 하나로 대체
- 기존 benign corpus 삭제

### 필수 회귀 테스트

```python
@pytest.mark.parametrize(
    "command",
    [
        "echo ok & rm peer.py",
        "echo ok\nrm peer.py",
        "command rm peer.py",
        "exec rm peer.py",
        "{ rm peer.py; }",
        "if true; then rm peer.py; fi",
        "for x in 1; do rm peer.py; done",
    ],
)
def test_r2_detects_destructive_commands_at_every_shell_command_position(command):
    ...
```

quote no-regression:

```python
'echo "a & rm peer.py"'
'python -c "print(\'rm peer.py\')"'
'printf "x\ny"'
```

### adapter boundary 테스트

Claude, Codex, Antigravity 중 최소 두 adapter subprocess에서:

```text
peer-owned target
+ command-position 우회
-> deny/block
```

을 검증한다.

### 완료 조건

- 위 우회 전부 block
- quote no-regression
- wrapper no-regression
- full R2 corpus green
- execution latency budget 유지

---

## R2-05B — Git clustered force flags

### 현재 확인된 우회

```bash
git checkout -qf main
git checkout -fB newbranch
git checkout -Bf newbranch
```

Git은 위 short-option cluster를 받아들이며 `f`가 포함되면 작업 트리 변경을 버릴 수 있다.

현재 exact-token 검사:

```text
-f
--force
```

만으로는 부족하다.

### 수정 계약

checkout option parser를 둔다.

```text
-f          force
-qf         q + f
-fB         f + B
-Bf         B + f
--force     force
--no-force  force 해제
```

주의:

- option value를 갖는 short flag와 cluster 규칙 구분
- `--` 이후는 pathspec
- `-b`, `-B`만 있고 `f`가 없으면 기존 branch creation 허용
- `--no-force`는 force로 오판하지 않음
- global git option은 별도 parser 또는 fail-closed 유지

### 테스트

```python
@pytest.mark.parametrize(
    "command",
    [
        "git checkout -qf main",
        "git checkout -fB newbranch",
        "git checkout -Bf newbranch",
    ],
)
def test_r2_blocks_clustered_checkout_force(command):
    ...
```

allow:

```text
git checkout -q main
git checkout -B newbranch
git checkout -b feature/x
git checkout --no-force main
```

---

## STATE-02 — state migration write barrier

### 재현된 실패

migration이 publish 직전인 순간 legacy state에 write하면 다음이 가능하다.

```text
migration result: migrated
authority: .smtw

.smtw/goals.json:       old
.fable-lite/goals.json: new-written-at-publish-boundary
```

호출자는 write 성공을 받았지만 최신 값은 authority 밖에 남는다.

### 원인

- migration은 project-level migration lock과 legacy `ledger.lock`을 잡음
- 일반 state writer는 migration lock을 공유하지 않음
- `MIGRATING` 동안 `state_dir()`은 legacy를 반환
- 마지막 source manifest 확인과 `os.rename(staging, target)` 사이에 일반 write 가능
- publish 뒤 `.smtw`가 authority가 되어 legacy 최신 write가 보이지 않음

### 목표

모든 state mutation이 migration과 같은 **layout write barrier**를 공유한다.

### 권장 primitive

```python
@contextmanager
def state_write_scope(project_root: str | Path):
    with layout_read_or_write_lock(...):
        authority = state_dir(project_root)
        yield authority
```

단순 read/write lock 구현이 과하면 exclusive project layout lock으로 시작할 수 있다.

### 적용 대상

최소:

```text
ledger
goals
intent
contract
agent logs
scorecard
coordination
quarantine
snapshots
turn baselines
provenance config
session registry
migration receipts
```

### 중요

writer가 lock을 얻기 전에 계산한 state path를 재사용하면 안 된다.

```text
lock 획득
-> authority 재판정
-> write
```

순서여야 한다.

### migration

```text
layout lock 획득
-> quiescence
-> source lock
-> manifest/copy/verify
-> publish
-> release
```

### 성능 고려

일반 hook마다 장기 lock 대기는 금지한다.

- state write는 짧게
- migration은 명시 CLI에서만 장기
- migration 중 hook writer는 짧게 기다린 뒤 명확한 health/block 정책
- deny decision은 lock 때문에 지연되지 않음
- read-only observer는 authority snapshot을 얻는 전략 명시

### 필수 race 테스트

#### Deterministic publish-boundary

```text
before_publish fault hook
-> goals write 동시 실행
-> migration 완료
-> 최종 authority에 write 존재
```

#### Multi-process

```text
1 migration process
8 writer process
각 100회 unique value/event
-> 성공 반환된 write 전부 최종 authority에 존재
```

#### Crash cuts

- lock 획득 후 crash
- staging copy 중 crash
- marker write 후 crash
- rename 후 crash
- receipt 실패

### 완료 조건

- 성공 반환 write 유실 0
- authority split 0
- legacy fallback 부활 0
- R2/state protection 유지
- Windows·POSIX green

---

## STALE-01 — mutation-capable `StaleTurn` 즉시 deny

### 현재 위험

흐름:

```text
resolve_active_invocation()
-> 새 prompt가 active turn 교체
-> begin_invocation() returns StaleTurn
-> invocation.identity_conflict는 이전 resolve 기준 false
-> mutation allow
```

현재 조건이 다음처럼 `identity_conflict`까지 요구하면 race를 놓친다.

```python
StaleTurn
and identity_conflict
and mutation_capable
```

### 수정

```python
if observation.error_kind == "StaleTurn" and invocation.mutation_capable:
    deny
```

`identity_conflict`는 message detail로만 사용한다.

### 적용 adapter

- Claude Code
- Codex CLI
- Antigravity

### read-only 정책

proven read-only command는 기존 정책을 유지할 수 있다.

mutation 여부가 불명확하면 보수적으로 mutation-capable로 처리한다.

### 필수 deterministic 테스트

fault injection:

```text
resolve 완료
-> active turn 교체
-> begin invocation
```

검증:

```text
mutation -> deny
read-only -> 기존 정책대로 allow 가능
ledger invocation 등록 0
mutation 실행 allow 0
```

---

# 6. P1 작업

## QUAR-01 — quarantine 원자적 목적지 예약

### 재현

동일 agent·동일 초에 64 process가 서로 다른 command를 저장:

```text
호출 성공 반환:   64
실제 파일:        약 15
유실 command:     약 49
```

원인:

```text
exists check
-> 동일 filename 선택
-> os.replace가 기존 destination overwrite
```

### 수정안

권장 파일명:

```text
blocked-<timestamp>-<agent>-<uuid>.txt
```

그리고 최종 생성은 원자적으로 예약한다.

```python
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
```

또는 `"xb"`.

temp→rename을 유지하려면 예약 placeholder와 ownership 검증이 필요하다.

### 추가 요구

- file permission 0600 best effort
- UUID collision retry
- Windows 지원
- GC가 방금 생성한 파일을 잘못 지우면 성공 반환 금지
- success path는 실제 존재·size·digest 확인

### 테스트

```text
64 process × 동일 timestamp × 동일 agent
-> 64 unique file
-> 64 unique body
-> overwrite 0
```

---

## GOALS-02 — identity-aware goals CLI와 recovery UX

### 현재 위험

두 exact identity가 활성인 프로젝트에서 identity 없이:

```bash
python goals/goals.py plan ...
```

을 실행하면 legacy `goals.json`에 기록된다.

N2 gate는 multi-identity에서 legacy file을 무시한다.

결과:

```text
안내대로 plan 실행
-> 다시 N2 block
-> 최대 2회 후 fail-open
```

### 목표 UX

canonical command를 제공한다.

```bash
smtw goals plan ...
smtw goals verify ...
smtw goals status ...
```

### active identity 자동 유도

규칙:

1. exact active turn 1개 → 자동 선택
2. 현재 host/session env 또는 hook receipt로 유일하게 식별 → 자동 선택
3. exact active turn 2개 이상 → 목록 출력 후 `--identity` 요구
4. legacy single-agent → legacy fallback
5. synthetic/ambiguous → 실패를 성공처럼 쓰지 않음

### denial message

복사 가능한 완전한 command를 제공한다.

```text
[smtw] N2 checkpoint required.

Run:
smtw goals plan \
  --root "<root>" \
  --identity "<exact-identity>" \
  --goal "..." \
  --story "..." \
  --verify-cmd "..."
```

목표·story를 자동 생성할 수 없다면 placeholder와 설명을 분리한다.

### CLI 계약

```text
--identity
또는
--host + --session-id + --agent
```

둘을 동시에 주면 일치 검증.

### 테스트

- exact 1개 자동선택
- exact 2개 ambiguity
- identity 완전 command
- legacy single identity
- wrong identity
- foreign identity checkpoint 차용 불가
- plan 후 N2 즉시 recover

---

## R2-06 — benign `tee` 처리

### 현재 review debt

다음은 파일을 덮어쓰지 않는다.

```bash
tee -a log.txt
tee --append log.txt
tee
```

단독 `tee`는 stdout mirror이고 `-a`는 append다.

모든 `tee` head를 unresolved truncation으로 block하면 false positive다.

### 목표

- output file이 있고 overwrite mode → destructive-shaped
- append mode → R2 truncation 대상 아님
- output operand 없음 → 대상 없음
- unknown dynamic option/operand → 필요한 경우 fail-closed
- pipeline `... | tee file`와 standalone 동일 parser 공유

### 테스트

block:

```text
tee file.txt
echo x | tee file.txt
```

allow:

```text
tee -a file.txt
tee --append file.txt
tee
tee --help
```

---

## CONFIG-02 — quoted·dotted TOML declaration 검출

### 현재 실패

malformed pyproject:

```toml
[tool."smtw"]
supervision = true
broken =
```

또는:

```toml
tool.smtw.supervision = true
broken =
```

canonical 선언을 인식하지 못해 legacy config로 fallback할 수 있다.

### 정책

pyproject parse가 실패했을 때:

- SMTW declaration이 없음을 확실히 증명할 수 있을 때만 `ABSENT`
- SMTW declaration 가능성이 있으면 `DECLARED_INVALID`
- false fallback보다 conservative invalid 우선

### 지원할 equivalent syntax

```toml
[tool.smtw]
[tool."smtw"]
["tool".smtw]
["tool"."smtw"]
tool.smtw.supervision = true
tool."smtw".supervision = true
```

comments·strings 속 가짜 text는 선언으로 보지 않는다.

### 구현

정규식 하나보다 parse-error tolerant lexical scanner를 권장한다.

- TOML comments
- basic/literal strings
- table header
- dotted keys
- quoted key segment

를 최소한 구분한다.

### 테스트

각 syntax + malformed tail + legacy `supervision=true`.

기대:

```text
DECLARED_INVALID
legacy fallback 없음
```

---

## MIGRATION-02 — invocation-status archive rotation

### 현재 실패

첫 status backfill:

```text
ledger.v2-invocation-status.json.bak 생성
```

이후 구버전 process가 status-less invocation 추가.

두 번째 backfill:

```text
existing archive differs from ledger.json
```

로 실패한다.

### 목표

각 distinct pre-migration state를 별도 archive로 보존한다.

권장:

```text
ledger.v2-invocation-status.<source-sha256>.bak
```

manifest/index:

```json
{
  "schema_version": 1,
  "archives": [
    {
      "digest": "...",
      "path": "...",
      "created_at": "...",
      "reason": "invocation_status_backfill"
    }
  ]
}
```

### retention

- 동일 digest 재사용
- 최대 개수
- 최대 총 bytes
- 가장 오래된 archive GC
- 현재 ledger에 필요한 rollback source는 GC 금지

### 테스트

- 첫 backfill
- old writer
- 둘째 backfill
- 셋째 identical replay
- archive write crash
- main write crash
- restore
- concurrent backfill

---

## ATTR-02 — logical candidate와 resolved R2 key 분리

### 현재 위험

```text
link.txt -> real.txt
```

candidate를 resolve하면 `real.txt`만 저장된다.

도구가 symlink 자체를 atomic replace하면 delta는 `link.txt`이며 candidate filter와 일치하지 않을 수 있다.

### 모델

```text
candidate_logical_paths
- tool이 명시한 project-relative lexical path
- PostTool attribution filter용

candidate_resolved_paths
- 현재 filesystem resolve key
- R2 peer matching용
```

### 요구

- 같은 path가 둘에 존재 가능
- out-of-root resolved target은 R2 정책에 따라 별도 처리
- logical path는 traversal normalize하되 symlink resolve하지 않음
- Windows case policy 일치
- legacy ledger read migration

### 테스트

- symlink replace
- symlink target content edit
- relative/absolute
- Windows casefold
- broken symlink
- out-of-root symlink

---

# 7. P2 작업

## HINT-02 — inline Python wrapper·alias

### 현재 미탐

```bash
env python -c "..."
X=1 python -c "..."
command python -c "..."
python -c "from pathlib import Path as P; P(...).replace(P(...))"
```

### 요구

#### executable prefix parser

R2의 command-position parser를 재사용해 실제 Python executable을 찾는다.

중복 shell parser를 만들지 않는다.

#### AST binding

추적:

```python
from pathlib import Path
from pathlib import Path as P
import pathlib
import pathlib as pl
```

인식:

```python
Path(...)
P(...)
pathlib.Path(...)
pl.Path(...)
```

이 기능은 friction이며 authorization boundary가 아니라는 문구 유지.

---

## QUAR-02 — truncation metadata

### 현재 문제

1 MiB 초과 command tail을 자르고도 완전 보관 메시지를 출력한다.

### record metadata

```text
original_bytes
stored_bytes
original_sha256
stored_sha256
truncated: true|false
encoding
```

### 정책

권장:

- 기본은 command 전체가 max를 넘으면 `incomplete` record
- multi-part chunk를 구현한다면 manifest·순서·digest 필요
- partial file을 자동 apply하지 않음
- CLI `show`가 truncation 경고를 먼저 출력

### message

완전:

```text
Blocked content preserved completely.
```

부분:

```text
Blocked content was only partially preserved; do not apply as a complete command.
```

---

## REL-03 — ignored local `uv.lock`

현재 정책이 “repository에 `uv.lock`을 commit하지 않는다”라면 ignored local file 존재만으로 release check를 실패시키지 않는다.

### 선택지

#### A. tracked 여부 검사

```bash
git ls-files --error-unmatch uv.lock
```

tracked일 때만 실패.

#### B. 로컬 파일도 금지

그렇다면 `.gitignore`와 CONTRIBUTING의 설명을 바꿔 명시적으로 금지.

현재처럼:

```text
.gitignore에는 넣음
하지만 존재하면 CI/local check 실패
```

는 혼란스럽다.

A를 권장한다.

---

## COMPAT-01 — source checkout version

현재 `package_version()`은 설치된 distribution metadata를 먼저 본다.

새 source checkout + 오래된 global non-editable install 조합에서 source code는 새 버전인데 version은 구버전을 출력할 수 있다.

### 선택지

#### A. source tree 우선

인접 `pyproject.toml`과 `.git` 또는 source marker가 있으면 source version.

#### B. unsupported 유지

그러면 `smtw doctor`가 다음을 명확히 경고한다.

```text
module path
distribution path
module version
distribution version
mismatch
```

A가 사용자 경험은 더 좋다.

---

## COMPAT-02 — legacy submodule `python -m`

현재 다음은 지원된다.

```text
python -m fable_lite
import fable_lite.cli
```

다음은 loader alias 문제로 실패할 수 있다.

```text
python -m fable_lite.cli
python -m fable_lite.scorecard
python -m fable_lite.migrate
```

### 결정

#### 지원

각 submodule에 실제 thin shim file을 둔다.

#### 비지원

README compatibility 표에 명시한다.

```text
Supported:
- import fable_lite
- import fable_lite.<public-module>
- python -m fable_lite
- fable-lite CLI

Not supported:
- python -m fable_lite.<submodule>
```

현재 “all public submodules are aliases”라는 문구와 실행 호환을 혼동하지 않게 한다.

---

## HOST-01 — Antigravity live receipt

현재 상태를 계속 정직하게 표시한다.

```text
payload injection conformance: confirmed
host schema parse/load: confirmed
actual host execution of config-path hooks: unconfirmed
```

후속으로 실제 host receipt를 만들 수 있다면:

- 실제 hook execution
- 5 handler
- block/allow
- path placeholder
- timeout
- Windows
- version

을 기록한다.

확인 전 “live supported”로 올리지 않는다.

---

# 8. README·README.ko 전면 개선 — DOC-02

이 항목은 문장 몇 줄 수정이 아니라 **제품의 현재 mental model을 다시 설명하는 작업**이다.

---

## 8.1 현재 제거·수정할 stale 문구

### stale state path

현재와 맞지 않는 표현:

```text
single state dir .fable-lite/
User state remains under .fable-lite/
internal state path remains .fable-lite/
```

수정:

```text
새 프로젝트의 canonical runtime state는 `.smtw/`.
미이관 legacy 프로젝트는 `.fable-lite/`.
명시적 `smtw migrate` 이후 authority는 `.smtw/`.
legacy activation config `.fable-lite/config.json`은 compatibility 기간 동안 fallback source로 남을 수 있음.
```

### stale contract path

현재와 맞지 않는 고정 표현:

```text
.fable-lite/contract.json
```

수정:

```text
현재 authoritative state tree 아래 identity-namespaced contract
예: `<state-dir>/contracts/<identity>.json`
```

정확한 public path contract가 아니라면 내부 path를 표에서 과도하게 고정하지 않는다.

### stale alias/design 문구

다음은 이미 구현됨:

```text
public alias is still at design stage
```

삭제.

### false “every hook fail-open”

runtime env conflict 등 일부는 의도적으로 fail-closed다.

수정 표:

| 경계 | 정책 |
|---|---|
| 일반 observer·health 오류 | 대체로 fail-open + warning |
| R2 destructive ambiguity | fail-closed |
| canonical/legacy env conflict | fail-closed |
| Stop max 2 blocks | 이후 fail-open |
| state layout conflict | authority 없음·관련 작업 block/degraded |

### activation 문구

legacy config만 안내하지 않는다.

canonical 순서:

```text
1. `.smtw.toml`
2. `pyproject.toml` `[tool.smtw]`
3. legacy `.fable-lite/config.json`
```

---

## 8.2 README 권장 구조

### 1. 한 문장 제품 설명

```text
show-me-the-work는 AI가 “완료했다”고 말하기 전에 실제 실행 증거가 있는지 검사하는 로컬 훅 기반 작업 감독 도구입니다.
```

### 2. 보장하는 것 / 보장하지 않는 것

보장:

- 실제 verification command 실행 관측
- changed-after-verification 탐지
- 일부 high-risk contract
- R2 peer destruction 보호
- 최대 2회 completion bounce
- audit evidence

비보장:

- 코드 정답
- DB·network remote side effect 완전 관측
- deliberate arbitrary-code evasion 완전 방어
- 인간 검토 대체
- 모든 host live 지원
- 모든 shell grammar 완전 해석

### 3. 이름·패키지·경로 표

| 표면 | canonical | legacy |
|---|---|---|
| 제품 | show-me-the-work | fable-lite 과거명 |
| CLI | `smtw` | `fable-lite` |
| Python import | `smtw` | `fable_lite` |
| distribution | 당분간 `fable-lite` | 동일 |
| runtime state | `.smtw/` | `.fable-lite/` |
| config | `.smtw.toml`, `[tool.smtw]` | `.fable-lite/config.json` |
| env | `SMTW_*` | `FABLE_LITE_*` |

### 4. 1분 설치

```bash
git clone ...
claude plugin marketplace add ...
claude plugin install ... --scope project
smtw init
smtw doctor
```

`init` 미구현 상태에서 위를 문서화하지 않는다. CLI-02와 동시에 반영한다.

### 5. canonical config 예시

`.smtw.toml`:

```toml
schema_version = 1
supervision = true
```

또는 pyproject:

```toml
[tool.smtw]
schema_version = 1
supervision = true
```

legacy 예시는 별도 “기존 프로젝트” 섹션으로 내린다.

### 6. state authority와 migration

설명:

```text
EMPTY
LEGACY
NATIVE
MIGRATING
MIGRATED
CONFLICT
```

명령:

```bash
smtw status
smtw migrate --root .
smtw doctor --root .
```

migration이 자동이 아니라는 점, active turn/open invocation이면 defer된다는 점, source를 즉시 삭제하지 않는다는 점을 설명한다.

### 7. quick operational commands

```bash
smtw doctor
smtw status
smtw migrate
smtw quarantine list
smtw scorecard
smtw goals status
```

### 8. gate 표

각 gate에:

- event
- evidence
- block cap
- fail policy
- Known Limitation

을 표시한다.

### 9. multi-agent

- identity
- candidate ownership
- settlement
- R2
- goals namespace
- quarantine
- stale turn
- migration 중 작업 금지/자동 barrier

### 10. compatibility

- v3.x legacy read window
- v4 제거 예정은 확정된 경우에만
- `fable_lite` warning
- strict warnings unsupported
- source/global metadata mismatch 정책
- legacy submodule `-m` 범위

### 11. performance

- inactive global install은 Python startup 비용이 있음
- project scope 권장
- 10k/256MiB envelope
- full reconciliation 시간
- state layout inspection cost

### 12. privacy

state에 저장될 수 있는 정보:

- command
- path
- prompt-derived intent/contract
- agent log
- scorecard
- quarantine content

보존·삭제:

```bash
smtw status
smtw quarantine clear
```

전체 state 삭제의 위험과 비활성화 방법을 설명한다.

### 13. development / verification

현 CI와 동일하게:

```bash
ruff
pytest
probes
e2e
compileall
build wheel
wheel RECORD
clean install
```

---

## 8.3 README copy에서 완화할 과장

현재 앞부분의 다음 류 표현은 fail-open cap과 함께 읽어도 과장으로 보일 수 있다.

```text
makes cutting corners impossible to finish
```

권장:

```text
makes unverified completion materially harder and observable;
it blocks up to twice, then records a fail-open escape to avoid deadlock.
```

한국어:

```text
검증 없는 완료를 최대 두 번 되돌려 보내고, 이후에는 교착을 막기 위해 경고·감사기록과 함께 통과시킵니다.
```

---

## 8.4 README 자동 검증

문서 회귀를 CI로 잡는다.

### command smoke

README code block에서 공개 command 추출 후:

```text
--help 또는 dry-run
```

### stale literal 검사

금지되는 일반 설명:

```text
single state dir `.fable-lite`
every hook fail-open
public alias design stage
```

예외:

- history
- compatibility
- changelog quote

### version

README badge, plugin, marketplace, pyproject, changelog, tag 일치.

---

# 9. CLI onboarding — CLI-02

## 9.1 `smtw doctor`

### 출력 항목

```text
tool version
distribution version
module path
Python version/path
project root
host
plugin registration
activation status
config source
config digest
runtime env source
env conflict
state layout
authoritative state dir
migration readiness
active turns
open invocations
ledger health
provenance health
quarantine count/bytes
last probe receipt
host support status
```

### exit code

```text
0 healthy
1 unsafe/error
2 inactive/deferred/action required
```

### 사람용 + JSON

```bash
smtw doctor
smtw doctor --json
```

secret raw value는 출력하지 않는다.

---

## 9.2 `smtw status`

doctor보다 짧은 runtime 상태.

```text
active/inactive
layout
authority
current turn
block counters
verification freshness
coordination degraded
```

---

## 9.3 `smtw init`

### 기본

- project root 확인
- exact home 거절
- `.smtw.toml` 생성
- 기존 canonical/legacy config 감지
- 덮어쓰기 금지
- `.gitignore`에 runtime state pattern 제안/추가
- `smtw doctor` 실행 안내

### 생성 config

```toml
schema_version = 1
supervision = true
```

### 기존 legacy 프로젝트

자동 migration하지 않는다.

```text
legacy config 발견
-> config 유지
-> `smtw migrate` 선택 안내
```

### 옵션

```bash
smtw init --config pyproject
smtw init --no-gitignore
smtw init --json
```

---

## 9.4 `smtw migrate` UX 보강

현재 JSON 한 줄만 출력하는 기본 UX를 개선한다.

사람용:

```text
Current layout: LEGACY
Active turn: none
Files: 201
Bytes: ...
Result: MIGRATED
Authority: .smtw
Legacy retained: .fable-lite
```

옵션:

```bash
smtw migrate --check
smtw migrate --json
smtw migrate --lock-wait-seconds 0
```

`--check`는 write-free.

---

## 9.5 goals subcommand

기존 standalone script를 canonical CLI에 통합한다.

```bash
smtw goals plan
smtw goals verify
smtw goals status
```

---

# 10. 릴리스·패키징 — REL-02

## 현재 문제

```text
main / plugin / pyproject: 2.6.0
GitHub latest public release: 2.1.0
```

### 목표

v2.6.1 tag와 GitHub Release를 실제 green commit에서 생성한다.

### release gate

- exact tag = plugin version
- marketplace version
- pyproject version
- README badge
- README.ko badge
- changelog heading
- wheel metadata
- CLI version
- GitHub Release title

### release notes

다음 구분:

```text
Fixed
Changed
Compatibility
Migration
Known Limitations
Upgrade steps
```

### artifact

- wheel
- probes receipt
- provenance receipt
- SHA-256
- optional SBOM
- CI run link

### tag commit

문서-only 후속 commit이 release commit보다 앞서지 않게 한다.

최신 main SHA 자체에 CI가 없으면 release workflow에서 전체 gate를 다시 실행한다.

---

# 11. CI — CI-02

## Python matrix

최소:

```text
Ubuntu:
- 3.12
- 3.13
- 3.14

Windows:
- 3.12
- 3.14
```

전체 suite가 너무 무거우면:

- 3.12 양 OS full
- 3.13/3.14 unit + packaging + compatibility

로 나눌 수 있다.

## quality tool pin

현재 최신 무고정 설치 대신 constraints 사용.

```text
pytest
ruff
build
setuptools
```

Dependabot 또는 정기 PR로 갱신.

## adversarial jobs

### R2 corpus

- single `&`
- newline
- wrappers
- control words
- short clusters
- quote negative corpus
- tee append

### migration race

- multi-process writer
- publish boundary
- crash cut
- Windows long path

### quarantine race

- 64 process
- truncation
- GC

### compatibility

- canonical import
- legacy import
- pickle fixture
- Windows spawn
- supported `python -m`
- strict warning intentional failure

### docs

- README command smoke
- stale claim check
- version surface

---

# 12. 리뷰·머지 프로세스 — GOV-01

현재 자동 리뷰가 실제 결함을 잘 찾지만 merge를 막지 못한다.

## branch protection

필수:

```text
Require status checks
Require conversation resolution
Require pull request
Disallow force push
P0/P1 label merge block
```

## review finding 처리

PR 본문 표:

| Finding | Severity | Status | Regression test | Commit |
|---|---:|---|---|---|

상태:

```text
FIXED
NOT_REPRODUCIBLE_WITH_PROOF
ACCEPTED_RISK
SUPERSEDED
```

## 시간 기준

자동 리뷰가 달린 직후 즉시 merge하지 않는다.

최소 조건은 시간이 아니라:

```text
모든 thread triage
P0/P1 0
full gate rerun
```

이다.

## 독립 리뷰

다음 변경은 독립 reviewer를 요구한다.

- R2
- state migration
- ledger schema
- fail-open/fail-closed
- compatibility shim
- release workflow

---

# 13. 전체 회귀 corpus

## 13.1 R2 block corpus

```text
echo ok && rm peer.py
echo ok ; rm peer.py
echo ok | rm peer.py
echo ok & rm peer.py
echo ok\nrm peer.py
command rm peer.py
exec rm peer.py
{ rm peer.py; }
if true; then rm peer.py; fi
for x in 1; do rm peer.py; done
bash -c "echo ok; rm peer.py"
git checkout -f main
git checkout --force main
git checkout -qf main
git checkout -fB newbranch
git checkout -Bf newbranch
```

## 13.2 R2 allow corpus

```text
echo "rm peer.py"
python -c "print('rm peer.py')"
git checkout main
git checkout -b feature/x
git checkout -B feature/x
git checkout --no-force main
tee -a log.txt
tee --append log.txt
tee
```

## 13.3 state migration

```text
empty
legacy
native
migrating
migrated
conflict
active turn
open invocation
writer during copy
writer at publish
writer after publish
crash each stage
orphan staging
legacy diverged
target diverged
```

## 13.4 quarantine

```text
64 process collision
1 MiB exact
1 MiB + 1
UTF-8 multibyte boundary
GC count
GC bytes
TTL
read-only dir
Windows long path
```

## 13.5 goals

```text
single exact
two exact
synthetic
legacy
wrong identity
foreign identity
plan/recover
verify/evidence
```

## 13.6 config

```text
.smtw.toml
[tool.smtw]
[tool."smtw"]
["tool"."smtw"]
dotted key
comment false positive
string false positive
malformed canonical
malformed unrelated
legacy fallback
```

---

# 14. PR 분할 권장안

## PR A — R2 command-position parser

```text
R2-05
R2-05B
R2-06
```

parser와 corpus만.

## PR B — state migration write barrier

```text
STATE-02
```

가장 위험하므로 독립 PR.

## PR C — stale turn adapter denial

```text
STALE-01
```

세 adapter 동일 invariant.

## PR D — quarantine durability

```text
QUAR-01
QUAR-02
```

## PR E — goals identity UX

```text
GOALS-02
CLI goals
```

## PR F — config·status migration hardening

```text
CONFIG-02
MIGRATION-02
```

필요하면 별도 PR.

## PR G — attribution·friction

```text
ATTR-02
HINT-02
```

## PR H — doctor/init/status

```text
CLI-02
COMPAT-01
COMPAT-02
```

## PR I — README·README.ko

```text
DOC-02
README command tests
```

CLI가 구현된 뒤.

## PR J — CI·Release·Governance

```text
CI-02
REL-02
REL-03
GOV-01
```

---

# 15. AI 에이전트 보고 형식

```markdown
## 작업 ID

R2-05

## 기준

- 시작 commit:
- version:
- 관련 PR/thread:
- 관련 invariant:

## 재현

- command/scenario:
- 수정 전 parser result:
- 실제 shell effect:
- adapter decision:
- RED test:

## 원인

- 직접 원인:
- 왜 기존 test가 놓쳤는가:
- 영향 host/OS:

## 수정

- 변경 파일:
- parser/state model:
- backward compatibility:
- fail-open/fail-closed 변화:
- latency 영향:

## 검증

- focused:
- full pytest:
- ruff:
- compileall:
- probes:
- e2e:
- wheel:
- Ubuntu:
- Windows:
- Python versions:
- multi-process:

## 회귀 분석

- false negative:
- false positive:
- known limitation:
- intentionally unsupported grammar:

## 문서

- README:
- README.ko:
- CHANGELOG:
- migration/upgrade note:

## 리뷰 debt

- thread:
- status:
  - FIXED
  - NOT_REPRODUCIBLE_WITH_PROOF
  - ACCEPTED_RISK
  - SUPERSEDED
- evidence:

## 배포

- version:
- tag:
- release:
- rollback:
```

---

# 16. 오케스트레이터 AI용 실행 프롬프트

```text
당신은 pinetreeB/show-me-the-work v2.6.1 안전 경계 안정화 책임자다.

목표는 신규 기능 추가가 아니라 첨부 지시서의 P0/P1 결함,
README·CLI·Release·CI·review-gate 부채를 regression-first로 폐쇄하는 것이다.

절대 규칙:
1. 최신 main에서 수정 전 실패를 재현한다.
2. R2 fail-closed를 약화하지 않는다.
3. 일반 benign command 오탐을 늘리지 않는다.
4. migration 시작 후 성공 반환된 state write는 최종 authority에 반드시 남아야 한다.
5. mutation-capable StaleTurn은 adapter에서 deny한다.
6. quarantine 성공 메시지는 실제 보존 상태와 일치해야 한다.
7. goals 안내 command는 실제 N2 gate를 해소해야 한다.
8. canonical config가 깨졌으면 legacy로 조용히 fallback하지 않는다.
9. README는 current state/config/migration/fail-policy/CLI를 정확히 설명해야 한다.
10. 문서에 없는 CLI를 구현된 것처럼 쓰지 않는다.
11. 자동 리뷰 P0/P1을 해결하지 않고 merge하지 않는다.
12. 전체 gate와 wheel clean-install을 rerun한다.
13. 한 PR에 한 문제군만 넣는다.
14. 완료 보고는 지시서 양식을 따른다.

작업 순서:
A. R2-05 / R2-05B / R2-06
B. STATE-02
C. STALE-01
D. QUAR-01 / QUAR-02
E. GOALS-02
F. CONFIG-02 / MIGRATION-02
G. ATTR-02 / HINT-02
H. CLI-02
I. DOC-02
J. CI-02 / REL-02 / GOV-01

P0 전체가 green이 되기 전에는 신규 gate나 host 기능을 시작하지 않는다.
```

---

# 17. v2.6.1 릴리스 체크리스트

## 코드

- [ ] R2 single `&`
- [ ] R2 newline
- [ ] `command`·`exec`
- [ ] shell control words
- [ ] Git short clusters
- [ ] benign tee
- [ ] state write barrier
- [ ] stale mutation deny
- [ ] quarantine atomic reserve
- [ ] quarantine truncation truth
- [ ] goals identity recovery
- [ ] TOML equivalent declaration
- [ ] status archive rotation
- [ ] logical/resolved candidate
- [ ] wrapper·Path alias hint

## CLI

- [ ] `smtw doctor`
- [ ] `smtw doctor --json`
- [ ] `smtw status`
- [ ] `smtw init`
- [ ] `smtw goals`
- [ ] migrate human output
- [ ] source/distribution mismatch warning

## 문서

- [ ] README state authority
- [ ] README config precedence
- [ ] README migration
- [ ] README fail policy
- [ ] README contract path
- [ ] README naming matrix
- [ ] README CLI quickstart
- [ ] README privacy
- [ ] README compatibility
- [ ] README.ko 동일
- [ ] stale phrase test

## CI

- [ ] Ubuntu 3.12
- [ ] Ubuntu 3.13
- [ ] Ubuntu 3.14
- [ ] Windows 3.12
- [ ] Windows 3.14
- [ ] quality tool constraints
- [ ] full pytest
- [ ] probes
- [ ] E2E
- [ ] wheel RECORD
- [ ] clean wheel install
- [ ] R2 adversarial corpus
- [ ] migration multi-process race
- [ ] quarantine 64-process race
- [ ] README command smoke

## 프로세스

- [ ] unresolved conversation 0
- [ ] P0/P1 open 0
- [ ] independent review
- [ ] review table
- [ ] branch protection
- [ ] release commit full CI

## 릴리스

- [ ] version surfaces 2.6.1
- [ ] tag `v2.6.1`
- [ ] GitHub Release
- [ ] wheel
- [ ] SHA-256
- [ ] probe receipt
- [ ] provenance receipt
- [ ] upgrade/migration note
- [ ] Known Limitations
- [ ] latest release가 2.6.1로 표시

---

# 18. 최종 제품 메시지

> show-me-the-work는 AI가 더 똑똑하다고 가정하지 않는다.  
> 실제 실행·변경·검증의 증거를 관측하고, 검증 없는 완료와 위험한 협업 행동을 더 어렵고 더 잘 보이게 만든다.
>
> 안전성은 테스트 개수나 AI 승인 문구가 아니라, shell grammar·동시성·crash·state authority·review finding이 실제로 닫혔는지로 판단한다.
>
> 문서는 현재 코드와 같은 제품을 설명해야 하며, 존재하지 않는 CLI·과거 state path·이미 바뀐 config를 안내해서는 안 된다.
>
> v2.6.1의 성공 기준은 새로운 기능이 아니라, 기존 제품의 가장 중요한 문장이 실제로 참이 되는 것이다.
>
> **“말로 검토했다고 하지 말고, 재현·수정·검증·릴리스 증거를 보여라.”**
