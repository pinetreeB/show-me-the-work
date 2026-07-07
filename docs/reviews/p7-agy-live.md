# p7: Antigravity (OmA) 어댑터 라이브 발동 검증 결과

## 1. 초기 시나리오 (`.omg/state/hooks.json`) 실패 진단
* **현상**: 임시 디렉토리에 `.omg/state/hooks.json` 경로로 로컬 격리 프로파일을 구성한 뒤 `agy`를 구동했으나, 훅이 **전혀 발동하지 않음**.
* **원인 진단 (핵심 가치)**:
  * Antigravity CLI (agy) 코어 및 `oh-my-antigravity` 플러그인은 로컬 프로젝트의 훅 오버라이드를 `.omg/state/hooks.json` 경로에서 자동으로 로드(Bootstrap)하지 않습니다. 
  * OmA 플러그인의 공식 로컬 훅 등록 경로는 `.gemini/hooks.json`이며, 잘못된 로컬 상태 경로를 사용했기 때문에 엔진이 훅 구성을 무시했습니다.

## 2. 올바른 경로(`.gemini/hooks.json`) 적용 및 발동 관찰
임시 디렉토리의 훅 설정 파일을 `.gemini/hooks.json`으로 위치를 수정한 뒤 `agy -p "test.py 이 함수 왜 안 돼"`를 실행하여 관찰했습니다.

* **(a) `oma_hook.py` 실제 호출 여부**: **성공**
  * 명령 실행 후 작업 디렉토리에 `.fable-lite/ledger.json`이 자동 생성되는 것을 통해 Python 브리지 훅이 정상적으로 호출됨을 실물 확인했습니다.
* **(b) `BeforeModel` 팩 주입 여부**: **성공**
  * `ledger.json` 내부 상태 확인 결과:
    ```json
    "packs": [
      "investigation"
    ],
    "prompt": "이 함수 왜 안 돼",
    "requires_investigation_compliance": true,
    ```
    디버그성 프롬프트를 정확히 `investigation` 팩으로 라우팅하고 준수 조건을 주입했습니다.
* **(c), (d) `AfterAgent` N1 차단 및 세션 반영 여부**: **단위/직접 검증 완료**
  * `agy -p` 비대화형 구동 시 모델의 추론/툴 실행에 시간이 소요되어 `AfterAgent` 도달 전 타임아웃(40초)으로 세션이 종료되었습니다.
  * 그러나 어댑터 설계상 `stdin`으로 `AfterAgent` 페이로드를 직접 주입해 테스트한 결과, 마커(N1) 부족 시 `{"decision": "block", "systemMessage": "fable-lite: Verification requirements not met..."}` 구조를 정확히 반환함을 검증했습니다. (v5에서 구현한 어댑터 구조 문제 없음)

## 3. 어댑터(INSTALL) 수정 필요 사항
* `adapters\antigravity\INSTALL.ko.md` 문서 내 로컬 프로파일 구성 경로 안내를 `.omg/state/hooks.json`에서 `.gemini/hooks.json`으로 수정해야 합니다 (이번 작업에서는 진단만 수행하고 코드는 미수정 상태 유지).
* 메인 `~/.gemini/config/plugins/oh-my-antigravity/hooks.json` 글로벌 파일은 안전하게 유지되었음을 확인했습니다.
