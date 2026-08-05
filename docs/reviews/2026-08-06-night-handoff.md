# 야간 인수인계 — 쇼미더워크 2026-08-06

> 상태 SSOT. 좌상 컨텍스트가 압축되거나 세션이 끊겨도 이 파일로 복원한다.
> 발동: 2026-08-06 01:07 KST (이미 야간 시간대 — 우하 교대 불필요, 사용자 지시로 세션 시작부터 Qoder 체제)

## 주간 종료 시점 상태

- **브랜치**: `main` = `188ef0d` (v2.8.0 릴리스 완료, 태그·Release 발행됨)
- **우상 codex**: `worker/review2-fix` 브랜치 생성 후 41건 수리 중 (과제 = `tmp/review2/TASK-codex-fix41.md`)
- **우하 Qoder**: 2차 리뷰 완료, 유휴 (다음 임무 = codex 수리분 적대 검증)
- **좌하 agy**: 미검토 영역 리뷰 완료, 유휴
- 미커밋 변경: `.codex/config.toml`(세션 시작 전부터 존재, 사용자 것 — 건드리지 말 것)

## 지금까지 완료 (되돌리지 말 것)

| 커밋/태그 | 내용 |
|---|---|
| `f44d2a4` | PR #53 머지 — 1차 하드리뷰 발견분 전량 수리 |
| `188ef0d` | v2.8.0 릴리스 커밋 (버전 표면 6파일) |
| 태그 `v2.8.0` | [Release](https://github.com/pinetreeB/show-me-the-work/releases/tag/v2.8.0) latest · wheel 자산 · release-quality success |
| 플러그인 | 2.7.1 → 2.8.0 갱신 완료 (CC 재시작 후 적용) |

## 야간 할 일 (우선순위순)

1. [ ] **codex 41건 수리 회수·검증** — 완료 기준: `PYTHONIOENCODING=utf-8 python eval/r2_vocabulary_acceptance.py` → **0/141**
   - 감시: `tmp/review2/fix41-result.md`에 `[CODEX-FIX41-DONE]`
   - 좌상이 **반드시 독립 재현**할 것 (워커 보고를 그대로 믿지 않는다 — 이 세션 규율)
2. [ ] **전체 회귀** — `python -m pytest tests/ -q` → 기준선 **1828 passed, 2 skipped** 이상, 회귀 0
   - ⚠️ 다른 워커가 테스트를 동시에 돌리면 부하성 flaky 발생(이 세션 실측). 조용할 때 돌릴 것
3. [ ] **우하 Qoder에 적대 검증 위임** — 새로 수리된 코드가 표적. 특히 G1(디렉터리 prefix 비교)이
   오탐을 만들지 않는지(`rm -r 내폴더`·`ls 공유폴더` 통과 유지)
4. [ ] **PR → 머지** — 브랜치 `worker/review2-fix` → main.
   ⚠️ **머지는 `gh pr merge --admin` 필요** — 이 저장소는 브랜치 보호 필수 체크 이름이 122자로
   등록됐는데 GitHub이 100자로 자르므로 정상 경로 머지가 불가능하다(이 세션 규명). CI 5조합
   green + 리뷰 스레드 해소를 확인한 뒤에만 admin 머지할 것
5. [ ] **v2.8.1 릴리스** — `.claude-plugin/plugin.json` 버전 범프 → `python scripts/sync_version.py`
   → CHANGELOG `[2.8.1]` 확정 → 커밋 → 태그 `v2.8.1` 푸시 → release-quality 대기 →
   `gh release create` + wheel 자산 → `claude plugin update show-me-the-work@fable-lite`
   - **patch 범프가 맞다**: 이번은 v2.8.0이 만든 불일치 수정 + 기존 갭 폐쇄이고 새 기능이 없다.
     (v2.8.0을 minor로 올린 건 차단 대상 집합을 크게 바꿨기 때문)
6. [ ] **문서 갱신** — `docs/specs/2026-08-05-hardreview-fix.md`에 2차 리뷰 절 추가 ·
   `docs/reviews/`에 2차 리뷰 3건 영속화 · Known Limitation 갱신

## 검증 방법

```
PYTHONIOENCODING=utf-8 python eval/r2_vocabulary_acceptance.py    # 0/141 이어야 함
PYTHONIOENCODING=utf-8 python -m pytest tests/ -q                 # 1828+ passed, 회귀 0
```

## 비가역 정책 (주간과 동일 — 롤백 확보 후 자동)

- **자동 진행**: 브랜치 push · PR 생성 · admin 머지 · 태그 · GitHub Release · 플러그인 갱신
  → 전부 롤백 경로 존재(브랜치 삭제 / PR 닫기 / revert / 태그·Release 삭제 — 이 저장소는
  v3.0.0→v2.5.0 정정 전례 있음)
- **상시 보류**: 외부 발송 · 결제 · force push → 아침 보고로 이관

## 남은 백로그 (이번 범위 밖 — 문서화만)

- **M2 따옴표·이스케이프 분할** `rm pe""er.py` — 수리 이전부터의 파서 일반 결함, `rm` 경로 전체 영향
- `core/scope_guard.py:45` `except Exception: return True` fail-open (agy 발견, R2가 별도로 막아 영향 제한적)
- codex 감사 F2·F3·F5·F6·F7 (git 전역옵션·dry-run·stash list 등 보수적 차단 — 정책 판단 필요)
- Qoder F4(POSIX cp 쉼표)·F5(따옴표 이름 파일)
- M3 R2 이전 창 fail-open (4개 어댑터 공통, 정상 payload에서는 유발 불가 실측)
- 조용한 fail-open 표면 — `warn_once`가 2회차부터 빈 JSON만 출력(agy 실측)
- **⚠️[사용자님] 브랜치 보호 필수 체크 이름 122자 문제** — 수정하면 admin 우회 없이 머지 가능해짐

## 야간 결과 (진행하며 기록)

> ⚠️ **아래 절 제목의 시각은 부정확하다.** 좌상이 실제 시계를 안 보고 경과 시간으로 추정해
> 적었다(01:07 발동 후 "02:00", "05:30", "08:10" 등). **04:29 KST 시점에 실측한 결과 실제로는
> 그보다 훨씬 이른 시각이었다.** 순서는 맞으나 절대 시각은 신뢰하지 말 것.
> **교훈: 시각을 보고에 쓸 때는 `date`로 실측할 것.**

### 01:07~02:00 — codex 41건 수리 회수·검증

- codex 보고: 코퍼스 141/141 · 전체 pytest **1900 passed, 2 skipped** · Ruff·basedpyright 클린
- **좌상 독립 재현**: 코퍼스 141/141 확인 ✅
- **G1(Critical) 정밀 검증** — 코퍼스 밖 변형으로 직접 확인:
  - 차단 확인: `rm -r ./shared-dir` · `rm -r shared-dir/` · `rm -rf shared-dir/../shared-dir` ·
    `Remove-Item -Recurse -Force shared-dir` · `rm -r shared-dir/sub/..` · `git checkout HEAD -- shared-dir`
  - **오탐 없음 확인(중요)**: `rm -r shared-directory`(이름 겹침) · `rm -r shared` · `ls -la shared-dir` ·
    `cat shared-dir/peer.py` · `grep -rn TODO shared-dir` · `find shared-dir -name x` · `du -sh` ·
    `git status` · `cd` · `rm -r my_own_dir` · `mv my_own_dir renamed` → 전건 ALLOW
- **오탐 수리 과잉 완화 검증**: `perl -Ilib`·`-Mstrict`·`sc stop`·`sc query`·`-WhatIf:$true` 통과하면서
  `perl -i`·`perl -pi`·`sc peer.py x`·`-WhatIf:$false`·`sed -ni` 는 계속 차단 ✅
- codex 자체 발견 1건: `bash -C`가 `-c`로 오인되던 대소문자 버그 → 수리됨

### 02:00~ — 좌상 코퍼스 결함 발견·수정 (G2)

codex가 P3에서 **정직하게 보고한 한계**가 실제 코퍼스 결함이었다:
`cp -tpeer-dir mine.py`의 실제 쓰기 대상은 `peer-dir/mine.py`인데 코퍼스 픽스처는 peer가 `peer.py`를
점유하는 상태였다 — **시험이 틀렸고**, codex는 그 틀린 기준을 맞추려 붙임형을 전면 차단하는
임시방편을 썼다(보고서에 명시함).

- 수정: G2를 DIR 픽스처(peer=`shared-dir/peer.py`)로 이동 + 대조군 `cp -tmy_own_dir mine.py` 추가
- 결과: **FAIL 1/146** — 임시방편이 오탐으로 드러남
- codex에 재위임(진행 중): `-tDIR` 붙임형을 GNU cluster 규칙대로 파싱해 `DIR/basename(source)`를
  귀속검사로 라우팅(무조건 차단이 아니라)
- **교훈: 워커가 "기준이 이상해서 우회했다"고 보고하면 그 기준부터 의심할 것.**
  이번엔 워커 보고가 정확했고 좌상 산출물이 틀렸다.

### 02:20~03:00 — G2 수리 완료 · 병행 검증 착수

- **코퍼스 146/146 PASS** 도달 (좌상 독립 확인)
- 전체 회귀 **1900 passed, 2 skipped** (G2 수리 직전 상태 기준 — codex가 최종본으로 재실행 중)
  기준선 대비: v2.8.0 1828 → 1900 (신규 회귀 테스트 72건 증가)
- codex 커밋 `0e6b8a7` (+1199/-111, 5파일). G2 최종 수리분은 아직 워킹트리 (codex가 회귀 후 갱신 예정)
- **우하 Qoder에 수리분 적대 검증 위임** — 표적은 이번에 새로 생긴 계층:
  컨테이너 prefix 비교 · PowerShell 바인더 정규화 · 동적 식 fail-closed 전수 · 파이프 래퍼 파생.
  오탐 우선 지시(폴더 비교가 어긋나면 무관한 `shared-dir2`까지 막혀 개발 마비)
- **v2.8.1 CHANGELOG 초안 작성 완료**: `tmp/review2/CHANGELOG-draft-281.md`

### 03:00~03:40 — 수리 확정·푸시

- codex 최종 커밋 **`8437e5c`** `fix(r2): close review2 destructive guard gaps`, 워킹트리 클린
- 좌상 독립 재현: 코퍼스 **146/146** ✅
- **G2 임시방편 → 정식 수리 확인**(좌상 직접 실측):
  - `cp -tshared-dir peer.py` · `mv -tshared-dir peer.py` · `cp --target-directory=shared-dir peer.py` → BLOCK
  - `cp -tmy_own_dir mine.py` · `cp -t my_own_dir mine.py` · `mv -tmy_own_dir mine.py` → **ALLOW**
    (전면 차단 임시방편이었다면 여기서 오탐이 났을 것)
- **G1 회귀 없음 재확인**: `rm -r shared-directory`·`ls`·`cat`·`rm -r my_own_dir` ALLOW 유지,
  `rm -r shared-dir` BLOCK 유지
- 원격 푸시 완료: `origin/worker/review2-fix` = `8437e5c` (롤백 = 브랜치 삭제)
- 전체 회귀 최종본 재실행 중

### 03:40~04:30 — Qoder 적대 검증 → 릴리스 차단 판정 → 후속 수리 착수

우하 Qoder가 108변형으로 수리분(`8437e5c`)을 적대 검증. **판정 = 릴리스 불가.**

**신규 오탐 0건** — 새 계층의 정밀도는 우수함이 확인됐다(이게 중요하다):
- 형제·유사 이름 디렉터리 `shared-dir2`·`shared-dirX`·`shared-dir.bak`·`xshared-dir`·`a/shared-dir` 전부 허용
- 괄호·@ 들어간 실제 파일명 `'report(1).txt'`·`'@notes.md'`·`'data (copy).txt'` 전부 허용
  (동적식 검사가 인용부로 무력화되지 않음)
- 정상 파이프 `ls | timeout 5 grep x`·`cat f | xargs echo`·`ls | env` 허용
- 직전 라운드 14건(N1~N4·F1~F5) 전부 폐쇄 실측 확인

**미탐 4건 발견 — 좌상 전건 재현 확인:**

| 건 | 명령 | 판정 | 왜 중대한가 |
|---|---|---|---|
| **M1 [High]** | `rd /s shared-dir` · `rd /S /Q` · `rmdir /s` | ALLOW | **이번 수리의 간판(G1 컨테이너 보호)이 Windows cmd 표준 문법에서 무력화.** 대조 `rm -r shared-dir`는 BLOCK. 사용자 환경이 Windows라 실사용 위험 |
| **M2 [High]** | `'rm peer.py' \| iex` | ALLOW | 새 파이프 바인딩이 **본명만** 인식(`\| Invoke-Expression`은 BLOCK). PowerShell에서 iex가 본명보다 흔함 |
| **M3 [High]** | `\| Invoke-Expression -Verbose` | ALLOW | 바인딩이 수신 토큰 **1개일 때만** 발동. 파라미터 하나로 무력화 |
| **M4 [Medium]** | `Get-Content x.ps1 \| iex` | ALLOW | 대조 `cat x.sh \| bash`는 BLOCK. 같은 불투명 실행인데 iex 계열만 통과 |

- 코퍼스 **158건**으로 확장, 기준선 **FAIL 9/158**. 오탐 대조군(`rd /s my_own_dir`,
  `rd shared-dir` 비재귀) 추가
- codex에 후속 수리 위임(진행 중 — 158/158·전체회귀·푸시까지 한 흐름으로 진행 중이라 보고함)
- 참고: `8437e5c` 기준 전체 회귀 **1907 passed, 2 skipped** (회귀 0). M1~M4 수리 후 재확인 필요
- **패턴 재확인**: 수리가 새 우회면을 만든다. M2·M3은 **이번에 새로 만든 파이프 바인딩 계층
  자체의 구멍**이다. 이 세션에서 4번째다(v2.7.1 캐시 · v2.8.0 신원승격 · v2.8.0 동적식 · 이번 바인딩)

### 04:50~05:30 — M1~M4 수리 완료 · 3라운드 검증 착수

- codex 커밋 **`c56647e`** `fix(r2): close nightly cmd and pipeline gaps`, 워킹트리 클린
- 코퍼스 **158/158 PASS** (좌상 독립 재현)
- **좌상 전방위 실측 검증** — 23개 명령 직접 확인:
  - 수리 확인(BLOCK): `rd /s`·`rd /S /Q`·`rmdir /s`·`rd /s /q` · `'rm peer.py' | iex`·`| iex -Force`·
    `| Invoke-Expression -Verbose`·`Get-Content x.ps1 | iex`
  - **오탐 없음(ALLOW)**: `rd /s my_own_dir`·`rmdir /s /q my_own_dir`·`rd shared-dir`(비재귀)·
    `rm -r shared-directory`·`ls -la shared-dir`·`Get-Content peer.py`·`ls | timeout 5 grep x`·
    `cat f | xargs echo`·`Remove-Item 'report(1).txt'`
  - **기존 방어 유지(BLOCK)**: `rm -r shared-dir`·`Remove-Item @(peer.py)`·`Remove-Item (gi peer.py)`·
    `cat x.sh | bash`·`sed -ni`·`ri peer.py`
- 전체 회귀 재실행 중
- **우하 Qoder에 3라운드 적대 검증 위임** — 표적은 **이번 수리가 또 새 구멍을 만들었는지**.
  근거: 이 세션에서 "수리가 새 우회면을 만든다"가 4회 반복됐고 Qoder의 M2·M3이 바로 직전
  수리(파이프 바인딩)의 구멍이었다. 구체 표적 = `/s` 인식과 다른 `/`옵션 충돌 · 바인딩 조건
  완화의 과잉 · eval fail-closed의 정상흐름 차단 · **오탐 최우선**

### 05:30~06:10 — ★3라운드 검증 통과, 첫 "릴리스 가능" 판정

Qoder 75변형 최종 검증. **이 세션에서 처음으로 신규 발견 0건.**

- **신규 미탐 0 · 신규 오탐 0**
- M1~M4 폐쇄 확인 + 그 우회 변형(플래그 위치 교환·대소문자·인용·파라미터 조합·다중 세그먼트·
  변수 입력)까지 전부 방어
- **완화의 정밀도 확인**: 명시적 `-Path`가 파이프 바인딩을 정확히 억제(`'peer.py' | Remove-Item -Path my.py`
  → ALLOW), 비-eval 수신자(`Select-String`·`ForEach-Object`·`Sort-Object`·`Tee-Object`) 누수 없음,
  정상 개발 흐름(`npm test | tee`·`python x > out.log`·git·docker·pip) 전원 ALLOW
- **Qoder가 실증 실험까지 수행**: `rd /sq`가 우회인지 의심돼 빈 임시 디렉터리로 실제 cmd 실행 →
  "스위치가 잘못되었습니다"로 거부됨을 확인(디렉터리 잔존). 실 cmd 문법이 아니므로 ALLOW가 정답
- **좌상 독립 재현 완료**: /s 변형 4건 BLOCK · 바인딩 정밀도 5건 · 다중세그먼트/변수 3건 BLOCK
- 원격 푸시: `origin/worker/review2-fix` = `c56647e`

**판정: v2.8.1 릴리스 진행.** 라운드마다 새 결함이 나오던 흐름이 처음으로 멈췄다.

#### 후속 백로그 (비차단, 이번 수리 도입 아님 — diff로 확인)
- **positional `Set-Content mine.txt 'data'`·`Out-File mine.txt`가 fail-closed 차단**된다.
  `-Path`/`-FilePath` 명시형은 통과. PowerShell positional은 일상 문법이라 실질 마찰 가능.
  **v2.8.0(`188ef0d`) 시점 코드와 동일**함을 Qoder가 diff로 확인 — c56647e가 만든 게 아니다
- `iex -Command 'Get-Date'` 같은 무해 리터럴도 불투명 래퍼 fail-closed로 차단(기존 보수적 자세)

### 06:40~07:20 — PR #54 생성 · 리뷰봇 P2 2건 수리 착수

- 근거 문서 5건 영속화 커밋 `8860b4e` (docs/reviews/2026-08-06-review2-*)
- **PR #54 생성** — CI 5조합 **전부 green**, 최신 커밋 기준 확인
- **GitHub 리뷰봇 P2 2건 지적 → 좌상 전건 재현**:

| 건 | 명령 | 판정 | 대조 |
|---|---|---|---|
| P2-1 | `Remove-Item -Path:'peer dir/file.py'` | ALLOW | 분리형 `-Path 'peer dir/file.py'`·위치 인자는 BLOCK |
| P2-2 | `git restore -s HEAD -p` · `git checkout --detach HEAD -p` · `-pq` · `git restore -pq` | ALLOW | `git checkout -p`·`git restore -p`는 BLOCK |

- P2-1 원인: 콜론 결합 값의 인용 경로가 첫 shlex 조각(`'peer`)만 취해짐. 공백 있는 경로는
  Windows에서 흔함
- P2-2 원인: 정확히 `-p`/`--patch` 토큰만 매칭하고 `_parse_git_pathspec()`이 옵션 인자를 소비하지
  않아 `HEAD`를 경로로 기록. pathspec 없는 patch는 repo 전역 대화식 되돌리기
- 코퍼스 **169건**으로 확장(공백 경로 전용 픽스처 추가), 기준선 **FAIL 7/169**
- codex 수리 위임(진행 중)
- **관찰**: 리뷰봇이 또 문서 근거(about_Parameters·about_Parsing·`git restore -h`)로 잡았다.
  3라운드 변형 검증(283변형)이 전부 놓친 것 — [[feedback_adversarial_variants_vs_spec_reading]] 재확인

### 07:20~08:10 — P2 수리 완료 · 좌상 검증 중 N5 신규 발견

- codex 커밋 `39f89a9`, 코퍼스 169/169. **P2 2건 폐쇄 확인**(좌상 실측):
  콜론 결합 인용 경로(`-Path:'peer dir/file.py'`) BLOCK · git patch 변형 6종
  (`-s HEAD -p`·`--detach HEAD -p`·`-pq`·`-q -p`·`--source=HEAD -p`) BLOCK ·
  오탐 대조(`-Path:'my own/file.py'`·`Get-Content -Path:`·`git checkout -q main`·
  `git restore -s HEAD my_own.py`·`git log -p`) ALLOW
- **★좌상이 cmdlet 전수 대조 중 별건 발견 (N5)**:
  `Move-Item`/`Rename-Item`의 **소스를 `-Path`로 명명하면 대상이 통째로 사라진다**
  ```
  Move-Item peer.py out            → BLOCK  targets=('peer.py','out','out/peer.py')
  Move-Item -Path peer.py out      → ALLOW  targets=()          ← 이름 붙이면 사라짐
  Rename-Item -Path peer.py new.py → ALLOW  targets=()
  ```
  공백·콜론과 무관(공백 없는 `peer.py`에서도 동일). `-LiteralPath`·별칭 `mi`도 동일.
- **기존 결함 확정**: v2.8.0(`188ef0d`) 워크트리 대조 → 거기서도 ALLOW. 이번 수리가 만든 것 아님
- 원인 계열: codex 감사 G9의 "named source와 접두사 destination을 섞으면 positional fallback도
  성립하지 않는다"와 동일. **명명 소스 + 위치 목적지** 조합만 뚫린다
  (둘 다 명명한 `-Path X -Destination Y`는 정상 차단)
- 코퍼스 **179건**으로 확장, 기준선 **FAIL 8/179**. codex 수리 위임(진행 중)
- **관찰**: 리뷰봇 지적을 수리한 뒤 좌상이 "그 수리가 어느 cmdlet까지 적용됐나"를 전수 대조하다
  발견했다. **수리 검증 시 같은 계열 전수 확인이 새 결함을 찾는다** — 이 세션 반복 패턴

### 미커밋 이슈 (해소됨)
codex가 워커 공통 패턴대로 커밋을 미뤘으나(이 세션 3회째) 지시 후 정상 커밋·푸시됨.
**좌상 회수 시 `git status`/`git log`로 커밋 여부를 반드시 확인할 것** — 보고서에 "커밋했다"고
써 있어도 실제로는 워킹트리에만 있는 경우가 있었다.

### 04:45~05:00 (실측) — N5 수리 확정 · PR #54 머지

- codex 커밋 `823a02d`, 코퍼스 **179/179**, 전체 회귀 **1956 passed, 2 skipped**
- 좌상 독립 검증: 코퍼스 밖 변형 9건 BLOCK(`-LiteralPath:` 결합·별칭 `mi`/`rni` 포함) ·
  오탐 대조 5건 ALLOW · 기존 방어 8건 유지
- 리뷰 스레드 2건 근거 코멘트 후 해소, CI 5조합 green(최신 커밋 기준 확인)
- **PR #54 머지 완료 → main `4e1b212`**
- v2.8.1 버전 범프: plugin.json SSOT → `sync_version.py`로 6파일 동기화 · CHANGELOG `[2.8.1]` 삽입

## 비가역 실행 기록 (실행 내용 + 롤백 방법)

| 실행 | 롤백 방법 |
|------|-----------|
| `worker/review2-fix` 원격 push (5회) | 브랜치 삭제 |
| PR #54 생성 | PR 닫기 |
| **PR #54 admin 머지 → main `4e1b212`** | `git revert -m 1 4e1b212` (머지 커밋 revert) |
| v2.8.1 버전 범프 커밋 (예정) | revert |
| 태그 `v2.8.1` push (예정) | 태그 삭제 — 이 저장소 v3.0.0→v2.5.0 정정 전례 있음 |
| GitHub Release 발행 (예정) | Release 삭제 |
| 플러그인 로컬 갱신 (예정) | `claude plugin update`로 이전 버전 재설치 |

⚠️ **admin 머지 사용 사유**: 이 저장소는 브랜치 보호의 필수 체크 이름이 122자로 등록됐는데
GitHub이 100자로 잘라 매칭이 불가능하다. 정상 경로 머지가 구조적으로 막혀 있어 관리자 우회가
유일한 방법이다. **실질 게이트(CI 5조합 green · 리뷰 스레드 0건 · 승인 요건 없음)는 전부 충족**한
상태에서만 사용했다. 사용자님 결정 필요 항목으로 아침 보고에 포함.

## 비가역 대기 (외부발송·결제·force push — 아침 사용자 결정)
