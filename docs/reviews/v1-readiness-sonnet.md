# v1.0.0 릴리스 심사 — 코드 견고성·엣지케이스 관점

**심사자**: Claude Code (Sonnet 5, 우하 pane) — 이 프로젝트를 P5(라이브 설치)·E1/E1b/E1c(A/B 반복 실측, 총 40+ 세션)로 가장 깊이 실측한 담당자.
**범위**: `core/`, `adapters/{claude_code,codex_cli,antigravity}/`, `fable_lite/`.
**방법**: 코드 직독 + 과거 실측 데이터(p5b·e1·e1b·e1c) 교차 대조 + 현재 `pytest`(53 passed, 직접 실행 확인) 대조. 지시받은 4개 관점을 우선 다루고, 리뷰 중 발견한 추가 항목을 덧붙였다.

## 요약

| # | 심각도 | 한 줄 요약 | 위치 |
|---|---|---|---|
| B1 | **Blocker** | antigravity 어댑터의 `core.*` import가 `main()`의 try 블록 **밖**(모듈 최상단)에 있어 fail-open 보장이 깨진다 | `adapters/antigravity/oma_hook.py:11-15` |
| B2 | **Blocker** | `stop_hook_active` 체크가 실제 재검사보다 먼저 실행돼 `MAX_STOP_BLOCKS=2`가 사실상 도달 불가능한 코드다 | `core/verify_state.py:63-64` |
| H1 | High | `codex_cli`의 `TEST_TERMS`가 v5·v6에서 고친 확장판이 아니라 구버전 그대로다 | `adapters/codex_cli/post_tool_use.py:10` |
| H2 | High | `codex_cli`의 `tool_success()`엔 텍스트 폴백 판정이 없다(`claude_code`만 있음) | `adapters/codex_cli/common.py:117-128` |
| H3 | High | 검증 인식 목록에 bash/sh 스크립트·컴파일 언어 테스트러너가 다수 빠져 있다 | `adapters/claude_code/post_tool_use.py:11-19` |
| M1 | Medium | `fable_lite check`가 `git status --porcelain=v1 -uall` 사용(대용량 repo 메모리 이슈 위험 플래그) | `fable_lite/check_support.py:14` |
| N1 | Nice-to-have | `codex_cli`가 `runpy.run_path`로 `common.py`를 동적 로드(비표준 패턴, IDE/타입체크 무력화) | `adapters/codex_cli/{pre_tool_use,post_tool_use,stop,user_prompt_submit}.py` |
| N2 | Nice-to-have | `card.verify`가 빈 문자열일 때 오류 메시지에 빈 백틱이 남음(치명적이진 않음 — 카드 검증에서 어차피 RED) | `fable_lite/card.py:113-115`, `fable_lite/check.py:189-192` |

---

## (1) 검증 인식 — v4·v5·v6에서 드러난 패턴, 아직 안 잡힌 패턴이 더 있다

**연혁 확인**(코드로 직접 확인): `adapters/claude_code/post_tool_use.py`는 이미 두 차례 확장됐다 — `TEST_TERMS`(11-15행)에 `python -c`/`unittest`/`jest`/`vitest`/`deno test`/`rspec`/`phpunit` 등(e1b 이후), `TEST_SCRIPT_RE`(17-19행, "E1c F1에서 관측" 주석 포함)로 `python3?|node|ruby|deno|bun|go run|php` 인터프리터 + 스크립트파일 재실행 패턴까지 커버됐다. `NON_VERIFY_TERMS`(21-24행)로 migrate/build/deploy 오탐도 막아뒀다 — 설계가 정교하다.

