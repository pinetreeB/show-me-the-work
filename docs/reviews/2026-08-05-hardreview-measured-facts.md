# 좌상 실측 사실 — 설계 토론의 근거 (추정 아님)

모든 수치는 이 세션에서 파서·게이트를 직접 호출해 얻었다. 파괴 명령은 실행하지 않았다.
기준 main `cb90ecb` (v2.7.1).

## F1. ★결정적 — "탐지"는 "차단"이 아니라 "귀속검사로 라우팅"이다

경쟁 에이전트가 없는 빈 프로젝트에서 `evaluate_r2_destructive_gate` 종단 판정:

| 명령 | 판정 |
|------|------|
| `rm my_own_scratch.py` | **ALLOW** |
| `rm -rf build/` | **ALLOW** |
| `Remove-Item my_own_scratch.py` | **ALLOW** |
| `del my_own_scratch.py` | **ALLOW** |

이미 탐지 어휘에 들어있는 명령조차, 남의 미정산 작업물을 건드리지 않으면 **조용히 통과**한다.

**따라서 어휘 확장의 마찰 비용은 거의 0이다.** 새로 탐지되는 명령은 차단되는 게 아니라
귀속 검사로 보내질 뿐이고, 정상 작업이면 통과한다. 실제로 막히는 경우는
"남의 미정산 작업물을 덮는 경우"뿐인데 그건 오탐이 아니라 **정탐**이다.

> agy의 마찰 예측표(옵션 B 30~50회 / 옵션 C 100회+ / 옵션 D 수백 회)는 이 전제를 오해한
> 추정치다. agy는 코드를 읽고 추론했을 뿐 게이트를 호출해 보지 않았다.

## F2. agy의 대안 근거는 사실이 아니다

agy는 옵션 A의 잔여 위험(`cp /dev/null f` 통과)을 "Quarantine 자동 백업이 있으니
복구 불가능한 파괴는 없다"로 상쇄했다. 코드 확인 결과 **둘 다 틀렸다**:

- `core/quarantine.py:77` `backup_blocked_command` — "**차단된 명령 원문**을 백업한다".
  파일 내용이 아니라 명령 텍스트다. 게다가 **차단된 경우에만** 동작한다.
  통과한 명령(`cp /dev/null f`)에는 아무 일도 일어나지 않는다.
- `core/provenance_snapshot.py:156-158` — blake2b **다이제스트**만 저장. 변경 탐지는 되지만 복원은 불가.

**결론: 파괴된 파일을 되살릴 2차 방어선은 존재하지 않는다.** 게이트를 통과하면 그것으로 끝이다.
따라서 옵션 A의 잔여 미탐은 상쇄되지 않는 실질 데이터 유실 위험이다.

## F3. agy의 옵션 C 마찰 시나리오는 이미 현재 동작이다

agy는 "옵션 C를 하면 `pytest > test.log` 같은 게 막혀 파이프라인이 폭발한다"고 예측했으나,
실측 결과 **이미 오늘 막힌다**:

| 명령 | 현재 판정 |
|------|-----------|
| `pytest > test.log` | BLOCK |
| `npm test > build.log` | BLOCK |
| `echo v1.0.0 > VERSION` | BLOCK |

리다이렉션은 이미 파괴 취급된다(확정된 Known Limitation — TOCTOU 방어 불가로 의도적).
즉 옵션 C의 마찰은 "새로 생길 위험"이 아니라 **기존 상태 서술**이다. 새로 얻을 것도 거의 없다.

## F4. Cantus가 경고한 git 파괴 벡터는 대부분 이미 막혀 있다

| 명령 | 현재 판정 |
|------|-----------|
| `git checkout .` | BLOCK |
| `git reset --hard HEAD` | BLOCK |
| `git clean -fd` | BLOCK |
| `git stash drop` | BLOCK |
| `tee f.py` | BLOCK |
| **`sed -i s/a/b/ f.py`** | **ALLOW** ← 신규 발견 갭 |
| `python -c "import os; os.remove(...)"` | ALLOW (선언된 비목표 — 임의코드) |

Cantus의 "6개월 뒤 사고는 rm이 아니라 git clean일 것"이라는 경고는 이 제품에는
**이미 대비돼 있다**(v2.4 R2-02 forced checkout, PR A tee). 다만 **`sed -i`는 진짜 갭**이다 —
리터럴 경로를 제자리 수정하는 정적 단일 명령이라 C1과 동일 계열이다. 코퍼스에 추가할 것.

## F5. 수리 전 기준선 (RED)

⚠️ 코퍼스 v1은 **파서 탐지 여부**로 재서 오탐을 잘못 집계했다(우상 Codex 적발 — F1과 모순).
v2에서 전부 `evaluate_r2_destructive_gate()` **게이트 종단 판정**으로 재작성했다.

`tmp/hardreview-fix/acceptance_corpus.py` (v2) 실행 결과: **미탐 23건 / 오탐 0건**
(MUST_BLOCK 33건 중 23건 실패 · MUST_ALLOW 32건 전건 통과)

미탐 내역: C1 계열 17건(unlink·shred×2·truncate·Clear-Content×2·dd·cp /dev/null·mv→/dev/null·
.NET Delete·WriteAllText·sed -i×2·mv 소스×2·cp 목적지×2) / H2 계열 3건(iex×3) /
H1 계열 3건(파이프→셸)

## F6. H1과 H2는 마찰 위험이 다르다 — 분리 판단 필요

