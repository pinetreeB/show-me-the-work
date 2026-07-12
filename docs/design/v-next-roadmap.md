# show-me-the-work v-Next 로드맵 — 3-AI 종합 (최종안)

> 2026-07-11. 입력: tmp/v-next-plan-codex.md(구현 실측) + tmp/v-next-plan-agy.md(제품·리스크) + tmp/v-next-plan-claude.md(오케스트레이터).
> 쟁점은 좌상 Claude가 코드 직접 검증으로 판정 완료. 사용자 방향승인 대기.

## 판정된 쟁점

| 쟁점 | agy 주장 | codex 주장 | 판정 (코드 검증) |
|------|----------|-----------|------------------|
| stop_hook_active 루프가드 | 1순위 결함 (1차단 후 무조건 통과) | 이미 닫힘 (2회 cap 동작) | **codex 옳음** — verify_state.py에 B2 수정 반영, MAX_STOP_BLOCKS=2 cap + fail-open 구조 확인. agy 근거는 v1.0.0 이전 낡은 정보. 재설계 불필요, 3-host conformance 검증만 |
| agy 어댑터 false success | (미발견) | AfterTool이 실패도 success=true 상수 기록 | **사실** — oma_hook.py:203-213, 실제 결과 payload 미판독. 치명 |
| Verification Epoch | (미발견) | verify→edit→Stop 순서가 allow | **사실** — verify_state.py:27-31 any(success)만 체크, 순서 미확인 |
| 버전 drift | (미발견) | plugin=1.1.3 vs pyproject/badge=1.0.0 | **사실** — 실측 확인 |

## v1.2 — "증거 무결성 릴리스" (Evidence Integrity)

> 3자 합의: 기능 확장보다 "실행 증거 기반 하드게이트" 주장이 3대 CLI에서 사실임을 먼저 입증. 마켓플레이스 등재는 이 게이트 통과 후.

### P0 (릴리스 차단)
1. **agy false-success 수정 + 라이브 E2E** — 실패 검증이 success=false로 기록되고 Stop이 block하는 negative path 필수. project-local hooks 격리
2. **Verification Epoch** — ledger에 event_seq 도입, 최신 변경보다 뒤의 성공 검증만 인정. "테스트 후 재수정하고 완료 선언"이라는 가장 현실적인 거짓 green 차단. 3어댑터 공통 코어
3. **Codex self-locating 설치 경로 + 라이브 E2E** — hooks.json 상대경로가 타 프로젝트에서 불성립하는 문제. 임의 cwd에서 4훅 실발동 검증 (오늘 세션에서 fable-lite repo 내 발동은 실관측됨 — 타 프로젝트 케이스가 남은 구멍)

### P1 (신뢰성·마찰)
4. stop_hook_active 3-host conformance (재설계 금지, 실payload fixture로 block→block→allow·회복 시나리오만 고정)
5. 오탐 corpus 수정 — next.js류 기술명 경로 오탐 + "생성" artifact 오탐 (agy: 비개발자 마찰=이탈 1순위 / codex: corpus 기반으로 기존 positive 회귀 0 보장)
6. 버전 SSOT — 한 파일 기준으로 plugin/marketplace/pyproject/README badge 자동 갱신 + CI drift 차단
7. 표준 검증 3종(pytest·strict probes·e2e smoke) + 3-host 라이브 receipt

### v1.2 하드게이트 (하나라도 미달 시 태깅·등재 보류)
- 실패 검증→성공 기록 경로 0 / stale 증거 인정 0 / 3-host positive+negative+recovery 실관측 / 표준 3종 green / 오탐 수정이 기존 positive 안 깨뜨림 / 글로벌 설정 무수정(테스트는 격리 환경만)

### P2 (배포·운영 — 전부 사용자 승인 후)
8. 커뮤니티 마켓플레이스 등재 (+ agy 제안: Setup Wizard·온보딩 데모 세트)
9. 소나무봇 격리 파일럿 (Python 3.12 선설치)
10. fablize uninstall

## v2.0 — Provenance & 멀티에이전트 (방향만 합의)

- **Change Provenance**: 도구 이름이 아니라 실제 파일시스템 delta로 변경 판정 — Bash 우회 문제의 근본 해법 (v1.2에서는 문서화+관측만, 정규식 하드게이트 금지에 3자 합의)
- **정규화 어댑터 계약**: 어댑터 drift(이번 agy false success 같은)를 구조적으로 차단하는 공통 이벤트 계약 + conformance replay
- **멀티에이전트 게이트** (3자 아이디어 수렴): wmux 오케스트레이션 게이트(Claude, 기존 설계문서 有) = Team-Handoff Gate(agy) = 오케스트레이터가 워커 원장을 기계 대조하는 "거짓 완료 필터". agy 경고 반영: ledger 동시 접근 race → file lock/에이전트별 상태 분리 선행
- **Session Quality Scorecard**(agy) + 게이트 텔레메트리 상시화(Claude): 세션마다 "차단 N회·강제 검증 N회" 성적표 → ROI 체감 + README 실측 근거 자동 갱신
- Hostile event-sequence 평가·Pack Contract Manifest(codex)

## 하지 않기로 3자 합의
- Bash 우회를 v1.2에서 정규식 하드게이트로 막기 (오탐 폭증, v2 provenance로)
- 적대 모델 방어로 위협모델 확장 (부주의 방지 유지)
- 서드파티 의존성·외부 텔레메트리 서버·wmux 데몬을 core에 추가 (zero-dep·플랫폼 중립 유지)
- 마켓플레이스 선등재 (증거 무결성 먼저)
- 전용 하네스 통합 (사용자 보류 결정 유지)
