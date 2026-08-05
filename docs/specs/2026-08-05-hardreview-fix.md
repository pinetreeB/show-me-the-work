# 하드리뷰 수리 확정 스펙 — R2 어휘 확장 + 어댑터 편차 폐쇄

- 일자: 2026-08-05 · 기준 main `cb90ecb` (v2.7.1)
- 발단: 사용자 지시로 좌하 agy·우하 Qoder에 **저장소 전체 적대적 하드리뷰**를 독립 수행시킴
- 입력: `tmp/hardreview-20260805/`(리뷰 2건 + 좌상 교차검증), `tmp/hardreview-fix/`(설계 3자 토론 + 실측)
- 방법론: **리뷰 지적을 실측으로 검증한 뒤에만 채택**([[feedback_review_vs_measurement]])

## 0. 리뷰 교차검증 결과 — 9건 중 6건 채택, 1건 기각, 2건 하향

| 리뷰어 | 지적 | 좌상 실측 판정 |
|--------|------|----------------|
| Qoder | C1 R2 삭제 어휘 구멍 | **확인** → 수리 |
| Qoder | H1 파이프-셸 비대칭 | **확인** → 수리 |
| Qoder | H2 bare iex/Invoke-Expression 우회 | **확인** → 수리 |
| Qoder | H3 antigravity 신원 승격 누락 | **확인** → 수리 |
| agy | High-01 codex_cli 3훅 캐시 누락 | **확인** → 수리 |
| agy | High-02 캐시 테스트 실검증 불능 | **확인** → 수리 |
| agy | Critical-01 process substitution 우회 | **기각** |
| agy | Critical-02 예외→fail_open 감독 무력화 | **하향**(R2 경로는 fail-closed) |
| agy | Critical-03 락 타임아웃→게이트 무력화 | **하향**(R2 이후 단계) |

**기각 근거(Critical-01)**: `cat <(rm -rf peer.py)`는 `rm -rf peer.py`와 **동일하게 탐지**된다
(`targets=('peer.py',)`). 파서가 process substitution을 이미 세그먼트로 분치한다.

**하향 근거(Critical-02/03)**: R2는 다른 ledger 읽기보다 먼저 실행되고(`pre_tool_use.py:160-163`),
내부에서 모든 예외를 degraded로 흡수하며(`destructive_guard.py:1491-1498`),
ledger 손상은 `_block("ledger_degraded")`로 **fail-CLOSED 차단**한다(`:1718-1720`).
block이면 즉시 반환(`:183`)이라 이후 broad except가 뒤집을 수 없다. 락 대기는
`begin_invocation`(`:225`)에서 발생해 R2 이후다. 잔여 창은 M3로 별도 기록(§5).

## 1. 확정 설계 — B-Routing (조건부 판정이 아니라 조건부 라우팅)

파서의 역할을 **"파괴적이다/아니다"의 최종 판정**에서 **"귀속 검사로 보낼지의 라우팅"**으로 낮춘다.
파서는 파일시스템을 만지지 않는다(정적 렉서 지위 유지). 존재·소유 판정은 이미 원장·fs를 조회하는
귀속 계층이 한다.

### 결정 근거 — 3자 토론이 갈렸고 실측이 판정했다

- **Codex(우상)**: B-Routing 권고
- **Cantus(단발)**: B를 "조건부 라우팅"으로 재정의해 권고 — 독립적으로 같은 결론
- **agy(좌하)**: 옵션 A(최소 확장) 권고 — **반대 근거 3건이 전부 실측에서 기각됨**

| agy 주장 | 실측 |
|----------|------|
| 어휘 확장 = 마찰 폭증(B 30~50회, C 100회+) | **기각.** "탐지=차단"이라는 틀린 전제. 경쟁 없으면 `rm -rf build/`도 ALLOW |
| 놓쳐도 quarantine 백업이 복구해준다 | **기각.** quarantine은 *차단된 명령 텍스트*만, provenance는 *해시*만 저장. 복구 수단 없음 |
| 옵션 C는 `pytest > test.log`를 막아 파이프라인 폭발 | **기각.** 이미 오늘 BLOCK인 현재 동작 |
| B는 stat 도입 → TOCTOU 자초 | **정당.** 파서 내 stat을 하지 않는 형태로 채택해 이 반론을 회피 |

