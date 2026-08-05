# agy(Antigravity) 하드리뷰 — smtw v2.7.1 (cb90ecb)

> 좌상이 터미널에서 회수해 저장(agy는 smtw 게이트상 파일 저장 불가 — [[feedback_agy_file_save_gate_blocked]]).

## 종합판정
- 점수: 72/100
- Critical 3건, High 2건, Medium 2건, Low 2건

## Critical

### [Critical-01] R1/R2 파서 셸 우회: Process Substitution `<(...)` / `>(...)` 세그먼트 미분치
- 위치: `core/destructive_guard.py:1077-1150` (_command_segments)
- 근거: 1110-1135줄이 `$(...)`와 backtick만 스캔, `<(...)`/`>(...)` 스캔 로직 미존재
- 재현: `cat <(rm -rf *)` 실행 시 tokens[0]이 cat으로 인식돼 parse_destructive_commands가 None 반환, 차단 바이패스
- 수리: _command_segments에 process substitution 스캔 추가

### [Critical-02] 예외/타임아웃/손상 시 감독 통째 무력화되는 Fail-Open 갭
- 위치: `adapters/claude_code/pre_tool_use.py:237-243`, `bootstrap.py:290-300`
- 근거: `fail_closed_runtime_env`가 SmtwEnvConflictError 외 모든 예외에 None 반환 → `fail_open()` (exit 0)
- 재현: ledger.json 손상(JSONDecodeError), OSError, TypeError/KeyError 발생 시 게이트 검사 건너뛰고 승인
- 수리: 안전-상태 비보장 예외 시 fail_closed deny 리턴

### [Critical-03] 다중 프로세스 경합 시 락 타임아웃이 게이트 무력화로 연결
- 위치: `core/file_lock.py:31` (_posix_guard), `core/state_layout.py:161` (state_write_scope)
- 근거: `raise TimeoutError(f"timed out waiting for file guard: {path}")`
- 재현: 동시 변형 이벤트 기록 시 락 대기 초과 → Critical-02의 최상위 except가 흡수 → fail_open
- 수리: 락 타임아웃 예외는 fail-open이 아닌 deny 리턴

## High

### [High-01] 어댑터 게이트 로직 편차: codex_cli 훅 3종 ledger_read_cache 누락
- 위치: `adapters/codex_cli/post_tool_use.py`, `user_prompt_submit.py`, `stop.py`
- 근거: codex_cli/pre_tool_use.py:41에만 `with ledger_read_cache():` 존재
- 재현: `grep -rn "ledger_read_cache" adapters/codex_cli/` → pre_tool_use.py에만 존재. v2.7.1 CHANGELOG 서술과 불일치
- 수리: 3개 엔트리포인트에 컨텍스트 매니저 적용

### [High-02] test_w4c_adapter_cache 테스트의 실검증 불능 (소스 텍스트 매칭)
- 위치: `tests/test_w4c_adapter_cache.py:114-122`
- 근거: `source = inspect.getsource(hook_module.main)` + `assert "cache_stack.enter_context(ledger_read_cache())" in source`
- 재현: 런타임 읽기 감소를 측정하지 않고 소스 텍스트 포함 여부만 검사 → codex_cli 누락을 못 잡고 패스
- 수리: `_load_ledger_uncached` 호출 횟수를 실제 훅 런타임 실행으로 검증

## Medium
1. **legacy/native 이중 트리 하드코딩·Ledger Shadowing 위험** — `tests/claude_hook_support.py:68,89`, `tests/test_fable_lite_cli.py:370,405`가 `.fable-lite/ledger.json` 하드코딩. 미이관 툴은 legacy에 기록, 어댑터는 native를 읽는 split-brain 위험
2. **CHANGELOG/README 과장** — `CHANGELOG.md:5-10`(v2.7.1) "어댑터 전반 캐시 적용" 주장 vs codex_cli 3훅 누락 / `:68-75`(v2.7.0) "0 false negatives" 주장 vs 우회 경로

## Low
1. `core/ledger.py:154` (_ledger_fingerprint): 동일 mtime_ns + 동일 st_size면 변경 미감지 가능성
2. `core/destructive_guard.py:571`: `_WRAPPER_OPTIONS_WITH_VALUE`에 powershell/pwsh 옵션 명세 부재 → 예외 메시지 라벨링 불명확

## 커버리지 고백
- 읽음: core/{destructive_guard,shell_command,ledger,file_lock,state_layout}.py, adapters/claude_code 전체, adapters/codex_cli 전체, antigravity/oma_hook.py, qwen_code/qwen_hook.py, tests/test_w4c_adapter_cache.py, CHANGELOG.md, README.md
- 못 봄: docs/reviews 구버전 15종, eval/ 보조 스크립트, packs/ 템플릿

[AGY-REVIEW-DONE]
