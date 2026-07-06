# fable-lite 아키텍처 계약 (동결 — 2026-07-06)

> P3 병렬 구현의 영역 계약. 각 작업자는 자기 영역만 수정한다. 변경 필요 시 좌상 오케스트레이터 승인.

## 디렉토리 계약

```
fable-lite/
├── .claude-plugin/plugin.json        # 플러그인 메타                      [Codex]
├── core/                             # 순수 Python 판정 코어 — CC 의존 import 0, stdlib only
│   ├── classify.py                   # 과제 분류: quick/normal/deep + risk flags + 한국어 패턴 (N2·N4)
│   ├── ledger.py                     # 증거 원장 CRUD (.fable-lite/ledger.json)
│   ├── verify_state.py               # Stop 판정: changed+unverified 차단 로직
│   ├── compliance.py                 # N1: 팩 준수 검증 (가설 수·증거 인용·기각 보고 파싱)
│   ├── scope_guard.py                # N3: 범위 이탈 감지
│   └── contract.py                   # R1: high-risk spec-before-edit 판정
├── goals/goals.py                    # S2: 체크포인트 엔진 (CLI, .fable-lite/goals.json)  [Codex]
├── adapters/claude_code/             # CC 훅 어댑터 (thin wrapper — 판정은 전부 core 호출) [Codex]
│   ├── hooks.json                    # UserPromptSubmit / PostToolUse / Stop / PreToolUse
│   └── *.py
├── packs/                            # 행동 팩 (텍스트)                    [우하 agy-Opus 전용]
│   ├── investigation.ko.md / investigation.en.md
│   ├── verification-grounding.ko.md / .en.md
│   └── completion.ko.md / .en.md
├── eval/                             # E1/E2 평가 루프                    [좌하 agy 설계 → 추후 구현]
├── tests/                            # pytest — 코어·훅 단위 테스트        [코드 소유자가 자기 테스트 작성]
├── docs/                             # 스펙·아키텍처 (본 파일)
└── README.ko.md / README.md          # 한국어 우선                        [Codex 초안]
```

## 설계 원칙 (전 영역 공통)

1. **fail-open**: 게이트 자체 오류는 세션을 절대 죽이지 않는다 (예외 삼키고 통과)
2. **stdlib only**: 외부 패키지 의존 0, 네트워크 호출 0
3. **Windows 네이티브**: bash 스크립트 금지 — Python만 (fablize의 bash 의존 반면교사)
4. **상태 단일화**: 대상 프로젝트의 `.fable-lite/` 한 곳만 사용
5. **코어/어댑터 분리**: core/는 dict in → dict out 순수 함수. Claude Code 이벤트 스키마 파싱은 adapters/만
6. **차단 상한**: Stop 게이트 최대 2회 차단 후 통과 (무한 트랩 금지, stop_hook_active 가드)
7. **fablize MIT 차용 표기**: 검증된 절차 구조는 차용하되 문장은 재작성, README에 출처 명기
8. **메시지 한국어 우선** (영어 병기)

## 영역 배타 (충돌 차단)

| 작업자 | allowed_paths | forbidden |
|--------|--------------|-----------|
| 우상 Codex | core/ goals/ adapters/ tests/ .claude-plugin/ README* | packs/ eval/ research/ docs/specs/ |
| 우하 agy-Opus | packs/ | 그 외 전부 |
| 좌하 agy | eval/ | 그 외 전부 |
