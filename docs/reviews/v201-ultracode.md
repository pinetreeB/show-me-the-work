# show-me-the-work v2.0.1 신뢰성 패치 — 다관점 병렬 P4 리뷰 (ultracode)

> 리뷰일: 2026-07-14 · 리뷰어: 우하 pane (implementation-ultracode, Opus 4.8) 오케스트레이션 + 5관점 병렬 서브에이전트 + 결함별 적대 검증
> 대상: v2.0.1 신뢰성 패치 = 커밋 `6832460^..0b93808` (17커밋, 최종 HEAD `0b93808`, 로컬 커밋·push/tag 없음)
> 감사 명세(정본): tmp/v2.0.1-ai-handoff-sol.md (§2 Task 1~3·§7 E2E A~G) · 구현 보고: tmp/v201-report-codex.md · 설계: docs/design/v2-provenance.md rev4
> 방법: 정적(Read+git diff+grep) + 표적 회귀 테스트 + 격리 재현 → 각 결함 적대 검증 1회

---

## 1. 한 줄 판정 (쉬운 말)

v2.0.1 패치는 "검증 없이 완료가 통과하던 세 구멍"(quick 면제·가짜 echo 검증·R1 evidence)을 **명세대로 정확히 막았고, 기존 게이트·성능·CI/패키징도 깨지지 않았습니다.** 5개 관점 중 4개(정확성·회귀·신뢰성·CI/패키징)는 **결함 0**입니다. 유일한 실결함은 새로 추가한 "원격 변경 감지" 기능에 있습니다 — `ssh`/`scp`를 `bash -c "..."`·`env ...`·`uv run ...` 같은 **래퍼로 감싸면 원격 변경 감지를 빠져나가** 검증 요구가 사라집니다(제 재현으로 실측). 표시·감독 보조 기능의 미탐이라 심각도는 medium이고, 로컬 파일 변경 감지에는 영향이 없습니다.

**하드게이트: 치명 결함 0.** 확정 결함 1건(RP-1, medium) + 문서화-범위 관찰 2건(비차단).

---

## 2. 결과 요약

| 관점 | 결함 | 판정 | 비고 |
|------|:---:|------|------|
| **정확성** (Task 1~3) | 0 | ✅ 명세 완전 충족 | Task 2 필수 21/21 오케스트레이터 실측 + 71 표적테스트 |
| **회귀** (기존 계약) | 0 | ✅ 무파손 | 2-cap·8-process race·docs-only·mode 변수 완전 제거 |
| **신뢰성** (scan budget) | 0 | ✅ fail-open 건전 | 10k/256MiB/8s/2s 배선 정확·partial 미커밋·경계 포함 |
| **원격 파서** | 3(1 생존) | ⚠️ **RP-1 medium** | RP-2 REFUTED·RP-3 설계범위 밖 |
| **CI/패키징** | 0 | ✅ 실행 가능 | Ruff 0·workflow 유효·wheel smoke 실효 |

**확정 결함 1건(RP-1 medium) · 비차단 관찰 2건 · 나머지 4관점 clean.**

---

## 3. 확정 결함 — RP-1 (medium): 원격 mutation 탐지 래퍼 우회 미탐

**쉬운 말:** v2.0.1은 "원격 서버를 바꾸는 명령(ssh/scp)을 하면 검증을 요구"하는 감지를 새로 넣었습니다. 그런데 그 감지가 **명령의 첫 단어가 정확히 `ssh`/`scp`일 때만** 작동합니다. 그래서 `bash -c "ssh host rm -rf /srv"`처럼 셸로 감싸거나 `env FOO=bar ssh ...`, `uv run ssh ...`로 감싸면 첫 단어가 `bash`/`env`/`uv`라 감지를 빠져나갑니다. 결과적으로 원격을 실제로 파괴해도 "원격 변경 없음"으로 취급돼, 로컬 파일 변경이 없으면 Stop이 그냥 통과합니다 — 이 패치가 막으려던 바로 그 오탐 B를 우회합니다.

**결정적 증거 — 형제 함수와의 비대칭:** 같은 패치가 만든 `is_verification_command`(검증 명령 판정)은 `bash -c`·`env`·`uv run` 래퍼를 **재귀적으로 벗겨** 안쪽을 판정합니다. 그런데 `is_remote_mutation_command`(원격 변경 판정)은 **bare `VAR=val`만 벗기고 래퍼는 안 벗깁니다.** 즉 래핑된 원격 *검증*은 인식(epoch 해소 가능)하는데 래핑된 원격 *변경*은 미탐 — 같은 저자의 두 형제 함수가 래퍼 처리에서 어긋납니다.

