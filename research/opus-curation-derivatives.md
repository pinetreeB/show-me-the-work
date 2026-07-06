# Fable 재현 시도 오픈소스 — 큐레이션/파생 추적 리서치

> **분석자**: Claude Opus 4.6 (Thinking) — fable-lite 우하 구현보조 pane  
> **일시**: 2026-07-06  
> **방법론**: 4개 각도(큐레이션 저장소 / 저자 프로필 / 마켓플레이스 / Reddit)에서 교차 추적

---

## 각도 1: awesome-claude-fable-5 큐레이션 저장소

**소스**: https://github.com/Anil-matcha/awesome-claude-fable-5 (★297, fork 69)

### 분석 결과

이 저장소는 94개 Fable 5 사용 사례를 수집하지만, **"하위 모델로 Fable 행동 재현" 관련 항목은 직접 포함하지 않는다**. 카테고리 8개(Coding / Agents / Games / Visual / Documents / Tutorials / Platform-API / Evaluations)에서 Fable 5의 *활용* 사례만 큐레이트하며, Opus에서 재현하려는 도구나 하네스는 범위 밖이다.

**간접 관련 항목 (Tutorials 카테고리에서 추출)**:

| # | 항목 | URL 유형 | 관련성 |
|---|---|---|---|
| Case 20 | How Fable 5 Changes Claude Code Workflows | X/Twitter 포스트 | 워크플로우 변화 관찰 — 재현 도구 아님 |
| Case 21 | One-Billion-Token Review and Routing Strategy | X/Twitter 포스트 | 모델 라우팅 전략 — Fable/Opus 사용 분리 논의 |
| Case 28 | Using Goals and Workflows in Claude Code (`/goal`) | X/Twitter 포스트 | `/goal` 내장 기능 활용법 — 재현보다 활용 |

> **결론**: awesome-claude-fable-5는 Fable *활용* 큐레이션이지 *재현* 도구 큐레이션이 아니다. "하위 모델에서 Fable 행동 재현" 도구는 **0건**. 이 저장소 소유자(Anil-matcha)는 MuAPI 프록시 서비스의 홍보 목적이 주된 동기로 보인다.

---

## 각도 2: fivetaku 저자 프로필 + fablize 파생 추적

### 2-1. fivetaku GitHub 프로필 분석

**소스**: https://github.com/fivetaku (138 repos, 293 followers)

fivetaku는 Claude Code 플러그인 생태계의 **주요 기여자**이며, fablize 외에도 관련 프로젝트를 다수 운영한다:

| 레포 | ★ | 설명 | fable-lite 관련성 |
|---|---|---|---|
| [gptaku_plugins](https://github.com/fivetaku/gptaku_plugins) | 688 | Claude Code 플러그인 마켓플레이스 | fablize의 배포 채널 — 마켓플레이스 구조 참고용 |
| [vibe-sunsang](https://github.com/fivetaku/vibe-sunsang) | 166 | Claude Code 바이브코더용 AI 멘토 에이전트 | 대화 분석·멘토링 — 행동 교정 접근법 참고 |
| [pumasi](https://github.com/fivetaku/pumasi) | 58 | Claude(PM) + Codex CLI(병렬 개발자) 오케스트레이션 | 다중 에이전트 병렬 구현 패턴 — fable-lite의 wmux 환경과 유사 |
| [show-me-the-prd](https://github.com/fivetaku/show-me-the-prd) | 32 | 인터뷰 기반 PRD 생성기 | 간접 관련 |
| [cc101](https://github.com/fivetaku/cc101) | 265 | Claude Code 한국어 입문 가이드 | 참고 문서 |
| [insane-search](https://github.com/fivetaku/insane-search) | — | 적응형 웹 바이패스 검색 | 무관 |

### 2-2. fablize Fork/Issue/PR 추적

**소스**: https://github.com/fivetaku/fablize (★768, Fork 90, Issues 0, PRs 0)

| 항목 | 결과 |
|---|---|
| Fork 수 | **90개** — 상당한 파생 활동 |
| Open Issues | 0 — 저자가 이슈를 받지 않거나 즉시 처리 |
| Open PRs | 0 — 외부 기여 없음 (혹은 거절) |
| Releases | 1 (v2.1.0, 2026-06-18) |
| 주요 파생 | **unifable** (아래 상세) |

> **90개 fork** 중 대부분은 개인 사용 목적 clone으로 추정되나, 검색에서 **unifable**이라는 이름의 공개 포크가 발견되었다.

---

## 각도 3: Claude Code 마켓플레이스 유사 플러그인

### 발굴된 프로젝트 (관련도 순)

#### ① rennf93/opus-fable-playbook ⭐ 유력 후보

| 항목 | 내용 |
|---|---|
| **URL** | https://github.com/rennf93/opus-fable-playbook |
| **접근법** | Fable 5의 "출력 스타일 교리(doctrine)"를 시스템 프롬프트로 정의 + 7개 하네스 훅으로 행동 드리프트 차단 + 12개 프로브·8차원 루브릭 평가 루프 |
| **fablize와의 차이** | fablize는 "절차 강제"에 집중하지만, opus-fable-playbook은 **"말투·스타일 모방" + 골든 트랜스크립트 기반 평가**까지 포함하여 더 넓은 범위를 다룸 |

**상세 분석**:
- **Doctrine Output Style**: Fable 5의 상호작용 패턴(단서 우선 가설, 측정 우선, 첫 문장 결론)을 시스템 프롬프트로 정의
- **Drift-Catching Hooks** (7개): `stop-gate.sh` 등 — 특정 턴 종료 표현 차단, `Read` 도구 사용 강제(cat 대신), 미이행 약속 차단
- **평가 루프**: 12개 프로브 + 8차원 루브릭 + 실제 Fable 5 출력을 "골든 트랜스크립트"로 사용
- **설치**: `/plugin marketplace add rennf93/opus-fable-playbook`
- **핵심 차이**: fablize가 의도적으로 제외한 **스타일 모방**을 적극적으로 포함하며, **평가/측정 루프가 모델 컨텍스트 내부**에서 작동 (fablize의 out-of-band 원칙과 대조적)

> **fable-lite 시사점**: 골든 트랜스크립트 기반 평가 루프는 fablize에 없는 독자적 접근법. 단, fablize 저자가 "스타일 모방은 효과 미미"라고 실험으로 판단한 점과 충돌 — 어느 쪽이 옳은지는 독립 검증 필요.

---

#### ② jaredboynton/unifable ⭐ 유력 후보

| 항목 | 내용 |
|---|---|
| **URL** | https://github.com/jaredboynton/unifable (claudepluginhub.com에서 확인) |
| **접근법** | fablize의 직접 fork — 핵심 워크플로우(검증 게이트, 조기 종료 방지, 작업 분해)를 유지하면서 더 넓은 모델 호환성 추구 |
| **fablize와의 차이** | fablize는 Claude 전용이지만 unifable은 Codex 등 **비-Claude 모델에서도 작동**하도록 adaptor 추가 시도 |

**상세 분석**:
- fablize의 핵심 메커니즘(investigation-protocol, verification-grounding, goals.py, finish-the-work)을 그대로 계승
- Claude Code의 hook 시스템(`UserPromptSubmit` 등)을 활용하되, 더 넓은 호환성을 위한 어댑터 레이어 포함
- "FableCodex"라는 이름의 관련 변형도 검색에서 언급됨

> **fable-lite 시사점**: fablize의 핵심을 검증 없이 fork한 접근 — 독자적 실험/측정 없이 "그냥 써보자" 수준. fable-lite는 이보다 근본적인 개선(팩 준수 검증 게이트 등)을 목표로 해야 차별화 가능.

---

#### ③ revfactory/harness

| 항목 | 내용 |
|---|---|
| **URL** | https://github.com/revfactory/harness |
| **접근법** | Claude Code용 메타-팩토리 — 1문장 도메인 설명으로 전문 에이전트 팀 + 스킬 + 오케스트레이션 패턴 자동 생성 |
| **fablize와의 차이** | fablize는 단일 에이전트의 행동 규율이지만, harness는 **다중 에이전트 팀 구조 자체를 생성**하는 메타 레이어 (L3) |

- 6가지 오케스트레이션 패턴 지원: Pipeline, Fan-out/Fan-in, Expert Pool, Producer-Reviewer, Supervisor, Hierarchical Delegation
- A/B 테스트에서 평균 품질 점수 49.5 → 79.3 상승 보고 (15개 SW 엔지니어링 과제)
- fable-lite의 wmux 4-pane 환경과 개념적 유사성이 있으나, 접근법이 근본적으로 다름 (행동 규율 vs 팀 구조)

---

#### ④ 일반 에이전트 프레임워크 (간접 관련)

| 프로젝트 | URL | 접근법 1줄 | fablize와의 차이 |
|---|---|---|---|
| Aider | https://github.com/paul-gauthier/aider | Git-native 터미널 AI 코딩 에이전트, 반복적 TDD 지원 | 범용 코딩 에이전트이지 "Fable 재현"은 목표가 아님 |
| Cline (구 Claude Dev) | https://github.com/cline/cline | VS Code 확장, 다단계 자율 에이전트 | 자율 에이전트이지 검증 규율 강제가 아님 |
| Codex CLI | https://github.com/openai/codex-cli | 터미널 기반 코딩 에이전트, 테스트 통과까지 반복 | GPT 전용, Fable 행동 재현 의도 없음 |
| OpenCode | https://github.com/opencode-ai/opencode | 터미널 에이전트, OpenRouter 통한 모델 유연성 | 범용 하네스, 검증 규율 없음 |

---

## 각도 4: Reddit 커뮤니티 발굴

**검색 대상**: r/ClaudeCode, r/ClaudeAI, r/ClaudeWorkflows, r/BuildWithClaude

### 발견된 패턴

Reddit 검색에서 "Fable을 Opus로" 류의 논의가 **활발히 존재함**이 확인되었으나, 대부분은 **개인 CLAUDE.md 커스터마이징 팁** 수준이며, 독립 프로젝트로 발전한 것은 위의 fablize·opus-fable-playbook·unifable 정도이다.

주요 논의 패턴:
1. **모델 라우팅 전략**: Fable 5를 아키텍트/오케스트레이터로, Opus를 실행자로 분리하는 접근
2. **Self-Correction Loop**: Opus가 코더, 별도 Opus/경량 모델이 "비평가(critic)"로 검증하는 2-모델 체인
3. **커스텀 시스템 프롬프트**: CLAUDE.md에 Fable의 행동 원칙을 직접 작성하는 DIY 접근
4. **harness 플러그인 사용**: revfactory/harness의 Producer-Reviewer 패턴 활용

> **결론**: Reddit에서는 fablize와 opus-fable-playbook이 가장 자주 언급되며, 그 외는 개인 블로그·트윗 수준의 일회성 팁이다. 독립 오픈소스 프로젝트로 발전한 것은 발견되지 않았다.

---

## 종합 발굴 결과 요약

### 발견된 프로젝트 전수 (관련도 순)

| # | 프로젝트 | URL | 유형 | 접근법 | fablize 대비 차이 | 유력도 |
|---|---|---|---|---|---|---|
| 1 | **opus-fable-playbook** | https://github.com/rennf93/opus-fable-playbook | 독립 | 스타일 교리 + 훅 + 평가 루프 | 스타일 모방 포함, 골든 트랜스크립트 기반 평가 | ⭐⭐⭐ |
| 2 | **unifable** | https://github.com/jaredboynton/unifable | Fork | fablize fork + 모델 호환성 확장 | 비-Claude 모델 지원 시도 | ⭐⭐ |
| 3 | **harness** | https://github.com/revfactory/harness | 독립 | 다중 에이전트 팀 메타-팩토리 | 행동 규율이 아닌 팀 구조 생성 | ⭐ |
| 4 | **pumasi** | https://github.com/fivetaku/pumasi | fivetaku | Claude PM + Codex 병렬 오케스트레이션 | 행동 규율 아닌 작업 분배 | ☆ |
| 5 | **vibe-sunsang** | https://github.com/fivetaku/vibe-sunsang | fivetaku | 바이브코더 멘토 에이전트 | 대화 분석·행동 교정 (간접 관련) | ☆ |

### 발굴 실패한 각도 (정직하게 기록)

| 각도 | 실패 내용 |
|---|---|
| awesome-claude-fable-5 | "Fable 재현 도구"는 **0건**. MuAPI 홍보용 활용 사례 큐레이션만 존재 |
| fablize Issues/PR | 이슈 0·PR 0 — 커뮤니티 피드백 추적 불가 |
| Claude Code 공식 마켓플레이스 | 공식 마켓에서 카테고리별 브라우징 API가 공개되지 않아 체계적 발굴 불가. 웹 검색에 의존 |
| Reddit 독립 프로젝트 | 개인 팁·CLAUDE.md 공유는 다수이나, 독립 오픈소스로 발전한 것은 fablize·opus-fable-playbook 외 **미발견** |
| FableCodex | unifable 검색 중 이름이 언급되었으나, 공개 레포 URL 확인 불가 — 비공개이거나 이름만 존재할 가능성 |

---

## fable-lite를 위한 경쟁 지형 시사점

1. **직접 경쟁자는 2개**: fablize(현 기준 최고, ★768)와 opus-fable-playbook(평가 루프 차별화)
2. **fablize가 이미 점유한 지위**: "검증된 절차만 탑재" 원칙으로 신뢰도 확보, 90개 fork
3. **빈 자리 (fable-lite가 노려야 할 곳)**:
   - **다중 에이전트 환경 지원** — fablize·opus-fable-playbook 모두 단일 에이전트 전제
   - **팩 준수 검증 게이트** — 아무도 하지 않음 (지시는 하되 따랐는지 확인 안 함)
   - **플랫폼 독립성** — 모두 Claude Code 전용
   - **한국어 네이티브** — 모두 영어 우선, 한국어는 최소 지원
4. **opus-fable-playbook의 골든 트랜스크립트 평가 루프**는 fablize에 없는 독자적 접근법이며, fable-lite에서 참고할 가치가 있음

---

> **분석 완료**. 4개 각도 모두 조사 완료, 발굴 실패 각도도 정직하게 기록했습니다.