**결정적 실험**(`tmp/hardreview-fix/bidirectional_proof.py`) — beta가 `peer.py` 미정산 점유 상태:
- `rm peer.py` → BLOCK (게이트 작동)
- `rm my_own_file.py` → **ALLOW** (탐지되지만 남의 것이 아니라 통과) ← 어휘 확장이 저렴한 이유
- `cp .env.example .env`, `mv old.py new.py` → ALLOW

## 2. 확장 범위

| 명령·가족 | 대상 추출 | 근거 |
|-----------|-----------|------|
| `unlink`, `shred` | 리터럴 경로 | `rm`과 동일 의미인데 집합에 없었음 |
| `truncate`, `Clear-Content` | 리터럴 대상(`-Path`/`-LiteralPath` 포함) | 내용 파괴 |
| `dd of=` | 리터럴 `of=PATH` | 덮어쓰기 |
| `cp`, `Copy-Item` | **목적지만** | 소스는 읽기라 파괴 아님 |
| `mv`, `Move-Item` | **소스와 목적지 둘 다**(sink 목적지면 소스만) | move는 소스 경로가 사라짐 |
| `[IO.File]::Delete` / `WriteAllText` 계열 | 첫 리터럴 인자 | 정적 메서드 호출 미인식 |
| `sed -i` / `--in-place` | 스크립트 뒤 리터럴 파일 | **좌상 신규 발견 갭** |
| `iex`, `Invoke-Expression` | 불투명 래퍼 | H2 |
| 파이프 수신 셸(`\| bash`/`sh`/`zsh`) | 불투명 실행 | H1 — `bash < f`가 이미 차단되는 것과의 비대칭 해소 |
| `>` / `>>` | **현행 유지** | 이미 탐지 중 |

### 구현 제약 (어기면 오탐)
1. 파서는 파일시스템을 조회하지 않는다 — 존재·소유 판정은 귀속 계층
2. 덮어쓰기 여부를 판정할 필요가 없다 — 목적지를 귀속 후보로 보내면 자동으로 갈린다
3. H2는 `WRAPPER_COMMAND_NAMES`와 `OPAQUE_WRAPPER_COMMAND_NAMES` **양쪽**에 추가.
   OPAQUE만 바꾸면 `_detect_wrapper_indirect()` 첫 membership 검사에서 빠져나가 **수리가 무효**

## 3. 어댑터 편차 폐쇄 (커밋 `23e2398`)

- **H3**: `antigravity/oma_hook.py` pre/post에 `legacy_default → exact` 승격 이식.
  다른 3개 어댑터에는 전부 있던 수리였다
- **High-01**: `codex_cli/{post_tool_use,stop,user_prompt_submit}.py`에 `ledger_read_cache` 적용.
  v2.7.1이 나머지 3개 *어댑터*를 덮었으나 codex 자신의 *형제 엔트리포인트*는 빠져 있었다.
  런타임 실측 uncached 로드: stop 9→1, user_prompt_submit 6→3, post_tool_use 12→2

> **★교훈 재발**: v2.7.1이 남긴 교훈은 "어댑터 1개 실측 ≠ 전 호스트 적용, 전수 grep하라"였다.
> 그때 어댑터 단위로는 전수했으나 **엔트리포인트 단위로는 전수하지 않아** 같은 계열이 재발했다.
> 전수의 단위는 "어댑터"가 아니라 "원장을 읽는 모든 진입점"이다.

## 4. 테스트 검출력 (High-02)

`tests/test_w4c_adapter_cache.py:114-122`가 `inspect.getsource` + 문자열 포함 검사였다.
코드를 실행하지 않으므로 캐시가 실제로 동작하는지 재지 않고, claude 엔트리포인트만 봐서
**codex 테스트가 0건**이었다 — 위 High-01 누락을 구조적으로 잡을 수 없었다.
런타임 계측(`_load_ledger_uncached` 호출 횟수) + 어댑터 × 엔트리포인트 매트릭스로 교체한다.

## 5. Known Limitation (정직 선언)

1. `> file` 리다이렉션은 새 파일 생성도 파괴로 취급(TOCTOU 방어 불가) — 기존 확정, 유지
2. `| iex`, `| python`, `| node` 등 **인터프리터** 파이프 수신 — 정당 관용구
   (`fnm env | iex`, `starship init powershell | iex`)와 정적 구분 불가. 미대응
