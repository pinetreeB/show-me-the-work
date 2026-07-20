# show-me-the-work 아키텍처 계약 (v2 provenance 추가 — 2026-07-13)

> P3 병렬 구현의 영역 계약. 각 작업자는 자기 영역만 수정한다. 변경 필요 시 좌상 오케스트레이터 승인.

## 디렉토리 계약

```
show-me-the-work/
├── .claude-plugin/plugin.json        # 플러그인 메타                      [Codex]
├── core/                             # 순수 Python 판정 코어 — CC 의존 import 0, stdlib only
│   ├── classify.py                   # 과제 분류: quick/normal/deep + risk flags + 한국어 패턴 (N2·N4)
│   ├── ledger.py                     # 증거 원장 CRUD (.fable-lite/ledger.json)
│   ├── verify_state.py               # Stop 판정: changed+unverified 차단 로직
│   ├── compliance.py                 # N1: 팩 준수 검증 (가설 수·증거 인용·기각 보고 파싱)
│   ├── scope_guard.py                # N3: 범위 이탈 감지
│   ├── contract.py                   # R1: high-risk spec-before-edit 판정
│   ├── provenance*.py                # manifest·lifecycle·source 귀속·snapshot 저장
│   ├── release_gate.py               # W9/W10 receipt 기반 v1 자동 migration 가드
│   └── release_receipts/             # 검토·승인된 immutable W9/W10 release 입력
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

## v2 change provenance 계층

v2는 어댑터의 도구 이름이나 shell parser 결과를 변경의 정본으로 사용하지 않는다. 각 어댑터는
후보 경로와 source hint만 전달하고, `core/provenance*.py`가 실제 파일시스템 snapshot을 만들어
동일한 canonical change event로 합친다.

1. 직전 Stop의 `workspace-current`가 유효하면 turn start와 변경 없는 PostTool은 metadata sweep만
   수행한다. metadata가 같은 파일은 digest를 재사용하며 콘텐츠 read는 0 bytes다.
2. 최초/cold/incomplete/policy 변경은 full baseline을 만들고, Stop은 parser 결과와 무관하게 전체
   콘텐츠를 reconcile한다. 파일별 `stat before -> hash -> stat after` 불일치는 한 번 재시도한다.
3. Windows 경로는 casefold canonical key를 쓰되 표시 경로를 보존한다. 충돌·불안정 경로·reparse
   위험은 임의의 clean 판정 대신 explicit incomplete로 남긴다.
4. observation 중에는 ledger lock을 잡지 않는다. commit 시 generation을 확인하고 한 번 rebase하며,
   같은 물리 변경은 `change_id`로 dedupe하고 소유권 경합은 `external`로 낮춘다.
5. snapshot은 `.fable-lite/snapshots/`, 감사 원장은 `.fable-lite/ledger.json`에 저장한다. clean fast
   turn baseline은 `workspace-current`의 hard link를 원자 교체해 중복 JSON 직렬화를 피한다.

turn bootstrap의 `baseline_status=ready`는 물리 baseline이 존재하고 그 snapshot ID가 ledger와
일치한다는 계약이다. 이 불변식을 증명하지 못하면 조용히 재기준화하지 않고
`baseline_status=degraded`와 incomplete 진단을 남긴다. bootstrap의 baseline 저장과 ledger 전이는 같은
root lock의 authoritative critical section에서 실행되고, coordination은 같은 ledger 커밋의 bounded
outbox에 먼저 저장한 뒤 unlock 후 strict writer로 drain한다. 따라서 coordination backlog나 I/O 실패는
gate verdict를 바꾸지 않지만 다음 이벤트에서 exact-content delivery를 재시도할 수 있다.

v1 원장 자동 migration은 패키지에 포함된
`core/release_receipts/provenance-latest.json`과
`core/release_receipts/bench-latest.json`의 hard gate가 모두 green일 때만 `record_event()`에 연결된다.
receipt가 없거나 red거나 malformed이면 v1 dual-read를 유지하고 archive/migration side effect를 만들지
않는다. `eval/results/`는 재실행 때 생기는 ignored 측정 산출물이며 스스로 release 승인을 갱신하지 않는다.
2026-07-13 rev3 W10 receipt는 1k/10k 규모별 hard gate가 모두 green이며 이 경로는 활성화됐다.

### 알려진 원자성·동시성 한계

- 파일 교체는 flush 후 atomic replace를 사용하지만 디렉터리 fsync까지 포함한 정전 내구성을 보장하지 않는다.
- stale lock은 live PID를 age만으로 탈취하지 않지만 PID 재사용을 process start-time으로 구분하지는 않는다.
- tombstone이 없던 pre-upgrade ledger의 stale turn은 살아 있는 child와 완전히 구분할 수 없다.
- coordination outbox/ack는 항목 수 256으로 제한되지만 항목별 직렬화 byte 상한은 없고, parser와 ledger schema 계약은 중복 구현이다.
- `PEER_EXCLUSION` coordination audit, exclusion lease 정책 강화, 누적 post-tool replay의 ledger 영속화는 후속 범위다. 현재 W3 replay 보정은 in-memory 귀속만 정합화한다.

## 영역 배타 (충돌 차단)

| 작업자 | allowed_paths | forbidden |
|--------|--------------|-----------|
| 우상 Codex | core/ goals/ adapters/ tests/ .claude-plugin/ README* | packs/ eval/ research/ docs/specs/ |
| 우하 agy-Opus | packs/ | 그 외 전부 |
| 좌하 agy | eval/ | 그 외 전부 |
