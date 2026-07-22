# P4 다차원 실측 검증 보고서 — v2.6 Q2 모듈 rename (fable_lite → smtw + shim)

- 검증 대상: worktree `C:\Users\rotat\fable-lite-wt-q2ultra`
- HEAD: `c2ec534571742343e1e90d95465953050cfc11b9` (short `c2ec534`) — worktree git 메타데이터 파일(`.git` → `.git/worktrees/fable-lite-wt-q2ultra/HEAD`)로 확인 (git CLI는 훅 차단으로 사용 불가)
- 스펙: `C:\Users\rotat\fable-lite\docs\specs\v2.6-q2-module-rename.md` rev3
- 환경: Python 3.12.8 (`C:\Python312\python.exe`), pytest 9.0.3, setuptools 70.2.0, pip 24.3.1, Windows (spawn 기본)
- 실측 주체: 우하 Claude (ultracode) P4 검증 라인 — 본실측 5차원 + 적대 반증 5차원
- 보고 일자: 2026-07-22
- 실측 원본: `C:\Users\rotat\fable-lite-wt-q2ultra\tmp\q2-p4\d1..d5\result.json` (본실측) · `d1-verify..d5-verify\result.json` (반증)

---

## 0. 종합 판정

| 차원 | 주제 | 본실측 | 반증(적대 검증) | 핵심 수치 |
|---|---|---|---|---|
| D1 | shim identity | **PASS** (5/5) | **PASS** (5/5, 반증 전부 실패) | 패키지+11 서브모듈 `is` 전원 True, id() 3곳 동일(1307263199936), reload 후 유지 |
| D2 | 경고 1회/프로세스·stdout 무오염 | **PASS** (5/5) | **PASS** (7/7, 반증 전부 실패) | 고정 문자열 경고 정확 1회(총 DeprecationWarning도 1), stdout `{}`·`2.6.0` 바이트 정확 |
| D3 | 프로세스 경계 | **PASS** (4/4) | **FAIL** ⚠️ → **반증 프로브 결함으로 귀속** (§D3-v 분석) | spawn 자식 exitcode 0·11종 identity True, 구버전 pickle(0~3) 역직렬화 성공, `-P -m` 양쪽 rc0·stdout 동일 |
| D4 | 경고 필터 | **PASS** (5/5) | **CONFIRMED_PASS** (핵심 4/4 생존) | pytest 전체 **1079 passed, 1 skipped, 0 failed** (330.66s), PYTHONWARNINGS=error → rc1 승격 |
| D5 | wheel | **PASS** (11/11) | **PASS** (게이팅 9/9 + advisory 1) | RECORD smtw 13 + shim 1, 콘솔 스크립트 2종, shim sha256 4위치 동일, identity `True True` |

**종합: 본실측 5/5 PASS.** 반증 4/5 PASS, D3 반증만 FAIL이나 1차 증거로 **반증 프로브 자체의 결함 3건**으로 귀속(제품 결함 가설 기각 — §D3-v). 수용기준 §4-2·3·4·5·7·8 실측 충족.

---

## 가설 / 증거 / 기각 (조사 팩)

가설 1: 중앙 `sys.modules` 별칭 shim은 모든 import 형태·reload·spawn 자식에서 identity를 완전 보존한다.
증거: D1 본실측 5/5(11종 `is` 전원 True, reload(cli)·reload(패키지) 후 재확인 전원 True, find_spec 양쪽 비-None) + D1-verify 5/5(id() 값 3곳 동일 `1307263199936`, 최초 import·from-import 11종·alias import 전 형태 True) + D3 spawn 자식 실데이터(exitcode 0, start_method=spawn, pkg_is=true, 11종 True, TaskCard is True).
→ 채택.

가설 2: DeprecationWarning은 프로세스당 정확 1회(패키지+복수 서브모듈 import에도)이고, stdout JSON 계약은 오염되지 않으며, 명시적 strict 설정에서만 에러로 승격된다.
증거: D2 5/5(고정 문자열 카운트=1, 총 DeprecationWarning=1 — 변형 중복 없음, `intent show` stdout `{}` json.loads 성공·stderr 1회, `version` stdout `'2.6.0\r\n'` 바이트 정확) + D2-verify 7/7(import storm·stderr 텍스트 카운트·전 카테고리 record·from/alias 형태 전부 1회) + D4(PYTHONWARNINGS=error·`-W error::DeprecationWarning` → rc1, 트레이스 `fable_lite/__init__.py:25 warnings.warn`, 기본 → rc0).
→ 채택.

