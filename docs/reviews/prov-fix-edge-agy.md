# Provenance 엣지 케이스 보강 리뷰 (agy)

**목적:** Codex가 2차 수리 이후 추가한 Uncommitted 엣지 보강 커밋(`tmp/prov-fix/uncommitted-edge.diff`)이 기존 R7/R8 방어망을 해치지 않는지, 그리고 신규 오탐/미탐이 없는지 검증합니다.

## 1. 변경 요지 및 목적
*   **`git_tracked_paths` 개편:** `git ls-files -z` 대신 `git ls-tree -r HEAD`와 `git diff --cached --diff-filter=ACMR`의 조합으로 변경되었습니다. 이는 `git add -N`(intent-to-add)으로 인덱스에 등록만 되고 내용은 없는 파일이 강제 추적되어 오탐을 유발하는 현상을 해결하기 위함입니다.
*   **Casefold 매칭 도입:** Windows 환경에서 Git 인덱스의 경로 대소문자와 파일 시스템의 대소문자가 다를 경우 정책(Policy) 검사가 누락되는 미탐을 막기 위해, `_policy_path`와 `_casefolded_config`를 적용했습니다.

## 2. 검증 항목 상세 리뷰

### (a) R7 커밋세탁 방어 유지 및 HEAD 삭제 파일 추적
*   **판정: [완벽히 유지됨]**
*   **근거:** `ls-tree HEAD`는 Working tree에서의 삭제 여부와 상관없이 "마지막 커밋 기준 추적 중인 모든 파일"을 반환합니다. 에이전트가 배제된 파일을 수정하고 커밋 세탁(`git commit -m "hide"`)을 하더라도, 해당 파일은 `HEAD`에 기록되므로 스캐너의 강제 관측(`forced_paths`) 범위에 여전히 포함됩니다. R7 방어가 전혀 무너지지 않았습니다.

### (b) Staged-only 신규 파일 관측
*   **판정: [정상 관측됨]**
*   **근거:** 새 파일을 생성하고 `git add`만 한 상태(커밋 전)에서는 `HEAD`에 없지만, `git diff --cached --diff-filter=ACMR`에서 `Added(A)`로 정확히 포착됩니다. 이 목록이 `head_paths`와 합집합 연산되어 강제 추적 목록에 포함되므로, 커밋 전 파일의 변경도 놓치지 않습니다.

### (c) R8 삭제 오탐 로직과의 상호작용
*   **판정: [상호 충돌 없음]**
*   **근거:** 턴 시작 전 지워진 파일이 `HEAD`에 남아있어 강제 추적(`forced_paths`)을 지시받더라도, 실제 Primary 스캐너(`os.scandir`)는 파일 시스템에 존재하지 않는 파일을 Baseline 스냅샷에 담지 않습니다. 따라서 `baseline_keys`에 등재되지 않으며, `check_support.py`의 `missing` 필터링 로직에 의해 여전히 오탐으로 처리되지 않고 올바르게 무시됩니다.

### (d) Casefold 로직의 정합성 및 정상 경로 오매칭 여부
*   **판정: [정합성 완벽]**
*   **근거:** 
    *   Linux/macOS(`os.name != "nt"`) 환경에서는 `_policy_path`가 원본을 그대로 반환하므로 Case-Sensitive 규칙이 그대로 유지됩니다.
    *   Windows에서는 Git에서 반환한 경로와 Config 정책 모두 `casefold()`된 상태에서 `is_path_in_scope` 평가를 받습니다. 평가가 끝난 뒤 스캐너로 넘겨지는 값은 소문자로 변환된 문자열이 아니라 **Git 원본 경로 문자열**입니다. 이후 스캐너 내부의 `canonical_manifest_key`가 윈도우 파일 시스템에 맞게 다시 처리하므로, 정상 파일 매칭에 어떤 부작용도 일으키지 않으며 대소문자 혼동을 악용한 우회를 원천 차단했습니다.

### (e) Fail-Closed 유지 (GitTrackedPathsError)
*   **판정: [유지됨]**
*   **근거:** 초기화 직후 커밋이 없는 저장소의 경우 `ls-tree HEAD`가 실패하지만, `rev-parse --verify HEAD`로 커밋 부재를 교차 검증하여 정상적인 빈 목록(`frozenset()`)을 반환합니다. 반면 커밋이 있는데도 트리가 깨져 실패한 경우 명시적으로 `GitTrackedPathsError`를 던져 `INCOMPLETE`로 처리(Fail-Closed)되므로, 고의적인 `.git` 훼손 공격을 여전히 방어합니다.

## 최종 결론
**APPROVE.** 
기존의 방어선을 100% 유지하면서, `git add -N`과 Windows 대소문자 환경의 취약한 엣지 케이스들을 매우 정교하게 보강한 훌륭한 패치입니다. 추가 수리가 필요하지 않습니다.
