# 홈 루트 provenance skip 구현 검토 보고서

## 검토 개요
- **대상 커밋**: 4167051..1870283
- **근거 문서**: docs/reviews/2026-07-15-home-root-provenance-scope-incident.md (A안)
- **리뷰어**: Gemini (Antigravity CLI)

## 검토 항목 결과

### 1. 홈 감지 정확성 (오탐 및 미탐)
- **정확성 보장**: `core/project_root.py`의 `_normalized` 함수에서 `Path.resolve()`를 통해 symlink, junction, 상대경로 등을 절대경로로 정규화하고, `os.path.normcase` 및 `casefold`를 사용하여 후행 슬래시와 대소문자 차이를 소거합니다.
- **오탐 없음**: `~` 및 `%USERPROFILE%` 환경변수를 정규화한 값과 정확히 `==`로 일치하는 경우에만 홈으로 판정합니다. 따라서 `~/myproj` 같은 하위 경로가 홈으로 오판되어 감독이 상실될 위험은 없습니다.
- **미탐 없음**: `candidates` 배열로 `~` 확장과 `%USERPROFILE%`을 모두 커버하여 홈 디렉토리를 놓치지 않습니다.

### 2. `verify_state` 게이트 영향 (과도한 완화 여부)
- **안전한 스킵**: `verify_state.py`에서 `home_root_unsupported` 변수를 통해 `SCOPE_TOO_LARGE` 및 `INCOMPLETE` 차단 게이트만 조건부로 우회합니다.
- **다른 게이트 유지**: 조사팩 검사(`check_investigation_compliance`), 디자인 게이트(`evaluate_design_stop`), 성공한 검증 증거 요구(`has_successful_verification`)는 `home_root_unsupported` 블록 이후에도 온전히 검사되거나 독립적으로 동작합니다. 즉, 과도한 완화가 발생하지 않고 꼭 필요한 안전 게이트들은 정상적으로 유지됩니다.

### 3. 3어댑터 공유 코어 일괄 적용 정합성
- **일괄 적용 확인**: `core/adapter_observation.py`의 5개 핵심 진입점(`start_turn`, `begin_invocation`, `observe_post_tool`, `finish_turn`, `reconcile_turn`)의 최상단에서 `_home_root_unsupported_report(root)`를 호출해 단락(short-circuit) 시킵니다.
- **어댑터 분기 없음**: 어댑터 측 코드 변경 없이 공통 관측 모듈에서 일괄로 제어하므로 정합성에 문제가 없습니다.

### 4. 에이전트의 우회 가능성
- **위험도 낮음**: 문서 A안에 기술된 대로, 만약 에이전트가 우회 목적으로 홈 루트로 설정한다 하더라도 기존 환경에서는 이미 cap 한계치(최대 2회 차단 후 통과)로 인해 무력화되던 상황이었으므로 실질적인 보안 손실은 늘어나지 않습니다. 오히려 명시적 예외 처리로 노이즈가 제거되어 긍정적입니다.

## 최종 결론
**APPROVE**

코드 변경사항은 Incident Report의 A안을 정확히 구현하였으며, 엣지 케이스(대소문자, 경로 정규화, 게이트 우회 범위 제한)에 대한 방어 로직이 적절히 구현되어 있습니다. Critical 또는 High 수준의 결함은 발견되지 않았습니다.