3. `python -c "os.remove(...)"` 등 임의 코드 — 기존 선언된 비목표, 유지
4. 미정산 원장에 없는 파일의 덮어쓰기 — 제품 범위가 "미정산 작업물 보호"라 설계상 대상 아님
5. **M3 — R2 이전 창의 fail-open**(백로그 이월): payload가 dict가 아니면
   `payload.get()`에서 예외 → 광역 except → fail_open으로 R2 미실행.
   4개 어댑터 공통 구조. 단 정상 JSON dict에서는 `common.py` 타입 가드로 유발 불가가 실측됨.
   수리안(R2 최선두 배치)은 귀속 신원이 아직 없다는 미해결 문제가 있어 이번 범위 제외
6. **조용한 fail-open 표면**(백로그 신규): `warn_once`가 1회차만 경고하고
   2회차부터 빈 JSON만 출력 → 같은 세션에서 장애가 반복되면 아무 경고도 안 보임(실측 확인)

## 6-A. 구현 결과 (완료)

| 커밋 | 내용 |
|------|------|
| `23e2398` | H3 antigravity 신원 승격 + High-01 codex_cli 3훅 캐시 |
| `6c9779d` | High-02 캐시 테스트를 런타임 매트릭스로 교체 + 메타 강제 |
| `72b71ff` | 신원 승격에도 동일한 구조적 매트릭스 (재발 방지) |
| `8e60b28` | C1/H1/H2/`sed -i` — R2 어휘 확장 B-Routing (+718/-23) |

- 코퍼스: **PASS 69/69 (미탐 0 · 오탐 0)** — MUST_BLOCK 37/37 BLOCK, MUST_ALLOW 32/32 ALLOW
- 신규 영속 회귀: `tests/test_hardreview_r2_v272_vocabulary.py` 78 passed
- RED 증거: 최초 실행 39 failed / 코퍼스 4건 확장 직후 8 failed → 수리 후 전건 GREEN
- 부수 보강: 새 어휘의 `--help`, PowerShell `-WhatIf` dry-run은 ALLOW 고정(오탐 우선 원칙)

**재발 방지 장치 2종 실증**(좌상 독립 검증): `adapters/`에 가짜 어댑터 디렉토리를 만들자
캐시 매트릭스·신원승격 매트릭스 메타 테스트가 **둘 다 즉시 RED**. 정리 후 워킹트리 무변경 확인.

## 6. 합격 기준

`tmp/hardreview-fix/acceptance_corpus.py`(게이트 종단 기준) **미탐 0 · 오탐 0**.
- 기준선: 미탐 23 / 오탐 0 (MUST_BLOCK 33건 중 23 실패, MUST_ALLOW 32건 전건 통과)
- 목표: MUST_BLOCK 33/33 BLOCK, MUST_ALLOW 32/32 ALLOW
- **오탐 0을 미탐 0보다 우선**한다. 감독이 정상 작업을 막으면 사용자가 도구를 끄고,
  꺼진 게이트는 미탐 100%다
- 전체 pytest 회귀 0 (어댑터 수리 시점 기준선 = 1660 passed, 2 skipped)

> ⚠️ 코퍼스 v1은 **파서 탐지 여부**로 재서 오탐을 잘못 집계했다(우상 Codex 적발).
> "탐지 ≠ 차단"이 이 설계의 핵심이므로 합격 판정도 게이트 종단으로 재야 한다.

## 7. CHANGELOG 정정 대상

`CHANGELOG.md:68-74`(v2.7.0)가 차단됐다고 나열한 8종 중 **7종이 래핑된 경우에만 차단**된다:
`[IO.File]::WriteAllText` · `Invoke-Expression` · `Add-Content` · `Clear-Content` ·
`Copy-Item` · `Move-Item` · `New-Item -Force`는 bare로 호출하면 통과(`Out-File`만 양쪽 차단).
원문이 "all passed through `powershell -Command`"로 범위를 밝히긴 했으나
"all 19 measured cases are blocked now" · "0 false negatives"가 일반적 안전 주장으로 읽힌다.
셸 자체가 PowerShell이라 **최상위 호출이 가장 흔한 형태**인데 그게 커버되지 않았다.
