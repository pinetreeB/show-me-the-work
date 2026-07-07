# P8 — 의도 게이트(Intent Gate) 라이브 E2E 실측

> 실행: Sonnet(우하). 방법론: P5/P5b와 동일 — 격리 디렉토리, `--plugin-dir C:\Users\rotat\fable-lite`,
> `--setting-sources project`, `--permission-mode bypassPermissions`, 도구 기본셋(인위적 제한 없음),
> `--debug hooks --debug-file`로 훅 이벤트 로그 확보. 실행 전 Codex의 `tmp\.done13-codex`(ambiguity.py
> 미탐 수정) 확인 후 시작. 전체 4개 라이브 세션(케이스 1~4, 케이스2는 turn1+`--resume` turn2) +
> 합성 payload 기반 게이트 메커니즘 직접 검증 1건.

## 결론 요약

| 항목 | 결과 |
|---|---|
| 모호성 판정(core/ambiguity.py) 정확도 | **4/4 정확** — 모호 2건(케이스1,4) 모두 flag, 명확 1건(케이스3) 미flag, 후속답변(케이스2 turn2) 재판정도 일관 |
| PreToolUse 게이트 차단/통과 메커니즘 | **정상** — block 2회 후 fail-open 통과 캡 확인, intent.json 존재 시 즉시 통과 확인(합성 payload 직접검증) |
| `python -m fable_lite intent set` 실사용 가능성 | **🔴 BLOCKER — 재현됨.** 플러그인 설치 상태(임의 프로젝트 디렉토리)에서 `ModuleNotFoundError: No module named fable_lite` |
| 검증인식(`text_indicates_success`) 정합성 | **🟠 HIGH — 3/4 세션에서 발견.** 명백히 성공한 증거인데도 `success:false`로 기록되는 사례 반복 |
| `확인질문 N:` 마커 포맷 준수 | 🟡 가변적 — 케이스1은 정확 준수, 케이스2 turn1은 서술문에 녹아들어 마커 누락 |

---

## 0. 직접 메커니즘 검증 (합성 payload, 라이브 세션 전 사전 확인)

`adapters/claude_code/pre_tool_use.py`에 `cwd`를 합성 payload로 직접 넣어 확인:

- ledger에 `intent_required=true` 시딩 후 Edit payload 3연속 호출 → **block, block, allow**(`intent_blocks` 0→1→2, 세 번째는 캡 도달로 fail-open). 무한루프 없음 확인.
- 별도 디렉토리에서 `intent_required=true` 시딩 → block 1회 확인 → `fable_lite intent set` 실행 → **intent.json 생성 후 같은 payload 재호출 시 즉시 allow**(캡 도달 전, 정상 회복 경로) 확인.
- 이 과정에서 "intent.json이 있는데도 계속 block"되는 것처럼 보이는 최초 실패가 있었으나, 원인은 **제 테스트 스크립트가 Git Bash의 POSIX→Windows 경로 자동변환을 CLI 인자와 JSON payload 문자열에 일관되게 적용하지 않은 것**(같은 `--root`/`cwd` 값이 두 경로에서 다르게 해석됨)이었습니다. 경로 형식을 통일하자 정상 동작 확인 — **fable-lite 코드 결함 아님, 제 테스트 방법론 아티팩트**로 판명.

→ **게이트 자체의 block/allow 로직과 2회 캡은 신뢰할 수 있음.**

---

## 1. 케이스 1 — 모호 프롬프트 "이거 좀 고쳐줘" (파일 미특정)

- ledger: `ambiguity_score: 3`(missing_target+pronoun_reference+ultra_short), `intent_required: true`, `packs: [investigation, intent-interview]` — **정확히 flag됨**.
- 모델 동작: `가설 1/증거/기각` 마커로 `app.py`의 `add` 버그를 정확히 특정(투자 팩 준수) → **Edit 시도 → 게이트가 실제로 차단**(`intent_blocks: 1`, hookdebug.log에 `permissionDecision: deny` 확인) → 차단 사유에서 `python -m fable_lite` 실행 시도 → **`No module named fable_lite`로 실패** (모델이 응답에 직접 보고) → 사용자 확인질문 1개로 수렴, 턴 종료.
- `확인질문 1:` 마커 정확히 사용. 다만 목표/범위/비목표 3개를 **개별 질문 대신 1개의 예/아니오 확인**으로 묶었음 — 팩의 "이미 명확한 건 빼라" 원칙에 비춰보면, 투자로 이미 좁혀진 상태에서는 합리적 판단으로 보임(불필요한 3회 왕복을 피함).
- `stop_blocks: 2` — Stop 게이트가 2회 차단 후 통과(B2 수정 이후 정상 동작, 세션이 실제로 무한루프 없이 종료됨).
- `app.py` 미변경(intent.json 미생성 상태에서 게이트가 끝까지 수정을 막음 — **의도대로 동작**).