**그런데도 남은 구멍**(`TEST_SCRIPT_RE`, 17-19행 정규식을 직접 대조):
- **`bash`/`sh` 자체가 인터프리터 목록에 없다.** `bash test.sh`, `sh run_tests.sh`, `./test.sh`(직접 실행) 전부 미인식. 이 프로젝트의 `SHELL_TOOLS = {"Bash", "PowerShell"}`(8행)이 지켜보는 두 툴 중 하나가 정확히 "Bash"인데, 그 안에서 실행되는 bash 스크립트 검증 자체는 못 잡는 역설적 상황이다.
- **PowerShell 네이티브 테스트(Pester `Invoke-Pester`)** 미인식 — SHELL_TOOLS의 나머지 한 축인데 대응이 없다.
- **컴파일 언어 생태계 전반 누락**: `make test`/`make check`, `ctest`(CMake), `mvn test`(Maven), `gradle test`/`./gradlew test`, `dotnet test`(.NET), `swift test`.
- **기타**: `tox`(Python 멀티환경), `rake test`(Ruby, rspec 외 대안).

**영향**: under-recognition은 안전한 방향으로 실패한다(거짓 "미검증"으로 판정돼 Stop 게이트가 더 엄격해질 뿐, 거짓 "검증됨"으로 새지는 않는다) — 그래서 Blocker는 아니다. 하지만 v1.0.0에 "안정"을 붙인다는 건 Python/JS 생태계 밖(특히 C/C++/Java/.NET 프로젝트, 그리고 bash/PowerShell 스크립트 자체)에서 S4 게이트가 체계적으로 정확도가 떨어진다는 뜻이라 High로 분류한다.
**제안**: `TEST_SCRIPT_RE`의 인터프리터 목록에 `bash|sh|zsh`를 추가하고, `make(?:\s|$)`나 `ctest|mvn|gradle|gradlew|dotnet|tox|rake` 같은 빌드/테스트 러너 키워드를 `TEST_TERMS`에 일괄 추가. 이번에 한 번에 정리해두면 향후 "또 하나 빠졌다" 식 산발적 패치를 막을 수 있다.

## (2) `stop_hook_active` 루프가드 — 검증 우회 구멍이다 (Blocker)

`core/verify_state.py:63-64`:
```python
if payload.get("stop_hook_active") is True:
    return {"decision": "allow", "message": "Stop hook loop guard: allow."}
```
이 체크는 `load_ledger`나 `_has_successful_verification` 같은 **실제 재검사보다 먼저** 실행된다. Claude Code(및 codex_cli/antigravity 호스트)는 훅이 한 번 `block`을 반환하면 그 강제 연속 응답에서의 다음 Stop 시도에 `stop_hook_active=true`를 실어 보낸다 — 그러면 `evaluate_stop`은 ledger 상태를 **아예 보지도 않고** 곧바로 allow를 반환한다.

`MAX_STOP_BLOCKS = 2`(12행)와 `_block_with_stop_counter`의 `stop_blocks >= MAX_STOP_BLOCKS` 체크(52행)는 **이 경로에 도달하기 전에 항상 loop guard에서 먼저 걸린다** — 즉 "최대 2회 차단"(ARCHITECTURE.md 설계 원칙 6)이라는 문서화된 설계와 달리, 실제 동작은 **"최초 1회만 진짜로 검사하고, 그 다음 시도는 무조건 통과"**다.

**실측 근거**: p5b(3회)·e1(5픽스처)·e1b(18세션)·e1c(10세션) 전체에서 차단이 발생한 모든 케이스의 `stop_blocks`가 **예외 없이 정확히 1**이었다 — 2를 관측한 적이 한 번도 없다. 이 가설을 코드로 확인하니 정확히 일치한다: 구조적으로 2에 도달할 수 없는 코드였다.

**왜 Blocker인가**: fable-lite 전체 설계 철학이 "하드 게이트 > 소프트 지시"(스펙 §1)인데, 이 하드 게이트는 사실 "1회 지적 후 사실상 자동 통과"로 동작한다. 모델이 첫 차단에 마커/검증을 채우지 않고 그냥 아무 텐트나 하고 다시 종료를 시도해도 통과된다 — 의도된 게 아니라 구현 실수로 보인다(loop guard는 원래 "무한 트랩 금지"용 안전장치인데, 이 게이트 자체의 재검사 로직보다 먼저 걸려버려서 재검사를 무력화한다).
**제안**: `stop_hook_active` 체크를 `stop_blocks >= MAX_STOP_BLOCKS`보다 **뒤에** 두거나, "이 턴에서 이미 1회 검사했다"와 "harness 자체의 무한루프 안전장치"를 별도 신호로 구분해야 한다.

