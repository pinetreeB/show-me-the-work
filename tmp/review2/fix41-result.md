# review2 41건 수리 결과

## 결과

- 브랜치: `worker/review2-fix`
- 수용 코퍼스: `PASS — 179/179 충족 (미탐 0 · 오탐 0)`
- 전체 회귀: `1956 passed, 2 skipped in 381.67s`
- 야간 후속 관련 회귀: `171 passed` (review2 문법·컨테이너·기존 v2.7.2 정책)
- N5 관련 회귀: `371 passed` (transfer·PowerShell·컨테이너·opaque parser)
- 신규 회귀: 최초 RED `59 failed, 13 passed`, G2 정정 RED `1/146` → GREEN `79 passed`, 야간 후속 RED `9 failed, 82 passed` → GREEN `93 passed`, PR #54 P2 RED `14 failed, 4 passed` → GREEN `19 passed`, N5 RED `13 failed, 22 passed` → GREEN `35 passed`
- 정적 검사: Ruff check/format 통과, basedpyright `0 errors, 0 warnings, 0 notes`

## P0 — 디렉터리 하위 귀속 [Critical]

- 전: 파서는 `shared-dir`을 올바르게 산출했지만 게이트가 canonical key 정확 일치만 검사해 `shared-dir/peer.py`를 놓쳤다.
- 후: 파괴 대상 중 컨테이너 의미가 있는 경로만 `container_targets`로 구분했다. 해당 경로는 `/` 경계가 보존된 하위 관계로 open invocation candidate와 영속 `path_attribution`을 모두 검사한다.
- 적용 범위: 재귀 rm/Remove-Item, mv/Move-Item/Rename-Item 소스, 명시적 git pathspec 컨테이너, 재귀 cp/Copy-Item의 `destination/source` 파생 경로.
- 오탐 방지: 관련 없는 sibling, `shared-dir` 대 `shared-directory`, 읽기 명령, own 디렉터리는 모두 ALLOW를 유지한다.

## P1 — PowerShell 동적 식 불일치

- 전: `_detect_remove`만 공용 정적 대상 resolver를 우회했고 `@(`·`+(`·`&{`·`.{`는 동적 식으로 분류되지 않았다.
- 후: Remove-Item 계열도 공용 resolver를 사용하며 모든 지정 동적 접두사를 fail-closed 처리한다. 인용된 `'(literal).py'`와 `'@(literal).py'`는 정적 파일명으로 유지한다.
- 동형 회귀: `mv (gi peer.py) out`과 `Remove-Item (gi peer.py)`가 모두 같은 `parse_unable_dynamic_expression` 판정을 낸다.

## P2 — PowerShell 공식 바인더 문법

- 전: `-Name:Value`, 유일 접두사, 정적 파이프라인 바인딩, `Tee-Object` 정식 이름을 놓쳤다.
- 후: cmdlet별 전체 parameter name과 common parameter를 기준으로 exact/alias/유일 접두사를 정규화한다. 모호한 접두사는 fail-closed하고 switch의 `$true/$false` 의미는 보존한다.
- 파이프라인: 엄격한 단일 인용 literal만 공식 by-value 수신 대상에 바인딩한다. Copy/Move/Remove/Rename은 Path, Invoke-Expression은 Command로 전달한다.
- Tee-Object: FilePath, LiteralPath, 위치 인수를 sink로 라우팅하고 Append는 기존 append 허용 정책을 유지한다.

## P3 — 옵션 문법

- sed: short option cluster 안의 실제 `i`만 in-place로 판정한다.
- find: `-fprint`, `-fprint0`, `-fls`, `-fprintf`의 파일 인수를 truncate sink로 수집하고 `-fprintf` format을 별도 소비한다.
- git: switch의 `-f/--force/--orphan`, checkout/restore의 무경로 `-p/--patch`를 차단한다.
- cp/mv: `--context`의 선택/무인수 문법, `--parents` 상대경로 보존, 재귀/보존 옵션을 반영했다.
- 붙임형 `-tDIR`와 `--target-directory=DIR`: GNU 규칙대로 destination을 DIR로 해석하고 쓰기 후보를 `DIR/basename(source)`로 산출해 귀속 검사에 전달한다. 정정된 코퍼스에서 peer 대상 3건은 BLOCK, `cp -tmy_own_dir mine.py`는 ALLOW다.

## P4 — 파이프 수신 셸 래퍼

- 전: `_EXECUTABLE_PREFIX_WRAPPERS` 수동 목록이 `WRAPPER_COMMAND_NAMES`와 드리프트했다.
- 후: opaque endpoint와 별도 백로그인 xargs를 제외한 값으로 원본 집합에서 파생한다. nice, setsid, ionice, stdbuf, time, busybox, toybox를 정상 unwrap한다.
- powershell, pwsh, cmd를 파이프 수신 셸 집합에 추가하되 명시적 command/script 옵션은 stdin script로 오인하지 않는다.
- 전체 회귀에서 Bash `-C`가 `-c`로 오인되는 1건을 발견했고, short option 대소문자를 보존하도록 수정 후 해당 회귀 84건과 전체 테스트를 재통과했다.

## P5 — 오탐

- Perl: option letter와 payload를 분리해 `-Ilib`·`-Mstrict` 내부의 `i`를 in-place로 오인하지 않는다. 실제 `-i`, `-pi`, `-i.bak`는 계속 탐지한다.
- sc: 확장자 없는 bare `sc`도 표준 서비스 제어 동사일 때만 Set-Content 별칭에서 면제한다. 일반 `sc <path> <value>` 형태는 면제하지 않는다.
- WhatIf: bare `-WhatIf`와 `-WhatIf:$true`는 ALLOW, `-WhatIf:$false`는 계속 실제 파괴 경로로 평가한다.

