# legacy status-less ledger 고착 — 조사·설계·검증 기록 (2026-08-07)

> 결론부터: **이 저장소의 감독 하네스가 17일간(2026-07-21 ~ 08-07) 아무것도 기록하지 못한 채 차단만 하고 있었다.**
> v2.8.1 릴리스 작업 전체가 그 상태에서 진행됐다. 고착은 자기 자신의 수정까지 막고 있었다.

## 1. 어떻게 발견했나

"잔여 3건 정리"(브랜치 보호 이름 수정 / ledger 정리 / `.smtw` 이사)로 시작한 세션에서,
`smtw maintenance apply`가 `removed: 0`으로 거부되는 것을 파고들다 드러났다.

관측된 모순이 첫 단서였다.

| 명령 | 보고한 활성 턴 |
|---|---|
| `smtw doctor` | **0** |
| `smtw migrate --check` | **39** |

둘 다 같은 파일을 읽는데 값이 달랐다. 이 불일치를 "둘 중 하나가 틀렸다"가 아니라
"왜 다른가"로 추적한 것이 원인 규명의 출발점이었다.

## 2. 확정된 결함 3건

### D1. 정리 단위(턴) ≠ 검증 단위(호출)

`_plan_candidates`는 **턴 단위**로 후보를 고르고, `serialize_v2_ledger`는 **남은 장부 전체**를 검사한다.
후보에서 제외된 턴이 status-less 행을 그대로 들고 있으면 검증이 실패하고 apply 전체가 롤백된다.

```
$ smtw maintenance apply --include-manual-only
{"rejected": true, "reason": "validate:…invocations.…status", "removed": 0}
```

운영자가 명시 승인(`--include-manual-only`)해도 도달할 수 없는 탈출구였다.

### D2. "현재 턴" 오판 — 죽은 턴이 영구 보호받음

`active_turn(ledger)`를 payload 없이 호출하면(maintenance가 그렇게 한다) 세션을 구분하지 않는다.

```python
# core/verification_covers.py:46-50
top_agent = ledger.get("agent")
if isinstance(top_agent, str):
    for turn in turns.values():
        if isinstance(turn, dict) and turn.get("agent") == top_agent:
            return turn          # dict 순서상 첫 매치. 세션 검사 없음
```

실측: 최상위 `agent='claude'`, `session_id`·`host`는 **없음**. `.agent == 'claude'`인 턴이 **12개**였고
그중 dict 순서 첫 번째가 current로 선택됐다. 그 턴은 **이미 종료된 과거 세션**이었고,
실제로 살아 있던 세션의 턴은 장부에 **0개**였다.

### D3. 자동 GC가 옛 턴을 영원히 건너뜀

```python
# core/ledger_v2.py:794
and (last_event := _parse_timestamp(raw_turn.get("last_event_at"))) is not None
```

`last_event_at`이 없으면 GC 대상이 아니다. 구 스키마 턴에는 그 필드가 없으므로 영원히 적체된다(39개).

### 세 결함의 결합

```
status-less 209건 (구 스키마)
  → serialize 거부 → 장부 invalid
  → 모든 이벤트 기록 실패 (17일)
  → contract_authored 감사 이벤트 기록 불가
  → R1이 high-risk 수정 차단
  → 고착을 고치는 코드 변경 자체가 불가능
```

codex는 계약서를 올바르게 작성하고도 3회 연속 차단당한 뒤 `blocked`를 선언했고,
**허위 완료를 피하려 완료 보고서를 일부러 만들지 않았다.** 하네스 철학대로 행동한 셈이다.
수정은 프로젝트 루트 밖(홈 세션, 게이트 미적용)에서 작성했다.

## 3. 채택하지 않은 안과 그 이유

### 기각 A — status를 `closed`로 채운다
`started_at`조차 없는 행은 종료를 증명할 수 없다. 완료로 표시하는 것은 감독 기록 위조이며,
`_missing_status_disposition`이 이를 거부하는 것은 **올바른 설계**다.

### 기각 B — 24시간 경과(stale)로 죽은 턴을 판정한다
좌상이 처음 제안했고 codex가 반증했다. `last_event_at`은 **이벤트 시각이지 생존 신호가 아니며**,
heartbeat 경로가 코드베이스에 없다. 24시간 넘게 도는 명령은 lease(30분)도 이미 만료되어
**실제로 살아 있는 턴이 삭제 후보가 된다.**