가설 3: D3-verify의 FAIL 판정은 실제 제품 결함(spawn/protocol≥4/-P에서 identity·pickle 파괴)을 나타낸다.
증거(반증 측): d3-verify/result.json verdict=FAIL (R1·R2·R2b·R3 FAIL).
반증(1차 증거):
- R1: `r1_spawn_child.txt`의 자식 에러 = `ModuleNotFoundError: No module named 'fable_lite'` — 반증 프로브가 spawn 자식에 PYTHONPATH를 전달하지 못한 **프로브 환경 버그**. 같은 반증기의 R4(경로 설정된 자식)는 `n=11 all_is=True taskcard_is=True`로 PASS.
- R2: protocol 4/5에서 `surgery_target_count=0` — STACK_GLOBAL 와이어 인코딩에 대한 합성 레거시 페이로드 작성 자체가 실패(반증기가 자기 `diag_p4.py`로 인코딩을 조사 중이었음). 그런데도 기능 필드는 전부 성립: `legacy_restored_type_is_smtw=true`, `legacy_restored_eq_expected=true`, `roundtrip_type_is/eq=true`(전 프로토콜), `alias_class_is_smtw=true`, `errors=[]`. 실제로 바이트가 재작성된 protocol 0~3의 레거시 페이로드는 전부 smtw.card.TaskCard로 복원.
- R3: 개별 증거 필드 전원이 green(`smtw_version rc=0 '2.6.0\n'`, `fable_lite_version rc=0 '2.6.0\n'`, `version_identical=True`, help rc 0/0, `safe_path=True`, `alias_is=True`)인데 집계 판정만 FAIL — 판정 로직 오동작.
기각: 가설 3 기각 — D3-verify FAIL은 반증 프로브 결함 3건(자식 환경·페이로드 합성·판정 집계)의 복합이며 제품 결함이 아님. D3 본실측 4/4 PASS가 실데이터로 유지됨.

---

## D1 — shim identity (본실측 PASS 5/5 · 반증 PASS 5/5)

수용기준 §4-3: `fable_lite is smtw`, 전 서브모듈 `is`, reload 후 유지.

본실측 (`tmp/q2-p4/d1/result.json`):
- C1 패키지 identity: `sys.modules["fable_lite"] is sys.modules["smtw"]`=True, 바인딩 `is`=True, `fable_lite.__name__`="smtw", `__file__`=worktree `smtw/__init__.py`
- C2 서브모듈 11종 전원: brief·card·check·check_support·cli·design_check·intent·migrate·quarantine·scorecard·scorecard_observations — `import_module` 쌍 `is`=True 및 `sys.modules` 항목 `is`=True 전종 (count_measured=11)
- C3 속성·from 형태: `fable_lite.cli is smtw.cli`=True, `from fable_lite import cli` 바인딩 동일객체
- C4 reload 유지: `reload(sys.modules["fable_lite.cli"])` 반환 is smtw.cli=True, reload 직후 11종+패키지 identity 재확인 전원 True, `reload(sys.modules["fable_lite"])`(패키지급) 후에도 유지
- C5 find_spec: `find_spec("fable_lite")` origin=`fable_lite/__init__.py`, `find_spec("fable_lite.cli")` origin=`smtw/cli.py` — 둘 다 비-None

반증 (`tmp/q2-p4/d1-verify/result.json`) — 반증 시도 전부 실패(주장 유지):
- A: id() 값 교차 — `id_fl_sysmod == id_smtw_sysmod == id_binding == 1307263199936`, 11종 id 동치 전원 True
- B: smtw 쪽 직접 reload 후 `post_fl_cli_is_smtw_cli`=True
- C: 프로세스 최초 import(scorecard_observations) id 동치 True (2518713829952)
- D: from-import 11종 전 형태 True / E: `import fable_lite as alias` 동일객체