## (3) 3개 어댑터 페이로드 파싱 일관성 — codex_cli가 뒤처져 있다 (High)

`claude_code`·`codex_cli`·`antigravity` 세 어댑터를 나란히 놓고 대조한 결과:

- **`codex_cli/post_tool_use.py:10`**: `TEST_TERMS = ("pytest", "python -m pytest", "npm test", "go test", "cargo test", "node --test")` — **v5(e1b) 이전의 원본 그대로다.** `python -c`, `unittest`, `TEST_SCRIPT_RE` 전부 없다. `claude_code`에서 고친 두 가지 검증인식 버그(python -c 한 줄 검증 미인식, 스크립트 재실행 미인식)가 **codex_cli 사용자에게는 여전히 살아있다.**
- **`codex_cli/common.py`의 `tool_success()`(117-128행)**: Codex CLI 고유의 "Exit code: 0" 텍스트 패턴 처리는 있지만, `claude_code/common.py`가 E1c 이후 추가한 **stdout 텍스트 기반 성공/실패 폴백(fail_signals/ok_signals 스캔, `common.py:115-125`)이 없다.** exit_code도 텍스트 패턴도 없으면 무조건 `payload.get("exit_code") == 0` → `None == 0` → `False`로 떨어진다 — E1b에서 발견한 "pytest가 실제로 통과했는데 success:false로 기록"되는 증상이 codex_cli에서 재발할 수 있는 구조다.
- 두 항목 다 "한쪽 어댑터에서 고친 버그가 다른 쪽엔 이식 안 됨" 패턴이다 — 세 어댑터가 각자 독립 파일로 유지되는 한 반복될 위험(아래 N1과 연결).

**제안**: `TEST_TERMS`/`TEST_SCRIPT_RE`/`NON_VERIFY_TERMS`와 `tool_success()`의 텍스트 폴백 로직을 `core/`(플랫폼 중립 영역)로 옮기고 세 어댑터가 공유하게 하면, 이번처럼 한쪽만 고쳐지는 회귀를 구조적으로 막을 수 있다.

## (4) fail-open 보장 — antigravity에서 깨진다 (Blocker)

`adapters/antigravity/oma_hook.py:1-15`:
```python
import sys
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.classify import classify_prompt
from core.ledger import classify_change_kind, load_ledger, record_event
from core.scope_guard import evaluate_scope
from core.contract import evaluate_pretool_contract
from core.verify_state import evaluate_stop
```
이 `from core.* import` 5줄은 **모듈이 로드되는 즉시(= `main()`이 호출되기 한참 전에) 실행된다.** `main()`(230행)의 `try`/`except Exception`은 저 아래 있어서, **이 import 구문들은 그 try 블록 안에 없다.**

대조: `claude_code/post_tool_use.py`·`codex_cli/post_tool_use.py`를 포함한 나머지 8개 훅 파일 전부 `from core.* import`/`from adapters.*.common import`를 **`main()` 함수 안, try 블록 내부**에 두고 있다(직접 확인). antigravity만 다르다.