> "24시간 경과는 '현재 선택을 신뢰할 수 없다'는 증거이지 '프로세스가 죽었다'는 증거가 아니다."

### 기각 C — 턴은 남기고 invocation만 격리한다
좌상 안. 형식 복구에는 실제로 충분했으나(실측: 209건만 제거 시 strict 직렬화 통과) 두 가지를 놓쳤다.

1. 좌상의 전제 "67건 턴은 경로 보호 때문에 살려둬야 한다"가 **틀렸다.** 경로 보호는
   `path_attribution[].owners[]`에 붙어 있고 active turn 존재를 참조 무결성으로 요구하지 않는다.
   codex가 predicate 실험으로 확인 — 턴을 제거해도 3개 경로 모두 차단이 유지됐고,
   그중 하나는 **원래부터 대응 invocation이 없었는데도** 차단이 살아 있었다.
2. active turn을 남기면 같은 turn id의 이벤트가 다시 수용되어 **되살아난다**(실험 확인).

## 4. 채택안 — 운영자 승인 기반 퇴역

status-less 턴을 **완료가 아니라 '증거 부족 퇴역'**으로 옮긴다.

```
smtw maintenance apply --retire-legacy-statusless \
  --ack-unverified 209 --source-digest ff79cb62…
```

- 원문 bytes를 **먼저** archive에 보관 → `retired_turns`에 `resolution: "unverified_legacy"` + archive digest 기록
- 운영자가 영향 invocation 수를 **정확히** 대야 열린다(우발적 실행 불가)
- `turn_is_terminal`(closed ∪ retired)로 replay 차단 연결
- `path_attribution`은 건드리지 않는다

### 안전 근거는 시각이 아니라 **스키마 세대**

`_remember_invocation`은 **언제나** `status: "open"`을 기록한다(`core/ledger_v2.py:735-756`).
따라서 status 부재는 구 스키마 데이터임을 뜻하며, **살아 있는 턴은 결코 대상이 될 수 없다.**
기각 B의 위험(장시간 실행 턴 오삭제)을 시간 판정 없이 회피한다.

## 5. 검증

live 장부는 실험 대상으로 쓰지 않았다. fixture 우선.

- **pytest 1965 passed / 2 skipped** (기준선 1956)
- **probes** `pass=17 fail=0 result=PASS` · **e2e smoke** green · **ruff** clean
- 신규 `tests/test_legacy_statusless_retirement.py` 9건 — current-identity·unsettled-attribution·정상 후보를
  **한 fixture에** 넣은 RED 재현 포함. 기존 테스트는 각 제외 사유를 따로만 시험해서 이 조합을 못 만들었다.

### fixture가 잡아낸 실제 결함

첫 fixture 실행에서 `retired: 2`가 나왔다(9여야 했다). `_reverify_candidates`가 새 카테고리를
걸러내지 않아 **status-less 턴 7개가 `closed`로 기록되던 중**이었다 — 이 변경이 막으려는 바로 그 위조다.
live가 아니라 fixture에서 터진 덕에 실데이터 오염이 없었다.

## 6. 적용 결과

| | 전 | 후 |
|---|---|---|
| ledger health | `invalid` | `healthy` |
| active turns | 39 (오판) | 0 |
| migrate | `DEFERRED` | `MIGRATED` (`.smtw`, legacy 보존) |
| snapshots | 156MB (고아 428개) | 12.7MB |

## 7. 다른 프로젝트 전수 점검

좌상이 "legacy 11곳 전부 같은 고착일 것"이라 추정했으나 **실측 결과 2곳뿐**이었다.

| 상태 | 프로젝트 |
|---|---|
| 같은 고착 → 수리 완료 | `.claude`(4건/1턴), `catalog-forge`(16건/2턴) |
| 정상·미이사 | ai-env-clone, kmc-os-v2, scoop-ts600, scoopmotor-web, material-erp-preview |
| 활성 턴 보유(정상, 미개입) | agy-model-bench, golfcart-lsv(8), kmc-os-v2-audit |

## 8. 남은 것

- `doctor`가 스키마 invalid일 때 실제 turn 수를 보고하도록 고쳤으나, **오류 경로에서 기본값을
  측정값처럼 반환하는 패턴이 다른 필드에도 남아 있는지** 별도 감사가 필요하다.
- snapshot **retention 정책이 아예 없다**(GC/prune 미구현). 이번엔 수동 정리했으나 재발한다.
- `.fable-lite` legacy는 안정 확인 후 삭제.
