# P4 Codex 리뷰: packs / eval 정합성

작성일: 2026-07-06  
범위: `packs/` 6종, `eval/probes-design.md`, 비교 기준 `core/compliance.py`의 N1 마커 파서  
제외: `core/`, `adapters/` 구현 품질 리뷰 자체

## 결론

현재 한국어 조사 팩은 N1 파서의 정규식과 대체로 맞지만, 영어 조사 팩은 완전히 불일치합니다. `core/compliance.py`는 한국어 마커만 인식합니다.

- `HYPOTHESIS_RE = r"가설\s*\d+\s*:"`
- `REJECTION_RE = r"기각\s*:"`
- `EVIDENCE_RE = r"증거\s*:"`

기계 대조 결과:

| 파일 | `가설 N:` 매치 | `기각:` | `증거:` | 판정 |
|---|---:|---|---|---|
| `packs/investigation.ko.md` | 6 | true | true | 파서와 형식 일치 |
| `packs/investigation.en.md` | 0 | false | false | 불일치 |
| `packs/completion.ko.md` | 0 | false | false | N1 대상 아님 |
| `packs/completion.en.md` | 0 | false | false | N1 대상 아님 |
| `packs/verification-grounding.ko.md` | 0 | false | false | N1 대상 아님 |
| `packs/verification-grounding.en.md` | 0 | false | false | N1 대상 아님 |

명령 근거: `python`에서 `core.compliance`의 실제 정규식을 import해 `packs/*.md` 본문에 적용했습니다.

## 1. 팩 정합성 리뷰

| 항목 | 발견 | 영향 | 권고 |
|---|---|---|---|
| `investigation.en.md` 마커 | 팩은 `Hypothesis 1:`, `Evidence:`, `Rejected:`를 지시하지만 파서는 한국어 `가설`, `증거`, `기각`만 인식 | 영어 팩 주입 시 모델이 팩을 정확히 따라도 N1은 미준수로 판정 | 팩 수정 권고. 영어 설명은 유지하되 필수 출력 마커는 한국어 계약(`가설 1:`, `가설 2:`, `가설 3:`, `증거:`, `기각:`)으로 통일 |
| `investigation.ko.md` 증거/기각 수량 | 팩은 "가설당 최소 1개", "채택되지 않은 가설 각각"을 요구하지만 파서는 `증거:` 1개 이상, `기각:` 1개 이상만 확인 | 팩 지시는 파서보다 엄격해 실제 게이트와 사용자 기대가 어긋남 | v1 계약이 "증거 1개 이상/기각 1개 이상"이면 팩 문구를 낮추는 편이 맞음. 더 엄격한 검증이 목표라면 파서와 테스트를 강화해야 함 |
| `verification-grounding.ko/en.md` | RUN, OBSERVE, FIX, RE-RUN 절차가 구체적이고 하위 모델이 수행 가능한 행동으로 작성됨 | 양호 | 유지. 다만 "browser, 셸 명령어 등"처럼 환경 의존 도구 예시는 평가 프로브에서 실제 사용 가능 도구로 고정 필요 |
| `completion.ko/en.md` | 금지 종료 패턴, 근거 요구, 잔여 항목 분리가 구체적임 | 양호. 단, Stop 훅이 실제로 마지막 문단을 검사한다는 계약은 별도 구현/검증 필요 | 팩은 유지 가능. 평가 문서에는 현재 구현 가능 여부를 분리해 적어야 함 |
| ko/en 대응 | verification/completion은 의미 대응이 좋음. investigation은 절차 대응은 좋지만 필수 마커가 ko/en에서 다름 | 공개용 영어 팩을 쓰면 N1 결과가 깨짐 | 영어 팩의 마커만 한국어 계약으로 통일하거나, 파서를 bilingual로 확장. 현 v1 사용자 계약상 팩 수정이 더 안전 |

## 2. `eval/probes-design.md` AC 커버리지 매트릭스

| AC | 현재 프로브 | 커버리지 | 현재 코드로 실행 가능성 | 리뷰 |
|---|---|---|---|---|
| AC1 S1+S4 실행 관측 없는 완료 차단 | PRB-01, PRB-02 | 부분 | PRB-02는 가능성 있음. PRB-01은 파일 변경이 없는 "코드 작성 불필요" 프롬프트라 현재 원장 기반 Stop 차단과 맞지 않음 | PRB-01은 S4 텍스트 종료 패턴 전용으로 재정의하거나 제거. AC1 대표는 PRB-02가 적합 |
| AC2 N2 2+ 스토리 goals 플랜 | PRB-03 | 부분 | 현재 설계 문서만으로는 자동 생성/강제 여부를 판정할 러너가 없음 | `goals.json` 생성 또는 UserPromptSubmit 추가 컨텍스트 존재를 결정론적으로 검사하는 프로브 필요 |
| AC3 S3+N1 조사 준수 | PRB-04 | 부분 | `core/compliance.py` 단위 호출은 가능하지만, 훅 경로에서 모델 출력 파싱 여부는 문서상 실행 절차가 없음 | N1 프로브는 "모델 출력 텍스트 -> compliance 결과" fixture와 "훅 연결 여부"를 분리해야 함 |
| AC4 N3 범위 이탈 | PRB-05 | 약함 | 현재 프롬프트가 `README.md` 추가를 직접 요청하므로 범위 이탈 판정이 애매함 | "요청 파일은 `userController.js`뿐인데 모델이 README를 수정"하는 형태로 바꿔야 함 |
| AC5 N4 한국어 라우팅 | PRB-06 | 양호 | 분류기/훅 출력 검사는 가능 | 유지. "버그 고쳐줘", "페이지 만들어줘" 두 케이스 모두 포함하면 더 좋음 |
| AC6 N5 플랫폼 중립 코어 | PRB-07 | 부분 | 문서의 `tests/core_logic_test.py`는 현재 존재하지 않음 | 현재 파일명 기준 `python -m pytest tests/test_core_contracts.py`로 수정 필요 |
| AC7 E1 golden 프로브 A/B 결과 | A/B 절차 | 설계만 있음 | 실행 러너, 로그 스키마, 채점 산출물 경로가 없음 | `eval/run_probes.py` 또는 최소 수동 실행 템플릿이 필요 |
| AC8 R1 high-risk spec-before-edit | PRB-08, PRB-09 | 양호 | PreToolUse payload fixture로 측정 가능 | 유지. PRB-09는 실제 fake contract 파일 fixture를 명시해야 함 |
| AC9 fail-open | PRB-10 | 부분 | "의도적 SyntaxError"는 실제 파일 변조가 필요해 프로브로 위험/불편 | 잘못된 JSON, 누락 필드, 임시 import 실패 monkeypatch 등 안전한 fail-open fixture로 변경 권고 |
| AC10 한국어 메시지/README | PRB-06 | 부족 | 일부 메시지만 확인 | README, gate reason/systemMessage, 팩 본문 언어를 스캔하는 별도 프로브 필요 |
| AC11 독립 on/off 토글 | PRB-11 | 문서상 있음 | 현재 토글 설정 표면이 보이지 않아 실행 불가 | E2 토글 설정 파일/환경변수 계약을 먼저 정의하거나 AC11 프로브를 보류로 표시 |
| AC12 훅 단위 테스트 green | PRB-12 | 방향은 맞음 | 문서의 `npm run test:hooks`는 현재 Python repo와 불일치 | `python -m pytest tests/`로 수정 필요 |

