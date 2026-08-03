# W4b 선행 계측 — 훅 지연 원인 실측 (좌상 Claude 직접, 2026-08-03)

> 배경: codex pane에서 PreToolUse/Stop 훅이 `hook timed out after 5s/10s`로 반복 실패. 초안 F8은 "8MB ledger 로드"를 유력 원인으로 봤고, codex 분석은 "load 0.2s라 단일 원인 아님"으로 판정(인과 미확정). 이 문서가 그 인과를 실측으로 좁힌다.
>
> 담당 교체 사유: 좌하 agy에 위임했으나 첫 Grep 후 **결과 출력 없이 중단**(완료 마커 미출력, 재확인 시 AGY-ALIVE 응답 — 긴 과제 조용한 중단 패턴). 좌상이 직접 계측.

## 실측 1 — 운영 저장소(fable-lite) 훅 실행시간

| 경로 | 실행시간 |
|---|---|
| PreToolUse (Bash, git status) | 477~522 ms |
| PreToolUse (Write, 편집 게이트 전체) | 278~292 ms |
| PostToolUse (쓰기 경로) | 288~334 ms |
| Stop (provenance 검증) | 276~295 ms |
| 참고: python 인터프리터 기동만 | 83~92 ms |
| 참고: `import smtw.cli`만 | 376~430 ms |

**⚠️ 이 수치의 한계**: 현재 fable-lite는 ledger가 스키마 무효(status 누락 209건) + 상태 authority 경고 상태라 훅이 **fail-open으로 조기 종료**한다(codex pane 로그: `[smtw] fail-open: state layout has no single authority`, `provenance incomplete; fail-open observation`). 즉 정상 전체 경로가 아닌 **하한선**이다.

## 실측 2 — 격리 프로젝트에서 ledger 크기 대조 (정상 경로)

임시 git 저장소 + `.smtw.toml` 활성화 + payload cwd 지정으로 훅 정상 경로 확보(sanity rc=0 확인).

| 조건 | 5회 실측 |
|---|---|
| ledger 없음 | 282 / 267 / 296 / 324 / 303 ms |
| **8MB ledger**(fixture-ledger-8mb.json) | 297 / 279 / 280 / 349 / 401 ms |

→ **8MB ledger가 추가하는 비용은 대략 30~100ms**. 5~10초와 2 자릿수 배율 차이.

## 실측 3 — 락 경합 가설 (동시 8 프로세스)

PreToolUse 훅 8개 동시 실행:
- 개별: 423 / 482 / 544 / 569 / 581 / 521 / 527 / 458 ms (최대 581ms)
- 총 wall-clock 4002 ms

→ 8-way 동시에서도 개별 훅은 **600ms 미만**. 단순 락 경합만으로 5~10초는 설명 안 됨.

## 결론 — 가설 판정

- **기각: "8MB ledger 로드가 훅 타임아웃의 원인"** — 크기 기여 30~100ms 실측. journal compact(③-5)를 해도 **훅 타임아웃은 해소되지 않는다**.
- **기각: "단순 락 경합"** — 8-way 동시에서 최대 581ms.
- **유력(미확정): 머신 부하 포화** — 타임아웃이 관측된 시점은 codex(ulw 다중 워커) + qwen + agy + Claude가 동시에 무거운 분석/구현을 돌리던 구간. 300~500ms 훅이 CPU/디스크 포화에서 10~20배로 늘어나면 5~10초에 도달. 검증하려면 부하 상태에서의 훅 지연을 재현 계측해야 한다(부하 유발 요인이 4-pane 자체라 재현 조건 통제가 필요).
- **부수 관측**: `import smtw.cli`가 훅 시간의 **약 80%**(376~430ms / 총 ~500ms). 훅 자체 로직보다 모듈 임포트가 지배적 — 지연 개선의 진짜 지렛대는 ledger 축소가 아니라 **임포트 비용 축소**(lazy import)다.

## W4b 범위 교정 제안

1. journal compact는 **디스크·메모리 위생 목적으로만** 정당화(훅 타임아웃 해소 근거로 쓰지 말 것) — 릴리스 노트에도 그렇게 기재.
2. 훅 지연 개선의 1순위는 **`smtw.cli` 임포트 경로 지연 로딩**. 벤치는 import/게이트/IO를 분리 계측해야 한다.
3. 타임아웃 재현은 **부하 조건 포함**이 필수. 무부하 벤치로 "timeout 0"를 달성해도 실사용 타임아웃은 안 사라진다.
4. codex 어댑터 타임아웃 상향은 여전히 금지(원인 수리가 아님) — 단 fail-open이라 안전 저하는 없음을 명시.
