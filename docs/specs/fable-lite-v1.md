# fable-lite v1 스펙

> 2026-07-06 확정 (P1 개입① 대기). 근거: `research/SYNTHESIS.md`(1차 — 전이성), `research/SYNTHESIS-2-opensource.md`(2차 — 생태계 전수).
> 사용자 결정 3건: **신규 구현** / **Claude Code 우선 + 플랫폼 중립 코어** / **풀스펙**.

## 1. 목표

Opus 등 하위 Claude 모델이 Claude Code에서 **Fable 5의 작업 규율을 절차로 재현**하게 하는 하네스.
리서치로 전이 가능이 확정된 행동만 대상으로 하고, **강제력을 최우선 설계 원칙**으로 한다(하드 게이트 > 소프트 지시 — 생태계 전수 조사에서 재확인된 유일한 확실 메커니즘).
능력 상한(자발적 함의 추적·스펙 밖 결함 발견)은 재현 대상이 아니며, 도달 불가 시 정직하게 에스컬레이션한다.

## 2. 범위 (v1)

### 계승 4종 — fablize(MIT) 검증 절차 차용·재작성 (출처 표기)
| ID | 기능 | 메커니즘 |
|----|------|---------|
| S1 | 검증 접지 | 렌더/실행 산출물 RUN→OBSERVE→FIX→RE-RUN 팩 |
| S2 | 분해 + 증거 게이트 | goals 체크포인트 엔진 — 최종 스토리 verify-cmd+evidence 필수 |
| S3 | 체계적 조사 | 재현→3+ 경쟁 가설→인과사슬→기각 보고 팩 |
| S4 | 조기종료 방지 | Stop hook — 미이행 약속("하겠습니다"류) 차단 |

### 신규 5종 — 생태계 빈 니치 (아무도 안 함)
| ID | 기능 | 메커니즘 |
|----|------|---------|
| N1 | **팩 준수 검증 게이트** | 조사 팩 주입 후 모델 출력을 파싱해 가설 수·증거 인용·기각 보고 존재를 확인, 미준수 시 차단/경고 (소프트 지시→검증 가능 절차로 승격) |
| N2 | **자동 트리거** | 프롬프트 복잡도 분류 → 2+ 스토리 예상 시 goals 플랜 강제 또는 명시 확인 요구 |
| N3 | **범위 이탈 감지** | PostToolUse에서 수정 파일 추적, 요청과 무관한 파일 수정 시 경고 주입 |
| N4 | **한국어 라우팅** | 라우터·분류기·게이트 메시지 한국어 네이티브 ("버그 고쳐줘"→조사 팩) |
| N5 | **플랫폼 중립 코어** | 판정 로직을 순수 Python 코어로 분리 + 어댑터 인터페이스 확정. v1 어댑터=Claude Code (Codex/agy 어댑터 구현은 v2) |

### 평가 루프 — opus-fable-playbook·fablever 기법 차용
| ID | 기능 | 메커니즘 |
|----|------|---------|
| E1 | golden transcript 평가 | 프로브 세트(≥12) + 루브릭 채점 — baseline Opus vs fable-lite-on 비교 |
| E2 | 기능별 on/off 측정 구조 | 각 게이트를 독립 토글 가능하게 — 정직한 A/B 문화 |

### 하드 게이트 (high-risk 한정 — WFB식 차용)
| ID | 기능 | 메커니즘 |
|----|------|---------|
| R1 | spec-before-edit | 인증·DB 마이그레이션·결제·대량삭제 신호 감지 시에만 PreToolUse edit 차단 + machine-readable task contract 요구. 가짜 증거 마커("assumed"·"would pass") 거부 |

## 3. 비범위 (하지 않는 것)

- ❌ 유출 시스템 프롬프트 사용 (Anthropic IP — Halalify 사례가 반면교사)
- ❌ 가중치 증류·Fable 트레이스 데이터셋 사용 (ToS 위반 파생물 — HF Qwable/Fable-5-traces 배제 확정)
- ❌ 스타일·말투 모방 단독 기능 (fablize 실험상 효과 미미)
- ❌ 능력 상한 돌파 주장 (전이 불가 목록은 에스컬레이션으로 처리)
- ❌ wmux 멀티에이전트 오케스트레이션 실장, Codex/agy 어댑터 실장 (v2)
- ❌ shadow 측정 인프라 동거 (fablize 반면교사 — E1/E2 최소한만)
- ❌ 전 작업 하드 게이트 (토큰 2~3배 — high-risk 한정)

## 4. 측정 가능 수용기준 (Given-When-Then)

- [ ] **AC1** Given HTML/실행 산출물 생성 과제, When 실행 관측 없이 완료 시도, Then Stop 차단 + 관측 요구 (S1+S4)
- [ ] **AC2** Given 2+ 스토리 과제, When 작업 시작, Then goals 플랜 자동 생성 또는 명시 확인 요구 (N2)
- [ ] **AC3** Given 디버깅 과제, When 가설 1개만으로 수정 진행, Then 준수 게이트 경고/차단 (S3+N1)
- [ ] **AC4** Given 요청 범위 밖 파일 수정, When PostToolUse, Then 범위 이탈 경고 주입 (N3)
- [ ] **AC5** Given 한국어 프롬프트("버그 고쳐줘"·"페이지 만들어줘"), When 라우팅, Then 해당 팩 주입 (N4)
- [ ] **AC6** 코어 모듈이 Claude Code 의존 import 0으로 단위 테스트 통과 (N5)
- [ ] **AC7** golden 프로브 ≥12개에서 baseline Opus 대비 채점 결과 산출 (E1) — 개선 방향 확인, 미달 항목 정직 기록
- [ ] **AC8** Given high-risk 파일(마이그레이션 등) 수정 시도, When spec 미존재, Then edit 차단 (R1)
- [ ] **AC9** 게이트 자체 오류 시 fail-open — 세션을 죽이지 않음 (전 훅 공통)
- [ ] **AC10** 모든 게이트 메시지·README 한국어 (영어 병기)
- [ ] **AC11** 각 게이트 독립 on/off 토글 동작 (E2)
- [ ] **AC12** 전체 훅 단위 테스트 스위트 green

## 5. 데이터·외부연동

- 외부 API·네트워크 의존 **0** (로컬 훅·파일·Python stdlib만 — fablever의 zero-dependency 원칙 차용)
- 상태 파일: 프로젝트 로컬 `.fable-lite/` 단일 디렉토리 (fablize의 3곳 분산 반면교사)
- 배포 형태: Claude Code 플러그인 구조 (`.claude-plugin/plugin.json`)

## 6. 비가역 플래그

- 개발·로컬 테스트: 전부 가역 (L1)
- **GitHub 공개 repo 생성·push: 사용자 명시 OK 필요** ④
- 마켓플레이스 등록: v1 완성 후 별도 결정

## 7. 언어·표기

문서(README.ko 우선)·게이트 메시지·팩 본문 = 한국어 우선, 영어 병기(공개 대비). 코드 식별자·주석 = 영어.
