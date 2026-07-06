# P5 실설치 E2E — fable-lite 플러그인 훅 실발동 검증

**검증자**: Claude Code (Sonnet 5, 우하 pane) · **방법**: 격리된 테스트 디렉토리에서 nested `claude -p` 세션을 `--plugin-dir`로 fable-lite를 로드해 3회 실행, `--debug hooks`로 훅 발동을 직접 관측.
**결론(한 줄)**: hooks.json 로드·`${CLAUDE_PLUGIN_ROOT}` 치환·4개 훅 전부 실제로 발동함을 라이브로 확인. 동시에 P4 리뷰의 Critical 발견 대부분이 그 사이 수정되어 있음을 코드+라이브 양쪽으로 재확인했고, 라이브 테스트에서만 드러나는 새 이슈 2건을 발견했다.

---

## 0. 설치 방법 선택

`.claude-plugin/marketplace.json`이 없어 `plugin.json`과 일치시켜 신규 작성(name/description/version 동일화, `plugins[0].source="./"`). `claude plugin validate "C:/Users/rotat/fable-lite"` → **✔ Validation passed** (읽기 전용 검증, 상태 변경 없음).

설치 방법은 두 가지를 저울질했다:
- **`claude --plugin-dir <path>`**: `claude --help`에 "Load a plugin from a directory ... **for this session only**"로 명시됨 — 세션 종료 시 아무 것도 영구화하지 않는다.
- **`/plugin marketplace add` + `/plugin install`** (CLI 등가물: `claude plugin marketplace add` / `claude plugin install`, `claude plugin --help`로 확인): `~/.claude/plugins/installed_plugins.json`·`known_marketplaces.json`에 영구 기록된다.

과제 지시의 "네 자신의 현재 세션 설정을 훼손하지 마라"를 최우선으로 삼아 **`--plugin-dir`만 실제 실행**했다. marketplace-install 경로는 CLI 서브커맨드 존재를 확인(`claude plugin --help`)하고 `claude plugin validate`로 매니페스트 유효성까지 확인했지만, 실제 설치는 실행하지 않았다 — `--plugin-dir`로 이미 결정적 증거를 얻었고, 굳이 영구 상태를 건드릴 필요가 없었기 때문이다.

## 1. 테스트 설계

격리 디렉토리(`<scratchpad>/fable-lite-e2e`, git init, 실제 fable-lite repo와 무관)에서 `--setting-sources project`(사용자 설정 미로딩) + `--no-session-persistence` + `--debug hooks --debug-file <log>`로 3회 실행:

| 테스트 | 프롬프트 | tools | 목적 |
|---|---|---|---|
| A | "이 함수가 왜 안 돼" | `""`(없음) | UserPromptSubmit → investigation 팩 주입 확인 |
| B | "calc.py의 add 함수가 ... 고쳐서 ..." | `Write` | PostToolUse/ledger 확인 (실패 — 아래 4.2) |
| C | "Write 도구로 note.txt ... 저장해라" | `Write` | PostToolUse/ledger 확인 (B의 교훈으로 재설계, 성공) |

## 2. 테스트 A — UserPromptSubmit 실발동 확인

디버그 로그에 다음이 그대로 찍혔다:

```
Read manifest hooks for plugin fable-lite (enabled=true): ./adapters/claude_code/hooks.json
Loaded inline plugin from path: fable-lite
Loaded 1 session-only plugins from --plugin-dir
Registered 4 hooks from 1 plugins
...
Hooks: Checking first line for async: {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
  "additionalContext": "fable-lite 활성화: 작업 규율을 절차로 적용하세요.\nmode=deep\n조사 팩 준수 필수: 출력에 `가설 1:`, `가설 2:`, `가설 3:`, `기각:`, `증거:`를 포함하세요.\n수정 전 재현과 경쟁 가설을 먼저 기록하세요."}}
Hook UserPromptSubmit (python "${CLAUDE_PLUGIN_ROOT}/adapters/claude_code/user_prompt_submit.py") provided additionalContext (133 chars)
```

