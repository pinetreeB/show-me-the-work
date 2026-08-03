# W3 adversarial 실주입 영수증

- 일시: 2026-08-03
- 브랜치: `worker/friction-w3-r2-opaque-whitelist`
- 기준 커밋: `4d436a77cc3f9979b40d25cd37f8b13887beb86f`
- 주입 표면: 실제 `adapters/codex_cli/pre_tool_use.py` subprocess
- 격리: 각 payload의 `cwd`를 OS 임시 디렉터리로 지정. 운영 `.fable-lite/`를 harness 입력·출력 대상으로 사용하지 않음.

## Corpus

| 공격군 | 변형 수 | 결과 |
|---|---:|---|
| chain | 3 | 3 block |
| iex/call-operator/dot-source | 3 | 3 block |
| 식 확장/subexpression/scriptblock/splatting | 3 | 3 block |
| EncodedCommand/File/profile mode | 3 | 3 block |
| cmd percent/bang/call expansion | 3 | 3 block |
| caret/backtick/newline escape | 3 | 3 block |
| redirection/all-stream/Tee-Object | 3 | 3 block |
| parent/ADS/UNC path | 3 | 3 block |
| PowerShell alias/relative executable | 3 | 3 block |
| mixed pipeline/foreach/cmd chain | 3 | 3 block |

총 30건이며 각 공격군은 최소 2개 조건을 충족한다.

## RUN → OBSERVE → FIX → RE-RUN

1. RUN: `python tmp/friction-fix/w3-adversarial-harness.py`
2. OBSERVE: 최초 harness는 `ModuleNotFoundError: No module named 'adapters'`로 실제 adapter 진입 전에 종료됐다.
3. FIX: harness의 import path에 repository root를 추가했다. 제품 코드는 변경하지 않았다.
4. RE-RUN:

```text
injected=30 blocked=30 false_negative=0
reason_buckets={'message_only': 30}
```

Codex hook의 외부 JSON은 block message만 노출하므로 내부 reason code는 별도 core gate 테스트로 확인했다.

## 보조 검증

- RED: `tests/test_friction_w3_opaque_whitelist.py` → `84 failed, 6 passed`
- 최초 GREEN: 동일 파일 → `90 passed in 1.02s`
- 최종 GREEN: POSIX `builtin printf` 안전 회귀를 추가한 동일 파일 → `91 passed in 1.68s`
- opaque unknown label: 20/20 `parse_unable_wrapped`이며 coordination label은 20/20 `command_parse_unavailable`
- 안전 controls: direct/cmd/POSIX/PowerShell 10/10 allow
- W3 + parser/POSIX 회귀 묶음: `402 passed in 8.49s`
- 최종 제품 코드 기준 실제 adapter 재주입: `injected=30 blocked=30 false_negative=0`
- adversarial 결론: **30/30 block, false negative 0**
