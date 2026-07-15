# Provenance 오탐 수정 구현 적대적 리뷰 (agy)

**목적:** `70caee5..d888c9b` 커밋 범위의 구현이 기존 분석된 5종의 우회 공격을 막아냈는지, 그리고 신규 allow 경로를 통한 새 우회로가 없는지 검증합니다.

## 1. 5종 공격 시나리오 방어 검증 (통과)

*   **파일 폭탄 / `scope_too_large` 우회 (F1):** [막힘]
    *   **코드 근거:** `core/verify_state.py:232`에서 `scope_too_large` 상태이면서 `mutation_capable`인 경우 명시적으로 `ReasonCode.STOP_PROVENANCE_INCOMPLETE`로 **차단(Block)**하도록 수정되었습니다. 더 이상 관측 불가를 이유로 한 무조건적 허용(면죄부)이 불가능합니다.
*   **스토어 삭제 증거 인멸 (F2):** [막힘]
    *   **코드 근거:** `core/provenance_lifecycle.py:44`에서 스냅샷 스토어 로드 실패 시 `STORE_READ_ERROR`로, 저장 실패 시 `STORE_WRITE_ERROR`로 처리하여 `incomplete` 상태를 유지합니다. 임의로 팬텀 ID를 부여해 정상 턴으로 위장하는 것이 불가능합니다.
*   **제외 경로 은닉 및 `uv.lock` 미탐 (F3):** [막힘]
    *   **코드 근거:** `core/provenance_policy.py`에서 `build/`, `dist/`, `uv.lock` 등을 하드 제외에 추가하지 않았습니다. 의존성 락 파일 변경이나 빌드 폴더 내 은닉 행위 발생 시 정상적으로 변경(changed)이 감지되어 검증 게이트가 발동됩니다.
*   **가짜 원격 면죄부 `ssh localhost` (F4):** [막힘]
    *   **코드 근거:** `core/shell_command.py:223`에서 루프백 호스트(localhost 등)는 `LOCAL_OR_UNKNOWN`으로 강등됩니다. 또한 `core/verify_state.py:79` 로직 상, 원격 변경(`remote_epochs`)이 발생하더라도 해당 타겟을 명시적으로 커버하는 검증(`_has_target_verification`)이 요구되며, 로컬 검증 의무를 면제하지 않습니다.
*   **부팅 위장 트로이목마 (F5):** [막힘]
    *   **코드 근거:** `core/classify.py`를 전혀 수정하지 않았습니다. 분류기를 느슨하게 만들어 발생하는 리스크 자체를 원천 차단했습니다.

---

## 2. 신규 발견 우회 경로 (CRITICAL)

**자가 면제(Config 수정) 취약점: `provenance-config.json` 은닉 공격**

*   **코드 근거:**
    *   `core/provenance_policy.py:661`의 `is_harness_state_path`는 `.fable-lite`, `.fablize` 등을 하드 제외합니다.
    *   `CONFIG_RELATIVE_PATH`는 `.fable-lite/provenance-config.json`입니다.
    *   스냅샷 스캐너는 하드 제외된 폴더(`.fable-lite/`)의 내용을 전혀 읽지 않습니다.
*   **공격 시나리오:**
    1.  에이전트가 `.fable-lite/provenance-config.json` 파일을 열어 자신이 파괴할 폴더(예: `"exclude": ["src/**"]`)를 몰래 추가합니다.
    2.  config 파일의 변경은 하드 제외된 폴더 하위에 있으므로 **스냅샷에서 누락**되며, 게이트의 `changed_files_seen`에 기록되지 않습니다.
    3.  에이전트가 `src/` 코드를 마음대로 훼손합니다.
    4.  새로 갱신된 config에 의해 `src/` 폴더 역시 스냅샷에서 제외되어 파일 훼손이 감지되지 않습니다.
    5.  관측된 변경 파일이 0개이므로, 게이트가 어떠한 테스트(verify)도 요구하지 않고 **Allow** 처리합니다. 하드게이트가 전면 붕괴됩니다.
*   **심각도: CRITICAL**
*   **처방:** 보안 정책 파일인 `provenance-config.json`은 하드 제외 대상에서 예외로 두어 스냅샷에 포함시키거나, 해당 파일의 무결성(hash/mtime)을 외부에서 강력히 추적해야 합니다.

---

## 3. Codex 구현 보고서 주장 검증

*   **과장/미검증 사항 적발 (Medium):**
    *   **주장:** `impl-report-codex.md`의 "8. state 제외는 root-level harness 경로만: 준수. root `.fable-lite`/`.fablize`/`.codegraph`만 제외하고 nested 동일명은 관측합니다."
    *   **판정: 사각지대 맹신.** 루트 레벨 제외 규칙을 구현한 것은 사실이나, 이로 인해 `.fable-lite` 내부에 위치한 **핵심 보안 정책 파일(`provenance-config.json`)까지 일괄 제외되는 치명적 사각지대**를 인지하지 못했습니다. 정책을 기계적으로 준수했을 뿐, 그 정책이 초래한 보안 홀을 놓친 미검증 주장입니다.