- **4개 훅 전부 등록됨**, `${CLAUDE_PLUGIN_ROOT}` 치환 후 실제 실행되어 valid JSON을 반환함(로그의 커맨드 문자열은 치환 전 템플릿이 그대로 로깅되는 것일 뿐 — 실행 자체는 성공했다는 게 핵심 증거).
- `mode=deep` 정확히 분류, investigation 팩 지시 정확히 주입 — N4(한국어 라우팅)가 실제 세션에서 작동함을 확인.
- **추가 확인**: Stop 훅에서 N1(조사 팩 마커 검증)이 실제로 발동해 차단했다:
  ```
  {"decision": "block", "reason": "fable-lite N1: 조사 팩 마커가 부족합니다. `가설 1:`/`Hypothesis 1:`, `증거:`/`Evidence:`, `기각:`/`Rejected:`를 포함하세요. / Investigation pack markers are required."}
  ```
  이는 이전 P4 리뷰(`p4-sonnet-adapters.md` C3)에서 "N1을 호출하는 훅이 전혀 없음"이라고 지적했던 것과 반대 결과다 — 코드를 다시 확인하니 `stop.py`가 `transcript_last_assistant_text(payload)`를 읽어 `evaluate_stop`에 넘기도록 그 사이 수정되어 있었다(§4.1). **P4의 C3 발견은 현재 코드 기준으로 해소된 것으로 정정한다.**

### 신규 발견 A — Stop 훅 반복 사이클 (라이브에서만 드러남)

차단 1회 이후, `stop_hook_active=true`로 넘어가 매번 `{"decision" 없음, "message": "Stop hook loop guard: allow.", additionalContext: 동일 문구}`를 반환했는데, 이 additionalContext가 채워져 있다는 이유만으로 Claude Code가 모델을 **약 12회 더 재호출**했다(11:53:53 → 11:55:38, 약 105초, 대부분 이 사이클에 소모). 최종적으로는 MCP 프록시 연결이 119초 만에 끊기면서(`connection closed after 119s`) 세션이 종료됐다 — 자연 수렴이 아니라 외부 타임아웃에 의해 끝난 것으로 보인다. `stop.py`가 allow 경로에서도 항상 `hookSpecificOutput.additionalContext`를 채워 보내는 것이 원인으로 보이나, Claude Code 내부 루프 로직 자체는 관측할 수 없어 정확한 인과는 단정하지 않는다. 결과: 세션 1회에 $0.19 / 118초 소요(과금·응답성 영향).
**권장**: `stop.py`의 allow 경로에서 `additionalContext`를 빈 문자열/생략으로 바꾸고 재현되는지 확인.

## 3. 테스트 B → C — PostToolUse/ledger 확인

### 3.1 테스트 B (실패 — 설계 결함, 훅 결함 아님)
"calc.py의 add 함수가 ... **고쳐서** ..." 프롬프트가 `investigation`+`completion` 두 팩을 동시에 요구하게 됐고(아래 신규 발견 B), 모델이 마커 없이 응답해 N1에 즉시 차단됨 → `Write`를 한 번도 호출하지 못한 채 끝남(`calc.py`는 수정되지 않은 채 그대로, ledger의 `changed_files_seen: []`). PreToolUse/PostToolUse 로그 자체가 아예 없었다 — 훅이 안 걸린 게 아니라 **모델이 도구를 호출할 기회 자체를 못 얻은 것**.