## 2. 케이스 2 — turn1(모호) + `--resume` turn2(답변)

- **turn1**: 케이스1과 동일 설정("이거 좀 고쳐줘")이지만 세션 지속 활성화. 조사 내용은 케이스1과 동등하나, **`확인질문 N:` 마커를 쓰지 않고** "앞서 드린 확인질문에 대한 답을 기다리는 상태입니다"처럼 서술문에 질문을 녹여 넣음 — **마커 포맷 준수가 매 턴 안정적이지 않다**는 실측 증거.
- **turn2**(`--resume`, 프롬프트: "1번 맞음. 그거예요. 동작만 고치고 스타일 관련된건 건드리지 마세요."):
  - `intent.json`이 **정상 스키마로 생성됨**(`save_intent()`가 실제로 만드는 필드만 존재: goal/scope/non_goals/assumed/confirmed_at_prompt, `confirmed_at_prompt`에 사용자 답변 그대로 기록).
  - `app.py` 정확히 수정(`a + b`), `utils.py`·스타일 무변경(비목표 준수).
  - **그러나** `verification_commands`에 `"C:/Users/rotat/fable-lite/goals/goals.py"` **절대경로**가 등장 — 이는 모델이 `python -m fable_lite`가 실패한 뒤(turn1~2 사이 어딘가에서, hookdebug.log엔 명령 텍스트가 없어 정확한 재시도 시점은 미확인) **플러그인 설치 경로를 스스로 탐색해 찾아냈다는 강한 정황 증거**입니다. `CLAUDE_PLUGIN_ROOT`가 Bash 도구에 노출되지 않음을 별도 프로브로 확인했으므로(§3 참조), 공식적으로 제공되는 정보가 아니라 **모델의 파일시스템 탐색 능력에 의존한 우회**로 보입니다.
  - 같은 턴에서 N2(goals) 게이트도 동시 발동(`needs_goals: true`) — intent 게이트와 무관한 별개 이슈라 상세 분석은 범위 밖으로 두되, `goals.py --help` 호출들이 `{"fail_open": true, "message": "argument parse failed: 0"}`를 evidence에 남기는 것은 참고로 기록만 해둡니다(Codex 영역).

## 3. 케이스 3 — 명확 프롬프트 "app.py의 add 함수 버그 고쳐줘"

- ledger: `ambiguity_score: 0`, `intent_required: false`, `packs: [investigation]`(intent-interview 없음) — **정확히 미flag**.
- 확인질문 없이 즉시 조사→수정→검증 진행. `app.py` 정확히 수정, `intent.json`은 생성되지 않음(불필요하므로 정상).

## 4. 케이스 4 — "그냥 알아서 고쳐"

- ledger: `ambiguity_score: 3`(missing_target+delegation+ultra_short), `intent_required: true` — 설계상 `그냥 해`/`묻지 말고`처럼 완전한 스킵 문구가 아니라 `그냥 알아서`라 **`_never_flag`의 SKIP_PHRASE_RE에 걸리지 않고 일단 flag되는 것을 확인**(→ 이후 모델이 팩 4단계 원칙에 따라 가정 선언+`--assumed`로 처리하는 것이 정상 경로임을 실측으로 확인).
- 모델이 먼저 Edit 시도 → 차단(`intent_blocks: 1`) → 이후 `intent.json`이 `"assumed": true`로 생성됨.
- **그러나 이 `intent.json`에 `"ambiguity_score": 3`라는, `core/intent.py`의 `save_intent()`가 절대 만들지 않는 필드가 포함**돼 있습니다 — `save_intent()`의 실제 반환 스키마는 goal/scope/non_goals/assumed/confirmed_at_prompt뿐입니다. 이는 **모델이 `fable_lite intent set` CLI를 통하지 않고 파일을 직접 수동으로 작성해 게이트를 통과시켰다는 결정적 증거**입니다(아마 ledger.json에 있는 `ambiguity_score` 필드를 참고해 흉내 낸 것으로 추정). 결과적으로 목표/범위/비목표 내용 자체는 합리적이었고 `app.py`도 정확히 수정됐지만, **의도된 CLI 경로가 아니라 우회로 목적을 달성한 것**입니다.
- `stop_blocks: 2` — 여기서도 N1(조사 팩 마커) 사유로 2회 차단 후 정상 통과.

