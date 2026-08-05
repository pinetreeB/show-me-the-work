# 설계 결정 — R2 파괴 명령 탐지 어휘 확장 (C1/H1/H2 + sed -i)

결정자: 좌상(오케스트레이터). 근거: 3자 토론(agy·Cantus·Codex) + 좌상 실측.
기준 main `cb90ecb` (v2.7.1). 실측 원본 = `measured-facts.md`, 코퍼스 = `acceptance_corpus.py`,
양방향 증명 = `bidirectional_proof.py`.

## 채택 — Cantus의 옵션 B, "조건부 판정"이 아니라 "조건부 라우팅"

파서의 역할을 **"파괴적이다/아니다"의 최종 판정**에서 **"귀속 검사로 보낼지의 라우팅"**으로 낮춘다.
파서는 파일시스템을 만지지 않는다(정적 렉서 지위 유지). 존재·소유 판정은 이미 원장·fs를 조회하는
귀속 계층이 한다.

### 왜 이 결정인가 — 토론이 갈렸고 실측이 판정했다

| 주장 | 판정 |
|------|------|
| agy: 어휘 확장 = 마찰 폭증(옵션 B 30~50회, C 100회+) | **기각.** "탐지=차단"이라는 틀린 전제. 실측상 탐지는 라우팅일 뿐 |
| agy: 놓쳐도 quarantine 백업이 복구해준다 | **기각.** quarantine은 *차단된 명령 텍스트*만, provenance는 *해시*만 저장. 복구 수단 없음 |
| agy: B는 stat 도입 → TOCTOU 자초 | **부분 인용.** 파서 내 stat은 하지 않는 형태로 채택해 이 반론을 회피 |
| Cantus: 탐지는 라우팅, 정상 작업은 무음 통과 | **확인.** 경쟁 있는 상태에서도 남의 파일 아니면 전건 ALLOW |
| Cantus: 진짜 위험은 git clean 계열 | **대부분 기각.** git checkout/reset/clean/stash drop·tee 이미 BLOCK. 단 `sed -i`는 진짜 갭 |

**결정적 근거(bidirectional_proof.py)** — beta가 `peer.py`를 점유한 상태에서 alpha:
- `rm peer.py` → BLOCK (게이트 작동)
- `rm my_own_file.py` → **ALLOW** (탐지되지만 남의 것이 아니라 통과) ← 어휘 확장이 저렴한 이유
- `cp .env.example .env`, `mv old.py new.py` → ALLOW

## 확장 어휘

### REMOVE 계열 추가 (리터럴 삭제)
`unlink`, `shred`

### TRUNCATE 계열 추가 (리터럴 절단·비우기)
`truncate`, `clear-content`

### 정적 판정 가능한 특수 대상 (파일시스템 접근 불필요)
`/dev/null`·`NUL`이 **명령 문자열 안에 리터럴로 존재**하는 경우만 판정한다:
- `cp /dev/null <target>` → target이 대상
- `mv <target> /dev/null` → target이 대상
- `dd if=/dev/zero of=<target>` / `dd of=<target>` → target이 대상

> agy의 TOCTOU 우려가 닿지 않는 영역이다. `/dev/null`은 문자열에 있고 stat이 필요 없다.

### 제자리 수정 (신규 발견 갭, F4)
`sed -i` — 리터럴 경로를 제자리 변경하는 정적 단일 명령. C1과 동일 계열

### .NET 정적 호출
`[System.IO.File]::Delete(...)`, `[IO.File]::WriteAllText(<path>, '')` — 리터럴 인자일 때만

## 🔴 구현 제약 (실측 + Codex 교정)

1. **`cp`/`Copy-Item`은 목적지만** 대상으로 잡는다.
   `cp peer.py backup_of_peer.py`는 남의 파일을 **읽기만** 하므로 파괴가 아니다.
2. **★`mv`/`Move-Item`은 소스와 목적지 둘 다** 잡는다 (Codex 교정 — 좌상 초안이 틀렸다).
   move는 **소스 경로가 사라지므로** 소스도 파괴 대상이다. 목적지만 보면
   `Move-Item peer.py ..\g` 갭이 그대로 남는다. 단 sink(`/dev/null`·`NUL`) 목적지일 땐 소스만.