### 신규 발견 B — MULTI_STORY_PATTERNS의 "하고" 잔존 과매칭
ledger에 `"packs": ["investigation", "completion"]`, `"needs_goals": true`가 찍혔다. 프롬프트에 "하고"가 들어간 곳은 "뺄셈을 **하고** 있어"뿐인데, `core/classify.py`의 `MULTI_STORY_PATTERNS`에 `"하고"`가 여전히 무조건 매칭 항목으로 남아 있어(같은 리스트의 "도"/"또"는 이전 리뷰 이후 "그리고 또" 같은 문맥 결합형으로 좁혀졌음을 확인했으나 "하고"는 그대로) 단일 버그 수정 요청이 다중 스토리로 오분류됐다. 이전 P4 리뷰 H2("도"/"또" 과매칭)와 같은 계열의 잔존 사례.
**권장**: "하고"도 "도"/"또"와 동일하게 문맥 결합형으로 좁히거나 제거.

### 3.2 테스트 C (성공 — investigation 팩을 안 건드리는 프롬프트로 재설계)
"Write 도구로 note.txt ... 저장해라"(`classify_prompt` 사전 확인: `mode=quick, packs=[]`)로 재실행:

```
"Hook PreToolUse:Write (PreToolUse) success:\n{}\n"
[INFO] tool_dispatch_start tool=Write ... permissionDecisionMs=9
Hooks: Checking first line for async: {"systemMessage": "fable-lite ledger: recorded 1 change(s)."}
"Hook PostToolUse:Write (PostToolUse) success:\n{\"systemMessage\": \"fable-lite ledger: recorded 1 change(s).\"}\n"
```

`note.txt` 실제로 생성됨(내용 "hello" 확인). 격리 디렉토리의 `.fable-lite/ledger.json`:
```json
{
  "changed_files_seen": ["C:\\...\\fable-lite-e2e\\note.txt"],
  "change_kinds": ["docs"],
  "task_mode": "quick", "packs": [], "stop_blocks": 0, ...
}
```
**PostToolUse가 실제 Write 호출의 절대경로 파일명을 정확히 잡아 ledger에 기록했다.** N1 차단이 없는 quick 모드라 Stop도 즉시 allow, 2턴 만에 종료(테스트 A/B의 반복 사이클 없음 — 신규 발견 A는 "차단이 한 번이라도 발생한 이후"에만 나타나는 것으로 보인다).

이 결과는 P4 리뷰(`p4-sonnet-adapters.md` C1)에서 "`payload.get(\"file_path\")`가 최상위 키가 아니라 실사용에서 항상 비어 ledger가 무력화된다"고 지적했던 것과 반대다. `adapters/claude_code/common.py`를 다시 읽어보니 그 사이 `tool_input(payload)`/`tool_response(payload)`/`tool_file_paths(payload)` 헬퍼가 추가되어 `payload["tool_input"]["file_path"]`를 올바르게 파고들도록 수정돼 있었다. **P4의 C1 발견도 현재 코드 기준으로 해소된 것으로 정정한다.**

## 4. P4 리뷰 대비 변경 사항 (코드 재확인 + 라이브 재확인)

라이브 테스트 도중 코드가 이미 수정된 상태임을 발견해 관련 파일을 다시 읽었다. P4에서 지적한 항목 대부분이 해소되어 있다:

| P4 발견 | 현재 상태 | 근거 |
|---|---|---|
| C1 tool_input 미매핑 | **해소** | `common.py`에 `tool_input`/`tool_response`/`tool_file_paths` 등 추가, 테스트 C로 라이브 확인 |
| C2 scope_guard 절대/상대경로 불일치 | **해소** | `scope_guard.py`의 `_canonical()`이 `Path.resolve()`로 절대경로화 후 비교 |
| C3 N1 미연결(compliance.py 호출 없음) | **해소** | `common.py`의 `transcript_last_assistant_text()` + `stop.py`가 이를 `evaluate_stop`에 전달, 테스트 A로 라이브 확인 |
| H3 PreToolUse matcher에 Bash/PowerShell 없음 | **해소** | `hooks.json:16` matcher에 `Bash|PowerShell` 추가됨 |
| M1 timeout 미설정 | **해소** | 4개 훅 전부 `"timeout": 10` 추가 |
| M3 scope_guard 대소문자 무시 없음 | **해소** | `_canonical()`에 `.casefold()` 적용 |
| M4 근본원인 사이드파일 경고 문구 미개선 | **해소** | 경고 메시지에 "근본원인 수정을 위한 사이드파일이면 무시 가능" 문구 추가됨 |
| H1/H2 classify.py 한국어 패턴(안되다 활용형·도/또) | **대부분 해소, "하고" 잔존** | `DEBUG_REGEXES`가 정규식화됨(`안\s*(되\|돼\|됨\|됐\|될\|되지\|되는\|되네\|되나)`), `risk_terms.py` 분리됨. 단 "하고"는 이번 라이브 테스트로 잔존 과매칭 재확인(§3.1 신규 발견 B) |

