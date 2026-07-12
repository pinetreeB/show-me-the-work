# 사건 기록: codex TUI 행 — 작업 완료 후 "Waiting for agents" 미귀환 (2026-07-13)

> 기록 목적: fable-lite가 다루는 codex 런타임 안전장치(리퍼 훅 등)의 **커버리지 갭** 실측 사례.
> 발생 세션: 스쿱 라이더스 wmux 4-pane, 좌상 Claude(Fable 5) 오케스트레이션 중 우상 codex pane.

## 환경

- codex CLI **v0.144.1**, Windows 11, PowerShell pane (wmux)
- 모델 `gpt-5.6-sol` / reasoning `high`, 기동 인자 `-c model=gpt-5.6-sol -c model_reasoning_effort=high --dangerously-bypass-approvals-and-sandbox`
- `/status` 기준 `Collaboration mode: Default`, Agents.md = `~/.codex/AGENTS.md` + repo `AGENTS.md`
- 작업 내용: 서버 감시 알람 구축 카드(로컬 repo 분석 + `ssh ai-workspace` 배경 터미널로 VM 파일 설치)

## 증상 (시간순 실측)

1. 카드 수행 중 매 편집 후 `Interacted with /root/goal_verify` · `/root/security_review` · `/root/quality_review` · `/root/qa_verify` 표시가 반복됨.
2. 중간에 `Waiting for agents` → `Finished waiting` → **`No agents completed yet`** 사이클 출력.
3. **산출물은 정상 완성** — 보고서(`tmp/launch-ops/server-report.md`)와 sentinel(`tmp/.codex-done-launchops`)이 디스크에 생성됨(오케스트레이터 폴링이 정상 발화).
4. 이후 TUI가 `◦ Working (17m 41s • esc to interrupt) · 1 background terminal running`에서 **타이머까지 완전 정지**.
5. `escape` ×2, `ctrl+c` 전부 무반응(입력 자체가 처리되지 않음 — 렌더·입력 루프 동반 정지로 추정).
6. 프로세스 실측: CPU 711s, WS 617MB로 살아있으나 무응답 → `Stop-Process -Id <pid>`(해당 PID만 정밀 종료)로 회수, 재기동 후 정상.

## 귀속 판정 (오귀속 정정 2회 포함 — 판정 변천 그대로 기록)

- **1차 오판**: 게이트 이름이 fable-lite 스타일이라 "fable-lite가 codex에 심은 게이트"로 보고했음.
- **1차 기각**: `grep goal_verify|qa_verify|quality_review|security_review` → fable-lite repo **0건**, `~/.codex/*.md` **0건**.
- **2차 추정**: codex v0.144 내장 협업(collaboration) 에이전트 (근거: Collaboration mode 표기 + "Waiting for agents").
- **3차 관측**: 같은 pane의 후속 카드(s2-core) 종료 시 `Running 2 PostToolUse hooks` → `Running Stop hook: (OmO 4.17.0) Checking Ulw-Loop Resume` 실측 — **LazyCodex(OmO) 4.17.0이 PostToolUse·Stop 훅을 전역 설치** 중임이 확인됨(행 후보 레이어 1).
- **4차 grep**: `~/.codex/plugins/**`에서도 게이트 이름 **0건** → 게이트들은 로컬 파일이 아님.
- **★최종 정리(두 후보 병존)**: `/root/goal_verify` 등은 Windows 로컬에 존재하지 않는 경로라 **codex 클라우드 샌드박스에서 도는 원격 협업 에이전트의 컨테이너 내부 경로**로 보는 것이 가장 정합적(후보 A — "Waiting for agents"와 일치). 별도로 OmO 4.17.0 훅 체인(후보 B)이 종료 단계에 항상 개입한다. 행 재발 시 분리 실험: OmO 훅 임시 제거 후 재현 여부로 A/B 판별.

## 경쟁 가설과 증거

- 가설 1(주): 내장 협업 에이전트 스폰이 완료 신호를 영영 못 받아 턴 종결이 블록됨 — 증거: `No agents completed yet` 반복 후 정지.
- 가설 2: 카드가 연 **배경 ssh 터미널 1개**가 미종료 상태로 남아 턴 종결을 차단 — 증거: 정지 화면에 `1 background terminal running` 상시 표기. (가설 1과 결합 가능)
- 가설 3: 단순 TUI 렌더 데드락 — 증거: 타이머 정지+입력 무반응은 설명하나, 정지 직전 에이전트 대기 로그를 설명 못함(보조 요인으로만).
- 기각: fable-lite 훅 원인설 — 위 grep 0건으로 기각.

## fable-lite 관점 함의 (커버리지 갭)

1. **리퍼 훅(`FABLE_LITE_CODEX_REAPER`, eb0053b)은 이 케이스를 못 잡는다** — 리퍼는 codex stop 시 *자기 세션 고아 프로세스* 회수용인데, 본 건은 **활성 pane의 살아있는 프로세스가 행**인 유형(stop 이벤트 자체가 안 옴).
2. 오케스트레이터 측 완화가 현실적: 산출물 sentinel 완성 후 N분 내 TUI가 프롬프트로 안 돌아오면 "완료-후-행"으로 판정하고 해당 PID 정밀 kill+재기동하는 절차(이번에 수동으로 수행한 것의 표준화). wmux 운영 문서 후보.
3. v-next 조사 항목: codex collaboration 에이전트를 비활성화하는 config 키가 있는지(`collaboration` 관련), 있다면 wmux 위임 기동 인자에 포함할지 평가.

## 재발 시 대응 절차 (검증됨)

1. 산출물·sentinel 존재 먼저 확인(있으면 작업 손실 0 — 서두르지 않아도 됨).
2. `Get-Process codex | Select Id,StartTime,CPU` 로 **해당 pane의 PID만** 식별(StartTime=기동 시각 대조. 무차별 kill 금지 — 타 워크스페이스 codex 보존).
3. `Stop-Process -Id <pid> -Force` → pane이 PS 프롬프트로 복귀 → 동일 인자로 재기동 → 다음 카드부터 정상.

— 기록: 좌상 Claude (Fable 5), 스쿱 prelaunch 세션 중.
