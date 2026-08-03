# friction-fix v2.7.0 — 감독 마찰 3종 수리 확정 스펙

- 일자: 2026-08-03 · 승인: 사용자 B안 포괄 승인 후 기술 세부는 3-AI 토론 수렴
- 입력: `tmp/friction-fix/`의 qwen-analysis·qwen-design-review·codex-review·qwenmax-opinion + agy 터미널 적대 리뷰 2회(트랙별 Critical 판정)
- 리뷰 라운드 요지: 초안 설계 중 ①-1(같은 턴 재-block 방지)·②-1(배타 화이트리스트)·②-2(리다이렉트 신규파일 예외)는 **3-AI 수렴으로 폐기**. 아래가 확정안.

## 확정 범위 (W1~W4, 병합 순서: W1 ∥ W3 → W2 → W4)

### W1. doctor 정직화 + 명시적 invocation status backfill — 담당: qwen(우하), 브랜치 `worker/friction-w1-doctor-migrate`
- doctor: `unreadable`(IO/JSON)과 `invalid`(스키마 필드) 분리, 위반 필드·`needs_migration`·다음 안전 명령을 JSON/text로 보고. exit 1 유지.
- `smtw migrate --invocation-statuses [--check]`: 기존 lease-aware backfill(`core/ledger_migration.py`) 재사용. `--check`=write-free. **unsafe row(증거 없는 209개類)는 절대 자동 close 금지** — apply는 exit 1 + 원본/archive 무변경.
- 계약: `test_doctor_03a_unsupported_schema_is_error` 메시지 단언 갱신 동반. `test_load_ledger_reads_v1_fixture_without_writing_or_migrating` 유지.

### W3. R2 opaque wrapper strict whitelist — 담당: codex(우상), 브랜치 `worker/friction-w3-r2-opaque-whitelist`
- `core/shell_command.py`: `_is_proven_read_only` → public `is_proven_read_only_command` 승격(단일 SSOT). `cmd.exe /c`·`pwsh|powershell -NoProfile -NonInteractive -Command` 제한 grammar 추가.
- `core/destructive_guard.py`: wrapper는 **shared parser 완전 성공 + 전 세그먼트 proven-read-only일 때만 allow**. opaque unknown은 현행 "빈 destructive=allow"(fail-open 실측: WriteAllText·iex·Add-Content·Copy/Move-Item 통과)가 아니라 `parse_unable_wrapped` **block** — 보안 갭 동시 폐쇄.
- 리다이렉트 예외 없음(전부 block 유지). 라벨 `command_parse_unavailable` 불변(계약 테스트 고정).
- 공격 벡터 10종(chain·iex/dot-source·식 확장·EncodedCommand/-File/profile·cmd %/!·escape smuggling·리다이렉트 변형·path 탈출/ADS/junction·alias/PATH 치환·pipeline 오염) 각 2+ 변형 corpus + **adversarial 실주입 30건 이상 false negative 0** 합격 기준.

### W2. gate preflight + proven-read-only goals 예외 — 담당: codex, W3 머지 후, 브랜치 `worker/friction-w2-gate-preflight`
- ①-2: 첫 block 메시지에 이 턴의 잔여 요구(goals+contract+verification/Stop)를 **예고**(recover·counter 기록 없음).
- ①-3: R2 통과 후 `is_proven_read_only_command` true인 셸만 goals block 생략(카운터 불변). 미지=기존 block. R2 판정 재사용 금지.
- 유지 계약: 체크포인트 삭제/변조 시 재차단(무결성), stop fail-closed, block⟺영속 불변식.

### W4. legacy lifecycle maintenance + journal compact + hook latency — 담당: codex, W1+W3 머지 후, 브랜치 `worker/friction-w4-ledger-maintenance`
- 신규 `smtw maintenance plan/apply`(doctor에 repair 넣지 않음): source digest CAS + 백업 + current identity/open lease/pending tx 제외 + timestamp-less legacy는 manual-only. 운영 ledger apply는 PR 밖(별도 승인).
- `core/provenance_manifest.py` journal: 최소 필드 projection + count·byte cap(현 3.3MB 원인). full event는 agent log에 보존(증거 소실 방지 — agy High 반영).
- codex 어댑터 fast path: proven-read-only면 identity resolve/provenance begin 생략(mutation/verification/unknown은 기존 경로).
- `eval/hook_latency_bench.py`: 8MB 픽스처 phase별 계측 — **벤치 RED가 구현 선택보다 선행**(F8 인과 미확정). 목표: 30회 timeout 0·p95<2s·compact 후 ≤4.5MB.

## 폐기 항목 (사유 기록)
- ①-1 같은 턴 재-block 방지: 실주입 결과 그런 재차단은 발생하지 않음(발생분은 identity 불일치·체크포인트 삭제 = 정당). 도입 시 무결성 보호 약화(agy Critical·qwen NO-GO·codex 우회 5종 수렴).
- ②-1 배타 화이트리스트("그 외 fail-closed"): 파서 암묵 기본이 비탐지=allow(fail-open)라 실현 불가 — W3의 wrapper 한정 whitelist로 대체.
- ②-2 리다이렉트 신규파일 허용: TOCTOU·symlink/junction·ADS를 사전 검사로 원자 방어 불가(codex·agy Critical 수렴). `cmd /c "echo x > newfile"` false positive 1건은 잔존 — Known Limitation, 파일 생성은 Write 도구 경로 안내.
- goals identity 전용 aging 규칙: maintenance 후 active_turns가 유일 authority(F7은 운영 maintenance로 해소).

## 최종 합격 게이트 (P5)
1. 신규 `test_friction_w*` + 기존 회귀(파서 corpus·doctor·schema·block 영속·scorecard) 전부 GREEN
2. full pytest + ruff + 양OS CI + wheel smoke GREEN
3. W3 adversarial 30+ 실주입 false negative 0 (PR artifact로 영수증 첨부)
4. 8MB 픽스처 훅 벤치 30회 timeout 0, p95<2s
5. 운영 ledger는 구현 PR에서 무변경(maintenance는 별도 승인 작업)
- 버전: v2.7.0 minor(신규 CLI·진단 JSON·정책 확장). SemVer 실사 재확인 후 태깅.

## 실측 근거 (P1)
private-repo gates.jsonl 5차단 13분 / quarantine 오차단 1건·실적발 3건(here-string 1·Python writer 2) / ledger 7.97MB=journal 3.36MB+active_turns 3.16MB / status-less invocation 209(unsafe) / stale exact identity 37 → goals ambiguous / private-repo turn_bootstrap_pending 1004(mutation-capable 876) / codex 훅 5-10s 타임아웃(인과 미확정, 8MB 로드는 ~0.2s)