**오케스트레이터 격리 재현 (RUN→OBSERVE, `v201_rp1_repro`):**
```
is_remote_mutation_command:
  'ssh deploy@host "rm -rf /srv"'            → True   (탐지 OK)
  'FOO=bar ssh host "touch x"'               → True   (bare env는 벗김 OK)
  'env FOO=bar ssh deploy@host "rm -rf ..."' → False  !!MISS (기대 True)
  'bash -c "ssh deploy@host rm -rf /srv"'    → False  !!MISS
  'sh -c "ssh host rm -rf /x"'               → False  !!MISS
  'uv run ssh host "touch x"'                → False  !!MISS
대조: is_verification_command('bash -c "ssh host pytest"') → True (래퍼 재귀 처리)
```

**end-to-end 성립:** `command_hint`가 `tool_input.command`를 가공 없이 전달(adapters/claude_code/common.py:89-91)하므로 미탐이 게이트까지 전파 → `core/adapter_observation.py`의 `_remote_mutation`이 False → `last_remote_mutation_seq` 미상승 → 로컬 변경 없으면 Stop allow.

**권고:** `is_remote_mutation_command`도 `core/verification.py`의 래퍼 언랩 로직(`env`·`bash/sh/pwsh -c`·`uv run` 재귀)을 **대칭적으로 재사용**해 래핑된 ssh/scp 내부 명령을 판정. 두 형제 함수가 동일한 래퍼 정규화를 공유하도록 통합 권장.

```
증거:
- core/shell_command.py:167-171 without_environment_assignments (bare VAR=val만 스트립)
- core/shell_command.py:191-206 is_remote_mutation_command (tokens[0]==ssh/scp만 판정)
- core/verification.py:77-82 (env/셸 -c/uv run 재귀 언랩) ← 비대칭 대비
- 오케스트레이터 재현: 래퍼 4종 전부 False(미탐), 형제 함수는 True
- 적대 검증: CONFIRMED, is_real_defect=True, medium 유지 (best-effort 원격 기능 미탐)
```

---

## 4. 비차단 관찰 (경쟁 가설 판정)

### RP-2 — remote epoch가 순수 로컬 검증으로도 해소됨 → **REFUTED (문서화된 한계)**

**가설 1(결함):** 원격 파괴(ssh host "rm -rf")로 epoch가 오른 뒤, 무관한 **로컬** pytest만으로도 remote epoch가 풀려 allow — 로컬 snapshot은 원격 결과를 증명 못하므로 의미론적 약점.
**가설 2(수용된 설계):** 설계 v2-provenance.md §6.5는 해소 조건을 "별도 검증 invocation의 frozen covers.through_seq ≥ remote epoch"로만 규정 — 원격 특정성 요구 없음. `docs/reviews/v201-agy.md`가 "명세 일치 의도적 한계"로 수용·문서화.
**기각:** 코드 동작은 재현되나(설계 텍스트에 부합) 배포 차단 결함 아님. 단 codex 보고서(:43) "broad SSH verification도 같은 remote parser로 epoch 해소" 표현이 원격 특정성을 시사하는데 실제는 임의(로컬 포함) 검증도 허용 → **보고서 표현이 실제보다 좁음(문서 정정 권고, 비차단).**

### RP-3 — 원격→원격 scp(`scp -3 host1:/a host2:/b`)가 epoch 미생성 → **CONFIRMED 메커니즘, is_real=False**

**증거:** `_is_scp_upload_with_options`는 모든 source가 로컬일 때만 upload로 판정 → 원격 source면 False. `scp -3 host1:/a host2:/b` → False(host2 실제 변경 미집계). **기각(배포 차단 아님):** 설계 §6.5가 remote epoch를 "로컬→원격 upload"로 명시 한정하고 download 제외 — 원격 source scp는 정의상 positive 범위 밖. 설계대로의 동작이며 문서화된 스코프 경계. 미탐 경계로만 기록.

---

## 5. 확정된 견고함 (P4는 견고성도 판정)

### 정확성 (Task 1~3 명세 완전 충족)
- **Task 1** (quick 면제 제거): `verify_state.py`에서 `mode == "quick"`가 allow 조건에서 제거되고 **mode/task_mode 지역변수 자체가 완전 삭제**(grep 참조 0). 5개 정책 분기(무변경·docs-only·fresh→allow / 비문서+무검증→mode무관 block / normal·deep 유지) 정적+동적 확인. 2-cap 유지.
- **Task 2** (가짜 검증 차단): substring TEST_TERMS 완전 제거 → shlex 토큰+명령별 화이트리스트. **명세 REJECT 6/ACCEPT 10 전량 일치**(오케스트레이터 실측 21/21). `python -c`는 AST 파싱으로 `assert`/`pytest.main`/`unittest.main` 있어야만 인정. shell chain(`||`,`&&`,`;`,`|`,`|&`,`&`)·CR/LF 우회 전부 차단. 3어댑터 동일 core 판정.
- **Task 3** (R1 evidence): `evidence` 미존재 시 True로 새던 버그 제거. restated_goal/acceptance/evidence 비공백 문자열배열 강제 + fake marker 거부. 명세 필수 8케이스 정확 동작.
- 표적 테스트 `test_verification + test_core_contracts + test_verification_epoch + test_verification_covers` **71 passed**.

