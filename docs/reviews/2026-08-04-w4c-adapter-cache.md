# W4c 결과

## 브랜치 / 커밋

- 브랜치: `worker/friction-w4c-adapter-cache`
- 기준: `main@5ac9cd512ea2c0baca277643a5a4960a034945a1`
- 구현 커밋: `dc052f0e2b95e8cebddd21bb92cd2af627598627`
- push: 하지 않음

## 구현

- Claude Code의 ledger-reading hook 4개(`user_prompt_submit`, `pre_tool_use`, `post_tool_use`, `stop`)를 activation 확인 뒤 `ledger_read_cache()` 범위에 넣었다.
- `session_end`는 실제 ledger load가 0회여서 캐시 비대상으로 유지하고 테스트로 고정했다.
- Antigravity `oma_hook.py`와 Qwen Code `qwen_hook.py`는 실제 handler dispatch 전체를 동일 캐시 범위에 넣었다.
- W4b의 lock 내부 fingerprint 재확인과 쓰기 전 캐시 무효화 구현은 변경하지 않았다.
- Claude 비활성 hook의 shared-core 무import 및 quiet 반환 계약을 보존했다.

## RED → GREEN load 횟수

대표 실제 `PreToolUse`에서 `_load_ledger_uncached`를 계측했다.

| 어댑터 | RED | GREEN |
| --- | ---: | ---: |
| Claude Code | 24회 | 1회 |
| Antigravity | 23회 | 1회 |
| Qwen Code | 24회 | 1회 |

추가 계약:

- Claude ledger-reading 4개 공개 진입점 모두 cache context 진입.
- Claude `session_end` uncached load 0회.
- status-less invocation을 포함한 ledger에서도 대표 read-heavy 경로 uncached load 1회.

## 어댑터별 before / after

조건: 실제 어댑터 subprocess, 샘플 5회, 동시성 1, matching active turn, 7,967,923-byte fixture, status-less invocation 209건.

| 어댑터 | before p50 / p95 | after p50 / p95 | p50 감소 |
| --- | ---: | ---: | ---: |
| Claude Code | 674.704 / 759.883 ms | 307.581 / 337.695 ms | 54.4% |
| Antigravity | 730.230 / 793.174 ms | 351.778 / 398.303 ms | 51.8% |
| Qwen Code | 607.226 / 628.112 ms | 370.414 / 445.139 ms | 39.0% |

영수증:

- `docs/reviews/2026-08-04-w4c-adapter-cache-before.json`
- `docs/reviews/2026-08-04-w4c-adapter-cache-after.json`
- fixture SHA-256: `ff79cb625347ae59ab37ff50dec7c1bb4e5672db1020559d4c4068dc10679d0a`

before는 별도 임시 worktree의 정확한 기준 SHA `5ac9cd5`에서 실행했고, after는 구현 트리에서 같은 벤치 조건으로 실행했다.

## 전체 테스트 결과

- W4c + Claude quiet opt-in: `23 passed`
- Claude/Antigravity/Qwen/W4b/W4c 어댑터 회귀: `91 passed`
- 경쟁 session registry 재현성 확인: 단독 1회 + 연속 10회 모두 통과
- 전체 pytest 최종: `1655 passed, 2 skipped in 521.09s`
- 전체 ruff: `All checks passed!`
- 변경 파일 LSP error: 0건
- 운영 `.fable-lite/ledger.json`: 7,967,923 bytes, SHA-256와 mtime 모두 작업 전과 동일

## 미해결

- W4c 범위의 미해결 기능 결함은 없다.
- 프로세스 시작과 OS 파일 캐시 변동 때문에 개별 latency 샘플 편차는 남지만, 세 어댑터의 p50과 p95가 모두 감소했다.