---

## 5. 발견 (심각도순)

### 🔴 BLOCKER — `python -m fable_lite`가 플러그인 설치 상태에서 임의 프로젝트 디렉토리 기준으로 동작하지 않음

**재현**: 격리 디렉토리(fable-lite 저장소가 아닌 임의 사용자 프로젝트를 대표)에서
```
python -m fable_lite intent set --root . --goal "test" --scope "app.py"
```
→ `No module named fable_lite`(exit 1). `pip show fable-lite` → not found. `pyproject.toml`은 `setuptools` 빌드로 `fable_lite`를 패키지에 포함하지만, **플러그인을 `--plugin-dir`/marketplace로 설치하는 것만으로는 `pip install`이 되지 않으므로** `fable_lite`가 `sys.path`에 없습니다.

추가로 직접 프로브(별도 미니 세션, "Bash로 `echo $CLAUDE_PLUGIN_ROOT` 정확히 그 명령만 실행해줘")한 결과 **`${CLAUDE_PLUGIN_ROOT}`는 hooks.json 커맨드 치환에만 쓰이는 토큰이며 모델이 실행하는 일반 Bash 도구 호출에는 노출되지 않습니다**(빈 문자열 확인). 즉 모델이 임의 프로젝트에서 자기 위치를 기준으로 `PYTHONPATH=$CLAUDE_PLUGIN_ROOT python -m fable_lite ...`처럼 우회할 공식 경로도 없습니다.

**영향**: `docs/design/intent-gate.md`와 `packs/intent-interview.ko/en.md`가 명시한 유일한 정식 회복 경로(`intent set`/`--assumed`)가 **fable-lite의 주 사용 형태(플러그인 설치)에서 구조적으로 실패**합니다. 라이브 실측 4건 중:
- 1건(케이스1)은 모델이 실패를 그대로 보고하고 사용자에게 확인 질문으로 전환(안전하지만 CLI 자체는 미작동 확인).
- 1건(케이스4)은 모델이 **CLI를 우회해 intent.json을 수동 작성**(비표준 필드가 남는 부작용, 결과적으로는 성공).
- 1건(케이스2 turn2)은 모델이 **플러그인 설치 경로를 스스로 탐색해 찾아낸 뒤** 정상 스키마로 성공.

세 경우 모두 **하위 모델이라면 재현이 보장되지 않는, 모델의 문제해결 역량에 의존한 우회**입니다. fable-lite의 존재 이유가 "하위 모델도 절차로 규율을 지키게 하는 것"이므로, 정식 회복 경로가 능력에 의존해서만 작동하는 것은 설계 목적과 정면으로 배치됩니다. 최악의 경우(모델이 우회를 못 찾음): `intent_blocks`가 2회 차단 후 fail-open으로 **intent.json 없이도 조용히 통과** — 이는 사용자 확인도, 가정 선언도 없이 게이트가 무력화되는 셈이라 기능 자체가 무의미해집니다.

**권고**(결정은 Codex/오케스트레이터 영역): (a) 플러그인 활성화 시 `pip install -e <plugin_root>`를 자동/안내하거나, (b) UserPromptSubmit/PreToolUse 훅이 `additionalContext`에 실제 절대경로를 포함한 커맨드(`python "<plugin_root>/fable_lite/__main__.py" intent set ...`)를 박아 넣거나, (c) `CLAUDE_PLUGIN_ROOT`를 Bash 세션에도 노출하는 방법을 Claude Code 쪽에 확인 — 이 중 하나가 필요해 보입니다.

