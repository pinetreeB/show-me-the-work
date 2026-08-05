# Qoder 리뷰 2차 — v2.8.0 신규 정규화 계층 적대 재검증

대상: main `188ef0d` (v2.8.0) · 신규 코드: `ec45067`(별칭·쉼표·파이프래퍼·건조실행) + `81a5651`(리뷰봇 4건)
방법: 게이트 종단(`evaluate_r2_destructive_gate`) 측정, `eval/r2_vocabulary_acceptance.py` 동일 경쟁 픽스처
(beta가 `peer.py` 미정산 점유). 시작 전 하네스 건전성 **93/93 PASS** 확인. 파괴 명령 미실행, 코드 수정 없음.

## 시도 변형 수
**100개** (1차 37 · 2차 36 · 3차 24 · 마이크로 3) — 직전 87변형과 중복 없음.

## 발견한 미탐

### N1 [Critical] 식 fail-closed가 `_detect_remove`에만 없다 — 새 규칙이 최다 삭제 계열을 비껴감
- 명령: `Remove-Item (gi peer.py)` · `ri (Join-Path a peer.py)` · `del (gi peer.py)` ·
  `Remove-Item +(gi peer.py)` · `Remove-Item (gi peer.py),a.py` → 전부 `allow`
- 파서 증거: `Remove-Item (gi peer.py)` → `resolved:os_remove:['(gi', 'peer.py)']` — 괄호 조각이
  그대로 대상이 돼 귀속 조회에서 빗나감.
- 원인: 81a5651이 추가한 `(` fail-closed는 `_resolved_static_targets`에만 있는데, `_detect_remove`는
  자체 대상 루프를 써서 그 검사를 거치지 않는다. **대조 실측: `mv (gi peer.py) out` → block** —
  동일 식 입력이 mv는 막히고 Remove-Item/rm/del/rd/rmdir(별칭 ri/erase 포함)은 뚫린다.
- 심각도: Critical — PowerShell 식 인자는 일상 문법이고, 새 정규화 계층이 하필 가장 많이 쓰이는
  삭제 탐지기에서만 무력화됨.

### N2 [High] `@(...)` 배열 부분식이 `(` 규칙 전체를 우회
- 명령: `Remove-Item @(peer.py)` · `Clear-Content -Path @(peer.py)` · `Set-Content -Path @(peer.py) -Value x` ·
  `New-Item -Force @(peer.py)` · `Move-Item @(peer.py) out` · `Copy-Item @(peer.py) out` ·
  `Rename-Item @(peer.py) new.py` · `Clear-Content @(peer.py,a.py)` → 전부 `allow`
- 원인: 새 검사가 `raw.lstrip().startswith("(")`라 `@(`는 걸리지 않음. 쉼표가 있어도 분할 조각
  (`@(peer.py`, `a.py)`)이 실경로와 안 맞아 우회. `$(`·`+(`도 동일 사각(단 `$(`는 `_validate_target`의
  `$` 규칙에 걸려 우연히 차단됨).
- 심각도: High — 새 탐지기 6종 전부가 동일한 한 글자(`@`)로 무력화.

### N3 [Medium] 파이프 수신 셸 — unwrap 세트·수신 셸 목록 잔존 구멍
- 명령: `echo x | powershell` · `| pwsh` · `| cmd` · `| busybox sh` · `| toybox bash` · `| time bash` ·
  `| nice bash` · `| ionice -c 2 bash` · `| setsid bash` · `| stdbuf -oL bash` → 전부 `allow`
- 원인: `_EXECUTABLE_PREFIX_WRAPPERS`가 sudo/doas/nohup/timeout만 추가 — WRAPPER_COMMAND_NAMES의
  nice/ionice/setsid/stdbuf/time/busybox/toybox가 여전히 빠져 있고, powershell/pwsh/cmd는 수신 셸
  판정(SHELL_COMMAND_NAMES)에서 파이프 표준입력 스크립트 실행으로 취급되지 않음.
- 심각도: Medium — P4의 M3와 동일 계열, 범위가 줄었을 뿐.

### N4 [Low] `Remove-Item ,` — 대상 0개 resolved 허용
- 파서: `resolved:os_remove:[]`. 쉼표 확장 후 빈 목록이 implicit_scope로 전환되지 않음. 악용 불가(대상 없음), 위생 문제.

## 발견한 오탐 (우선 항목)

### F1 [Medium] `perl -Ilib -e …` 읽기 전용 실행이 in-place로 오인
- 명령: `perl -Ilib -e s/x/y/ peer.py` → `block` (대조: `-i`가 진짜로 붙은 형태만 차단돼야 정상)
- 원인: perl 클러스터 검사 `"i" in short_options`가 대소문자는 구분하지만 **붙은 값 안의 소문자 i**까지
  잡음 — `-Ilib` → `"Ilib"`에 `i` 포함 → in-place 오인. `-Iinclude`·`-Isite` 등 include 경로 지정은
  perl 일상 문법. `perl -Ilib t/test.t` 류의 순수 읽기 실행이 차단됨.

