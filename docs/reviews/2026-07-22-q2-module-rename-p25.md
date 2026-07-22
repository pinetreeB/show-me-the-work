# P2.5 미션모드 심사 기록 — v2.6 Q2 모듈명 통일 (2026-07-22)

> 대상 스펙: `docs/specs/v2.6-q2-module-rename.md` · 심사자: codex@영진(gpt-5.6-sol high, 격리 재현 실험 동반) + agy(Gemini 3.6 Flash High) + 좌상 Claude(중재·게이트)

## 라운드 요약
- **1R**: codex 58 REJECT(critical 4: CI canonical 미검증·R3 클린 게이트 부재·혼재 설치 버전 오출력 실측·-W error 양립 불가) / agy 88 REJECT(critical 2: shim 템플릿 미고정·filterwarnings 부재) → rev2 반영.
- **R1 중재(좌상)**: shim 방식은 codex 격리 실측(identity·reload·-m·find_spec 성공) 채택 — 중앙 sys.modules 별칭+eager 등록. agy 가설A는 eager 등록으로 반증, 물리 재수출안은 이중 초기화(게이밍 표면) 근거로 양측 합의 기각.
- **2R**: agy FINAL APPROVE(critical 0, high 1=adapters replay 게이트) / codex 잔여 critical 1(**filterwarnings 모듈 패턴이 stacklevel=2 귀속과 불일치 — 재현으로 무효 입증**) + high 4(혼재 비지원 명시·clean venv 구체화·inventory 제외 명시·심사 추적성) → rev3에서 메시지 기반 필터로 교체 + 전건 반영.
- **3R (최종 PASS)**: codex **98 · 잔여 critical 없음**(메시지 필터 정상 매칭·PYTHONWARNINGS=error 승격 양방향 재현, "§4-8은 pytest가 child Python subprocess 실행 방식이어야 함 — pytest 자체 strict 재실행 금지" 주의 부착) / agy **97 · FINAL APPROVE 유지** / 좌상 96. **가중평균 ≥95 AND critical 0 → 하드게이트 통과, 구현 착수 승인.** 점수표=`tmp/q2-rename/mission-scores.md`(임시)·본 문서가 영속 기록.

## 원문 1 — agy 1R 심사 전문
# v2.6 Q2 모듈명 통일 스펙 (`v2.6-q2-module-rename.md`) P2.5 미션모드 적대 심사 보고서

