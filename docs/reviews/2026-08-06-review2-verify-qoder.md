# Qoder 야간 P4 — 41+1건 수리분(0e6b8a7) 적대 검증

대상: main `0e6b8a7` `fix(r2): close review2 destructive guard gaps` (+847)
시작 전 건전성: `eval/r2_vocabulary_acceptance.py` **146/146 PASS** 확인.
방법: 게이트 종단(`evaluate_r2_destructive_gate`) 측정. 경쟁 픽스처 2종 — beta가 `peer.py`(루트)와
`shared-dir/peer.py`(디렉터리) 미정산 점유. 파괴 명령 미실행, 코드 수정 없음, 단계별 함수 추적 병행.

## 시도 변형 수
**108개** (컨테이너 26 · 바인더/동적식/파이프/기타 52 · iex·rd 추적 12 · 보충 18) — 직전 100변형과 중복 없음.

## 발견한 미탐

### M1 [High] cmd 재귀 플래그 `/s` 미인식 — 컨테이너 보호가 cmd 문법에서 무력화
- 명령: `rd /s shared-dir` · `rd /S /Q shared-dir` · `rmdir /s shared-dir` → 전부 `allow`
- 원인: 컨테이너 귀속 비교(G1)는 `-r`/`--recursive`/`-recurse`(+rm 클러스터)만 재귀로 인식.
  `_detect_remove`에서 `/s`는 `token.startswith("/")` 분기에서 단순 스킵돼 `recursive=False` →
  대상이 디렉터리 자체로만 귀속 검사됨 → 하위 `shared-dir/peer.py` 매칭 실패 → 통과.
- 대조(방어 정상): `rm -r shared-dir` 계열 10종(슬래시·닷닷·대소문자·백슬래시 포함) 전부 차단,
  비재귀 `rd shared-dir`·`rm -d`·`Remove-Item shared-dir`은 정확히 허용(비어 있지 않으면 실패하는 명령).
- 심각도: High — 이번 수리의 간판 기능(G1 컨테이너 보호)이 Windows cmd의 표준 재귀 문법에서 뚫림.
  del/rd가 1급 어휘인 이 파서에서 `/s`만 빠져 있음.

### M2 [High] 파이프 바인딩의 별칭 구멍: `'rm peer.py' | iex`
- 명령: `'rm peer.py' | iex` · `"rm peer.py" | iex` → `allow` (parse: no parse)
- 대조: `'rm peer.py' | Invoke-Expression` → `block(parse_unable_wrapped)` — 본명만 동작.
- 원인(단계별 추적 확정): `_bind_static_pipeline_input`이 `_command_name(tokens[0]) == "invoke-expression"`
  과 비교하는데, `_command_name("iex")`는 `"iex"`를 그대로 돌려준다(iex는 `_POWERSHELL_COMMAND_ALIASES`에
  없고 `INVOKE_EXPRESSION_COMMAND_NAMES`에만 등록). 바인딩 스킵 → payload 없는 iex → 래퍼 탐지 None → 통과.
  별칭 테이블에 있는 계열은 정상 동작 확인(`'peer.py' | ri`·`| clc`·`Get-Content peer.py | Clear-Content` → block).
- 심각도: High — 새 계층(파이프 by-value 바인딩) 자체의 정규화 불일치. PowerShell에서 iex는 본명보다 흔함.

### M3 [High] 바인딩의 `len(tokens) == 1` 조건 — 파라미터 하나면 무력화
- 명령: `'rm peer.py' | Invoke-Expression -Verbose` · `'rm peer.py' | iex -Force` → `allow`
- 원인: 바인딩이 수신 토큰이 정확히 1개일 때만 발동. `-Verbose` 같은 공통 파라미터가 붙어도
  PowerShell은 파이프 입력을 실행하지만 파서는 바인딩을 포기하고 bare 래퍼(빈 payload)로 봐 통과.
- 심각도: High — 조건이 지나치게 협함. M2와 결합하면 정적 리터럴 파이프 실행이 쉽게 우회됨.

### M4 [Medium] 비-literal 파이프 입력을 eval 계열이 받아도 fail-closed 아님
- 명령: `Get-Content x.ps1 | iex` · `Get-Content x.ps1 | Invoke-Expression` · `cat x.sh | iex` → `allow`
- 원인: 직전 세그먼트가 인용 리터럴일 때만 바인딩. 동적 입력이 Invoke-Expression으로 흘러도
  파이프 수신 셸(`| bash`)처럼 불투명 실행으로 fail-closed 처리되지 않음.
- 심각도: Medium — arbitrary-code 비목표 경계에 걸치나, 이 계층이 다루기로 한 바인딩의 사각.

### M5 [Low] 위생 건
- `rm --recursive=false shared-dir` → allow. GNU rm은 플래그 옵션의 `=value`를 거부해 실제 파괴 불가 —
  이론적 항목, 기록만.

