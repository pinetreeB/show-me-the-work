# 디자인 게이트 (Design Gate) — DESIGN-OPS 편입 설계 (착수 대기)

> 2026-07-14. 배경: wmux 4-pane에서 AI(Claude·Codex·agy·ultracode)마다 UI 결과물이 갈리는 문제를 막기 위해 디자인 규칙 SSOT `~/.claude/DESIGN-OPS.md`를 신설(3-AI 토론 도출 — Codex 구현성 검토 + 일관성 감사). 규칙 **내용**은 그 파일이 SSOT이고, 이 문서는 그 규칙을 show-me-the-work의 **팩·게이트로 기계 강제**하는 편입 설계다. "규칙 문서만으론 하위 모델이 '확인함' 자기선언 후 우회한다"(Codex)를 기존 하네스로 차단한다.
>
> ⚠️ **선결 조건 (이 작업보다 먼저)**: 이 편입은 show-me-the-work 재활성화가 전제다. 현재 홈/대형/원격 세션 provenance 오탐으로 플러그인 비활성(메모리 `fable-lite/project.md` v-next 근거 #1~#4). 그 수정·재활성화가 착수 전 완료돼야 한다.

## 편입 원칙 — 새 하네스를 짓지 않는다

show-me-the-work가 이미 하는 3층 강제에 디자인을 얹는다(승현님 설계 결정 — 중복 구축 회피):
- **팩(소프트)**: 상황 감지 시 규율 주입 → `design-review` 팩 신설
- **N1/Stop 게이트(하드)**: 검증 없으면 완료 차단 → 디자인 검증을 Stop 계약에 편입
- **provenance/ledger(증거)**: 완료 증거 기록 → 디자인 검사 결과·스크린샷 경로 기록

## 동작 흐름

```
UserPromptSubmit/classify: domain=UI or CREATION(.html/.tsx/.vue/.svelte/.css) 감지
  → design-review 팩 + verification-grounding 팩 주입, ledger에 design_required=true
  ↓
모델: DESIGN-OPS §1·§2 준수해 시안 생성 → 렌더 검증(playwright 스크린샷·측정)
  ↓
PostToolUse: UI 파일 Edit/Write 기록 → design_touched=true
  ↓
Stop: design_required AND design_touched 인데
      ① 정적 검사(design_lint) 미통과 → 차단
      ② 렌더 검증 tool 기록 없음 → 차단   (상한 2회, fail-open)
      통과 → 완료 인정 + 검사결과·스크린샷 경로를 ledger에 증거 기록
```

## 검증 3층 (판정 방식별 구현)

DESIGN-OPS §4 체크리스트 8항을 판정 방식으로 나눠 구현한다. **core는 CC 의존 0(렌더러 없음)이라 렌더 항목을 직접 측정하지 않는다** — tool 호출 기록만 확인:

| 층 | 항목 | 구현 |
|----|------|------|
| **A. 정적 (core 직접)** | 토큰 하드코딩 0 · 컴포넌트 상태 명세 존재 | `core/design_lint.py` — Stylelint 규칙(`color-no-hex`·색 속성 `var()` 강제·간격 raw px 금지) + AST(JS/TSX·`StyleSheet.create`·인라인·SVG·Tailwind `[#..]`/`[13px]`). `fable_lite check`에 통합. 예외경계 = DESIGN-OPS §2(토큰 파일 literal 허용·`0`·헤어라인·차트색·allowlist) |
| **B. 렌더 (tool 기록 확인)** | 히어로≤상한 · keep-all · 대비AA · 다크모드 · 반응형 · 상태별 렌더 | core는 직접 측정 안 함. Stop 게이트가 "UI 변경 턴에 렌더 검증 tool(playwright/chrome-devtools) 호출 기록"을 요구(verification epoch 확장). 실제 관측은 `verification-grounding` 팩이 오케스트레이터에게 지시(computed 타이포·라인박스·axe·뷰포트 오버플로) |
| **C. 사람 (게이트 아님)** | AI냄새(§1.4) | 자동 게이트 제외 — 임계·기준이미지 부재 + 심사 AI 자기통과 편향. 개입③(사용자 시각 승인). §1.4 관찰 기준(그라데이션≤1·장식 이모지 0)으로 1차만 |

## design-review 팩 (packs/design-review.ko.md / .en.md)

- 내용: DESIGN-OPS §1 전역규칙 + §4 체크리스트를 팩 문장으로. **규칙 원문은 DESIGN-OPS가 SSOT**, 팩은 요약 + 주의 신호(중복 서술 최소, 상세는 그 파일 참조).
- 트리거: `classify` domain=UI or CREATION(.html/.tsx/.vue/.svelte/.css 생성). 기존 `classify_task` 재사용, 새 분류기 만들지 않음.
- `verification-grounding` 팩과 **함께** 주입 — 그 팩의 "관측(OBSERVE)" 단계에 디자인 체크를 추가(레이아웃 옆에 히어로≤상한·keep-all·대비).
- 마커 계약: 시안 완료 시 렌더 검증 tool 실행이 곧 증거(말로 "규칙 지켰습니다" 금지 — 기존 팩 철학과 동일).

## fable_lite check 확장 (디자인 검증)

- `fable_lite check --root . --design` → `design_lint` 실행 + 결과 JSON.
- 기존 프로젝트: **변경 라인부터** 적용(전체 실패 금지). 예외는 사유·만료일 allowlist(DESIGN-OPS §2·§4-A).
- 출력: 위반 파일·라인·규칙 ID + 통과 여부. Stop 게이트가 이 결과를 재사용(이중 실행 회피).

## Stop 게이트 (gate_stop 확장)

- 조건: `ledger.design_required` AND `design_touched`.
- ① `design_lint`(A층) 미통과 → block("디자인 규칙 위반: `<파일:라인>`. DESIGN-OPS §N 참조").
- ② 렌더 검증 tool(B층) 기록 없음 → block("UI 변경인데 렌더 관측 없음. playwright 스크린샷으로 확인 후 완료").
- 상한 2회 후 통과(기존 stop 카운터 철학, 별도 `design_blocks` 카운터), fail-open. Bash·조사·질문 턴은 차단 안 함.
- 통과 시 검사결과·스크린샷 경로를 완료 증거로 ledger 기록(provenance와 정합).

## gate.config (design/gate.config, 프로젝트별)

- 기계 판독 형식으로 검사 대상 경로·viewport·테마·상태·브라우저·스크린샷 diff 임계를 선언.
- 없으면 기본값: viewport 375(모바일)·1280(데스크톱), 테마 light/dark, 상태 hover/focus/disabled/loading/empty/error.

## 온오프 토글 (2026-07-14 승현님 결정 — opt-in, 기본 OFF)

디자인 게이트 전체를 켜고 끌 수 있게 한다. 프로젝트의 기존 opt-in 선례(리퍼 훅 `FABLE_LITE_CODEX_REAPER`·auto migration 해금)와 동일 철학:

- **기본 OFF** — 토글 꺼짐이면 design-review 팩 주입·design_lint·Stop 디자인 차단 전부 비활성(제로 마찰). 기존 사용자 무영향으로 독립 landing 가능.
- **3층 토글**: ① 환경변수 `FABLE_LITE_DESIGN_GATE=1`(전역) ② 프로젝트 `design/gate.config`의 `enabled: true`(프로젝트별 — config 존재≠활성, 명시 필드) ③ `fable_lite check --design`(1회성 수동 실행은 토글 무관 항상 가능).
- 우선순위: 프로젝트 config > 환경변수. 충돌 시 프로젝트 설정이 이긴다.
- **선결 조건 완화 효과**: 기본 OFF라 provenance 오탐 수정(#1~#5)과 독립적으로 코드 landing 가능. 단 **라이브 E2E(실 발동 검증)는 플러그인 재활성화 이후**로 순서 유지.

## 하지 않는 것

- LLM로 AI냄새 판정(결정론 위반·자기통과 편향) ❌ → 사람(개입③)
- core가 렌더 직접 측정(CC 의존 0 원칙 위반) ❌ → tool 기록 확인만
- 토큰 원본 파일 하드코딩 차단(출처라 literal 필요) ❌ → 예외경계(DESIGN-OPS §2)
- 최초 토큰 "고정"(정상 진화 막음) ❌ → 버전·마이그레이션 허용(DESIGN-OPS §2)
- 새 위임 래퍼(delegate-ui.ps1)·별도 검증 러너 신설 ❌ → 이 하네스의 팩·게이트로 대체(편입 원칙)

## 영역 배타 (병렬 착수 시)

| 작업자 | 영역 |
|--------|------|
| Codex | `core/design_lint.py`(Stylelint+AST) · ledger `design_*` 필드 · `gate_stop` 확장 · `fable_lite check --design` · tests |
| Sonnet/ultracode | `packs/design-review.{ko,en}.md` + `verification-grounding` 관측 확장 + 라이브 E2E(실 UI 프로젝트서 게이트 발동·차단·회복 실관측) |
| agy | 오탐 측정: 실 UI 커밋 코퍼스로 `design_lint` 오탐/미탐률 + 예외경계 누락(SVG·차트색·RN·Tailwind) 검증 |

## 참조

- 규칙 SSOT: `~/.claude/DESIGN-OPS.md` (§1 전역·§2 토큰·§4 체크리스트·§5 게이트·§9 로드맵)
- 진입 배선(완료): `AGENTS.md`·`CLAUDE.md`·`WORKFLOW.md §4` — UI 작업 전 DESIGN-OPS 필독 포인터(Phase 1)
- 선결: 메모리 `fable-lite/project.md` v-next 근거 #1~#4(홈/대형/원격 provenance 오탐 수정·재활성화)
- 형식 선례: `intent-gate.md`(게이트 편입 평행 구조)·`v2-provenance.md`(증거 대조)
