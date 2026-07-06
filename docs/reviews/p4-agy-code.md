# fable-lite 코드 리뷰 리포트 (P4 - 적대적 리뷰)

본 문서는 `fable-lite` 하네스의 `core/`, `adapters/claude_code/`, `goals/` 디렉토리에 위치한 모든 코드에 대해 진행한 독립적인 적대적 리뷰(Adversarial Review) 결과입니다. 코드베이스의 신뢰성, Windows 호환성, 동시성, 그리고 v1 스펙(AC1~12) 충족 여부를 중점적으로 점검했습니다.

## 1. 엣지 케이스 및 논리적 결함 (Bugs/Edge Cases)

*   **[Medium] `core/scope_guard.py` - 경로 대소문자 문제**
    *   **위치**: `core/scope_guard.py`의 `_under` 및 `_prompt_mentions` 함수
    *   **이슈**: Windows는 파일 시스템에서 대소문자를 구분하지 않지만(Case-insensitive), 경로 문자열을 비교할 때 단순히 `.replace("\\", "/")`만 수행하고 일관된 `.lower()` 정규화를 생략한 부분이 있어 매칭이 실패할 수 있습니다.
    *   **제안**: `normalized`와 `base`를 비교할 때 대소문자 정규화를 추가하세요.
*   **[Medium] `goals/goals.py` - 리스트 뮤테이션 불일치**
    *   **위치**: `goals/goals.py`의 `verify()` 내 (L71-L86)
    *   **이슈**: `data["stories"]`를 읽어온 뒤 새로운 리스트(`updated`)를 만들어 할당하지만, 이 과정에서 예기치 않은 참조 전달이나 타입 불일치가 발생할 수 있습니다.
    *   **제안**: 데이터 구조를 불변(immutable)으로 관리하거나 pydantic/dataclasses를 도입하여 구조를 강제하세요.

## 2. Fail-open 원칙 위반 (크래시 위험)

*   **[Critical] 최상단 모듈 임포트 시 예외 처리 누락**
    *   **위치**: `adapters/claude_code/*.py` (모든 훅 스크립트 최상단)
    *   **이슈**: 
        ```python
        ROOT = Path(__file__).resolve().parents[2]
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        ```
        이 코드는 `main()` 함수의 `try...except` 블록 바깥에 위치합니다. 파일 권한 문제나 심볼릭 링크 오류 등으로 인해 `resolve()`에서 예외가 발생할 경우, 훅 프로세스가 그대로 크래시되며 Claude Code 세션 전체가 중단(Fail-open 실패)됩니다.
    *   **제안**: `sys.path` 조작 로직 자체를 `main()` 안의 `try...except` 내로 이동시키거나 별도의 안전한 부트스트랩 스크립트로 처리하세요.
*   **[High] `goals.py`에 Fail-open 부재**
    *   **위치**: `goals/goals.py` `main()`
    *   **이슈**: `goals.py`가 플러그인에서 서브프로세스로 호출될 때, `json.loads` 또는 `argparse` 파싱에서 에러가 나면 프로그램이 스택 트레이스와 함께 종료됩니다.
    *   **제안**: `try...except`를 씌워 에러 시 JSON 형태로 fail-open 메시지를 반환하도록 강제하세요.

## 3. Windows 호환성 (경로/CRLF)

*   **[Medium] 파일 저장 시 OS 기본 개행 문자 사용**
    *   **위치**: `core/ledger.py` (`save_ledger`), `goals/goals.py` (`_save`)
    *   **이슈**: `path.write_text(..., encoding="utf-8")`는 인코딩은 강제하지만 개행 문자는 강제하지 않아 Windows 환경에서 암묵적으로 CRLF(`\r\n`)로 쓰입니다. 이는 크로스 플랫폼 환경에서 git 상태나 로그 파싱에 문제를 일으킬 수 있습니다.
    *   **제안**: `newline="\n"` 파라미터를 추가하여 LF로 일관되게 쓰도록 수정하세요.

## 4. 차단 상한 및 가드 준수 (Stop Hook)

