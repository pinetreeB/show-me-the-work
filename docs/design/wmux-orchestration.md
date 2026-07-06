# v2 설계 — wmux 멀티에이전트 오케스트레이션

> 2026-07-06 초안 (좌상 오케스트레이터 작성). 구현은 v2. 전제 문서: `docs/ARCHITECTURE.md`, `docs/design/v2-adapters.md`(좌하 병행 작성).

## 1. 문제 정의

fable-lite v1은 **Claude Code 단일 에이전트** 하네스다. 그러나 실전 환경(wmux 4-pane: Claude 오케스트레이터 + Codex 구현 + agy 리뷰 + Claude 구현보조)에서는 여러 AI가 같은 프로젝트를 동시에 수정한다. 이때 규율이 새는 지점:

1. **원장 분열**: 각 에이전트가 자기 시야의 변경만 알고, 프로젝트 전체의 "변경됨/검증됨" 상태를 아무도 모른다.
2. **게이트 부재 CLI**: Codex·agy는 CC 훅 같은 결정론적 차단 표면이 없다(v2-adapters 조사 대상). 소프트 지시만으로는 v1 리뷰에서 증명됐듯 우회된다.
3. **완료 주장 vs 증거 불일치**: 워커가 "끝났다"고 보고해도(sentinel 생성) 검증 증거가 원장에 없을 수 있다 — 현재는 오케스트레이터가 수동으로 재검증한다.

## 2. 설계 원칙

- **코어 재사용**: core/는 이미 플랫폼 중립(N5, dict-in/dict-out). 오케스트레이션은 코어를 감싸는 레이어이지 코어 수정이 아니다.
- **게이트가 없는 곳에서는 오케스트레이터가 게이트다**: 훅이 안 걸리는 CLI의 산출물은 좌상이 원장·diff 대조로 사후 판정한다. 판정 로직은 동일한 core 함수를 사용한다(이중 구현 금지).
- **원장은 프로젝트 단위 공유, 기록은 에이전트 네임스페이스**: 충돌 없이 병합 가능해야 한다.

## 3. 구성 요소

### 3-1. 공유 원장 (ledger v2)

```
<project>/.fable-lite/
├── ledger.json              # v1 호환 (CC pane 네이티브 훅이 계속 사용)
├── agents/
│   ├── codex.jsonl          # 에이전트별 append-only 이벤트 로그
│   ├── agy.jsonl
│   └── claude-2.jsonl
└── merged-view.json         # 오케스트레이터가 생성하는 통합 스냅샷 (파생물, SSOT 아님)
```

- append-only JSONL로 동시성 문제 제거(원자적 쓰기는 line-append 수준으로 충분).
- 이벤트 스키마는 v1 ledger 이벤트(change/verification/scope_warning/prompt)에 `agent` 필드만 추가.

### 3-2. 오케스트레이터 CLI — `fable-lite check`

좌상이 위임 회수 시 실행하는 사후 게이트 (v2 신규, core 함수 재사용):

```powershell
python -m fable_lite check --root <project> --agent codex --since <위임시각marker>
# 출력: changed+unverified 목록 / scope 이탈 / 미이행 약속 패턴 / R1 위반 → exit code로 게이트
```

- **작업카드 연동**: 작업카드의 `allowed_paths` → scope_guard의 `requested_paths`로, `verify` → 검증 증거 요구로 주입.
- **sentinel 크로스체크**: sentinel 존재 ≠ 완료. `check`가 green이어야 오케스트레이터가 완료로 인정 — "완료 감지 3중"(sentinel·결과파일·idle)에 **4번째 축(원장 증거)** 추가.

### 3-3. 위임 프롬프트 자동 규율 주입 — `fable-lite brief`

위임 메시지 앞에 붙일 규율 블록 생성기:

```powershell
python -m fable_lite brief --task-card <card.json> --target codex
# 출력: 대상 CLI에 맞는 규율 텍스트(팩 발췌 + 마커 계약 + allowed_paths + sentinel 규칙)
```

- 대상별 강도 조절: CC pane → "훅이 강제하니 간결" / Codex·agy → "소프트 지시 + 사후 check 예고"를 명시(사후 게이트가 있음을 알리는 것 자체가 준수율을 높인다 — 관측 가능성 효과).

### 3-4. 에이전트별 강제 수단 매트릭스 (v2-adapters 결과와 결합)

| 게이트 | CC pane | Codex pane | agy pane |
|--------|---------|-----------|----------|
| S4/N1 (Stop·준수) | 네이티브 훅 (하드) | brief 지시 + 사후 check | brief 지시 + 사후 check |
| N3 (범위) | 훅 경고 | 사후 check (diff 대조) | 사후 check |
| R1 (high-risk) | PreToolUse 차단 | **위임 자체를 좌상이 보류** (오케스트레이터 정책) | 동일 |
| S2 (goals) | 훅 넛지 | 작업카드가 goals를 대체 | 동일 |

핵심: R1급(비가역)은 훅이 없는 CLI에 아예 위임하지 않는 것이 오케스트레이터 정책 — 도구로 막는 게 아니라 배정으로 막는다.

## 4. v2 구현 우선순위

1. **P1**: ledger v2 (agent 필드 + agents/*.jsonl) — 코어 소폭 확장
2. **P2**: `fable_lite check` CLI — 사후 게이트 (가장 큰 실익: 지금 수동 재검증을 자동화)
3. **P3**: `fable_lite brief` — 위임 규율 생성기
4. **P4**: merged-view + 오케스트레이터용 상태 요약
5. 보류: pane 자동 감시 데몬(wmux MCP 의존이라 fable-lite 범위 밖 — 사용자 환경 스크립트로)

## 5. 비범위 (v2에서도 하지 않음)

- wmux MCP 자체에 대한 의존(플랫폼 중립 위반) — wmux 연동은 사용 예제 문서로만
- 에이전트 간 실시간 lock/조정(복잡도 대비 실익 낮음 — 작업카드 경로 배타가 이미 해결)
- 원격(소나무봇) 분산 원장 동기화 (v3 후보)
