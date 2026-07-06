# fable-lite v2 멀티 CLI 어댑터 리서치 및 설계

본 문서는 `fable-lite`의 판정 코어(순수 Python, dict-in/dict-out 구조)를 Claude Code 외의 타 CLI 환경(Codex CLI, Antigravity CLI)으로 확장하기 위한 v2 어댑터 설계 및 이식성 분석 결과입니다.

## 1. 타겟 CLI 환경 분석

### 1.1 OpenAI Codex CLI
Codex CLI는 Claude Code와 매우 유사한 설정 및 훅 구조를 가집니다.
*   **설정 표면 (`config.toml`)**: 글로벌(`~/.codex/config.toml`) 및 로컬 프로젝트(`.codex/config.toml`) 설정을 지원합니다. MCP 서버 등록과 훅 활성화(`[features] codex_hooks = true`)가 여기서 이루어집니다.
*   **규율 주입 (`AGENTS.md`)**: `AGENTS.md`를 통해 프롬프트 수준의 소프트 지시(Soft Prompting)를 기본 지원합니다. `fablever`가 `--codex-style-only` 플래그로 이 방식을 사용해 스타일을 이식한 선례가 있습니다 (참고: [fablever README](https://github.com/elon-choo/fablever)).
*   **하드 게이트 가능성 (Hooks)**: `hooks.json`을 통해 외부 스크립트를 연결할 수 있으며, 지원 이벤트가 `SessionStart`, `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`으로 **Claude Code의 훅 인터페이스와 사실상 동일**합니다. 따라서 하드 차단(Hard Blocking)이 가능합니다.
*   **MCP 연동 여부**: `config.toml`에 `[mcp_servers]` 블록을 통해 지원합니다. 단, MCP는 "도구 제공"에 특화되어 있어 모델의 자발적 종료(Stop)나 도구 사용 전(PreToolUse)을 가로채어 차단하는 용도로는 한계가 있습니다.

### 1.2 Antigravity CLI (agy)
Antigravity(OmA - oh-my-antigravity 플러그인 생태계)는 고도화된 자체 훅 및 파이프라인 매니저를 보유하고 있습니다.
*   **설정 및 훅 표면 (`hooks.json`)**: `.omg/state/hooks.json`을 통해 세밀한 훅 프로파일 제어가 가능합니다 (경로 추측 기반: `~/.gemini/config/plugins/oh-my-antigravity/skills/hooks/SKILL.md` 분석).
*   **지원 이벤트**: `BeforeAgent`, `AfterAgent`, `BeforeModel`, `AfterModel`, `BeforeTool`, `AfterTool`, `Notification` 등 Claude Code보다 훨씬 세분화된 네이티브 이벤트를 지원합니다.
*   **하드 게이트 가능성**: `P0-safety` 레인과 같은 결정론적 실행 경로를 통해 `blocked`, `stopped` 상태로 강제 전환할 수 있어 하드 강제가 완벽히 지원됩니다.

---

## 2. 게이트별 이식성 매트릭스 (Portability)

현재 `fable-lite` 코어 로직을 3개 CLI 플랫폼에 이식할 때의 강제력 수준입니다.

| ID | 기능명 | Claude Code (v1) | Codex CLI (v2 타겟) | Antigravity CLI (agy) (v2 타겟) |
| :--- | :--- | :--- | :--- | :--- |
| **S1** | 검증 접지 (관측 강제) | 하드 강제 (`Stop`) | 하드 강제 (`Stop`) | 하드 강제 (`AfterAgent` / `SessionEnd`) |
| **S2** | 분해 + 증거 게이트 | 하드 강제 (`Stop`) | 하드 강제 (`Stop`) | 하드 강제 (`AfterAgent`) |
| **S3** | 체계적 조사 (가설 등) | 소프트 지시 (팩 주입) | 소프트 지시 (`AGENTS.md`) | 소프트 지시 (팩 주입) |
| **S4** | 조기종료 방지 | 하드 강제 (`Stop`) | 하드 강제 (`Stop`) | 하드 강제 (`AfterAgent` 차단) |
| **N1** | 팩 준수 검증 (마커 확인) | 하드 강제 (구현 시) | 하드 강제 (`PostToolUse` / `Stop`) | 하드 강제 (`AfterModel` 파싱) |
| **N2** | 복합 스토리 플랜 강제 | 하드 강제 (`PreToolUse`) | 하드 강제 (`PreToolUse`) | 하드 강제 (`BeforeTool` 차단) |
| **N3** | 범위 이탈 감지 | 하드 강제 (`PostToolUse`) | 하드 강제 (`PostToolUse`) | 하드 강제 (`AfterTool` 경고) |
| **R1** | High-risk (spec-before-edit) | 하드 강제 (`PreToolUse`) | 하드 강제 (`PreToolUse`) | 하드 강제 (`BeforeTool` 차단) |

> **결론**: Codex CLI와 Antigravity CLI 모두 하드 게이트 구현에 필요한 훅 진입점(Hook Entrypoints)을 네이티브로 제공하므로, **모든 기능을 하드 강제 수준으로 이식 가능**합니다.

---

## 3. v2 권장 스코프 및 아키텍처

fable-lite v2 확장을 위한 최적의 접근법과 순서는 다음과 같습니다.

### 3.1 1단계: Codex CLI 어댑터 우선 개발
*   **이유**: Codex CLI의 훅 인터페이스(`PreToolUse`, `PostToolUse`, `Stop`) 구조가 Claude Code와 가장 유사합니다. 기존 `adapters/claude_code/*.py` 래퍼 스크립트의 페이로드 매핑 계층(Payload Mapping Layer)만 약간 수정하면 `fable-lite`의 순수 Python 코어(`core/`)를 그대로 100% 재사용할 수 있습니다.
*   **형태 (래퍼 스크립트 기반)**: 
    1.  `fable-lite` 패키지 내에 `adapters/codex_cli/` 경로를 신설.
    2.  Codex의 `hooks.json`이 `adapters/codex_cli/` 하위의 Python 래퍼 스크립트를 호출하도록 구성.
    3.  소프트 지시(S3 팩 등)는 `AGENTS.md` 자동 생성기/병합기를 통해 프로젝트 루트에 동적으로 주입(fablever 방식 차용).

### 3.2 2단계: Antigravity CLI (agy) 어댑터
*   **이유**: OmA 파이프라인에 대한 이해도가 필요하나, 하드 게이트의 이념(Team-safety, P0-safety)이 `fable-lite`와 완벽히 부합합니다.
*   **형태 (OmA 훅 파이프라인 통합)**:
    1.  `adapters/agy/` 어댑터를 생성하여 `BeforeTool` -> `evaluate_pretool_contract()`, `AfterAgent` -> `evaluate_stop()` 등으로 매핑.
    2.  `.omg/state/hooks.json`을 수정하여 `P0-safety` 레인에 fable-lite 판정 스크립트를 등록.

### 3.3 배제된 형태 (왜 MCP 서버가 아닌가?)
*   **이유**: MCP(Model Context Protocol)는 모델에게 '외부 시스템(DB, 파일, API)을 읽거나 쓰는 도구(Tool)'를 부여하는 표준입니다. 그러나 `fable-lite`의 핵심 가치인 "모델이 검증 없이 종료하려고 할 때 강제로 막는(Stop hook 차단) 행위"나 "도구 실행 전 강제로 스펙을 요구하는(PreToolUse 차단) 행위"는 모델의 자율적 행동을 외부에서 개입(Intercept)해야 하므로 MCP 서버의 권한 밖입니다. 따라서 **훅(Hook) 시스템에 바인딩된 래퍼 스크립트 형태**가 필수적입니다.