- **심사 대상**: [v2.6-q2-module-rename.md](file:///C:/Users/rotat/fable-lite/docs/specs/v2.6-q2-module-rename.md)
- **심사자**: wmux 좌하 리뷰분석 담당 (Antigravity `agy`, Gemini 3.6 Flash High)
- **심사 일시**: 2026-07-22
- **판정 점수**: **88점** / 100점 (미션모드 하드게이트 95점 미달 — **Critical 2건 적발**)

---

## 1. 종합 심사 총평

본 스펙은 v2.5.0으로 완료된 Q1(디렉토리 `.smtw/`), Q3(env `SMTW_*`), Q4(config `.smtw.toml`)의 안정화 토대 위에서 파이썬 모듈 표준명(`smtw`)을 통일하고 하위호환 shim (`fable_lite`)을 신설하기 위한 구현 가이드입니다. 

그러나 파이썬 `import` 메커니즘(`find_spec`, `runpy`)의 디스크 탐색 방식과 경고(Warning) 승격 환경, 그리고 모듈 전역 상태(Singleton/State Lock) 관점에서 **서브모듈 직접 import 시 `ModuleNotFoundError` 발생**, **모듈 전역 상태 이중 초기화로 인한 게이트 락 감독 무력화 리스크**, **DeprecationWarning의 pytest 게이트 파괴** 등 치명적 결함 2건이 적발되어 현 스펙 그대로 구현 착수 시 미션모드 하드게이트(품질 95점, Critical 0건)를 통과할 수 없습니다.

---

## 2. Critical 치명 결함 목록 (2건)

### 🚨 [Critical-01] `sys.modules` 별칭 권장(물리 파일 미생성) 시 서브모듈 직접 import 파괴 및 물리 shim 작성 시 모듈 전역 상태 이중 초기화 (State Double-Initialization Gaming)

- **위험도**: **Critical**
- **관련 항목**: 스펙 §2-2 ("sys.modules 별칭 등록 방식 권장(물리 파일 N개 복제 금지)"), §5 R1
- **증거 및 가설검증 (경쟁 가설 3개)**:
  - **가설 A (`sys.modules` 별칭만 런타임 등록)**: `fable_lite/__init__.py`에서 `sys.modules[\'fable_lite.cli\'] = smtw.cli`를 바인딩함.
    - **실측/근거**: 파이썬 `import` 문은 `import fable_lite`를 거치지 않고 타 코드/구버전 어댑터가 `from fable_lite.cli import main` 또는 `import fable_lite.check`를 직접 호출할 때, `sys.modules` 탐색 전 파이썬 Finder가 디스크의 `fable_lite/` 디렉터리 내에 `cli.py` 또는 `check.py` 파일이 존재하는지 검사합니다. 디스크에 해당 물리 파일이 없으면 `sys.modules`에 등록되기도 전에 **`ModuleNotFoundError: No module named \'fable_lite.cli\'`**가 발생하여 구버전 훅/어댑터가 즉시 불능 상태에 빠집니다. 또한 `python -m fable_lite.check` 실행 역시 디스크 내 파일 존재성 검사를 통과하지 못합니다.
  - **가설 B (물리 재수출 파일 `from smtw.sub import *` 배치)**: 디스크에 `fable_lite/cli.py`, `fable_lite/check.py` 등을 물리 파일로 둡니다.
    - **실측/근거**: `fable_lite.cli`와 `smtw.cli`가 파이썬 인터프리터 메모리 내에서 **서로 다른 2개의 독립 모듈 객체**로 로드됩니다. 그 결과 `core/` 및 `smtw/` 모듈에 존재하는 singleton 카운터, lock 객체, cache registry, `_BOOTSTRAP_STATE` 등의 전역 변수가 이중으로 초기화됩니다. 한쪽(`fable_lite`)에서 state lock을 획득해도 타방(`smtw`)에서는 락을 인식하지 못해 **게이트 락 무력화 및 race condition, verification epoch 분리 등 감독 우회(Gaming) 표면**이 크게 열립니다.
  - **가설 C (물리 Shim + `sys.modules` Canonical 바인딩 템플릿 - 채택 필수)**:
    - `fable_lite/` 패키지 디렉토리에 각 모듈별 2줄짜리 물리 shim 파일(`fable_lite/check.py` 등)을 배치하되, 파일 최상단에서 `import smtw.check; sys.modules[\'fable_lite.check\'] = smtw.check` 구문을 실행하여 파이썬 import finder를 만족시킴과 동시에 메모리 상 모듈 객체를 `smtw` canonical 객체 1개로 완벽히 일치시킵니다.
- **수정 요구사항**: 스펙 §2-2의 "sys.modules 별칭 등록 방식 권장(물리 파일 N개 복제 금지)" 문구를 철회하고, **"모듈 객체 단일화 물리 Shim 템플릿 규약"**을 스펙에 명시해야 함.

---

### 🚨 [Critical-02] `DeprecationWarning`발행에 따른 CI/게이트 파괴 및 `-W error` 환경 거부

- **위험도**: **Critical**
- **관련 항목**: 스펙 §2-2, §4 수용기준 5 ("pytest 전체 green"), §5 R4
- **증거 및 가설검증 (경쟁 가설 3개)**:
  - **가설 A (경고 발행 시 pytest `-W error` 파괴)**:
    - **실측/근거**: 스펙은 `import fable_lite` 시 DeprecationWarning을 1회 발행하도록 규정하였습니다. 그러나 [pyproject.toml](file:///C:/Users/rotat/fable-lite/pyproject.toml#L25-L27) 및 pytest configuration에 `filterwarnings` ignore 설정이 수록되어 있지 않습니다. 타 에이전트나 사용자/CI 환경이 `pytest -W error` 또는 `PYTHONWARNINGS=error` 상태로 수용기준 5번 게이트(`pytest`)를 실행하거나 shim 테스트를 수행할 경우, `DeprecationWarning`이 즉시 처리되지 않은 예외(Uncaught Exception)로 승격되어 전체 CI/게이트가 Red(실패)로 파괴됩니다.
  - **가설 B (stdout/stderr 계약 오염)**:
    - **실측/근거**: 훅 어댑터(`adapters/claude_code/user_prompt_submit.py` 등)는 stdout JSON을 주고 받습니다. `warnings.warn()`은 `sys.stderr`로 흘러가므로 standard stdout JSON 파싱은 무사하나, stderr와 stdout을 병합 파싱하는 구버전 래퍼나 `eval/design_e2e.py` 등의 서브프로세스 래퍼에서 `json.JSONDecodeError`가 발생할 수 있습니다.
  - **가설 C (1회/프로세스 제어 미흡으로 스팸 발생)**:
    - `warnings.warn(..., category=DeprecationWarning, stacklevel=2)`을 적절한 `stacklevel` 없이 호출 시 경고 주체가 호출자가 아닌 shim 내부로 찍혀 정답 위치 추적이 불가능해집니다.
- **수정 요구사항**: `pyproject.toml` [tool.pytest.ini_options]에 `filterwarnings = [\"ignore::DeprecationWarning:fable_lite.*\"]` 항목을 추가하도록 스펙 §2-3 및 §4 수용기준 5에 명시해야 함.

---

## 3. High 이하 권고사항 (4건)

### ⚠️ [High-01] R2 적대검증: 구버전 플러그인/어댑터의 `-m fable_lite` 호출과 fail-closed 마비 여부

- **심사 결과**: **안전 (Fail-closed 마비 없음)**
- **분석 내용**:
  스펙대로 `fable_lite/__main__.py` 물리 파일이 유지되고 `smtw.cli:main`을 포워딩할 경우, 구버전 플러그인 캐시나 훅이 `-m fable_lite`를 실행하더라도 실행 엔진은 신버전 `smtw` 코어를 그대로 거치게 됩니다. v2.5.0에서 통일된 [core/state_layout.py](file:///C:/Users/rotat/fable-lite/core/state_layout.py#L90-L103)의 `state_dir()` Facade가 `.smtw/` 및 `.fable-lite/` 상태 경로를 원자적으로 판별하므로 R2 차단, Provenance 스냅샷 검사, Verification Epoch 게이트는 원본과 동일하게 **fail-closed**로 완벽히 가동됩니다.

---

### ⚠️ [High-02] R3 패키징 빌드: `fable_lite.egg-info/` 및 stale `__pycache__` 잔존물 클린 절차 명시 누락

- **위험도**: **High**
- **관련 항목**: 스펙 §5 R3, §6 구현 웨이브 W1
- **분석 내용**:
  v2.5.0 릴리스 당시 `fable_lite.egg-info/` 잔류로 인해 로컬 packaging test가 1fail 되었던 전례가 존재합니다. `git mv fable_lite smtw` 완료 후에도 디스크에 `fable_lite/__pycache__/` 및 구버전 `.egg-info`가 남아있으면 파이썬 인터프리터가 stale `.pyc`를 우선 로드하여 `smtw` 포워딩이 제대로 동작하지 않는 **Ghost Module Import (스펙터 모듈 로드)** 현상이 일어납니다.
- **권고**: §6 W1 시작 직전에 `rm -rf build/ dist/ *.egg-info fable_lite/__pycache__ smtw/__pycache__` 사전 클린 절차를 필수 명령으로 명시할 것.

---

### ⚠️ [Medium-01] `fable-lite-cli.py` 파서 프로그램명(`prog`) 불일치로 인한 어댑터 힌트 파싱 미스

- **위험도**: **Medium**
- **관련 항목**: 스펙 §2-4, §2-6 (`fable-lite-cli.py`)
- **분석 내용**:
  [fable-lite-cli.py](file:///C:/Users/rotat/fable-lite/fable-lite-cli.py#L12)는 레거시 훅들의 절대경로 실행 계약입니다. `fable-lite-cli.py` 내부에서 `smtw.cli`를 호출할 경우 [cli.py](file:///C:/Users/rotat/fable-lite/fable_lite/cli.py#L40)의 `prog=\"smtw\"` 로 변경됨에 따라 도움말 및 에러 메시지가 `[smtw] ...`로 출력됩니다.
  만약 구버전 어댑터나 훅의 정규식이 `fable_lite check`라는 문자열만 고정하여 파싱할 경우, 가이드 메시지 치환이 동작하지 않을 가능성이 있습니다.
- **권고**: CLI 안내 문구 통합 시 구버전 어댑터 정규식 패턴과 호환되는지 `adapters/` E2E replay 테스트를 W3 게이트에 포함할 것.

---

### ⚠️ [Low-01] Windows (영진 PC) `git mv` 실행 시 대소문자 구별 및 NTFS 장경로 주의

- **위험도**: **Low**
- **관련 항목**: 스펙 §5 R5
- **분석 내용**:
  Windows 환경에서 `git mv fable_lite smtw` 실행 자체는 이름이 완전히 다르므로 case-only rename 문제는 발생하지 않으나, 이동 직후 열려있는 프로세스(IDE, pytest watcher)가 기존 `fable_lite` 디렉토리 핸들을 쥐고 있을 경우 Windows File Lock(`PermissionError`)으로 git index 오염이 발생할 수 있습니다.
- **권고**: `git mv` 작업 전 모든 백그라운드 pytest/python 프로세스를 수동 종료 후 진행할 것.
## 원문 2 — codex 1R 심사 전문
# Q2 모듈명 통일 스펙 적대 심사

## 판정

점수: **58/100 — REJECT**

95점·치명결함 0 하드게이트를 통과하지 못합니다. 구현 착수 전 스펙 보강이 필요합니다.

## Critical 결함

1. **CI가 canonical `smtw`를 검증하지 않음**

   스펙의 수정 표면에서 CI 파일이 빠졌습니다. 현재 두 워크플로 모두 Ruff·compileall 대상으로 `fable_lite`만 지정하며, wheel smoke도 구 모듈만 실행합니다.

   - `.github/workflows/ci.yml:31`
   - `.github/workflows/release-quality.yml:38`

   이대로면 shim만 검사하고 깨진 `smtw` wheel도 CI green이 가능합니다. 두 모듈 import, 두 console script, 서브모듈 동일 객체까지 설치 wheel에서 검사해야 합니다.

2. **R3 정리 범위가 불완전해 stale 파일이 wheel에 섞일 수 있음**

   현재 실제 상태:

   - `fable_lite.egg-info/entry_points.txt`는 여전히 `fable_lite.cli:main`
   - `SOURCES.txt`는 구 패키지 전체를 열거
   - `build/lib/fable_lite/`에도 구 구현 모듈 전체가 잔존
   - 둘 다 `.gitignore` 대상이라 clean Git 상태로는 탐지되지 않음

   배포판 이름을 `fable-lite`로 유지하므로 `fable_lite.egg-info`라는 디렉터리명 자체는 정상이며 빌드 때 다시 생깁니다. 따라서 단순 삭제가 아니라 `build/`, `dist/`, egg-info를 명시적으로 정리한 뒤 재생성하고 wheel `RECORD`를 검사해야 합니다. 설치 wheel에서도 `fable_lite.cli is smtw.cli`를 검사해야 stale 물리 모듈 혼입을 잡습니다.

3. **혼재 설치에서 이미 테스트가 실패하며 shim으로 흡수되지 않음**

   `fable_lite/cli.py:16`은 실행 중인 소스가 아니라 설치된 배포판 메타데이터 `version("fable-lite")`를 우선합니다. 현재 서버에서:

   - 로드 모듈: 현재 저장소 `fable_lite/__init__.py`
   - 설치 메타데이터: `2.4.1`
   - 소스 버전: `2.5.0`
   - 관련 pytest: **55 passed, 1 failed**
   - 실패: `python -m fable_lite version`이 `2.5.0` 대신 `2.4.1` 출력

   즉 R2의 “구 설치본과 신 저장소 혼재를 shim이 전부 흡수”한다는 가정은 거짓입니다. clean venv를 구현 전제·게이트로 고정하거나, 소스 체크아웃 실행 시 버전 SSOT를 판별하는 정책이 필요합니다.

4. **“breaking 0”과 `DeprecationWarning` 강제 발생이 `-W error`에서 양립하지 않음**

   격리 재현에서 sys.modules shim은 일반 import·`-m` 실행에 성공했지만, `python -W error::DeprecationWarning -c "import fable_lite"`는 즉시 종료코드 1이었습니다. “모든 기존 호출 동작 불변”을 유지할지, strict-warning 사용자는 호환 대상에서 제외할지 스펙이 결정해야 합니다.

## R1 권고안

**`sys.modules` 중앙 별칭 방식을 채택하고 단순 물리 재수출은 기각**하는 것이 맞습니다.

권장 구조:

- 실제 구현은 `smtw/` 한 벌만 유지
- `fable_lite/__init__.py` 하나가 프로세스당 1회 경고
- `smtw` 및 11개 현재 서브모듈을 canonical 이름으로 import
- `sys.modules["fable_lite.<name>"]`에 동일 객체 등록
- 마지막에 `sys.modules["fable_lite"] = smtw`
- `__main__`은 eager alias 목록에서 제외해 runpy 중복 경고 방지
- 서브모듈 목록과 실제 `smtw/*.py` inventory가 일치하는 회귀 테스트 추가

권고 shim 템플릿:

```python
from __future__ import annotations

import importlib
import sys
import warnings

import smtw as _smtw

_SUBMODULES = (
    "brief",
    "card",
    "check",
    "check_support",
    "cli",
    "design_check",
    "intent",
    "migrate",
    "quarantine",
    "scorecard",
    "scorecard_observations",
)

if not getattr(_smtw, "_fable_lite_deprecation_warned", False):
    warnings.warn(
        "fable_lite is deprecated; import smtw instead",
        DeprecationWarning,
        stacklevel=2,
    )
    _smtw._fable_lite_deprecation_warned = True

for _name in _SUBMODULES:
    _module = importlib.import_module(f"smtw.{_name}")
    sys.modules[f"fable_lite.{_name}"] = _module

sys.modules[__name__] = _smtw
```

이 템플릿은 방향을 고정하기 위한 것이며, `_SUBMODULES` inventory 검증과 경고 정책 결정을 동반해야 합니다. `__main__`은 eager alias하지 않습니다.

격리 실험에서는 다음이 확인됐습니다.

- `fable_lite is smtw`: 성공
- `fable_lite.cli is smtw.cli`: 성공
- `importlib.reload(fable_lite.cli)` 후 동일 객체: 성공
- `python -m fable_lite`: canonical `__main__` 실행 성공
- `find_spec("fable_lite.cli").name`: `smtw.cli` 반환
- `-W error`: 실패

`from smtw.cli import *` 형태의 물리 wrapper는 별도 module 객체를 만들므로 수용기준의 동일 객체 조건을 위반합니다. 각 wrapper가 다시 `sys.modules`를 덮는 방식은 결국 분산된 alias 구현일 뿐이며 파일 수·drift만 증가합니다.

추가 R1 테스트가 필요합니다.

- 구 버전에서 만든 pickle fixture 역직렬화
- Windows `multiprocessing` spawn 자식에서 양쪽 import identity
- reload 후 양쪽 `sys.modules` identity
- package + 복수 서브모듈 import 시 경고 정확히 1회
- stdout JSON 무오염
- 설치 wheel 밖 디렉터리에서 `-P -m smtw`와 `-P -m fable_lite`

## R3 판정

**FAIL — 위험은 인지했으나 실행 절차와 hard gate가 없음.**

정리 절차를 W3와 수용기준에 명시해야 합니다.

- 정확히 검증된 저장소 경로의 `build/`, `dist/`, `fable_lite.egg-info/`만 정리
- clean build
- wheel 내부 파일 목록 검사
- `smtw/**` 전체와 최소 shim만 존재하는지 확인
- egg-info `entry_points.txt`가 양쪽 모두 `smtw.cli:main`인지 확인
- 설치 wheel에서 최상위·모든 서브모듈 identity 확인

## R5 판정

**Low risk / 절차 보강 필요.**

실제 이동 대상은 ASCII 이름의 추적 파일 13개이고 저장소 경로도 짧아 장경로·cp949가 핵심 장애는 아닙니다. 다만 `git mv`가 rename history를 별도로 저장하는 것은 아니므로 대량 내용 수정과 분리한 move-only 커밋이 유리합니다.

Windows Git Bash 기준 권고:

```bash
cd /c/Users/gustj/fable-lite-dev
git status --short
git mv -- fable_lite smtw
git status --short
```

그다음 move-only 커밋 후 import/string 변경과 shim을 별도 커밋으로 두는 편이 rename 추론과 리뷰에 안전합니다. 실제 `git mv --dry-run`은 하네스가 파괴 가능 명령으로 차단해 실행되지 않았습니다.

## 전수 grep 결과와 누락 표면

추적 파일 전체에서 `fable_lite`는 **175줄/65파일**입니다. 비범위인 `docs/design/**`, `docs/reviews/**`를 제외하면 **67줄/36파일**입니다.

| 분류 | 줄/파일 | 판정 |
|---|---:|---|
| CI workflows | 6/2 | 스펙 누락, 반드시 수정 |
| 런타임 source·launcher | 3/3 | `brief.py`, `cli.py` 누락 |
| adapters/core/packs/design eval | 14/10 | 대체로 스펙에 포함 |
| probe 출력 schema | 1/1 | 맹목 교체 금지, 계약 결정 필요 |
| pyproject | 3/1 | 포함 |
| tests | 31/17 | “17개 import 파일”이라는 설명은 부정확 |
| README.ko/CHANGELOG | 9/2 | 역사 항목과 신규 안내 분리 필요 |

구체적인 누락:

- `.github/workflows/ci.yml:31`: Ruff, compileall, wheel smoke
- `.github/workflows/release-quality.yml:38`: 동일
- `fable_lite/brief.py:60`: 사용자에게 구 명령을 생성
- `fable_lite/cli.py:40`: `ArgumentParser(prog="fable_lite")`
- `eval/run_probes.py:197`: 출력 JSON 키 `"fable_lite"`—동일 출력 계약이면 보존해야 함

테스트 17개는 모두 import 파일이 아닙니다. 그 안에는 subprocess 호출, 패키징 기대값, 스캔 루트, task-card fixture, probe JSON schema가 섞여 있습니다. 특히 `tests/test_runtime_env.py:34`와 `tests/test_state_consumer_ssot.py:46`의 production root를 `smtw`로 바꾸지 않으면 canonical 코드를 검사하지 않습니다. 반대로 probe 키와 일부 legacy 호출은 호환성 검증용으로 남겨야 합니다.

## High 이하 권고

- **High:** CLI help의 `prog` 정책을 결정하십시오. canonical 호출은 `smtw`를 보여야 하지만 구 호출의 “동일 출력”과 충돌합니다.
- **High:** 버전 순서를 `.claude-plugin/plugin.json` → CHANGELOG → `sync_version.py`로 고정하십시오. 실제 스크립트는 plugin manifest를 SSOT로 사용해 marketplace, pyproject, README 2종을 갱신합니다.
- **High:** 기존 테스트를 canonical 테스트와 legacy shim 테스트로 명시적으로 분리하십시오.
- **Medium:** `eval/run_probes.py`의 `"fable_lite"` 키는 공개 결과 schema인지 내부 이름인지 결정 후 변경하십시오.
- **Medium:** `tests/test_fable_lite_cli.py` 파일명은 호환 테스트로 유지하거나 canonical/legacy 파일로 분리하십시오.

검증 증거: 관련 pytest **55 passed, 1 failed**, `sync_version.py --check` 성공, 추적 작업 트리 clean. 구현·커밋은 없으며 하네스 차단 과정에서 ignored `.fable-lite/quarantine/` 기록만 자동 생성됐습니다. 심사 소요는 약 7분 43초입니다.