## 야간 후속 M1~M4

- M1 cmd 재귀: `rd`/`rmdir`에 한해서 대소문자 무관 `/s`를 재귀 컨테이너 표식으로 해석한다. `/q`는 부가 옵션으로 소비하며 비재귀 `rd shared-dir`는 계속 ALLOW다.
- M2 별칭: 정적 파이프 바인딩이 문자열 `invoke-expression` 하나가 아니라 `iex`를 포함한 Invoke-Expression 계열 판정을 사용한다.
- M3 공통 파라미터: `-Verbose`·`-Force`처럼 Command 입력을 제공하지 않는 옵션이 있어도 파이프 literal을 바인딩한다. 반대로 위치 Command나 명시적 `-Command`가 있으면 파이프 입력을 덧붙이지 않는다.
- M4 불투명 입력: 비-literal 파이프가 명시적 Command 없이 Invoke-Expression 계열로 들어가면 셸 stdin script와 동일하게 `parse_unable_pipeline`로 fail-closed한다.
- 정책 정합: 과거 `fnm env | iex` ALLOW 회귀는 새 M4 계약과 모순되어 파이프 코드 수신기 BLOCK 회귀로 이동했다. 명령별 예외를 두어 같은 우회를 남기지 않았다.
- 오탐 대조: `rd /s my_own_dir`, `rmdir /s /q my_own_dir`, 비재귀 `rd shared-dir`, `'pwd' | iex -Verbose`, 명시적 `-Command 'pwd'`는 모두 ALLOW를 유지한다.

## PR #54 리뷰봇 P2

- P2-1 콜론 결합 인용 값: PowerShell parameter 정규화가 `-Path:'peer dir/file.py'`처럼 토큰화된 인용 값을 닫는 따옴표까지 다시 결합한다. Path/LiteralPath/Destination 모두 기존 cmdlet별 바인딩으로 전달되며, own 경로와 읽기 전용 Get-Content는 계속 ALLOW다.
- P2-2 git patch 모드: checkout/restore의 공식 값 옵션을 먼저 소비하고 short cluster를 문법대로 해석한다. 따라서 `-s HEAD`와 `--detach HEAD`의 HEAD를 pathspec으로 오인하지 않고, `-pq` 안의 `p`도 patch 모드로 판정한다.
- 자체 점검 보강: parameter normalizer의 index 기반 순회에서 미등록 파라미터 분기도 반드시 전진하도록 고정해 무한루프 회귀를 차단했다.
- 게이트 종단 QA: 원래 미탐 7건은 모두 BLOCK, `Remove-Item -Path:'my own/file.py'`와 `Get-Content -Path:'peer dir/file.py'`는 ALLOW였다.
- 확인된 파서 결과: 네 git 명령은 모두 `resolved=False`, `reason=implicit_scope`; 콜론 결합 세 명령은 완전한 `peer dir/file.py`를 쓰기 후보로 산출한다.

## N5 — 명명 소스 + 위치 목적지

- 원인: PowerShell 정규화는 `-Path`/`-LiteralPath` 값과 남은 위치 인자를 정상 보존했지만, `_transfer_operands()`가 명명 소스의 존재와 무관하게 위치 인자 두 개를 요구했다. 목적지 하나만 남는 혼합 바인딩은 `None`이 되어 게이트 라우팅 전체가 사라졌다.
- 수리: 명명 소스가 이미 있으면 위치 인자 하나를 목적지로 받아들이고, 명명 소스가 없을 때는 기존대로 source+destination 두 개를 요구한다. 공용 seam 한 조건만 변경해 Move-Item, Rename-Item, Copy-Item에 동일하게 적용했다.
- Move/Rename: 소스·목적지·목적지/basename(source)를 기존 정책대로 모두 라우팅한다. Rename의 상대 NewName은 소스 부모 아래 경로로 해석한다.
- Copy: 명명 소스는 읽기이므로 파괴 대상으로 추가하지 않고, 위치 목적지와 파생 목적지만 라우팅한다. `Copy-Item -Path mine.py peer.py`는 BLOCK, peer 파일을 읽어 own 목적지로 복사하는 `Copy-Item -Path peer.py out`은 ALLOW다.
- 게이트 종단 QA: 코퍼스 N5 8건과 Copy 목적지 대조는 BLOCK, own Move/Rename과 read-only Copy 대조는 ALLOW였다.

## 영속 회귀 테스트

- `tests/test_review2_fix41_containers.py`: open/persisted peer descendant, 재귀 복사 파생 범위, 경로 경계, own/read 대조군.
- `tests/test_review2_fix41_syntax.py`: P1~P5 문법, 동형 단언, ambiguous prefix, WhatIf true/false, Tee append, 모든 래퍼 대조군.
- `tests/test_hardreview_r2_v272_vocabulary.py`: 동적 입력의 bash/iex 코드 수신기를 동일한 fail-closed 정책으로 고정한다.
- `tests/test_pr54_p2_parser.py`: 콜론 결합 인용 경로 보존, git 값 옵션 소비, short cluster patch 판정, N5 혼합 바인딩과 Copy sibling, own/read/non-patch 대조군을 게이트 종단과 parser 단위 양쪽에 고정한다.

## 범위 제외 백로그

- positional Set-Content/Out-File, git global option/dry-run/stash 추가 문법, option-like filename, 따옴표·escape 분할, dd append, gsed/xargs, POSIX cp comma/quoted name.
- `core/scope_guard.py` 광역 예외 fail-open.
- 사용자 제공 `eval/r2_vocabulary_acceptance.py`는 수정하지 않았고 커밋 대상에만 포함한다.
- 관련 없는 `.codex/config.toml` 변경은 손대지 않고 스테이징에서 제외한다.

[CODEX-FIX41-DONE]
