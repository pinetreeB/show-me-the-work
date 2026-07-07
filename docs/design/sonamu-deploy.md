# 소나무봇(Pinetree-B) fable-lite 배포 가능성 조사

## 1. 조사 결과 요약 (Online 및 파일 상태)
* **네트워크 상태**: `pinetree-b` SSH 접속 가능 (Online 확인됨, Hostname: `Pinetree_B`)
* **Repo 존재 여부**: `C:\Users\rotat\fable-lite` 디렉토리가 존재하지 않음 (`dir` 결과 "파일을 찾을 수 없습니다" 에러 확인). 원격에 복사하거나 clone 해야 함.

## 2. 소나무봇 환경 (종속성 및 CLI 확인)
* **Python 환경**: 시스템 전역 Python이 연결되어 있지 않음. `python --version` 실행 시 `uv trampoline failed to spawn Python child process. Caused by: entity not found (os error 2)` 발생. (설치 또는 `uv` 연결 필요)
* **CLI 툴 체인**:
  * `claude`: 설치됨 (버전 2.1.186)
  * `codex`: 설치됨 (버전 0.142.5)
  * `agy (OmA)`: 설치됨 (버전 1.0.16)

## 3. 설치 및 검증 절차 (초안)
원격지에서의 설치 및 실행은 반드시 사용자의 명시적 OK 후 진행해야 합니다.

1. **사전 준비 (Python)**:
   * 소나무봇에 Python 3.12 설치 혹은 `uv`를 통한 Python 런타임 활성화 수행 (`uv python install 3.12` 등).
2. **Repo 배포**:
   * 로컬에서 `scp -r`을 통해 `fable-lite` 코드를 넘기거나, 소나무봇에서 `git clone`을 통해 리포지토리 구성.
3. **어댑터 적용**:
   * **Antigravity (agy)**: 메인 `hooks.json` 훼손을 막기 위해, 배포된 `fable-lite` 디렉토리 안에 `.omg/state/hooks.json`을 구성해 로컬(격리) 프로파일로 훅을 등록.
   * **Codex**: 프로젝트 루트에 `.codex/hooks.json`을 구성하여 훅 주입.
4. **검증**:
   * `pytest`를 활용하여 원격지에서 어댑터 및 코어 로직이 정상 작동하는지 확인 (`python -m pytest tests/`).

## 4. 리스크 및 주의사항
* **세션 설정 훼손 위험**: 소나무봇은 분산 워커 노트북이므로 글로벌 `hooks.json`을 수정하면 다른 진행 중인 작업에 치명적인 영향을 줍니다. 반드시 프로젝트 로컬 기반 훅(isolated profile)을 사용해야 합니다.
* **데이터 회수(Bundle/SCP)**: 원격에서 A/B 테스트나 eval 평가를 돌린 후, 생성된 결과물(`eval/ab/*` 등)을 로컬로 다시 가져오기 위한 스크립트나 회수(scp) 루틴이 필수적입니다.