3. **파서는 파일시스템을 조회하지 않는다.** 존재 여부 판정을 파서에 넣지 말 것(agy의 정당한 경고).
   경로 후보를 넉넉히 반환하고 **존재·소유 판정은 귀속 계층에 맡긴다** — 이게 B-Routing의 요체다.
4. **덮어쓰기 여부를 판정할 필요가 없다.** 목적지를 그냥 귀속 후보로 보내면
   남의 미정산 파일이면 BLOCK, 아니면 ALLOW가 자동으로 나온다. stat 불필요.

## H2 — bare `iex`/`Invoke-Expression`: 채택
**★`WRAPPER_COMMAND_NAMES`와 `OPAQUE_WRAPPER_COMMAND_NAMES` 양쪽에 추가**한다 (Codex 적발한 구현 함정).
OPAQUE 집합만 바꾸면 `_detect_wrapper_indirect()`의 첫 membership 검사(`:526-529`)에서 그대로
빠져나가 **수리가 무효**가 된다.
인자가 리터럴 문자열이라 `is_proven_read_only_command` 증명 경로가 살아 있어 마찰이 낮다.
`CHANGELOG.md:68-74`의 v2.7.0 "Invoke-Expression 차단" 주장이 실제로 참이 된다(현재는 래핑된 경우만 참).

## H1 — 파이프 수신 셸: 축소 채택 + 경계 선언
agy가 실재 관용구를 지적했다(`fnm env | iex`, `starship init powershell | iex`,
`oh-my-posh init pwsh | iex` — 현재 ALLOW). 페이로드가 런타임 산출이라 read-only 증명이 불가능해
전면 차단 시 정당 용법이 막힌다.

- **채택**: 파이프 수신자가 `bash`/`sh`/`zsh` 등 **셸**이면 불투명 실행으로 취급(`cat x.sh | bash`,
  `curl ... | bash`). 이건 `bash < x.sh`가 이미 차단되는 것과의 **비대칭 해소**라 새 정책이 아니다.
- **보류**: `| iex`, `| python`, `| node` 등 인터프리터 수신은 이번 범위 제외.
  `| head`·`| grep`·`| jq` 등 비실행 수신자는 **당연히 영향 없어야 한다**(회귀 테스트로 고정).
- 보류분은 Known Limitation에 명시한다.

## Known Limitation (문서에 정직하게 선언할 것)
1. `> file` 리다이렉션은 새 파일 생성도 파괴로 취급(기존 확정 한계, TOCTOU 방어 불가) — 유지
2. `| iex`, `| python`, `| node` 등 **인터프리터** 파이프 수신 — 정당 관용구(`fnm env | iex`,
   `starship init powershell | iex`)와 정적으로 구분 불가. 미대응 (셸 수신 `| bash`는 이번에 대응)
3. `python -c "os.remove(...)"` 등 임의 코드 실행 — 기존 선언된 비목표, 유지
4. 미정산 원장에 없는 파일(이미 커밋된 남의 작업물 등)의 덮어쓰기 — 제품 범위 자체가
   "미정산 작업물 보호"라 설계상 대상 아님

> 좌상 초안의 KL "cp a b 덮어쓰기 판정 불가"는 **철회**한다. 덮어쓰기를 판정할 필요가 없다 —
> 목적지를 귀속 후보로 보내면 남의 미정산 파일일 때만 자동으로 막힌다(제약 4).

## 합격 기준
`tmp/hardreview-fix/acceptance_corpus.py`(v2, 게이트 종단 기준) **미탐 0 · 오탐 0**.
- 현재 기준선: **미탐 23건 / 오탐 0건** (MUST_BLOCK 33건 중 23건 실패, MUST_ALLOW 32건 전건 통과)
- 수리 후: MUST_BLOCK 33/33 BLOCK, MUST_ALLOW 32/32 ALLOW
- 추가: 전체 pytest 회귀 0
