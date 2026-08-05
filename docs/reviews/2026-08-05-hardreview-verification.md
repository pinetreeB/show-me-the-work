# 좌상(Claude) 독립 검증 — 2026-08-05 하드리뷰 2건 교차 검증

기준: main `cb90ecb` (v2.7.1). 방법: 파서 직접 호출 + 코드 대조 grep. 파괴 명령은 실행하지 않음.
근거 규율: [[feedback_review_vs_measurement]] — 리뷰 지적은 실측 전까지 사실이 아니다.

## 판정 요약

| 리뷰어 | 지적 | 좌상 실측 판정 |
|--------|------|----------------|
| Qoder | C1 R2 삭제 어휘 구멍 | **확인(진짜)** |
| Qoder | H1 파이프-셸 비대칭 | **확인(진짜)** |
| Qoder | H2 bare iex/Invoke-Expression 우회 | **확인(진짜)** |
| Qoder | H3 antigravity 신원 승격 누락 | **확인(진짜)** |
| agy | Critical-01 process substitution 우회 | **반증(거짓)** |
| agy | Critical-02 예외→fail_open 감독 무력화 | **과장** (R2 경로는 fail-closed) |
| agy | Critical-03 락 타임아웃→게이트 무력화 | **과장** (R2 이후 단계) |
| agy | High-01 codex_cli 3훅 캐시 누락 | **확인(진짜)** |
| agy | High-02 캐시 테스트 실검증 불능 | **확인(진짜)** |

## 실측 기록

### Qoder C1 — R2 삭제 어휘 구멍 (확인)
`parse_destructive_commands()` 직접 호출 결과:
- 탐지됨(정상): `rm -rf peer.py`, `Remove-Item peer.py`, `del peer.py`
- **미탐지 통과**: `unlink peer.py`, `Clear-Content peer.py`, `shred -u peer.py`, `truncate -s 0 peer.py`,
  `dd if=/dev/zero of=peer.py`, `cp /dev/null peer.py`, `mv peer.py /dev/null`,
  `Move-Item peer.py ..\gone.py`, `[System.IO.File]::Delete('peer.py')`
- 원인: `core/destructive_guard.py:34` `REMOVE_COMMAND_NAMES = {"rm","remove-item","del","rd","rmdir"}`,
  `:35` `TRUNCATE_COMMAND_NAMES = {"set-content","out-file"}` — 실측으로 집합 내용 확인
- 성격: 인코딩·동적코드 우회가 아닌 **리터럴 정적 단일 명령**이라 README 비목표 조항으로 면책되지 않음

### Qoder H1/H2 — 비대칭 (확인)
- `cat payload.sh | bash` → 통과 / `bash < payload.sh` → 탐지 (같은 외부 스크립트 실행인데 반대 판정)
- `iex 'Remove-Item f'`, `Invoke-Expression 'Remove-Item f'` → 통과 / `powershell -Command 'Remove-Item f'` → 탐지
- `core/destructive_guard.py:76-86` OPAQUE_WRAPPER_COMMAND_NAMES에 `eval`은 있으나 `invoke-expression`/`iex` 부재 확인

### Qoder H3 / agy High-01 — 어댑터 편차 (둘 다 확인)
`grep -rn "legacy_default" adapters/*/*.py`:
- claude_code pre:205 / post:101, codex_cli pre:97 / post:53, qwen_code :248 / :314 → 승격 있음
- **antigravity/oma_hook.py → 없음**

`grep -rn "ledger_read_cache" adapters/`:
- claude_code 4훅 전부 있음, antigravity :285 있음, qwen_code :452 있음
- **codex_cli는 pre_tool_use.py:41에만** — post_tool_use.py / stop.py / user_prompt_submit.py 파일은 존재하나 캐시 없음
- `tests/test_w4c_adapter_cache.py` 테스트 이름 확인: claude(소스텍스트 매칭) / antigravity / qwen 런타임 테스트만 존재,
  **codex 테스트 0건** → 구조적으로 이 누락을 잡을 수 없었음

### agy Critical-01 — process substitution (반증)
```
'cat <(rm -rf peer.py)'  -> targets=('peer.py',)   # 탐지됨
'echo >(rm -rf peer.py)' -> targets=('peer.py',)   # 탐지됨
'rm -rf peer.py'         -> targets=('peer.py',)   # 동일
```
파서가 process substitution 내부 명령을 이미 세그먼트로 분치해 bare `rm`과 동일하게 탐지한다.

### agy Critical-02/03 — fail-open (과장)
- `adapters/claude_code/pre_tool_use.py:160-163` 주석·구조: **R2가 다른 ledger 읽기보다 먼저 실행**
- `core/destructive_guard.py:1491-1498`: R2 내부에서 **모든 예외를 degraded로 흡수**
- `:1718-1720`: `ledger, degraded = _load_ledger_for_r2(root); if degraded: return _block("ledger_degraded")`
  → agy가 지목한 "ledger.json 손상" 시나리오는 코드가 **정반대(fail-CLOSED 차단)**
- `:1726-1727`: health degraded/capacity_exceeded → block
- `pre_tool_use.py:183`: R2 block이면 **즉시 반환** → 이후 broad except(:237)가 뒤집을 수 없음
- 락 대기는 `begin_invocation`(:225)에서 발생 = R2(:163) **이후** → R2 차단 우회 불가
- 잔여 실체: R2 이전의 좁은 창(`canonical_invocation`/`tool_file_paths`/`tool_command`, :151-159)에서 예외 시
  R2 미실행 → fail_open. 이건 **Qoder M3가 정확히 좁혀 지적한 범위**와 일치.

## 결론
- **즉시 수리 대상(실측 확정)**: Qoder C1(최우선) · H2 · H3 · agy High-01/High-02 · Qoder M3
- **기각**: agy Critical-01
- **하향 조정**: agy Critical-02/03 → Qoder M3 범위로 축소해 Medium 취급
- 두 리뷰의 유일한 중복 지적은 "어댑터 간 편차"이나 **서로 다른 어댑터를 잡았다**
  (agy=codex_cli 캐시, Qoder=antigravity 신원 승격). 두 건 다 진짜이므로 독립 2인 리뷰가 실효를 냈다.