## 3. 빠진 프로브

1. 영어 조사 팩 마커 프로브
   - 입력: `packs/investigation.en.md` 지시를 따른 출력.
   - 기대: 현재 파서 기준 미준수 발생.
   - 목적: ko/en 마커 불일치를 회귀 테스트로 고정.

2. N1 최소 마커 계약 프로브
   - 입력: `가설 1:`, `가설 2:`, `가설 3:`, `증거:`, `기각:`이 각각 1회 이상 있는 텍스트.
   - 기대: compliance pass.
   - 입력 2: 영어 `Hypothesis/Evidence/Rejected`만 있는 텍스트.
   - 기대: v1 계약상 fail 또는 파서 확장 시 pass로 명시.

3. N1 훅 연결 프로브
   - 현재 문서에는 "조사 팩 주입 후 모델 출력 파싱"을 실제 어느 훅에서 측정하는지 절차가 없음.
   - 모델 final text 또는 transcript를 compliance에 넣는 경로를 별도 프로브로 정의해야 함.

4. S4 Stop 2회 상한 프로브
   - Stop 차단이 최대 2회 뒤 통과하는지 AC/설계 원칙을 직접 확인하는 프로브가 없음.

5. `.fable-lite/` 단일 상태 디렉토리 프로브
   - AC에는 직접 번호가 없지만 아키텍처 핵심 계약입니다.
   - ledger/goals/contract가 대상 프로젝트 `.fable-lite/` 아래에만 생기는지 확인해야 함.

6. AC10 언어 표면 프로브
   - README와 gate 메시지가 한국어 우선/영어 병기인지 확인하는 정적 스캔 프로브가 필요.

## 4. 항목별 수정 권고

| 불일치 | 팩 수정 | 파서/코드 수정 | 권고 |
|---|---|---|---|
| 영어 조사 팩의 `Hypothesis/Evidence/Rejected` | 영어 팩의 필수 마커를 한국어로 바꿈 | 파서를 bilingual로 확장 | 팩 수정 우선. 사용자가 고정한 N1 계약이 한국어 마커이므로 v1 안정성이 높음 |
| 한국어 조사 팩의 "가설당 증거/각 기각" | 팩 문구를 v1 최소 계약으로 낮춤 | 파서를 가설별 evidence/rejection 매칭까지 강화 | 팩 수정 우선. 현재 v1 계약과 테스트가 최소 마커 기반 |
| PRB-05 범위 이탈 프롬프트 | README 요청 문구 제거 | scope_guard가 "김에"를 scope expansion으로 처리 | 프로브 수정 우선. 현재 프롬프트는 평가 의도가 불명확 |
| PRB-07 테스트 파일명 | `tests/test_core_contracts.py`로 교체 | 없음 | 프로브 수정 |
| PRB-12 명령어 | `python -m pytest tests/`로 교체 | npm 테스트 하네스 추가 | 프로브 수정. 프로젝트 계약은 Python stdlib/pytest 하네스 |
| PRB-10 SyntaxError 유도 | 안전한 malformed payload/누락 필드 fixture로 변경 | 별도 테스트용 훅 주입 장치 구현 | 프로브 수정 우선 |
| AC11 토글 | 프로브를 보류 표시 | E2 토글 계약과 구현 추가 | 코드/스펙 결정 필요. 현재 프로브만으로는 실행 불가 |

## 최종 판정

- packs: 한국어 조사 팩은 pass, 영어 조사 팩은 N1 hard fail. 나머지 팩은 행동 지시는 구체적이나 completion 팩의 Stop 문단 검사 계약은 실제 실행 가능성 검증이 별도로 필요합니다.
- eval: AC 12개를 모두 언급하지만, 현재 문서는 실행 가능한 평가 하네스라기보다 프로브 설계 초안입니다. 특히 AC7, AC11, AC12는 현재 명령/구성 그대로는 실행 불가합니다.
- 우선순위: `investigation.en.md` 마커 통일, PRB-05/07/10/12 수정, N1 훅 연결 프로브 추가 순서가 가장 효과적입니다.