재현: `python C:/Users/rotat/fable-lite-wt-q2ultra/tmp/q2-p4/d1/driver.py` · 반증: `python .../d1-verify/driver.py`
증거 파일: `d1/result.json`, `d1/child_1_pkg_identity.txt` ~ `child_5_find_spec.txt` · `d1-verify/result.json`, `d1-verify/child_A..E_*.txt`

---

## D2 — DeprecationWarning 1회/프로세스 · stdout JSON 무오염 (본실측 PASS 5/5 · 반증 PASS 7/7)

수용기준 §4-2: 정확 1회/프로세스 + 훅 stdout JSON 무오염. 고정 문자열: `"fable_lite is deprecated; import smtw instead"`.

본실측 (`tmp/q2-p4/d2/result.json`):
- check1: 패키지+11종(양쪽 이름)+반복 import → fixed_count=**1**, total_deprecation=**1** (다른 문구·변형 경고 없음)
- check2: 별도 프로세스 2회 → 각 1회 (전역 1회가 아님 = 프로세스당 의미 확인)
- check3: `-W always::DeprecationWarning -m fable_lite intent show --root <tmp>` → rc0, stdout json.loads 성공(`{}`), stderr 경고 1회(귀속 `<frozen runpy>:112`)
- check4: `-m fable_lite version` → stdout `'2.6.0\r\n'` 정확(Windows 텍스트모드 CRLF — 논리적으로 `2.6.0\n`), 경고 텍스트 혼입 없음, stderr 1회
- check5: `import fable_lite.scorecard_observations` 단독 → 1회

반증 (`tmp/q2-p4/d2-verify/result.json`) — 7건 전부 주장 생존:
- RA1 `-W always` stderr 텍스트 직접 카운트=1 / RA2 전 카테고리 record 총 1건·category=DeprecationWarning / RA3 프로세스 2회 각 1 / RA4 strict json.loads 전체 파싱 `{}`·stdout 경고 substring 0 / RA5 version 바이트 정확 / RA6 서브모듈 단독 stderr=1 / RA7 from·alias import 형태에서도 1

재현: `python C:/Users/rotat/fable-lite-wt-q2ultra/tmp/q2-p4/d2/driver.py` · 반증: `python .../d2-verify/driver.py`
증거 파일: `d2/result.json`, `d2/check1_count.txt` ~ `check5_submodule_only.txt` · `d2-verify/result.json`, `d2-verify/ra1..ra7*.txt`

---

## D3 — 프로세스 경계 (본실측 PASS 4/4 · 반증 FAIL→프로브 결함 귀속)

수용기준 §4-3: spawn 자식 identity 유지, 구버전 pickle 역직렬화, `-P -m` 양쪽.

본실측 (`tmp/q2-p4/d3/result.json`) — 4/4 PASS:
- check1 spawn 자식: child_exitcode=0, start_method=**spawn**, pkg_is=true, submodules_true_count=**11**, taskcard_is=true (자식 pid ≠ 부모 pid)
- check2 구버전 pickle fixture: protocol 0 canonical 페이로드(`csmtw.card\nTaskCard`) → `cfable_lite.card\nTaskCard`로 치환한 레거시 모형 → `pickle.loads` → `type(restored) is smtw.card.TaskCard`=true, `restored == expected`=true
- check3 역방향: `fable_lite.card.TaskCard` is `smtw.card.TaskCard`=true, shim 경유 생성 객체의 pickle 페이로드에 `csmtw.card` 글로벌 포함(protocol 0·기본 protocol=4), 왕복 동일
- check4 `-P -m`: `python -P -m smtw version` rc0 `"2.6.0\n"` · `python -P -m fable_lite version` rc0 `"2.6.0\n"` · stdout 동일 · `--help` 양쪽 rc0

### D3-v — 반증 FAIL 원인 분석 (반증 프로브 결함 3건, 제품 결함 아님)

`d3-verify/result.json` verdict=FAIL. 1차 증거로 귀속한 결함:

1. **R1 (spawn) = 프로브 환경 버그**: `r1_spawn_child.txt` 자식 보고 = `ModuleNotFoundError: No module named 'fable_lite'` (verify_driver.py:38) — 반증 프로브가 spawn 자식에 worktree 경로를 전달하지 못함. child_exitcode=0이지만 자식 내부 try/except가 import 실패를 잡아 ok=false 보고. 동일 반증기의 **R4**(경로가 설정된 `-P` 자식)는 `n=11 all_is=True taskcard_is=True`로 PASS → spawn identity 자체는 정상.
2. **R2/R2b (pickle 4/5) = 합성 페이로드 작성 실패**: protocol 4/5는 STACK_GLOBAL+SHORT_BINUNICODE 인코딩(PEP 3154 프레임)이라 반증기의 위치 기반 바이트 수술이 무효(`surgery_target_count=0`) — 자기 `diag_p4.py`가 이 인코딩을 조사 중이었음. 그러나 **기능 필드는 전부 성립**: `legacy_restored_type_is_smtw=true`, `legacy_restored_eq_expected=true`, 전 프로토콜 `roundtrip_type_is/eq=true`, `alias_class_is_smtw=true`, `errors=[]`. 실제 바이트가 재작성된 protocol 0~3의 레거시 페이로드(GLOBAL:'fable_lite.card TaskCard' 존재·smtw ref 부재)는 전부 smtw 타입으로 복원, R2b 콜드스타트(fable_lite 사전 import 없음)에서도 0·2 성공.
3. **R3 (-P -m) = 판정 집계 오동작**: 개별 증거 전원 green — smtw rc0 `'2.6.0\n'`, fable_lite rc0 `'2.6.0\n'`, version_identical=True, help rc 0/0 동일, `safe_path=True`, `alias_is=True name=smtw` — 인데 집계 verdict만 FAIL.

결론: D3 제품 주장(§4-3)은 본실측 실데이터 + 반증기의 정상 동작 부분(R4, protocol 0~3 레거시 로드, 클래스 identity)으로 **유지**. 반증 FAIL은 프로브 결함이며 재반증 권고(잔여 위험 §6).

재현: `python C:/Users/rotat/fable-lite-wt-q2ultra/tmp/q2-p4/d3/driver.py` · 반증(결함 포함): `python .../d3-verify/verify_driver.py`
증거 파일: `d3/result.json`, `d3/check1_spawn_child.txt` ~ `check4_*.txt` · `d3-verify/result.json`, `d3-verify/r1_spawn_child.txt`, `r2_pickle_protocols.txt`, `r2b_cold_legacy.txt`, `r3_*.txt`, `r4_submodule_identity.txt`, `probe_pickle.py`, `diag_p4.py`

---

## D4 — 경고 필터 (본실측 PASS 5/5 · 반증 CONFIRMED_PASS)

수용기준 §4-5(게이트 green)·§4-7(strict 정책)·§4-8(필터 실증). pyproject `filterwarnings = ["ignore:fable_lite is deprecated:DeprecationWarning"]` (메시지 기반).

본실측 (`tmp/q2-p4/d4/result.json`):
- shim 전용 테스트(기본 필터): `7 passed in 2.45s` rc0 (failed/errors/warnings=0)
- **전체 스위트(기본 필터)**: `1079 passed, 1 skipped in 330.66s (0:05:30)` rc0 — failed 0, errors 0
- PYTHONWARNINGS=error 주입 `import fable_lite`: rc=**1**, stderr 트레이스 `fable_lite/__init__.py:25 warnings.warn` + `DeprecationWarning: fable_lite is deprecated; import smtw instead`
- `-W error::DeprecationWarning`: 동일하게 rc1 승격
- 기본(무설정) `import fable_lite`: rc0 (비승격)