## 5. 안전성 확인 (설치 제거 · 설정 무결성)

- 격리 테스트 디렉토리(`<scratchpad>/fable-lite-e2e`) **완전 삭제 완료**.
- 실제 fable-lite repo에 `.fable-lite/` 등 테스트 흔적 **없음**(확인 완료).
- 전/후 해시 대조 — 4개 핵심 공유 설정 파일 **완전 동일**(바이트 단위):
  - `~/.claude/plugins/installed_plugins.json` — 동일
  - `~/.claude/plugins/known_marketplaces.json` — 동일
  - `~/.claude/settings.json` — 동일
  - `~/.claude/settings.local.json` — 동일
- `~/.claude.json`은 해시가 변경됐으나 내용 대조 결과 Claude Code가 모든 세션에서 공통으로 남기는 프로젝트 북키핑 + 플러그인 사용횟수 카운터(`"fable-lite@inline": {"usageCount": 39, ...}`) 증가뿐이었다 — 설치 레코드가 아니며, `installed_plugins.json`이 불변이므로 재부팅해도 fable-lite가 자동 로드되지 않는다.
- `/plugin marketplace add`+`/plugin install` 경로는 실행하지 않았다(§0 사유).

## 6. 스모크 테스트 대비 이번 검증이 추가로 확인한 것

기존 스모크 테스트(`tests/test_adapters.py`)는 훅 스크립트를 손수 구성한 payload로 직접 호출해 "스크립트가 정상 종료하는가"만 확인했다. 이번 라이브 검증은 그 위에:
1. Claude Code가 **plugin.json/hooks.json을 실제로 파싱**해 4개 훅을 등록하는가
2. `${CLAUDE_PLUGIN_ROOT}` 치환이 **실제 실행 시점**에 정상 동작하는가
3. **실제 Claude Code가 만드는 진짜 payload 형태**(중첩 `tool_input`/`transcript_path`)로 어댑터가 정상 동작하는가
4. 여러 훅이 **한 세션 안에서 연쇄**할 때(UserPromptSubmit → PostToolUse → Stop) 발생하는 상호작용(신규 발견 A의 반복 사이클처럼, 개별 훅 단위 테스트로는 절대 드러나지 않는 문제)

를 추가로 검증했다 — 이 네 가지는 원리상 스모크 테스트로는 검증 불가능한 영역이다.

## 다음 확인 권장 사항
1. `stop.py`의 allow 경로 additionalContext 반복 사이클(신규 발견 A) 원인 조사 — 재현 프롬프트: 임의의 디버그성 한국어 프롬프트 + `--tools ""`.
2. `MULTI_STORY_PATTERNS`의 `"하고"` 완화(신규 발견 B).
3. 이번 테스트는 `--tools ""`/`"Write"`로 제한한 상태라 모델이 "실제로 재현·조사"할 수단이 없었다 — 도구 제한 없는 정상 세션에서도 동일하게 N1 마커 없이 첫 응답이 나오는지, 아니면 도구를 써서 자연스럽게 조사 후 마커를 채우는지는 별도 확인 필요(이번 테스트의 인위적 제약일 가능성 있음).