### 🟠 HIGH — `text_indicates_success`가 명백히 성공한 증거를 실패로 오판 (4세션 중 3세션에서 발견)

케이스2/3/4에서 반복 확인:
- `"add(2,3) = 5\r\nadd(-1,1) = 0\r\nmultiply(2,3) = 6"` → `success: false`
- `"ok add=5 multiply=6"` → `success: false`(문자열이 "ok"로 **시작**해 앞에 공백이 없어 `" ok "` 패턴 불일치)
- `"OK: 모든 검증 통과"` → `success: false`(``"ok"`` 뒤에 공백이 아닌 콜론이 와서 `" ok\n"`/`" ok "` 불일치, 게다가 stdout 캡처 자체도 모지박 손상)

`core/verification.py`의 `OK_SIGNALS`(`"passed"`, `"verify_ok"`, `"success"`, `" ok\n"`, `" ok "`, `"all tests"`, `"✓"`)가 특정 프레임워크 출력 문구에만 맞춰져 있어, **실제로 매우 흔한 "assert 통과 후 값 출력" 스타일의 임시 검증**을 인식하지 못합니다. 결과적으로 완전히 정상 종료된 세션도 Stop 게이트가 "미검증"으로 보고 2회 차단(`stop_blocks: 2`)을 유발합니다 — 사용자 경험상 불필요한 마찰이며, v1fix에서 다룬 문제와 같은 계열이지만 다른 구체적 갭입니다. (참고: 이번 세션에서 새로 발견한 것이며, intent 게이트 자체의 결함은 아닙니다.)

### 🟡 MEDIUM — `확인질문 N:` 마커 포맷 준수가 매 턴 안정적이지 않음

케이스1은 `확인질문 1: ...`로 정확했으나, 케이스2 turn1은 동일 설정에서도 질문을 마커 없이 서술문에 녹여 넣었습니다("앞서 드린 확인질문에 대한 답을 기다리는 상태입니다"). 마커를 텍스트로 파싱하는 별도 컴플라이언스 게이트가 없어(설계상 PreToolUse는 intent.json **파일 존재 여부**만 봄) 이 자체가 기능을 막지는 않지만, 팩의 명시적 형식 요구가 100% 안정적으로 지켜지진 않는다는 실측 데이터입니다.

---

## 6. 긍정 확인 사항

- 모호성 판정 정확도 4/4(과탐·미탐 없음) — Codex의 미탐 수정판이 이번 4개 실측 케이스에서 모두 올바르게 동작.
- PreToolUse 게이트의 block/allow 및 2회 캡 로직은 경로 일관성만 지키면 완전히 신뢰 가능(합성 검증으로 별도 확인).
- Bash는 의도 확정 전에도 차단되지 않음(설계대로) — 모든 케이스에서 조사·재현 단계가 자유롭게 진행됨.
- `--assumed` 경로의 최종 산출물(목표/범위/비목표 텍스트 품질)은 CLI를 우회했음에도 합리적이었음(케이스4) — 팩의 지시 내용 자체는 잘 전달됨.
- B2(stop_hook_active 수정)가 실제 중첩 세션에서도 정상 동작 확인 — 케이스1·2·4 전부 2회 차단 후 무한루프 없이 종료.

## 7. 정리·무결성 확인

- 격리 스크래치 디렉토리(케이스1~4, 합성검증용 보조 디렉토리, env 프로브)는 증거 추출 후 전부 삭제 완료.
- `~/.claude.json`만 변경(세션 북키핑, 기존 패턴과 동일 — 양성), `settings.json`/`settings.local.json`/`installed_plugins.json`/`known_marketplaces.json` 4개 파일 해시 불변 확인.
- 실제 저장소(`C:\Users\rotat\fable-lite`)에 `.fable-lite/` 상태 누출 없음 확인.
- 코드 변경 없음(순수 리뷰/실측 과제) — 별도 pytest 재실행 불필요.

---

## 8. 재검증(blocker 수정 후) — 2026-07-07

