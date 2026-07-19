# Yeongjin 서버 pytest 2건 환경 의존 실패 조사

## 본문 — 쉬운 결론

두 실패는 서로 다른 결함이 아니었습니다. 첫 번째 adapter 테스트가 실패했고, 두 번째 probe 테스트는 내부에서 그 adapter 테스트 묶음을 다시 실행하기 때문에 함께 실패했습니다.

첫 번째 실패의 원인은 이 서버의 Windows 경로 길이 제한입니다. 테스트가 만든 임시 폴더 이름에 세션 식별용 긴 해시와 원자적 저장용 임시 파일 이름이 차례로 붙으면서 전체 경로가 262자가 됐습니다. 이 서버는 긴 경로 기능이 꺼져 있어 260자부터 파일 교체가 실패했습니다. 그 예외는 훅의 안전한 fail-open 메시지로 바뀌었고, 테스트는 정상 범위 경고 객체가 올 것이라고 가정해 키를 찾다가 실패했습니다.

즉 `uv run`, cp949, 이 clone의 감독 상태, Python 3.13은 근본 원인이 아닙니다. `uv`를 거치지 않은 독립 venv, UTF-8 강제, 상태가 전혀 없는 clean archive, Python 3.12에서도 긴 임시 경로이면 똑같이 실패했습니다. 반대로 같은 코드·같은 Python에서 임시 루트만 짧게 바꾸면 adapter 테스트와 전체 deterministic probe가 모두 통과했습니다.

오케스트레이터 머신에서 통과하는 이유는 그 머신의 임시 경로가 더 짧거나 Windows 긴 경로 정책이 켜져 있기 때문일 가능성이 높습니다. 오케스트레이터 환경 영수증을 이번 서버에서 직접 읽지는 못했으므로 둘 중 어느 쪽인지는 추론으로만 남깁니다.

프로젝트 코드는 수정하지 않았습니다. 이 서버에서 즉시 쓸 수 있는 우회는 pytest의 base temp를 짧은 사용자 Temp 경로로 지정하는 것입니다. 영구 수정은 원자적 저장 계층이 Windows 장경로를 안전하게 처리하도록 하거나, session/plugin-data 파일명 길이를 줄이되 기존 원자성·충돌 방지 계약을 유지하는 방향이 적절합니다.

## 관찰된 영향

- 정상 기대: PostToolUseFailure가 범위 이탈 경고와 원래 event name을 담은 `hookSpecificOutput`을 반환한다 (`adapters/claude_code/post_tool_use.py:159-183`, `tests/test_adapters.py:414-440`).
- 실제 실패: 범위 경고의 “한 번만 표시” 상태 저장 중 임시 파일이 사라진 것으로 보고돼 `systemMessage=[smtw] health: fail-open: [Errno 2] ...tmp`가 반환됐고, 테스트가 `hookSpecificOutput` key를 읽다가 실패했다 (`adapters/claude_code/post_tool_use.py:186-187`, `adapters/claude_code/bootstrap.py:233-248`).
- probe 실패는 독립적인 비결정성이 아니다. PRB-12가 `tests/test_adapters.py`를 포함한 기존 훅 테스트를 subprocess로 실행하고 return code를 그대로 판정한다 (`eval/run_probes.py:126-140`, `eval/run_probes.py:271-273`). 외부 테스트는 report result가 PASS일 것을 요구하므로 PRB-12 하나의 실패가 두 번째 pytest failure가 된다 (`tests/test_eval_runner.py:81-91`).

## 재현 및 전후 검증

### Before — 원래 환경

```text
RUN: uv run --no-sync --with pytest python -m pytest -q \
     tests/test_adapters.py::test_failure_hook_scope_context_uses_matching_event_name \
     tests/test_eval_runner.py::test_probe_runner_is_green_without_external_activation_environment
OBSERVE: 2 failed in 32.41s
```

독립 Python 3.13 venv에서 `uv run` 없이 직접 실행해도 `2 failed in 31.44s`였다. 따라서 uv 실행 래퍼는 필요조건이 아니다. 테스트 helper가 실제 hook subprocess에 현재 `sys.executable`을 전달하는 구조는 `tests/test_adapters.py:26-36`, probe runner가 같은 interpreter로 pytest를 재호출하는 구조는 `eval/run_probes.py:126-135`에 있다.

### 원인 직접 프로브

`atomic_file.secrets.token_hex`만 고정한 임시 프로브로 최종 temporary path 길이를 256~265자로 변화시켰다. Python 3.13.14와 3.12.13 모두 같은 결과였다.

```text
temporary path 256,257,258,259: PASS
temporary path 260,261,262,263,264,265: FileNotFoundError errno=2
Windows LongPathsEnabled: 0
실패 테스트에서 관측한 temporary path: 262자
```

