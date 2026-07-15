# 사건 기록: 홈 루트 세션에서 provenance 게이트 구조적 무력화 (2026-07-15)

> 기록 목적: 프로젝트 루트가 **사용자 홈 디렉토리**인 세션에서 Change Provenance가 `scope_too_large`로 고정되어, Stop 게이트가 매 턴 2회 차단 후 cap으로 통과되는 **구조적 무력화** 실측 사례.
> 발생 세션: `C--Users-rotat`(홈) 좌상 Claude(Opus 4.8 1M), wmux 우하 pane 모델 교체 작업 중.
> 성격: 코드 결함 아님. **환경(홈 루트)과 스캔 예산의 불일치** + **상태 디렉토리 세션 공유**의 설계 경계 문제.

## 환경

- show-me-the-work v2.0.0 (로컬 디렉토리 `~/fable-lite`), Windows 11
- 세션 cwd = `C:\Users\rotat` (홈). 프로젝트 폴더가 아닌 홈에서 Claude Code 기동
- 상태 디렉토리 = `C:\Users\rotat\.fable-lite\` (홈에 1개)
- 작업 내용: `~/.claude/skills/wmux-project-start/SKILL.md` 외 3개 파일 편집 (총 수 KB)

## 증상 (실측)

1. Stop 훅이 차단: `[smtw] Stop gate: provenance 범위가 너무 커서 local-or-unknown 변경 가능성을 안전하게 관측할 수 없습니다. 프로젝트 루트를 좁히고 다시 관측하세요.`
2. 게이트 메시지대로 "루트를 좁히기"를 **세션 내에서 수행할 수 없음** — 루트는 cwd 파생이고 세션 중 변경 불가 (아래 귀속 판정 참조).
3. 조사·증거 제시로 재응답해도 동일 차단 반복 (게이트는 어시스턴트 텍스트가 아니라 ledger 상태만 판정하므로 예상된 동작).
4. 2회 차단 후 cap(`verify_state.py:205-217`, "최대 2회 차단 후 통과합니다")으로 통과 — 작업은 진행되나 게이트는 아무것도 검증하지 못함.

## 원인 (ledger 실측)

`.fable-lite/ledger.json` → `active_turns["claude_code:8019050a-c68f-4e22-a098-04d94735c6eb:claude"]`:

```
provenance_status         = 'scope_too_large'
provenance_status_reason  = 'byte_limit'      ← entry_limit·deadline 아님
provenance_mutation_capable = True
provenance_incomplete     = False
```

Stop 게이트(`core/verify_state.py:232-245`)는 `status == SCOPE_TOO_LARGE AND mutation_capable is True`이면 **verified 여부와 무관하게 무조건 block**. 파일을 쓸 수 있는 세션인 한 홈 루트에서는 탈출 경로가 cap뿐이다.

### 예산 대비 실측

| 항목 | 예산 (`core/provenance_types.py:10-12`) | 홈 실측 | 배수 |
|---|---|---|---|
| bytes | `DEFAULT_MAX_SCAN_BYTES` = 256MB | `.claude` 단독 2,835.8MB | **11.1배** |
| entries | `DEFAULT_MAX_SCAN_ENTRIES` = 10,000 | `.claude` 단독 37,129 | **3.7배** |
| seconds | `DEFAULT_FULL_SCAN_SECONDS` = 8.0 | 홈 전체 `du` 120초 타임아웃 초과 | 측정 불가 |

`.claude`만으로 이미 11배이며, 홈에는 `OneDrive` 3.5GB가 추가로 있다. **어떤 예산 상향으로도 홈 루트는 성립하지 않는다.**

### ★ 예산을 소진시키는 주체가 감독 도구 자신의 부산물

`.claude` 2,835.8MB 중 **`.claude/projects` = 1,744.7MB (61%)** 가 Claude Code **세션 대화 로그(jsonl)** 다. 최대 단일 파일 108.9MB(`ba4289e9-…jsonl`), 그 다음 50.0MB / 36.2MB. 감독 대상 소스가 아니라 **에이전트가 append하는 로그**가 스캔 예산을 잡아먹어 감독을 마비시킨다. 게다가 이 로그는 턴마다 증가하므로 매 턴 재현된다.

## ★ 부수 발견 (더 심각): 홈 루트에서 상태 디렉토리가 전 세션에 공유됨

`.fable-lite/`는 루트당 1개다. 루트가 홈이면 **홈에서 켠 모든 Claude 세션이 같은 ledger·contract를 공유**한다.

- `ledger.json`의 `active_turns`에 서로 다른 세션 **4개**가 동시 등재:
  `674a4796…` / `68983a5c…` / `8019050a…`(본 세션) / `e48909ec…`
  → 앞 3개는 전부 `scope_too_large` + `byte_limit`으로 동일. `e48909ec…`는 `provenance_incomplete=True`.
- **`contract.json`이 본 세션 작업과 무관한 내용이었다** — 조회 시점 내용은 *"agy(Antigravity CLI)의 Gemini 3.5 Flash vs 3.1 Pro 실측 벤치마크"* (같은 날 다른 세션의 계약). 즉 게이트가 **A 세션의 계약으로 B 세션의 턴을 판정**하는 상태.
- Intent/계약 대조가 성립하지 않으므로, 홈 루트에서는 provenance뿐 아니라 **계약 기반 게이트 전반의 전제가 깨진다**.

### cap 회계: "최대 2회 차단"은 **사용자 발화 1회당** 2회다

`MAX_STOP_BLOCKS = 2`(`verify_state.py:31`)의 카운터는 `turn["blocks"]["stop"]`이며, 새 턴 생성 시 `turn["blocks"] = {"stop": 0}`으로 **리셋**된다(`core/ledger_v2.py:104`). 새 턴은 UserPromptSubmit → `start_turn`(`adapters/claude_code/user_prompt_submit.py:81,87`) 경로로 **사용자가 프롬프트를 보낼 때마다** 시작된다.

- Stop 차단 후 어시스턴트가 재응답하는 동안은 같은 턴 → 카운터 누적 → 2회째 이후 cap 통과.
- **사용자가 새로 말하면 카운터가 0으로 리셋** → 그 발화에서 다시 2회 차단.

본 세션 실측이 이 회계와 정확히 일치한다:

```
blocked_attempts = 3, cap_allows = 1, turn.blocks.stop = 1   (top-level stop_blocks = 2)
→ block(1) → block(2) → cap_allow(1) → [사용자 새 발화 → 리셋] → block(3), stop=1
```

**정정**: 이는 무한 루프가 아니다(조사 중 1차로 "cap 무한 루프" 가설을 세웠으나 카운터 추적으로 **기각**). 다만 `e48909ec…`의 `cap_allows=9`는 "9회 무력 통과"이자 곧 **사용자 발화 9회 × 매번 2턴 재생성**을 의미한다. 홈 루트에서는 이 비용이 **모든 발화에 무조건 부과**된다 — 게이트가 한 번도 해소되지 않으므로(`resolved_attempts=0`) 순수 손실이다.

### 게이트 무력화 실측 (scorecard)

```
e48909ec…: by_reason['stop.provenance_incomplete'] = {blocked_attempts: 2, cap_allows: 9, resolved_attempts: 0, recovered_scopes: 0}
8019050a…: by_reason['stop.provenance_incomplete'] = {blocked_attempts: 1, cap_allows: 0, resolved_attempts: 0, recovered_scopes: 0}
```

`cap_allows=9` = 해당 세션에서 **9회** "2번 막고 그냥 통과"가 발생. `resolved_attempts=0` / `recovered_scopes=0` — **단 한 번도 실제로 해소된 적이 없다.** 게이트는 검증을 제공하지 않고 턴 재생성 비용(홈 세션은 Opus 1M 컨텍스트)만 부과하고 있다.

## 경쟁 가설과 증거

- **가설 1**: 해당 턴의 변경량이 커서 예산 초과 → **기각**. 변경은 4파일 수 KB. 결정적으로, **파일을 전혀 변경하지 않은 세션(`674a4796…`, `68983a5c…`)도 동일하게 `scope_too_large`/`byte_limit`** — 턴 변경량과 무관함이 실증됨.
- **가설 2**(채택): 루트=홈이라 스캔 대상 총 바이트가 예산을 구조적으로 초과 → **지지**. 실측 11.1배, 홈 전체 스캔은 8초 예산도 초과. 홈에서 켠 모든 세션에서 재현.
- **가설 3**: provenance 구현 결함 → **기각**. 예산 초과 시 clean을 주장하지 않고 `SCOPE_TOO_LARGE`를 정직하게 선언하는 것은 **설계된 안전 동작**(`core/provenance.py:302-304`). 결함이 아니라 적용 범위 밖 환경에 놓인 것.
- **기각**: "게이트/훅을 꺼서 소음만 제거" — 원인이 아니라 경보를 끄는 조치. 진짜 프로젝트 루트 세션의 감독까지 잃는다.

## fable-lite 관점 함의 (커버리지 갭)

1. **홈 루트는 지원 범위 밖인데 그 사실이 표현되지 않는다.** 현재는 "예산 초과"라는 증상으로만 드러나고, 사용자에게는 매 턴 차단 → cap 통과라는 소음으로 체감된다. 홈(또는 `$HOME` 자체)이 루트일 때는 **명시적으로 unsupported 판정**을 내리는 편이 정직하다.
2. **게이트 메시지가 실행 불가능한 지시를 준다.** "프로젝트 루트를 좁히고 다시 관측하세요"는 루트가 cwd 파생(`adapters/claude_code/common.py:42-52` — `payload.project_root`가 없으면 cwd, 그리고 cwd 밖이면 cwd로 강제)이라 **세션 내에서 수행 불가**. 에이전트가 따를 수 있는 지시(예: "프로젝트 폴더에서 세션을 다시 여세요")로 바꿔야 한다.
3. **상태 디렉토리의 세션 격리 부재**가 홈 루트에서 계약 오염으로 표면화. `.fable-lite/`가 루트 1개인 설계는 "루트 = 단일 프로젝트 = 단일 작업"을 암묵 전제하는데, 홈 루트는 이 전제를 깬다. 멀티에이전트 게이트 본편(v-next) 설계 시 **동일 루트 다중 세션**을 1급 케이스로 다뤄야 한다.
4. **cap이 무력화를 은폐한다.** `cap_allows=9 / resolved_attempts=0`이면 게이트는 사실상 없는 것과 같은데, 겉으로는 "차단 후 통과"라 정상 동작처럼 보인다. Scorecard(v-next)에서 **`cap_allows >> resolved_attempts` 패턴을 무력화 신호로 승격**해 사용자에게 노출할 것.
5. 스캔 예산 소진의 61%가 `.claude/projects`(에이전트 로그)라는 사실은 **어댑터 기본 제외 목록**의 후보를 시사한다. 다만 제외해도 잔여 1,091MB로 여전히 4.3배 → **단독으로는 해결책이 아니다**(부분 완화).

## 해결 옵션 (미결정 — 사용자 판단 대기)

| 안 | 내용 | 효과 | 리스크 |
|---|---|---|---|
| **A (권고)** | 루트가 `$HOME`이면 provenance 게이트를 **unsupported로 명시 skip** | 홈 세션 소음 제거. 계약 오염 전제 자체를 인정. 진짜 프로젝트 세션 감독은 100% 유지 | 홈에서의 변경은 감독 공백 — 단 현재도 cap으로 공백이므로 실질 손실 없음(정직성만 개선) |
| **B** | `.claude/projects` 등 에이전트 로그를 기본 제외 | 2,836MB → 1,091MB | **여전히 예산 4.3배로 미해결**. 계약 오염 미해결 |
| **C** | 현행 유지 | 없음 | 매 턴 2회 차단 + 턴 재생성 비용, 게이트 무력화 지속 |

A와 B는 배타적이지 않다(B는 프로젝트 루트 세션의 스캔 효율에도 도움). 다만 **본 사건의 해결책은 A**이며 B는 별건의 최적화로 취급하는 것이 정확하다.

## 재현 절차

```bash
# 1. 홈에서 Claude Code 세션 기동 (cwd = %USERPROFILE%)
# 2. 아무 파일이나 1개 편집 후 턴 종료
# 3. Stop 게이트가 scope_too_large로 차단 → 2회 후 cap 통과 확인
python -c "
import json; d=json.load(open('.fable-lite/ledger.json',encoding='utf-8'))
for k,v in d['active_turns'].items():
    print(k, v.get('provenance_status'), v.get('provenance_status_reason'))
"
```

## 관련

- `docs/reviews/prov-fix-ultracode.md`, `docs/reviews/prov-fix-agy.md` — provenance 수정 이력
- `docs/reviews/session-scorecard-*.md` — Scorecard 설계 (본 건의 함의 4 반영 대상)
- `core/verify_state.py:232-245` (게이트), `core/provenance.py:297-308` (예산), `core/provenance_types.py:10-12` (기본값), `adapters/claude_code/common.py:42-52` (루트 결정)