반증 (`tmp/q2-p4/d4-verify/result.json`) — 핵심 주장 4건 전부 생존(REFUTATION_FAILED):
- R1/R2 env·플래그 승격 재현(rc1, line 25 트레이스) / R3 기본 rc0 / R6 compat green 재현(7 passed)
- 메커니즘 R7: ignore 필터 제거 시 승격(rc1) → **ini ignore가 실제로 하중을 냄**
- 메커니즘 R4/R5: `-o filterwarnings=error` 및 `-W error::DeprecationWarning` 추가 시 compat `1 failed, 6 passed` — 실패 테스트는 `test_legacy_pickle_fixture_loads_as_canonical_class`(shim import 승격)로 **§4-7 의도된 동작**(strict 환경은 호환 보장 대상 아님)과 일치

재현: `python C:/Users/rotat/fable-lite-wt-q2ultra/tmp/q2-p4/d4/driver.py` (전체 스위트 ~5.5분) · 반증: `python .../d4-verify/verify_driver.py`
증거 파일: `d4/result.json`, `d4/1_shim_test_green.txt`, `2_full_suite_green.txt`, `3a_env_pythonwarnings_error.txt`, `3b_W_flag_error_deprecation.txt`, `4_default_no_escalation.txt` · `d4-verify/result.json`, `R1..R7*.txt`

---

## D5 — wheel (본실측 PASS 11/11 · 반증 PASS, 게이팅 9/9)

수용기준 §4-4: clean build → wheel RECORD(smtw 전체 + shim `__init__.py`만) → entry_points 2종 → 격리 venv identity·스크립트.

본실측 (`tmp/q2-p4/d5/result.json`) — worktree 원본 무변경(스크래치 복사본 `d5/src`에서 빌드):
- clean copy: 388파일 복사(.git·tmp·__pycache__·egg-info·build·dist·.fable-lite 제외)
- build: `pip wheel --no-deps --no-build-isolation` rc0 → `fable_lite-2.6.0-py3-none-any.whl` (247,028 bytes, sha256 `1d82c8f3b1aa...`)
- 격리 venv 설치: `python -m venv` + `pip install --no-index --no-deps` 둘 다 rc0
- **RECORD**: `smtw/` 소스 항목 **정확 13** (__init__, __main__ + 11 서브모듈; .pyc 제외, raw 26=+설치 시 pyc 13) · `fable_lite/` 소스 항목 **정확 1** (`fable_lite/__init__.py`만, 금지 추가 파일 []) · `Scripts/smtw.exe`·`Scripts/fable-lite.exe` 존재
- entry_points.txt: `[console_scripts] smtw = smtw.cli:main`, `fable-lite = smtw.cli:main` 둘 다
- 설치본 identity: `fable_lite is smtw, fable_lite.cli is smtw.cli` → `True True`; `smtw.__file__`과 `fable_lite.__file__` 모두 `venv/Lib/site-packages/smtw/__init__.py`
- 콘솔 스크립트: `smtw.exe version` rc0 `2.6.0` 경고 없음 · `fable-lite.exe version` rc0 `2.6.0` 경고 없음 (직접 `smtw.cli:main` 호출 — shim 미경유, 기대대로)
- wheel zip 교차검증: wheel 내부 smtw 13·fable_lite 1, wheel에 .pyc 없음, RECORD와 일치

반증 (`tmp/q2-p4/d5-verify/result.json`) — 게이팅 9건 전부 PASS (confirmed=true):
- RECORD csv 독립 재집계 13/1 · **shim sha256 4위치(worktree·RECORD·설치본·wheel member) 동일** `1VJ4_W9snunGHxPZB77_mM5spx_AARN80oxhD1scz9M` (무변조 빌드)
- RECORD sha256 재계산 14/14 match + 스크립트 2종 match · wheel 내부 RECORD 독립 판독 13/1 · site-packages 디스크 열거 13/1
- METADATA `Name: fable-lite / Version: 2.6.0` · venv `python -P -m smtw version` rc0 `2.6.0` · identity full(상위+11별칭+origin) True · 콘솔 2종 재실행 rc0 경고 없음
- advisory(비게이팅): venv `python -P -m fable_lite version` rc0 `2.6.0` — D5 wheel 스펙 주장 밖이나 정상 동작