## 발견한 오탐
**신규 오탐 0건.** 오탐 우선 원칙으로 집중 공략했으나 새 계층의 정밀도는 우수:
- 컨테이너 형제·유사 이름: `rm -r shared-dir2`·`shared-dirX`·`shared-dir.bak`·`xshared-dir`·
  `a/shared-dir` 전부 허용 (prefix 비교가 `scope/` 경계를 정확히 지킴)
- 재귀 복사 파생 과도 차단 없음: `cp -R src shared-dir`·`cp -R shared-dir backup` 허용
- 괄호·@ 파일명: `Remove-Item 'report(1).txt'`·`'report(1).txt','notes(2).md'`·`'@notes.md'`·
  `mv 'data (copy).txt' archive/`·`cp 'report (final).docx' out/`·`sed -i … 'report(1).txt'`·
  `truncate -s 0 'data (v2).log'`·`'report (1).txt' | Remove-Item`·`-LiteralPath 'report(1).txt'` 전부 허용
  (동적식 검사가 인용부로 무력화되지 않음 — C 계층 설계 정상)
- 바인더: `-WhatIf:$true` 허용·`-WhatIf:$false` 차단 유지, `-Force:$false`도 대상 차단 유지
- `sc stop/delete peersvc` 허용(F2 유지), `perl -Ilib`·`-Iinclude` 허용(F1 유지, 참 in-place는 여전히 차단)
- 파이프 정상 흐름: `ls | timeout 5 grep x`·`cat f | xargs echo`·`echo hi | nice grep x`·`ls | time`·`ls | env` 허용
- 비재귀 디렉터리 제거(비어 있지 않으면 실패하는 명령)·git rm `--cached`/`-n` 허용

## 뚫지 못한 각도 (방어 확인)
- 컨테이너 경로 표기 변형 전원 차단: `shared-dir/`·`//`·`./…/../shared-dir`·`SHARED-DIR`·`shared-dir\`·`-rf`·`-R`·`--recursive`
- 컨테이너 발생 지점 전원: `rm -r`(POSIX/클러스터)·`mv`/`Move-Item`(소스 컨테이너)·`git rm -r`·
  `Remove-Item -Recurse`·`-Recurse:$true`(차단)·`-Recurse:$false`(허용)·`mv shared-dir shared-dir2`(차단)
- 바인더 정규화: 모호 접두사 `-P`·`-W` fail-closed 차단 / 유일 접두사 `-Pa`·`-Stre` 해석 /
  `-Path:value`·`-Path::weird`·`-Path:`·`-Force:maybe` 전부 fail-closed / `-Path:C:\…` 루트 밖 허용
- 동적 식: `(`·`@(`·`+(`·`&{`·`,(` 전원 차단(직전 N1·N2 폐쇄 확인)
- 파이프 래퍼 파생: nice·ionice·setsid·stdbuf·time·busybox·toybox·powershell·pwsh·cmd 수신 차단(직전 N3 폐쇄),
  중첩 `sudo -E nice bash`·`env nice bash`도 차단, xargs 의도적 제외 정상

## 종합 판정 — **v2.8.1로 이대로 릴리스 불가, 소규모 후속 패치 필요**

차단 사유:
1. **M1은 이번 수리의 간판(G1 컨테이너 보호)을 그 수리가 겨냥한 운영체제(cmd)의 표준 문법에서 무력화**한다.
   `rd /s`는 `rm -r`과 동일한 작업이며, 재귀 인식 목록에 `/s`(와 대문자 `/S`)만 추가하면 닫힌다.
2. **M2·M3은 새 파이프 바인딩 계층 자체의 구멍**이다. 본명은 막히고 별칭·파라미터 변형은 뚫리는
   정규화 불일치로, 계층의 보호 범위가 실제 PowerShell 사용 패턴보다 좁다.

완화 요소(판정 근거의 반대편): 신규 오탐 0건 — 계층의 정밀도는 높아 정상 개발 흐름 마찰이 없고,
직전 라운드 14건(N1~N4·F1~F5 계열)이 전부 폐쇄된 것을 실측 확인했다. 남은 구멍은 4개(M1~M4)로
수리 범위가 작다: ① rd/rmdir/del의 `/s` 재귀 인식 ② 바인딩 비교를 `_command_name` 결과가 아니라
INVOKE_EXPRESSION 계열 판정으로 ③ `len==1` 조건을 "파라미터는 있으되 -Path/-LiteralPath 없음"으로 완화
④ eval 계열의 비-literal 파이프 수신 fail-closed. 회귀 테스트에 `rd /s shared-dir` block과
`'rm x' | iex` block, `'x' | Invoke-Expression -Verbose` block을 추가할 것.

[QODER-VERIFY-DONE]
