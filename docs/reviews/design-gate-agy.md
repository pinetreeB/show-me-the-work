# Design Gate 구현 (0fc688d) P4 교차검토 보고서 (agy)

## 1. 종합 판정
미완 백로그 5건과 실제 구현 코드를 분석한 결과, 하드게이트를 우회하거나 린터를 무력화할 수 있는 **Critical 1건, High 3건**의 치명적 결함이 확인되었습니다. OFF 상태의 안전성은 검증되었으나, 에이전트에 의한 게이트 자가 무력화(Self-Disabling) 위험이 존재합니다.

---

## 2. 상세 결함 및 심각도 분석

### [CRITICAL] Allowlist 세탁을 통한 TOCTOU 자가면제 (R7 우회와 동일 계열)
* **원인:** `design/gate.config` 파일이 수정되어도 UI 확장자(`.css`, `.tsx` 등)가 아니기 때문에 `is_ui_path`가 `False`를 반환하며, 결과적으로 `design_last_change_seq`가 갱신되지 않습니다.
* **우회 시나리오:**
  1. 에이전트가 UI 파일에 하드코딩 위반을 추가합니다 (`change_seq` 갱신됨).
  2. 에이전트가 `gate.config` allowlist에 방금 추가한 위반을 일시적으로 허용하도록 수정합니다 (`change_seq` **미갱신**).
  3. `fable_lite check --design`을 실행하여 PASS 기록과 최신 `check_seq`를 획득합니다.
  4. 에이전트가 다시 `gate.config`에서 해당 allowlist 항목을 슬쩍 삭제합니다 (`change_seq` **미갱신**).
* **결과:** Stop 하드게이트는 `check_seq > change_seq` 조건과 `passed=True`만 검사하므로 통과시킵니다. 최종 코드에는 하드코딩이 남고 allowlist 기록은 사라집니다. 이는 Provenance R7의 Git 커밋 세탁과 완전히 동일한 Time-of-Check to Time-of-Use 취약점입니다.

### [HIGH] 닫힌 chartData 뒤 문맥 오염 (Context Bleeding 미탐)
* **원인:** `core/design_lint.py`의 `_chart_data_color(context)` 함수가 현재 줄을 기준으로 **이전의 전체 누적 텍스트(`context`)**에 대해 `CHART_DATA_COLOR_RE.search`를 수행합니다.
* **결과:** 파일 상단에 `chartData`가 한 번이라도 등장하면 정규식 `search()`가 True를 반환하므로, 그 이후 파일 끝까지 등장하는 **모든 UI 스타일링 코드의 Raw Color 하드코딩이 "차트 데이터 색상"으로 오인되어 영구 면제(미탐)**됩니다.

### [HIGH] 정규식 한계에 따른 실제 UI 패턴 미탐/오탐
* **미탐 (False Negatives - 하드코딩 통과):**
  * **JS Shorthand 간격:** `JS_SPACING_RE`가 `margin: "0 16px"` 같은 문자열에서 첫 번째 숫자(`0`)만 캡처하고 `any(float(match) > 0)`을 검사하므로, `16px`이라는 명백한 하드코딩을 잡지 못합니다.
  * **CSS 속성 및 문법 누락:** `box-shadow`의 색상, CSS 변수(`--color: #hex`), `rgba()` 등의 표현이 `COLOR_PROPERTY_RE` 및 `HEX_RE`에서 누락되었습니다.
  * **음수 Tailwind:** `[-13px]`와 같이 음수가 포함된 임의값이 정규식에 없어 그대로 통과됩니다.
* **오탐 (False Positives - 정상 코드 차단):**
  * 규칙 SSOT(`DESIGN-OPS.md §2.1`)에서 명시적으로 허용한 **`1px` 헤어라인**이 `float("1") > 0` 조건에 걸려 오탐으로 차단됩니다. (현재 로직상 `0px`만 통과 가능).

### [HIGH] 에이전트 자가 무력화 (Self-Disabling Threat)
* **원인:** `design/gate.config`는 UI 파일이 아니며, 이 설정 파일 자체의 변조를 막는 린트나 게이트 보호 장치가 없습니다.
* **결과:** 에이전트가 임의로 `enabled: false`로 조작해버리면, 현재 턴은 `design_required` 상태로 인해 검증을 받더라도 **미래의 모든 턴에서 디자인 게이트가 영구 무력화**됩니다.

### [MEDIUM] CAS 신호 불일치
* 린트 결과 저장 시 CAS가 실패해도 CLI가 `exit 0`을 유지합니다. Stop 게이트는 Stale 결과를 거부하므로 하드게이트 붕괴로 이어지진 않지만, 에이전트의 재시도 로직에 혼선을 유발합니다.

---

## 3. 온오프(ON/OFF) 안전성 검증
* **OFF 상태 완전 무영향:** `gate.config`에서 `enabled: false`이거나 환경변수 `FABLE_LITE_DESIGN_GATE=0`인 경우, `classify_prompt` 단계에서 `design_required` 자체가 발동하지 않습니다. 
* 결과적으로 분류기, Ledger 업데이트, Stop 게이트의 모든 로직이 초기에 Early Return되므로 **기존 시스템에 어떠한 부하나 부작용도 일으키지 않아 완벽히 안전**합니다.
