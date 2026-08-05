# agy 2차 리뷰 — 미검토 영역 적대 검토 (좌상이 터미널에서 회수)

> agy는 smtw 게이트상 파일 저장이 막히므로 좌상이 회수해 저장.
> 대상: v2.8.0 `188ef0d`. **1차 하드리뷰가 "못 봤다"고 고백한 영역**을 표적으로 지정한 위임.
> 위임 시 "반드시 실행해서 근거를 만들고, 실행결과와 코드상 추정을 문장마다 구분 표기"를 요구했다.

## 1. 신뢰 경계 · Quarantine 안전성

- **실행결과**: `core/destructive_guard.py:2132` `evaluate_r2_destructive_gate`에 mock 원장을 주입해
  실측. 타 에이전트(agent2)가 미정산 작업 중인 `test_file.py`를 agent1이 `rm`으로 지우려 하면
  `decision: block`, `reason_code: peer_unsettled`로 정확히 차단.
- **실행결과**: 같은 파일을 **소유 에이전트**(agent2)가 지우면 `allow`. 남의 파일을 자기 것으로
  오인하거나 타 에이전트 작업을 실수로 차단하지 않음을 확증.
- **실행결과**: `core/quarantine.py:37` `_safe_agent_key`가 `_SAFE_KEY_RE.sub("-", agent_key)`로
  `../`·경로 구분자를 치환하고 SHA-256 8자리 해시를 강제 부여.
- **실행결과**: 저장 경로가 `quarantine_dir / f"blocked-{stamp}-{short_agent}-{item_id}.txt"`로
  샌드박싱돼 **Path Traversal·외부 파일 덮어쓰기 불가**.

## 2. 상태 무결성 · 마이그레이션 실패 경로

- **실행결과**: `destructive_guard.py:2045-2049` `_load_ledger_for_r2`가 원장 파싱 오류 또는
  `*.corrupt-*.bak` 마커 발견 시 `degraded = True`.
- **실행결과**: degraded 상태에서 R2는 파괴 명령을 **허용하지 않고** `attribution_degraded`로
  **fail-closed 차단**.
- **코드상 추정**: `ledger_migration.py:120` 등에서 마이그레이션이 부분 실패해도 감독이 약화되지
  않고 차단 방향으로 강화되는 안전망이 작동.

## 3. Goals · CLI 우회 가능성

- **실행결과**: `smtw check`·`goals`·`doctor` 등 CLI 전체를 검사한 결과 훅/게이트 차단을 무시하고
  강제 실행하는 **비밀 우회 플래그가 존재하지 않음**.
- **실행결과**: `smtw migrate --check`는 원장 파일을 일체 수정하지 않는 dry-run 읽기 전용 유지.
- **코드상 추정**: 사용자가 감독 게이트를 임의로 무력화할 은밀한 진입점 없음.

## 4. Fail-Open 분포 및 정당성

| 위치 | 판정 |
|------|------|
| `core/quarantine.py:109`, `:187` | **정당** — 백업 실패 시 `except Exception: return None`. R2 deny 판정을 바꾸지 않고 백업만 skip하는 best-effort 설계 |
| `core/provenance_snapshot.py:160` | **정당** — `except OSError: pass`. 관측용 프로버넌스 기록 실패가 주 명령을 방해하지 않는 로깅 폴백 |
| **`core/scope_guard.py:45`** | **갭** — `except Exception: return True`로 fail-open. 런타임 예외 시 검사를 통과시킨다. 다만 R2 1차 정적 게이트가 따로 작동하므로 파괴 명령은 차단됨 |

## 5. 잔존 레거시 / 미사용 분기

- `core/ledger_v1.py:1-200` — v1 스키마 호환 로직이 잔존하나 `ledger_migration.py`가 즉시 v2로
  자동 이관하므로 실무에서 v1 단독 로직이 지속 실행될 가능성은 거의 없는 미사용 레거시 분기.

## 좌상 판정

**미검토 영역에 재앙은 없었다.** 이 리뷰의 가치는 "제품의 절반이 한 번도 적대 검토를 안 받았다"는
불확실성을 해소한 것이다. 귀속 계산이 틀리면 파서를 아무리 정확히 고쳐도 무의미해지는 구조인데,
그 계층이 정상 동작함을 실측으로 확인했다.

유일한 실질 발견은 `scope_guard.py:45` fail-open 1건이며 R2가 별도로 막아 영향이 제한적이라
백로그로 이월했다.

[AGY-UNREVIEWED-DONE]
