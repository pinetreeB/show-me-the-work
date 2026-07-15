# Provenance 수정본 최종 적대 재검증 (agy)

**목적:** `d888c9b..HEAD` 커밋 범위의 수정 사항(R1~R4 등)이 이전에 보고된 Critical 결함들을 막아냈는지, 그리고 미탐/오탐 경로가 새로 생기지 않았는지 재검증합니다.

## 1. 1차 리뷰 Critical 및 Ultracode 보고건 방어 검증

*   **R1 (shlex `#` 삼킴에 의한 로컬 훼손 은닉):** [막힘]
    *   `shell_command.py`의 `_command_tokens`에서 `lexer.commenters = ""`로 설정하여 쉘 주석 기호를 일반 토큰으로 파싱하게 수정되었습니다. `cat x#; rm -rf src` 같은 페이로드가 정상적으로 `LOCAL_OR_UNKNOWN`으로 분류됩니다.
*   **R2 (loopback 별칭을 통한 원격 면책 우회):** [막힘]
    *   `socket.inet_aton`을 사용한 IP 정규화가 추가되어 `127.1`, `0.0.0.0`, 정수형 IP 별칭 등이 모두 루프백으로 정확히 강등됩니다.
*   **R4 (fail-open baseline으로 인한 검증 우회):** [막힘]
    *   `finish_turn`에서 `MissingTurnBaselineError`를 명시적으로 포착하여 턴을 `incomplete` 상태로 만들고, Stop 게이트에서 정상 차단되도록 수정되었습니다.

---

## 2. 미해결 취약점 및 신규 오탐 (CRITICAL & HIGH)

### [CRITICAL] CONFIG 자가 면제 취약점 우회 (여전히 뚫림)

*   **판정:** 수리 보고서의 "보안 config는 별도 Git 증거로 fail-closed 처리" 주장은 **완벽히 우회 가능**합니다.
*   **원인 및 공격 시나리오:**
    1.  수리된 코드는 `fable_lite/check.py`에서 `git status --porcelain`을 사용하여 `CONFIG_RELATIVE_PATH`의 변경을 잡으려 시도했습니다.
    2.  하지만 에이전트가 `.fable-lite/provenance-config.json`을 수정하여 특정 폴더(`src/`)를 제외시킨 후, 타겟 폴더를 훼손합니다.
    3.  그리고 **`git add . && git commit -m "hide"`를 실행하여 변경 사항을 커밋**해 버리면, `git status`의 출력은 비어 있게 됩니다.
    4.  `core/provenance_policy.py:is_harness_state_path`는 여전히 `.fable-lite`를 하드 제외하고 있으므로, 기본 스캐너의 `changed_files_seen` 역시 비어 있습니다.
    5.  결과적으로 Stop 게이트와 Check 표면 모두 0개의 변경으로 인식하고 **검증 없이 통과(Allow)** 시킵니다. 하드게이트 전면 붕괴.
*   **처방:** Git 상태에 의존하는 백스톱은 커밋에 의해 무력화됩니다. `core/provenance_policy.py`의 `is_harness_state_path`에서 `.fable-lite/provenance-config.json` 파일 자체를 반드시 하드 제외 대상에서 예외로 처리하여 스냅샷 스캐너가 직접 해시를 추적하게 만들어야 합니다.

### [HIGH] 삭제 경로 보수적 포함에 의한 정상 턴 과차단 (오탐 재발)

*   **판정:** 수리 보고서 76번 라인("삭제 시점 완전복원 불가, 보수적 포함")의 결정이 **순수 읽기 전용 턴(read-only turn)을 영구적으로 과차단(False Positive)하는 회귀(Regression)**를 낳았습니다.
*   **원인 및 시나리오:**
    1.  사용자가 깃에 추적되는 파일을 하나 삭제하고 커밋하지 않은 상태(uncommitted deletion)로 둡니다.
    2.  에이전트가 코드를 읽기만 하는 정상적인 `PROVEN_READ_ONLY` 턴을 수행합니다.
    3.  수정된 `fable_lite/check_support.py`의 `git_changes_since_baseline`은 `git status`에 포착된 파일 중 실제 존재하지 않는 파일(`missing`)을 발견하면, 해당 삭제가 **턴 시작 이전에 발생했는지 여부를 따지지 않고 무조건 이번 턴의 변경으로 포함(`path in missing`)** 시킵니다.
    4.  결과적으로 에이전트가 아무것도 변경하지 않았음에도 삭제된 파일에 대한 검증 증거를 요구하며 Stop 게이트가 영구적으로 차단(Block)됩니다. (수정된 파일의 경우 `calculate_net_delta`로 걸러져 오탐이 없으나, 삭제 파일 처리에만 치명적 비대칭이 생겼습니다.)
*   **처방:** 삭제된 파일(`missing`)이 `baseline` 스냅샷에도 존재하지 않았다면(즉, 턴 시작 전부터 이미 삭제 상태였다면) 현재 턴의 변경에서 제외하도록 필터링 로직을 보완해야 합니다.
