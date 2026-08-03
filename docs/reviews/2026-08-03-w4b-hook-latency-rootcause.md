# W4b 근본원인 확정 — 훅 지연은 임포트가 아니라 ledger 재로드 + backfill deepcopy (좌상 실측, 2026-08-03)

## 경로 정정 (중요)

`.codex/hooks.json` 실측 결과 codex 훅은 **`python adapters/codex_cli/pre_tool_use.py`를 직접 실행**한다. `python -m smtw.cli hook ...`이 아니다(agy가 앞서 보고한 hooks.json 내용은 오독이었고, codex 인수인계 §진행중이던 것의 지적이 맞다).

| 경로 | 실행시간 |
|---|---|
| `python -m smtw.cli hook pre-tool-use` (W4b lazy import 후) | 99~146 ms |
| **`python adapters/codex_cli/pre_tool_use.py` (실제 훅)** | **1591~2115 ms** |
| core 3모듈 임포트만 | 182~212 ms |

→ **W4b의 CLI lazy import는 실제 codex 훅 지연과 무관**하다(그 경로를 안 탄다). 임포트는 전체의 ~12%.

## cProfile — 나머지 1500ms의 정체

`python -m cProfile -s cumulative adapters/codex_cli/pre_tool_use.py` (총 4.077s 실행 기준):

```
   11    3.614s  ledger.py:73(load_ledger)             ← 훅 1회에 11번 호출
   11    3.007s  ledger_migration.py:94(backfill_invocation_statuses)
2588168  2.999s  copy.py:118(deepcopy)                 ← 260만 회
    1    2.419s  adapter_observation.py:149(begin_invocation)
    1    1.049s  contract.py:740(evaluate_pretool_contract)
```

**기전**: `load_ledger()`가 읽기 시점 동적 마이그레이션으로 `backfill_invocation_statuses`를 호출 → 8MB 구조를 통째로 deepcopy. 이게 **훅 1회에 11번** 반복된다.

## 대조 실험 (격리 프로젝트, 각 3회)

| 조건 | 실측 |
|---|---|
| A. 8MB + status-less 209건 (운영 실물 형태) | 4340 / 2431 / 1861 ms |
| B. 8MB + status 전부 채움 (209건 closed로 채운 사본) | 1814 / 856 / 865 ms |
| C. ledger 없음 | 469 / 269 / 301 ms |

→ 두 요인이 **곱해진다**: ①status-less가 매 로드 backfill+deepcopy 유발(A vs B ≈ 2배) ②8MB 자체를 11번 로드(B vs C ≈ 3배). 무부하에서 A가 이미 ~2초이니, 머신 포화 시 3~5배면 **5~10초 타임아웃에 정확히 도달**한다.

## W4b 범위 재교정 — 우선순위

1. **[최우선] 프로세스 내 ledger 로드 캐시** — 훅 1회 11회 로드를 1회로. 같은 프로세스·같은 파일 mtime/size 기준 캐시. 쓰기 경로의 재로드 필요성은 유지(락 안에서의 재확인은 캐시 무효화).
2. **[최우선] backfill deepcopy 제거/단축** — status-less가 0건이면 즉시 반환(스캔만), 있어도 전체 deepcopy 대신 변경분만 복사. 현재는 바꿀 게 없어도 매번 8MB를 복제한다.
3. [보조] CLI lazy import — 이미 WIP 커밋에 있고 그 자체로 유효(smtw.cli 경로 477→100ms). **단 실제 codex 훅 개선 근거로 쓰지 말 것.**
4. [데이터] W1 `migrate --invocation-statuses`는 209건이 unsafe라 자동 해소 불가 → W4a `maintenance`로 별도 승인 정리해야 A→B 효과를 얻는다.

## 합격 기준 (교정)

- **실제 어댑터 경로**(`adapters/codex_cli/pre_tool_use.py`)로 before/after 측정. `smtw.cli` 경로 수치는 보조 지표.
- 조건 A(운영 실물 형태 = status-less 포함 8MB)에서 측정할 것. 깨끗한 픽스처만 쓰면 개선폭이 과대평가된다.
- 캐시 도입 시 **정합성 계약 필수**: 락 안 재확인, 쓰기 후 무효화, 다중 프로세스 간 stale read 금지.