원자 저장은 destination 옆에 `.<filename>.<16 hex>.tmp`를 만들고 flush/fsync 후 `os.replace`한다 (`adapters/claude_code/atomic_file.py:13-23`). warning destination 자체도 64자리 session digest와 16자리 code digest를 포함한다 (`adapters/claude_code/session_registry.py:192-200`).

### After — 짧은 base temp

```text
RUN: python -m pytest -q \
     --basetemp=C:\Users\gustj\AppData\Local\Temp\smtw-bt-short \
     tests/test_adapters.py::test_failure_hook_scope_context_uses_matching_event_name
OBSERVE: 1 passed in 0.86s

RUN: PYTEST_ADDOPTS=--basetemp=C:/Users/gustj/AppData/Local/Temp/smtw-probe-short \
     python eval/run_probes.py --output <tmp-output>
OBSERVE: probes pass=17 fail=0 manual=3 total=20 result=PASS
```

pytest 8.4.2에서도 긴 기본 temp는 `1 failed in 0.94s`, 짧은 temp는 `1 passed in 0.85s`였다. 즉 pytest 9.1.1 고유 회귀도 아니다.

## 인과사슬

1. pytest의 session temp 아래에 `claude-plugin-data`가 생기고, autouse fixture가 test node id의 SHA-256 전체 64자를 하위 디렉터리로 사용한다 (`tests/conftest.py:10-26`).
2. 범위 이탈이 발견되면 PostToolUse가 `show_scope_once()`를 호출한다 (`adapters/claude_code/post_tool_use.py:151-183`).
3. `show_scope_once()`는 session id와 `scope:<agent>:<turn>` code를 `warn_once()`에 넘긴다 (`adapters/claude_code/bootstrap.py:211-217`).
4. `warn_once()`는 다시 64자리 session digest와 16자리 code digest를 붙인 warning 파일을 만든다 (`adapters/claude_code/session_registry.py:192-200`).
5. atomic writer가 leading dot, 16자리 random token, `.tmp`를 더해 이번 실행에서 262자 경로를 만든다 (`adapters/claude_code/atomic_file.py:13-21`).
6. `LongPathsEnabled=0`인 이 서버에서는 260자부터 `os.replace`가 `FileNotFoundError(2)`를 낸다. 259/260 경계 프로브가 이를 직접 재현했다.
7. PostToolUse top-level exception handler가 정상 범위 경고 대신 health fail-open을 반환한다 (`adapters/claude_code/post_tool_use.py:186-187`, `adapters/claude_code/bootstrap.py:233-248`).
8. adapter 테스트가 `hookSpecificOutput`을 직접 인덱싱해 첫 실패가 된다 (`tests/test_adapters.py:439-440`).
9. PRB-12가 adapter test suite를 재실행해 FAIL이 되고, eval-runner 계약 테스트가 report result FAIL을 받아 두 번째 실패가 된다 (`eval/run_probes.py:271-273`, `tests/test_eval_runner.py:81-91`).

## 경쟁 가설 기록

가설 1: `uv run --with pytest`의 ephemeral environment가 hook subprocess 동작을 바꾼다. 신뢰도 초기 중간 → 최종 낮음.

증거: `uv run` 기준선은 2 FAIL이었지만, `tmp`의 독립 Python 3.13 venv interpreter를 직접 호출해도 같은 두 테스트가 2 FAIL이었다; 두 경로 모두 테스트 helper가 자신의 `sys.executable`로 hook/pytest subprocess를 실행한다 (`tests/test_adapters.py:26-36`, `eval/run_probes.py:126-135`).

기각: 가설 1 — plain venv direct run에서도 동일해 uv wrapper는 원인이 아니다. pytest 8.4.2/9.1.1 양쪽의 긴/짧은 temp 결과도 동일했다.

가설 2: cp949 기본 console encoding이 한국어 prompt 또는 hook JSON을 손상시켜 범위 판정을 놓친다. 신뢰도 초기 중간 → 최종 낮음.

증거: 서버 baseline은 preferred/stdin/stdout cp949·UTF-8 mode off였다. 그러나 `PYTHONUTF8=1`과 `PYTHONIOENCODING=utf-8`을 강제해도 긴 temp에서는 FAIL했고, 같은 UTF-8 환경에서 짧은 temp를 쓰면 PASS했다. hook helper 자체도 stdin/stdout을 UTF-8로 명시한다 (`tests/test_adapters.py:26-36`).

기각: 가설 2 — encoding을 고정한 채 temp 길이만 바꾸면 FAIL→PASS가 뒤집혔다. 다만 probe report의 pytest tail은 child cp949 bytes를 UTF-8 `errors=replace`로 읽어 한글이 깨지는 별도 관측성 문제는 있다 (`eval/run_probes.py:126-135`).