### F2 [Medium] `sc stop peersvc` — 별칭 정규화가 cmd의 서비스 제어와 충돌
- 명령: `sc stop peersvc` → `block` · 대조: `sc.exe stop peersvc` → `allow`
- 원인: 별칭 테이블이 bare `sc`→set-content로 매핑. PowerShell에서는 맞는 해석이지만 cmd 콘텍스트의
  `sc`(서비스 제어) 사용은 억울한 차단. `.exe` 형태만 면제돼 절반짜리 충돌 해결.

### F3 [Low-Medium] `-WhatIf:$true` / `-WhatIf: $true` 건조 실행 차단
- 명령: `Remove-Item peer.py -WhatIf:$true` · `-WhatIf: $true` → `block`
- 원인: 면제 목록이 bare `-whatif` 토큰만 일치. 명시적 dry-run 형태가 누락. (대조: `-WhatIf:$false`
  차단 유지 — 이건 올바른 fail-closed.)

### F4 [Low] POSIX `cp` 목적지 파일명에 쉼표 — PS 규칙 오적용
- 명령: `cp mine.py a,peer.py` → `block` (파서: 목적지가 `a`,`peer.py`로 분할돼 `peer.py` 포함)
- POSIX cp는 쉼표를 분할하지 않고 문자 그대로의 파일 `a,peer.py`를 만들 뿐이라 peer.py는 무사함.
  쉼표 규칙은 PowerShell 전용인데 `_detect_transfer`가 cp/mv에도 적용.

### F5 [Low] 따옴표가 이름에 포함된 파일 — 정규화가 벗겨냄
- 명령: `Remove-Item "'peer.py'"` → `block` — canonicalization이 따옴표를 벗겨 peer.py로 해석.
  문자 그대로 따옴표 이름 파일의 삭제라 초엣지, 보수적 차단 수용 가능. 기록만.

## 뚫지 못한 각도 (방어 확인)
- 리뷰봇 4건 수리 실증: `"a.py","peer.py"`·`'a.py','peer.py'`·혼합 따옴표 전부 차단, `sed -ibak` 포함
  `-in`·`-i''`·`-i.orig -n` 전부 차단 (자기 파일은 허용)
- 식 fail-closed 동작 지점: `Clear-Content -Path (…)`·`Set-Content -Path (…)`·`New-Item -Force -Path (…)`·
  `Move-Item -Destination (…)`·`dd of=(…)`·`mv (gi …)` 전부 차단
- git rm 면제: `-n`·`--dry-run`·`--cached`·복합 허용, 파괴적 `--ignore-unmatch`는 여전히 차단
- `-WhatIf:$false` 차단(fail-closed 정상), 별칭 대소문자(`ERASE`) 차단
- 기존 어휘·파이프 회귀 없음: `unlink`·`ri`·`cat x | bash`·`echo x | sudo bash` 차단 유지
- 일상 흐름: `cp mine.py config`·`mv a.py b.py`·`cp peer.py out`(읽기)·`sed -i` 자기 파일·
  `cp a/b/peer.py out`(복사=읽기) 전부 허용 — 상시 basename 후보 추가의 마찰 회귀 없음
- `perl -i -Ilib -pe` 혼합은 차단(정방향 동작)

## 종합 판정 — **후속 패치 필요 (v2.8.0을 이대로 두면 안 됨)**

사유:
1. 이번 수리의 핵심인 **식 fail-closed 정규화 계층이 N1·N2로 가장 흔한 형태에서 무력화**됐다.
   과제가 경고한 그대로 "변형 계층 자체가 우회면"이 된 사례: `(` 검사 추가가 `_detect_remove`를
   비껴가고, `@(` 한 글자가 검사 전체를 무효화한다. `(gi peer.py)`·`@(peer.py)`는 난독화가 아닌
   PowerShell 일상 문법이라 arbitrary-code 비목표로 면책 불가.
2. N3는 P4 M3의 부분 재발 — unwrap 세트가 WRAPPER_COMMAND_NAMES보다 계속 작다. 구조적으로
   두 집합을 연결하지 않으면 같은 사고가 반복된다.
3. 오탐 우선 원칙에서 F1(perl -I)·F3(-WhatIf:$true)은 정상 개발 관행을 막는 실질 마찰이다.

권고 패치(우선순위): ① `_detect_remove` 대상을 식 검사 경로로 통합(N1) ② `(` 규칙을
`@(`·`+(`·`&{`까지 확장(N2) ③ `_EXECUTABLE_PREFIX_WRAPPERS`를 WRAPPER_COMMAND_NAMES에서
파생시키거나 powershell/pwsh/cmd를 파이프 수신 셸로 취급(N3) ④ perl `-I` 값 오탐 수정(F1)
⑤ `-WhatIf:$true` 면제(F3). ②③은 회귀 테스트에 `mv (gi …) block` 대비 `Remove-Item (gi …) block`
동형 단언을 넣을 것.

[QODER-NEWCODE-DONE]