**영향**: `core/verify_state.py`나 `core/classify.py` 등에 구문 오류·순환 import·의존성 문제가 생기면(리팩터링 실수, 향후 core 확장 중 실수 등) claude_code/codex_cli는 정상적으로 fail-open 메시지를 내고 세션을 살리지만, **antigravity는 처리되지 않은 ImportError/SyntaxError로 그대로 죽는다** — 설계 원칙 1번("게이트 자체 오류는 세션을 절대 죽이지 않는다")의 명백한 위반이다. `tests/test_antigravity_adapter.py`의 `test_oma_hooks_fail_open_on_malformed_payload`(136-139행)는 **JSON 페이로드 파싱 실패**만 검증하지, **core 임포트 실패** 시나리오는 애초에 테스트 대상이 아니라 이 구멍이 그린 테스트 스위트(53 passed, 직접 실행 확인)로는 드러나지 않는다.
**제안**: antigravity의 `from core.* import` 5줄을 `main()` 함수 안, `try` 블록 내부로 이동. 다른 8개 파일과 동일 패턴으로 맞추면 끝나는 간단한 수정이나, v1.0.0 "안정" 딱지 붙이기 전 반드시 고쳐야 할 항목이라 Blocker로 분류한다.

(참고로 `fable_lite/`의 `cli.py`/`check.py`/`brief.py`는 훅이 아니라 오케스트레이터가 직접 실행하는 CLI 도구라 `SystemExit`으로 실패하는 게 올바른 UX다 — 여긴 fail-open 기준을 적용 대상이 아니라고 판단해 제외했다.)

## 추가 발견 (요청 범위 밖, 리뷰 중 확인)

### M1. `fable_lite check`가 `git status -uall` 사용

`fable_lite/check_support.py:14`: `["git", "-C", str(root), "status", "--porcelain=v1", "-uall", ...]`. `-uall`은 untracked 디렉토리 안의 파일을 전부 개별 나열하는 플래그라 대용량/모노레포성 대상 프로젝트(`node_modules` gitignore 누락 등)에서 느려지거나 메모리를 많이 쓸 수 있다. `fable_lite check`는 임의의 대상 프로젝트에 대해 오케스트레이터가 위임 후 사후 검증용으로 돌리는 도구라 이 리스크가 실사용에 노출될 가능성이 있다.
**제안**: `-uall` 대신 기본 동작(디렉토리 단위 표시) 사용을 검토하거나, 대용량 repo 대비 타임아웃/사이즈 가드 추가.

### N1. `codex_cli`가 `runpy.run_path`로 `common.py`를 동적 로드

`codex_cli`의 4개 훅 파일(`pre_tool_use.py:25`, `post_tool_use.py:25` 등)은 `common = runpy.run_path(str(Path(__file__).with_name("common.py")))` 후 `common["read_payload"]()`처럼 딕셔너리 접근으로 함수를 호출한다. 정상 동작은 하지만 일반 `from adapters.codex_cli.common import ...`(claude_code가 쓰는 방식)보다 비표준적이고, 타입체커·IDE 자동완성이 무력화되며, `common.py`에 오탈자가 생겨도 정적으로 못 잡는다. 기능 버그는 아니라 nice-to-have.

### N2. `card.verify`가 빈 문자열일 때 메시지에 빈 백틱

`fable_lite/card.py:113-115`의 `card_verify_success`가 `card.verify`가 falsy일 때 무조건 `False`를 반환하고, `check.py:189-192`의 `verify_findings`가 `f"verify \`{card.verify}\` 성공 기록이 없습니다"`를 그대로 출력해 빈 백틱(` `` `)이 메시지에 남는다. 다만 `card_validation_findings`가 `verify` 필드 누락을 이미 별도로 RED 처리하므로 실질적 피해는 없는 화면 표시 문제.

## 결론

v1.0.0 "안정" 표기 전 반드시 처리할 항목은 **B1·B2** 둘이다 — 둘 다 파일 하나·몇 줄 수정으로 끝나는 작지만 근본적인 수정이고, 특히 B2는 이 프로젝트의 핵심 가치 제안("하드 게이트")이 실제로는 절반만 작동한다는 뜻이라 우선순위가 가장 높다. H1-H3는 "출시 자체를 막을 정도"는 아니지만 "안정" 주장의 신뢰도에 직결되므로 v1.0.0 범위 안에서 처리하길 권한다. M1·N1·N2는 후속 릴리스로 미뤄도 무방하다.