가설 3: 이 clone의 `.fable-lite` ledger/goals/scorecard가 테스트 session identity를 오염시킨다. 신뢰도 초기 중간 → 최종 낮음.

증거: tracked HEAD만 archive한 clean copy에는 `.fable-lite`가 없음을 확인했지만, 그 copy에서 기본 긴 system temp로 실행한 동일 테스트도 FAIL했다. 원 테스트의 autouse fixture는 test별 SHA-256 plugin-data 디렉터리와 강제 활성화를 별도로 설정한다 (`tests/conftest.py:10-26`).

기각: 가설 3 — clean copy에서도 동일했고, 테스트 project root와 plugin data가 clone state와 분리돼 있다.

가설 4: Python 3.13의 filesystem/subprocess 동작 변화다. 신뢰도 초기 중간 → 최종 낮음.

증거: Python 3.13.14와 별도 설치한 Python 3.12.13 모두 기본 긴 temp에서 adapter 테스트가 FAIL했고, atomic path 프로브도 양쪽 모두 259 PASS/260 FAIL이었다.

기각: 가설 4 — 두 Python minor version의 실패 경계와 증상이 일치했다. 서버 OS long-path 정책이 공통 원인이다.

가설 5: pytest temp + 64자리 plugin-data digest + warning filename + atomic suffix가 Windows 260자 경계를 넘는다. 신뢰도 초기 중간 → 최종 높음(채택).

증거: 실제 fail-open에 표시된 temporary path는 262자였고, 독립 atomic writer 프로브는 259자까지 PASS, 260자부터 같은 `FileNotFoundError(2)`를 냈다. 레지스트리 `LongPathsEnabled` 값은 0이었다. 짧은 external base temp만 적용하면 adapter 1 PASS와 probes 17 PASS/0 FAIL로 회복했다 (`adapters/claude_code/atomic_file.py:13-23`, `adapters/claude_code/session_registry.py:192-200`).

가설 6: probe runner 자체에 별도의 비결정성/encoding 실패가 있다. 신뢰도 초기 중간 → 최종 낮음.

증거: 실패 report에서 유일한 deterministic failure는 PRB-12였고, PRB-12는 adapter/core/goals pytest return code만 검사한다 (`eval/run_probes.py:271-273`). 짧은 nested pytest base temp에서는 동일 runner가 `pass=17 fail=0 manual=3 result=PASS`였다.

기각: 가설 6 — 두 번째 pytest failure는 첫 번째 adapter failure의 파생 결과다. 한글 tail mojibake는 진단 품질 문제지만 PASS/FAIL 계산에는 관여하지 않는다 (`eval/run_probes.py:126-140`).

## 권고

1. **즉시 서버 우회(S/낮은 리스크):** CI/수동 pytest에 repo 밖의 짧은 `--basetemp`를 지정한다. probe runner의 nested pytest에도 같은 short temp를 전달해야 한다 (`eval/run_probes.py:126-135`).
2. **영구 제품 수정(M/중간 리스크):** `atomic_write()`가 Windows 장경로에서도 open→fsync→replace 원자성을 보존하도록 path normalization/장경로 API를 검토한다. 단순히 예외를 삼키면 scope warning과 health state가 사라지므로 부적절하다 (`adapters/claude_code/atomic_file.py:13-23`).
3. **길이 예산 축소 대안(S~M/중간 리스크):** test fixture의 전체 SHA-256 directory 또는 warning filename digest 길이를 충돌 분석 후 줄인다. fixture만 줄이면 production의 깊은 plugin-data 경로 결함은 남으므로 근본 수정은 아니다 (`tests/conftest.py:21-25`, `adapters/claude_code/session_registry.py:192-200`).
4. **회귀 테스트(S/낮은 리스크):** Windows에서 atomic temp 259자 성공/260자 처리 계약과 실제 `warn_once` deep-path 시나리오를 추가한다. 정책상 장경로 미지원 환경을 지원하지 않을 결정이라면 명시적·actionable error로 고정한다.
5. **관측성 분리(S/낮은 리스크):** probe runner가 child console encoding을 실제 값에 맞춰 decode하거나 child에 UTF-8을 강제해 failure tail의 한글 손상을 막는다 (`eval/run_probes.py:126-135`). 이는 테스트 실패 원인 수정과 별도다.

## 정리 상태

- 임시로 설치한 uv-managed Python 3.12.13은 `uv python uninstall 3.12`로 제거했고, 작성한 probe `.py` 파일과 JSON/tar 결과 파일은 삭제했다.
- tracked worktree는 `main...origin/main` clean이다.
- 최종 정리 검증에서 `C:\Users\gustj\fable-lite-dev\tmp\testfail-env` 및 네 개의 `smtw-*` 시스템 임시 디렉터리는 모두 존재하지 않았다. 프로젝트 코드는 수정하지 않았다.