*   **[Pass / Low Risk] Stop 훅 2회 상한 및 가드**
    *   **위치**: `core/verify_state.py`
    *   **이슈**: `MAX_STOP_BLOCKS = 2` 정책과 `payload.get("stop_hook_active")` 체크가 정상적으로 구현되어 있습니다. 
    *   **주의점**: `stop_blocks` 카운터가 검증(verification) 성공 시 초기화(0으로 리셋)되지 않는 구조입니다. 이는 한 세션 전체에서 2번만 방어하겠다는 의미라면 맞지만, 각 태스크(도구 사용)마다 2번이라면 리셋 로직이 추가되어야 합니다.

## 5. 상태 파일 동시성(Concurrency) 및 데이터 손상 내성

*   **[Critical] 파일 락(File Locking) 부재로 인한 JSON 손상 및 데이터 유실**
    *   **위치**: `core/ledger.py` (`load_ledger`, `save_ledger`)
    *   **이슈**: Claude Code가 여러 도구(예: 셸 실행과 에디터 수정)를 병렬로 호출하여 훅이 동시에 트리거될 경우, `ledger.json`을 읽고 쓰는 과정에 레이스 컨디션(Race Condition)이 발생합니다.
    *   **더 큰 문제**: `load_ledger`에서 `try...except (OSError, json.JSONDecodeError): return default_ledger()`로 처리되어 있습니다. 동시 쓰기로 인해 일시적으로 JSON이 깨졌을 때, 기존의 모든 히스토리를 덮어쓰고(초기화) 저장하게 되어 **심각한 데이터 유실**을 초래합니다.
    *   **제안**: 파일 락(예: Windows의 경우 `msvcrt.locking`, Unix는 `fcntl`, 크로스플랫폼 `filelock` 라이브러리)을 도입하여 읽기-수정-쓰기 주기를 원자적(Atomic)으로 보호하세요.

## 6. 보안 (경로 조작)

*   **[Medium] Directory Traversal 방어 누락**
    *   **위치**: `core/common.py` 및 각종 어댑터의 `project_root` 파싱
    *   **이슈**: `payload.get("project_root")`에서 외부 입력을 받아 절대 경로로 바로 결합(`Path(root).resolve() / ".fable-lite"`)합니다. 악의적인 페이로드가 `../../` 등을 주입하면 원치 않는 위치에 상태 디렉토리를 생성할 수 있습니다.
    *   **제안**: `root` 경로가 현재 실행 환경을 벗어나지 않는지 검증하는 로직을 추가하세요.

## 7. 스펙(fable-lite-v1.md) 충족 여부 대조 및 미충족 항목

*   **[Critical] AC3 (N1 게이트 - 팩 준수 검증) 미구현**
    *   **이슈**: 스펙 상 "조사 팩 주입 후 모델 출력을 파싱해 가설 수·증거 인용·기각 보고 존재를 확인, 미준수 시 차단(N1)" 해야 합니다. 그러나 `user_prompt_submit.py`에서 텍스트로 지시사항을 주입할 뿐, `post_tool_use.py`나 `stop.py` 등 어디에서도 모델의 출력을 파싱하여 **가설 3개가 존재하는지 결정론적으로 검증하고 차단하는 실제 게이트 로직(N1)이 전혀 구현되어 있지 않습니다.**
    *   **제안**: `core/verify_state.py` 또는 `post_tool_use.py`에 출력 텍스트 파싱을 통한 규율 준수 여부 판정 기능을 신규 구현해야 합니다.
*   **[High] AC2 (N2 - 복합 스토리 플랜 강제) 검증 로직 누락**
    *   **이슈**: `classify.py`에서 `needs_goals`를 True로 판정하여 텍스트를 출력하지만, 실제로 `goals.json`이 존재하는지, 플랜이 수립되었는지를 체크하여 ToolUse를 차단하거나 강제하는 '게이트'는 존재하지 않습니다. 소프트 지시(Soft Prompting)에만 의존하고 있습니다.
    *   **제안**: PreToolUse 단계에서 `needs_goals`일 경우 `goals.json`의 존재 유무 및 작성 여부를 검사하는 하드 게이트 로직을 추가하세요.
