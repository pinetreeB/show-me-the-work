# v1.0.0 릴리스 준비도 심사 결과 (실사용자 및 제품 관점)

`fable-lite` 전체 코드베이스 및 문서를 사용자 시각에서 스캔하고, 안정화(v1.0.0) 표방 전 반드시 해결해야 할 항목(Blocker)과 미뤄도 되는 항목(Nice-to-have)으로 분류했습니다.

## 1. 문서 정합성 (Documentation)

### [Blocker] Antigravity 어댑터 설치 가이드의 혼동 및 개발 메모 잔존
* **파일**: `adapters/antigravity/INSTALL.ko.md`
* **문제**: 로컬 훅 등록 파일 경로가 `.omg/state/hooks.json`이 아니라 `.gemini/hooks.json`임을 v7 라이브 검증 단계에서 깨닫고 문서에 추가했으나, 문서 상단 서문에는 여전히 `.omg/state/hooks.json`을 직접 수정하지 말라고 언급하여 혼동을 줍니다. 또한 `(p7 라이브 검증으로 확정)`이라는 내부 개발 진행용 메타 노트가 릴리스 문서에 그대로 노출되어 있어 제품 완성도를 떨어뜨립니다.
* **해결**: 혼동을 주는 `.omg/state/hooks.json` 언급을 완전히 제거하고, 정규 매뉴얼 톤으로 단일화 및 개발 메모 삭제.

### [Blocker] 검증 불가능한 플러그인 설치 명령어
* **파일**: `README.md`, `README.ko.md`
* **문제**: `/plugin marketplace add pinetreeB/fable-lite` 명령어는 마켓플레이스 정식 등록 절차가 완료되기 전이라면 작동하지 않을 수 있습니다. 사용자가 첫 단계에서 복사-붙여넣기 후 즉각적인 에러를 경험할 수 있습니다.
* **해결**: 마켓플레이스 등록 전이라면 URL 기반(`https://github.com/pinetreeB/fable-lite`) 추가 구문으로 정정하거나, 로컬 클론 후 설치(`plugin install`) 방식을 최우선 1옵션으로 안내해야 합니다.

### [Blocker] 플러그인 매니페스트 버전 불일치
* **파일**: `.claude-plugin/plugin.json`
* **문제**: v1.0.0 릴리스를 표방함에도 해당 파일 내부 버전 속성은 `"version": "0.6.1"`에 머물러 있습니다.
* **해결**: 릴리스 전 `"version": "1.0.0"`으로 동기화 필수.

## 2. 첫 사용자 경험 (First User Experience)

### [Blocker] Python 패키지 설치 설정(`pyproject.toml`) 부재
* **파일**: `pyproject.toml` 또는 `setup.py` (루트에 없음)
* **문제**: README는 타겟 프로젝트(예: 사용자의 개인 작업 폴더)에서 `python -m fable_lite brief ...` 명령어를 실행하라고 안내합니다. 하지만 `pip install .`를 할 수 있는 패키징 설정이 없어, 사용자가 클론한 디렉토리 밖에서 모듈을 실행하면 즉시 `ModuleNotFoundError` 장애를 겪습니다. 하네스는 "다른 프로젝트"에 적용되는 것이 본질이므로 전역 공간에 설치할 수 있어야 합니다.
* **해결**: 표준 `pyproject.toml`을 작성하여 `pip install -e .`가 가능하도록 조치해야 합니다.

### [Nice-to-Have] CLI 명령어의 파편화
* **파일**: `fable_lite/__main__.py`, `goals/goals.py`
* **문제**: 오케스트레이터 CLI(`python -m fable_lite`)와 목표 체크포인트 CLI(`python goals/goals.py`)가 분리되어 있어 진입점이 통일되지 않았습니다.
* **해결**: `python -m fable_lite goals plan ...` 형태로 서브 명령어로 통합하면 사용성이 향상됩니다 (v1 이후로 미뤄도 무방).

## 3. 미완성 / TODO 흔적

* **PASS**: 코드 전체(`core/`, `fable_lite/`, `adapters/`)를 스캔한 결과, `TODO`나 `NotImplementedError` 등 미완성을 방치한 부끄러운 흔적은 발견되지 않았습니다. 
* **Nice-to-Have**: `adapters/antigravity/hooks.json` 템플릿의 `{FABLE_LITE_ROOT}` 치환은 수동 작업이 필요합니다. 추후 초기화 스크립트를 제공하면 좋습니다.

## 4. 오픈소스 위생 (Open Source Hygiene)

### [Blocker] 기여 안내서 누락
* **파일**: `CONTRIBUTING.md` (루트에 없음)
* **문제**: v1.0.0 안정화 버전을 오픈소스로 공개할 때 커뮤니티의 팩(Pack) 추가 방법, 타 환경(Codex/OmA) 어댑터 기여 방식, Pytest 실행법 등을 안내하는 기여 문서가 필수적입니다.
* **해결**: 템플릿 형태라도 `CONTRIBUTING.md` 작성.

### [PASS] 라이선스 및 크레딧
* **파일**: `LICENSE`, `README.md`, `README.ko.md`
* **결과**: MIT 라이선스가 정상 명시되어 있으며, 선행 프로젝트(fablize, playbook, fablever)에 대한 Attribution(출처 표기)과 디자인 차용 근거가 투명하고 매우 훌륭하게 정리되어 있습니다.