### 회귀 (10 confirmations)
- **2-cap 무파손**(MAX_STOP_BLOCKS=2, CAP_ALLOW 로직 미변경). **8-process race 불변**(7 passed, block 2/allow 6/counter 2). Task 1이 mode 변수를 완전 제거하는 방식이라 normal/deep 동작 mode 무관 동일. docs-only allow 유지(신규 `remote_seq is None` 가드는 원격 흐름에만 작용). 기존 테스트 약화 은폐 없음.

### 신뢰성 (11 confirmations)
- scan budget 상수 정확(entry 10,000·256 MiB·full 8s·incremental 2s, `provenance_types.py:10-13`). 초과 검사가 **hash 전** 수행(`_reserve_entry`). 경계 **포함**(정확히 상한까지 허용). 상한 초과 시 **partial snapshot 미커밋** → 다음 턴 오염 방지. soft exclude 전 깊이 적용(node_modules 등 제외). scope_too_large vs incomplete 구분, fail-open(게이트 판정 불변·관측만 저하).

### CI/패키징 (8 confirmations)
- Ruff **0건**(exit 0). `ci.yml`/`release-quality.yml` **YAML 유효 + actions 버전 유효**(checkout@v4·setup-python@v5·upload-artifact@v4). 호출 스크립트/모듈/플래그 전부 실재·실행 가능. **wheel smoke 실효 확인** — clean venv 오프라인 설치 후 `-I -m fable_lite --help` exit 0, `-I`가 소스트리를 sys.path에서 제외함을 대조실험으로 확인(소스트리 오탐 아님). 버전 SSOT 2.0.1 동기화(`sync_version.py --check`).

---

## 6. 오케스트레이터 독립 검증 (first-hand)

- **Task 2 명세 충족:** `v201_task2_repro.py` — REJECT 11/11(명세6 + chain5) + ACCEPT 10/10 실 `is_verification_command` 판정 일치.
- **RP-1 실결함:** `v201_rp1_repro` — 래퍼 4종(env/bash -c/sh -c/uv run) 전부 미탐(False) + 형제 `is_verification_command`은 래퍼 True로 비대칭 실측.
- 커밋 범위(17)·변경 파일(49)·shell_command.py 482줄 신규 확인.

**검증 인프라:** 워크플로우 8에이전트 전원 성공(무오류). 전체 pytest·벤치 미재실행(표적 테스트·격리 재현·코드 트레이스로 갈음) — codex 보고 "354 passed·W9 FN0/FP0·W10 SLO green"은 값-로직 정합만 확인, 재생성 확증은 미수행.

---

## 7. 권고 (우선순위)

| 우선 | 항목 | 근거 |
|------|------|------|
| **P2** | RP-1: `is_remote_mutation_command`에 래퍼 언랩(env/셸 -c/uv run) 대칭 적용 | 원격 mutation 감지를 셸 래퍼로 우회 가능 — v2.0.1 오탐 B 수리의 실질 구멍, 형제 함수와 비대칭 |
| **P3** | RP-2: codex 보고서 §43 "broad SSH verification" 표현을 실제(임의 검증도 해소)에 맞게 정정 | 보고서-코드 표현 불일치(설계 자체는 문서화됨) |
| **P3(참고)** | RP-3: 원격→원격 scp 미탐은 설계 §6.5 스코프 경계로 문서화 | 설계대로, 비차단 |
| **P3(참고)** | Task 3 fake marker 회귀 테스트가 5종 중 "not run" 1종만 고정 | 코드는 5종 다 block, 나머지 4종 무테스트(회귀 미감지 위험, 경미) |

---

## 8. 결론

**v2.0.1 신뢰성 패치는 명세의 핵심 3구멍(Task 1~3)을 정확히 봉합했고, 기존 게이트 계약·성능·CI/패키징을 훼손하지 않았다.** 5관점 중 4관점 결함 0, 명세 필수 테스트 전량 충족(Task 2는 오케스트레이터가 21/21 독립 실측). **유일한 실결함은 RP-1(medium)** — 새로 추가한 원격 mutation 감지가 `bash -c`/`env`/`uv run` 래퍼로 우회되는 미탐으로, 형제 `is_verification_command`이 이미 래퍼를 재귀 처리하는 것과 **비대칭**이라 수정 방향이 명확하다(래퍼 언랩 로직 공유). 나머지 RP-2(문서화된 한계)·RP-3(설계 스코프 밖)은 비차단이다.

**배포 판정:** 치명 결함 0. RP-1은 원격 감독 보조 기능의 미탐(로컬 변경 감지 무영향)이므로 **RP-1 수정 후 릴리스** 또는 **RP-1을 알려진 한계로 문서화하고 릴리스**하는 선택이 가능하다 — 전자를 권한다(수정 폭이 작고 형제 함수 재사용으로 대칭 확보).
