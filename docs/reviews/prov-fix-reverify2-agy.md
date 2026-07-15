# Provenance 2차 수리 최종 재검증 (agy)

**목적:** `063ba22..284427b` 커밋 범위의 2차 수정 사항이 직전 리뷰(`docs/reviews/prov-fix-reverify-agy.md`)에서 제기된 R7(Critical) 및 R8(High) 결함을 완벽하게 방어하는지, 신규 오탐/미탐은 없는지 검증합니다.

## 1. R7 [CRITICAL] CONFIG 자가 면제 취약점 우회 (Git Commit 세탁)
*   **판정: [완벽히 막힘]**
*   **검증 내용:**
    *   `core/provenance_policy.py`에서 `is_path_in_scope`와 `should_descend` 최상단에 `CONFIG_RELATIVE_PATH`(`.fable-lite/provenance-config.json`)를 하드 제외(Hard Exclude)보다 먼저 예외 처리하도록 수정되었습니다. 이로 인해 에이전트가 `provenance-config.json`을 변조하려 할 때 스캐너가 이를 무조건 추적하게 되어 자가 면제를 원천 차단합니다.
    *   가장 치명적이었던 "Git Commit 세탁" 공격(추적 중인 파일을 config로 제외한 뒤 수정하고 커밋해버려 `git status`를 비우는 공격)은 `core/provenance_lifecycle_scope.py`의 `tracked_force_paths` 도입으로 완전히 봉쇄되었습니다. 
    *   Git에 추적되는 파일(`git_tracked_paths`)은 config에서 제외(exclude)되어 있더라도 **무조건 스냅샷 스캐너의 `forced_paths`에 강제 포함**되도록 로직이 강화되었습니다. 결과적으로 `git status`가 비어있어도 Primary 스캐너가 Baseline과 Current 간의 해시 변화를 잡아내어 턴 변경 사항으로 기록(`turn_paths`)하므로 검증을 피할 수 없습니다.

## 2. R8 [HIGH] 삭제 경로 보수적 포함에 의한 정상 턴 과차단 (오탐)
*   **판정: [완벽히 해소됨]**
*   **검증 내용:**
    *   `fable_lite/check_support.py`의 `git_changes_since_baseline`에서 누락된 파일(`missing`)을 판별할 때, **턴 시작 시점의 Baseline 스냅샷에 해당 파일이 존재했는지(`baseline_keys`)를 대조**하는 조건이 추가되었습니다.
    *   이로 인해 턴 시작 전에 이미 삭제되었으나 커밋되지 않은 파일은 Baseline 스냅샷에 포함되지 않으므로, 읽기 전용 턴(read-only turn)이 끝났을 때 `missing` 변경 사항으로 억울하게 과차단되는 오탐(False Positive) 문제가 완벽하게 해결되었습니다.
    *   반대로 턴 도중에 에이전트가 삭제한 파일은 Baseline에 존재했으므로 정상적으로 `missing`으로 적발됩니다.

## 3. 신규 회귀(Regression) 및 우회(Bypass) 가능성 검토
*   **Config 항상 추적에 의한 노이즈 여부:** `.fable-lite/` 디렉터리에 진입하더라도 `provenance-config.json`만 정확히 예외 처리되며, `ledger.json` 등 다른 Harness 파일들은 여전히 `is_hard_excluded`에 의해 스킵됩니다. 신규 노이즈는 없습니다.
*   **Git 트래킹 강제 포함에 의한 노이즈 여부:** 빌드 아티팩트(`node_modules/`, `vendor/` 등)가 Untracked 상태라면 `tracked_force_paths`에 의해 강제 포함되지 않으므로 기존 의도대로 스캔에서 제외됩니다. (오탐 없음)
*   **Git 메타데이터 손상 공격:** 에이전트가 `.git/index` 등을 훼손하여 `git ls-files`를 실패하게 만드는 공격을 대비해, `GitTrackedPathsError`가 발생하면 Snapshot을 `INCOMPLETE(SNAPSHOT_UNAVAILABLE)`로 Fail-closed 처리하도록 방어되어 있습니다.
*   **OS 대소문자 혼동(Windows):** `force_paths`의 강제 추적 로직이 `canonical_manifest_key`를 통해 Windows의 Case Insensitive 환경에서도 일관되게 동작하도록 구현되어 우회로를 막았습니다.

## 최종 결론
**APPROVE.** 
이전에 보고된 Critical/High 취약점과 회귀 버그가 부작용 없이 근본적으로 해결되었습니다. 신규 공격 벡터나 우회 경로는 발견되지 않았습니다.
