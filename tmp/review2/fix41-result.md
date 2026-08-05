# review2 41건 수리 결과

## 결과

- 브랜치: `worker/review2-fix`
- 수용 코퍼스: `PASS — 146/146 충족 (미탐 0 · 오탐 0)`
- 전체 회귀: `1907 passed, 2 skipped in 392.32s`
- 관련 회귀: `235 passed`
- 신규 회귀: 최초 RED `59 failed, 13 passed`, G2 정정 RED `1/146` → GREEN `79 passed`
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

## 영속 회귀 테스트

- `tests/test_review2_fix41_containers.py`: open/persisted peer descendant, 재귀 복사 파생 범위, 경로 경계, own/read 대조군.
- `tests/test_review2_fix41_syntax.py`: P1~P5 문법, 동형 단언, ambiguous prefix, WhatIf true/false, Tee append, 모든 래퍼 대조군.

## 범위 제외 백로그

- positional Set-Content/Out-File, git global option/dry-run/stash 추가 문법, option-like filename, 따옴표·escape 분할, dd append, gsed/xargs, POSIX cp comma/quoted name.
- `core/scope_guard.py` 광역 예외 fail-open.
- 사용자 제공 `eval/r2_vocabulary_acceptance.py`는 수정하지 않았고 커밋 대상에만 포함한다.
- 관련 없는 `.codex/config.toml` 변경은 손대지 않고 스테이징에서 제외한다.

[CODEX-FIX41-DONE]