Codex가 §5의 BLOCKER를 수정: 저장소 루트에 self-locating 런처 `fable-lite-cli.py` 신설(자기 위치 기준으로
`fable_lite` 패키지를 찾아 sys.path에 넣으므로 pip 설치·PYTHONPATH 불필요) + `adapters/intent_command.py`가
훅 파일 위치에서 이 런처의 **절대경로**를 계산 + `user_prompt_submit.py`의 컨텍스트와 `core/contract.py`의
차단 사유 양쪽에 `python "<절대경로>/fable-lite-cli.py" intent set ...` 명령을 그대로 박아 넣도록 배선.

**사전 메커니즘 확인**: 임의 디렉토리에서 `python "C:\Users\rotat\fable-lite\fable-lite-cli.py" intent set --root . --goal "mech check" --scope "app.py"` → exit 0, intent.json 정상 생성 확인(ModuleNotFoundError 재현 안 됨).

**라이브 재검증** (P8 케이스1과 동일 셋업 — 격리 디렉토리, `--plugin-dir`, "이거 좀 고쳐줘", 세션 지속 활성화 후 `--resume`으로 답변 1턴):

- **turn1**: 조사(가설1/2/3·증거·기각, 다만 "조사 팩 마커를 포함해 다시 정리합니다"라는 메타발언이 마커 앞에 붙음 — 여전히 발생하는 편차) 후 Edit 시도 → 차단(`intent_blocks:1`) → **`확인질문 1(목표)`/`확인질문 2(범위)`/`확인질문 3(비목표)` 3개를 마커로 정확히 구분해 물었으나 한 턴에 전부 나열**(P8 케이스1은 반대로 1개로 뭉쳤음 — "1문1답, 한 번에 하나씩" 원칙 준수가 매번 다름을 재확인). `fable-lite-cli.py` 명령은 아직 실행하지 않고 사용자 답변 대기.
- **turn2**(`--resume`, "네 맞습니다. 1,2,3 모두 그대로 진행해주세요."): **모델이 주입된 명령을 정확히 그대로 실행** —
  ```
  python "C:\Users\rotat\fable-lite\fable-lite-cli.py" intent set --root . --goal "app.py의 add 함수 버그 수정: a - b를 a + b로 변경" --scope "app.py의 add 함수 한 줄만 수정" --non-goal "multiply·utils.greet 등 정상 코드 변경, 테스트 추가, 리팩터링, 타 파일 변경 제외"
  ```
  → `intent.json`이 **`save_intent()` 표준 스키마 그대로, 단 한 번의 시도로 생성**(경로 탐색·수동 파일 작성 흔적 전혀 없음 — P8 케이스4·2와 뚜렷이 대비됨). 이어서 `app.py` 정확히 수정(`a + b`), `intent_required: false`로 재판정, `stop_blocks: 0`(무리 없이 바로 통과).

**판정**: (a) 모델이 차단 메시지의 명령을 그대로 복사-실행함 — **예, 확인됨**. (b) intent.json이 CLI 경유 정상 스키마로 생성(수동 작성 흔적 없음) — **예, 확인됨**. (c) 이후 수정 진행 — **예, 확인됨**. **P8의 BLOCKER는 해소된 것으로 판단합니다.**

**남은 관찰(참고, 이번 재검증의 판정 대상은 아님)**:
- `verification_results`의 evidence 텍스트에 한글이 모지박 손상(`app.py�� add �Լ�...`)돼 저장됨 — intent.json **파일 자체**는 UTF-8로 정상 기록됐으니 콘솔/캡처 인코딩 표시 문제로 보임(§5 HIGH 항목과는 별개, 신규는 아니고 P8에서도 이미 관측된 동일 계열).
- 두 verification_results 항목 모두 명백히 성공한 증거(`intent set` JSON 출력, `add(2,3)=5` 등)인데 `success: false` — §5 HIGH(`text_indicates_success` 브리틀니스)가 여전히 재현됨. 이번 과제 범위 밖이라 별도 조치 없이 기록만 남김.

**정리·무결성**: 스크래치 디렉토리(`p14-mechcheck`, `p14-recheck`) 삭제 완료. `~/.claude.json`만 변경(세션 북키핑, 양성), 나머지 4개 설정 파일 해시 불변. 실제 저장소에 `.fable-lite/` 누출 없음. 코드 변경 없음(순수 재검증).