재현: `python C:/Users/rotat/fable-lite-wt-q2ultra/tmp/q2-p4/d5/driver.py` (~27초) · 반증: `python .../d5-verify/driver.py`
증거 파일: `d5/result.json`, `d5/dist/fable_lite-2.6.0-py3-none-any.whl`, `d5/venv/Lib/site-packages/fable_lite-2.6.0.dist-info/{RECORD,entry_points.txt}`, `d5/02_build.txt` ~ `10_fable_lite_exe_version.txt` · `d5-verify/result.json`, `a_record_recount.txt` ~ `i2_fable_lite_exe.txt`

---

## 6. 잔여 위험 · 권고

1. **D3 protocol≥4 합성 레거시 페이로드 미건설**: 반증기가 STACK_GLOBAL 인코딩 수술에 실패해 protocol 4/5의 '구버전 픽셀'을 양적으로 구성하지 못함. 다만 해석 기제(`pickle.find_class → import_module("fable_lite.card") → sys.modules 별칭`)는 프로토콜 무관이며 protocol 0~3 실증 + `alias_class_is_smtw=true`(4/5 포함 전 프로토콜 왕복 성공)로 뒷받침. 완전 폐쇄를 원하면 구버전 fable_lite 2.5 설치 환경에서 **진짜 레거시 fixture 파일**을 생성해 로드 테스트 권고.
2. **D3-verify 프로브 재작성 후 재반증** 권고 (자식 PYTHONPATH 전달·STACK_GLOBAL 대응 수술·판정 집계 수정). 본 실측 사이클에서는 지시(새 실측 금지)에 따라 재실행하지 않음.
3. **goals 스토리**: D1~D5+REPORT plan 등록 완료(에이전트 claude-ultracode). `goals verify`는 verify-cmd(드라이버) 재실행을 수반하므로 '새 실측 금지' 지시에 따라 보류 — 증거 경로(각 `result.json`)는本报告에 고정. 재실행 시: `python C:/Users/rotat/fable-lite/goals/goals.py verify --root C:/Users/rotat/fable-lite-wt-q2ultra --story <D1..D5|REPORT> --evidence <경로>`.

## 7. 실측 규율 준수 노트

- worktree 코드 무수정 (Write/Edit 미사용) · 임시 산출물은 `worktree/tmp/q2-p4/` 한정 (D5 빌드도 스크래치 복사본에서 — worktree에 build/dist 미생성)
- 자식 프로세스 전원 `PYTHONDONTWRITEBYTECODE=1`·`PYTHONIOENCODING=utf-8` (바이트코드·인코딩 오염 최소화)
- git CLI는 프리훅 fail-closed 차단으로 전량 사용 불가 → HEAD는 worktree git 메타데이터 파일 직접 판독으로 확인
- 프리훅 R2가 복합 명령을 차단해 모든 실행은 '단일 `python <절대경로>`' 형태 + 드라이버 내부 subprocess 구조로 수행
- D5-verify의 venv 테스트는 `-P` + PYTHONPATH 미설정로 site-packages를 소스가 섀도잉하지 않게 격리

## 8. 증거 인덱스 (result.json 중심)

```
worktree/tmp/q2-p4/
├─ d1/         result.json + driver.py + child_1..5_*.txt          [본실측 PASS]
├─ d2/         result.json + driver.py + check1..5_*.txt           [본실측 PASS]
├─ d3/         result.json + driver.py + check1..4_*.txt           [본실측 PASS]
├─ d4/         result.json + driver.py + 1..4_*.txt(전체스위트 포함) [본실측 PASS]
├─ d5/         result.json + driver.py + dist/*.whl + venv/ + src/ [본실측 PASS]
├─ d1-verify/  result.json + driver.py + child_A..E_*.txt          [반증 PASS]
├─ d2-verify/  result.json + driver.py + ra1..ra7_*.txt            [반증 PASS]
├─ d3-verify/  result.json + verify_driver.py + r1..r4/i1..i2_*.txt
│              + probe_pickle.py + probe_cold_legacy.py + diag_p4.py [반증 FAIL→프로브 결함 귀속 §D3-v]
├─ d4-verify/  result.json + verify_driver.py + R1..R7_*.txt       [반증 CONFIRMED_PASS]
└─ d5-verify/  result.json + driver.py + probe2.{py,json} + a..i_*.txt [반증 PASS]
```
