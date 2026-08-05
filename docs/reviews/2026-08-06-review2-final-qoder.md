# Qoder 최종 검증 — 야간 수리분(c56647e) 릴리스 판정

대상: main `c56647e` `fix(r2): close nightly cmd and pipeline gaps` (M1~M4 수리)
시작 전 건전성: `eval/r2_vocabulary_acceptance.py` **158/158 PASS** 확인.
방법: 게이트 종단 측정 + 파서 단계 추적 + **무해한 cmd 실증 실험**(빈 임시 디렉터리). 파괴 명령 미실행, 코드 수정 없음.

## 시도 변형 수
**75개** ((a) /s 계열 17 · (b) 바인딩 16 · (c) eval 수신자 20 · (d) 오탐·정상흐름 22) — 직전 108·100변형과 중복 없음.

## 발견한 미탐
**신규 미탐 0건.** 의심 1건은 실증으로 비우회 판정:
- `rd /sq shared-dir` → allow. cmd가 결합 플래그 `/sq`를 실제로 받아들이면 M1과 동급 우회라
  **빈 임시 디렉터리로 cmd 실증**: `rd /sq`는 "스위치가 잘못되었습니다" 오류로 거부됨(디렉터리 잔존 확인).
  실제 cmd 문법이 아니므로 allow가 올바른 판정. `/S:q` 역시 실 cmd 문법 아님.

## 직전 라운드 결함(M1~M4) 폐쇄 확인
- M1: `rd /s`·`rmdir /s`·`/S`·`/s /q`·`/q /s`·`-r`·`./`경로·인용·대소문자(`RD /S`) 전원 block,
  자기 디렉터리·형제 이름(`shared-dir2`)·비재귀 `rd`는 전원 allow
- M2: `'rm peer.py' | iex` block (별칭 바인딩 정상화 확인)
- M3: `'rm peer.py' | iex -Force`·`| Invoke-Expression -Verbose`·`| iex -ErrorAction Stop` block (len==1 조건 제거 확인)
- M4: `Get-Content x.ps1 | iex`·`$env:X | iex`·`$script | Invoke-Expression`·`ls | sort | iex`(다중 세그먼트) block

## 발견한 오탐
**이번 수리(c56647e)가 새로 만든 오탐: 0건.** 완화의 정밀도 검증:
- **과잉 바인딩 없음**: `'peer.py' | Remove-Item -Path my.py` → allow(명시적 -Path가 바인딩 억제),
  `'peer.py' | Remove-Item -WhatIf` → allow
- **-Command 명시 존중**: `iex -Command 'Get-Date'`·`iex -Command $script`·`Invoke-Expression -Command:$script`는
  파이프 eval로 오인되지 않음(바인딩·수신자 검사 모두 회피, 기존 불투명 래퍼 경로로 위임)
- **eval 아님 수신자 무영향**: `Get-Content x | Select-String foo`·`Select-Object -First 5`·`Where-Object …`·
  `ForEach-Object`·`Sort-Object`·`Tee-Object mine.txt`·`Measure-Object`·`Out-Host` 전원 allow
- **정상 개발 흐름 전원 allow**: mv·Copy-Item·`npm test | tee`·`python x > out.log`·git status·
  docker build·pip install·`Get-Content peer.py | Select-String TODO`(peer 읽기)·`rmdir build\empty`·`del /q mine`

### 기존 발견 오탐(이번 수리 도입 아님 — 백로그 권고)
- **`Set-Content mine.txt 'data'`·`Out-File mine.txt`·`sc mine.txt -Value x` → block**: positional 경로가
  `-Path` 없음을 이유로 fail-closed. **v2.8.0(188ef0d) 시점 코드와 동일함을 diff로 확인** — c56647e 도입 아님.
  `-Path`/`-FilePath` 명시형은 allow. PowerShell positional는 일상 문법이라 마찰 가능성이 실질적.
  (안전 방향 실패라 릴리스 차단 사유는 아니나 후속 수리 권고)
- 보수적 차단(기존 자세): `iex -Command 'Get-Date'`·`'Get-Date' | iex`·`iex ''` — 무해 리터럴도
  불투명 래퍼 fail-closed로 차단. 드문 형태, 기록만.

## 뚫지 못한 각도 (방어 확인)
- (a): /s의 위치 교환(`/q /s`)·대문자·`./`·인용·중첩 경로 전부 인식, `del /s`·`erase /s`는 직접 대상 차단
- (b): 파라미터 조합(-Force/-Verbose/-ErrorAction)·다중 파이프·변수 입력·빈 리터럴(`'' | iex`) 전부 fail-closed,
  명시적 -Path/-Command는 정확히 면제
- (c): 수신자 판정이 INVOKE_EXPRESSION 계열에 한정돼 비-eval 파이프라인에 누수 없음,
  bare `iex`·`Invoke-Expression`(인자 없음)은 무해하므로 allow 정상
- 컨테이너·바인더·동적식 이전 계층 회귀 없음(직전 방어 표본 재확인: 형제 디렉터리·괄호 파일명·모호 접두사)

## 종합 판정 — **v2.8.1 릴리스 가능**

근거:
1. 직전 라운드 차단 사유였던 M1~M4가 4/4 폐쇄됐고, 그 우회 변형(플래그 위치·대소문자·인용·파라미터 조합·
   다중 세그먼트·변수 입력)까지 전부 방어됨을 75변형으로 확인.
2. **오탐 우선 기준 충족**: 세 변경(/s 인식·바인딩 완화·eval fail-closed)이 정상 개발 흐름을 새로 막는
   사례 0건. 완화는 명시적 -Path/-Command를 정확히 면제하고 비-eval 수신자에 누수가 없음.
3. 유일한 의문(`rd /sq`)은 cmd 실증으로 비우회 판정.
4. 하네스 158/158.

후속 백로그(비차단): positional `Set-Content`/`Out-File` fail-closed 마찰(v2.8.0부터 존재) 수리 권고.

[QODER-FINAL-DONE]