agy가 지적한 `fnm env | iex`·`starship init powershell | iex`·`oh-my-posh init pwsh | iex`는
PowerShell 환경 초기화의 표준 관용구다(현재 ALLOW). 이건 **H1(파이프 수신)** 문제지
**H2(bare iex + 리터럴 문자열)** 문제가 아니다.

- **H2** `iex 'Remove-Item f'` — 인자가 리터럴이라 read-only 증명 가능성이 있고 위험이 명백. 마찰 낮음
- **H1** `<무언가> | iex`, `<무언가> | bash` — 페이로드가 런타임 산출이라 증명 불가 → 전부 차단됨.
  `fnm env | iex` 같은 정당 용법이 실재하므로 **마찰 위험 높음**

같은 수리로 묶지 말고 강도를 달리할 것.

## 종합 판단 (좌상)

**Cantus의 옵션 B(라우팅 재정의) 채택.** F1이 agy의 반대 근거를 무너뜨렸고,
F2가 agy의 대안(2차 방어선 위임)을 무너뜨렸다. 단 Cantus의 B도 파서에서 stat을 하지 않는
형태여야 하며, `cp /dev/null`·`mv → /dev/null`은 **문자열만으로 판정 가능**하므로
파일시스템 접근 없이 처리된다(agy의 TOCTOU 우려가 닿지 않는 영역).

- `>` 리다이렉션: F3에 따라 현상 유지 + Known Limitation 유지
- `sed -i`: F4 신규 갭, C1과 함께 수리
- H1: F6에 따라 신중히 — 정당 용법 실측 후 결정

## F7. v2.7.0 CHANGELOG의 "19종 차단" 주장은 래핑된 경우에만 참이다

`CHANGELOG.md:68-74`가 v2.7.0에서 차단됐다고 나열한 명령들의 bare 대 래핑 판정:

| 명령 | bare | `powershell -Command` 래핑 |
|------|------|---------------------------|
| `[IO.File]::WriteAllText('f','')` | **ALLOW** | BLOCK |
| `Invoke-Expression 'rm f'` | **ALLOW** | BLOCK |
| `Add-Content f x` | **ALLOW** | BLOCK |
| `Clear-Content f` | **ALLOW** | BLOCK |
| `Copy-Item a f` | **ALLOW** | BLOCK |
| `Move-Item a f` | **ALLOW** | BLOCK |
| `New-Item -Force f` | **ALLOW** | BLOCK |
| `Out-File f` | BLOCK | BLOCK |

8종 중 **7종이 래핑된 경우에만 차단**된다. 원문은 "all passed through `powershell -Command`
before this change"로 범위를 밝히긴 했으나, 이어지는 "all 19 measured cases are blocked now" ·
"30 blocked, 0 false negatives"가 일반적 안전 주장으로 읽힌다. 실제로는 셸 자체가 PowerShell이라
**최상위로 호출되는 가장 흔한 형태**가 커버되지 않았다.

→ CHANGELOG에 범위를 명시하고, 이번 수리가 bare 형태를 덮는다는 것을 기록할 것.

## F8. P4 검증 — agy가 지적한 마찰은 우리가 만든 것이 아니다

agy P4가 "iex와 파이프 수신 셸을 무조건 차단해 마찰이 발생한다"고 지적했다.
수리 전 커밋(`cb90ecb`) 워크트리를 만들어 동일 명령을 대조 측정한 결과:

| 명령 | 수리 전 | 수리 후 | 판정 |
|------|---------|---------|------|
| `powershell -Command "Get-Content README.md"` | BLOCK | BLOCK | **기존 마찰**(v2.7.0 W3 산물) |
| `powershell -Command "Get-ChildItem"` | BLOCK | BLOCK | **기존 마찰** |
| `iex "Get-Content README.md"` | ALLOW | **ALLOW** | 회귀 없음 — proven-read-only 경로 정상 작동 |
| `echo hello \| bash` | ALLOW | BLOCK | **의도된 신규 차단**(H1 수리, 설계 확정분) |
| `git log \| head -5` | ALLOW | ALLOW | 회귀 없음 |

- agy 주장 중 "`iex "Get-Content README.md"`가 차단된다"는 **오측**이다. 실제로는 ALLOW다
  (codex가 보고한 "리터럴 proven-read-only payload는 ALLOW" 가 맞다).
- `Invoke-Expression Get-Process`는 BLOCK이나 인용 없는 bare 인자라 리터럴 payload로
  증명할 수 없는 경우다. 설계상 fail-closed가 맞다.
- 파이프 수신 셸 차단은 **설계에서 명시적으로 채택한 것**이고 `curl … | bash`와 정적으로
  구분 불가하다는 근거가 스펙에 기록돼 있다.

**신규 백로그 항목**: `powershell -Command "Get-ChildItem"`·`"Get-Content f"` 같은
명백한 조회 명령이 래퍼를 통과하면 차단된다(수리 전부터). v2.7.0의 proven-read-only
화이트리스트가 PowerShell 조회 cmdlet을 충분히 덮지 못하는 것으로 보인다. 이번 범위는 아니나
실사용 마찰이므로 기록한다.

## F9. 성능 회귀 없음 (agy 실측)

agy가 `evaluate_r2_destructive_gate`를 1,000회 연속 호출해 계측: **1회당 평균 22.2 µs**.
v2.7.1이 달성한 훅 p50 300~370 ms 대비 새 탐지기의 오버헤드는 마이크로초 단위라
훅 지연 회귀가 없다. (v2.7.0~2.7.1 트랙이 훅 지연을 17배 개선한 직후라 이 확인이 필요했다.)
