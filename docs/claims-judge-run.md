# Phase 2 수동 실행

좌상만 보이는 터미널에서 아래 세 줄을 실행합니다. Codex 구현·시험은 실제 모델을 호출하지 않습니다.
작업 디렉터리는 show-me-the-work 저장소입니다. 출력 폴더는 매 회차 새 이름으로 정합니다.

```powershell
python -X utf8 scripts/claims_eval.py generate --cases-root C:\Users\example\tmp\smtw-rework-20261018\cases --out C:\Users\example\tmp\smtw-rework-20261018\eval\phase2\l4-left
python -X utf8 scripts/claims_eval.py summary --packets C:\Users\example\tmp\smtw-rework-20261018\eval\phase2\l4-left\packets
python -X utf8 scripts/claims_judge.py --packets C:\Users\example\tmp\smtw-rework-20261018\eval\phase2\l4-left\packets --max-calls 5
```

두 번째 표의 출처와 가림 수를 직접 확인한 후에만 세 번째 줄을 실행합니다.
개인 구독 설정과 claude-sonnet-5-5만 허용합니다. Windows에서는 설치된 native claude.exe를 우선 사용하며,
claude-p와 동일하게 CLAUDE_CONFIG_DIR=~/.claude-personal 및 CLAUDE_CODE_PLUGIN_CACHE_DIR=~/.claude/plugins를 지정합니다. 도구는 비활성화되고 세션은 저장하지 않습니다.
사용량 5시간 60% 이상 또는 주간 70% 이상이면 거부합니다. 사용량 자료가 없거나 오래됐으면
기본 거부이며, 좌상이 사용량을 확인하고 명시적으로 --allow-unknown-usage를 줄 때만 허용됩니다.
회차 상한은 기본 4, 최대 10입니다. 패킷 수가 상한을 넘으면 호출 전에 모두 거부합니다.
백그라운드·예약·훅 실행은 금지입니다. stdin과 stdout 모두 TTY여야 합니다.

판정자 --json-schema 인자는 저장소의 Phase 1 schema 파일에서 읽어 compact 한 줄로 직렬화한 JSON 내용으로 고정됩니다.
여러 줄 JSON을 .cmd 인자로 보내지 않습니다. native 경로를 찾지 못한 경우에만 claude-p를 사용합니다.
기존 패킷을 수정하거나 프롬프트·스키마가 바뀌면 새로 생성해야 합니다.
패킷 SHA·출처 목록·가림 패턴·30,000자 한도를 실행 직전에 다시 확인합니다.
부모 ANTHROPIC_API_KEY 이름이 있으면 값 조회 없이 거부하며, 자식의 *_API_KEY·*_TOKEN은 제거합니다.
실제 자격증명 파일이나 값을 읽어 확인하지 않습니다.

calls.jsonl은 eval/phase2 아래에 추가 기록만 합니다. 원출력, 스키마 통과 JSON, 인용 재검증 결과는
judge-runs/<회차>/에 따로 저장합니다. 판정 결과는 “LLM 참고 표시 — 판정 아님” 구역에만 사용하며,
원래 대조기의 status·요약·종료 코드는 변경하지 않습니다. 잘못된 인용은 표시하지 않습니다.
호출 실패·원출력 형식 오류는 중단하며 자동 재시도하지 않습니다.

L4는 SMTW 95d18194와 agent-gate 3a589085의 저장소 파일 줄을 git show로 읽어
경로:줄 번호와 정확한 인용을 포함한 합성 문장을 만듭니다. 코드·문서 줄을 섞으며,
20자 미만 원문·민감 경로·비밀 패턴이 있는 줄과 발췌는 제외합니다.
원본 100개와 변조 100개를 만들고, 원문 문자열이 같은 줄은 중복으로 셉니다.
서로 다른 원문이 60개 미만이면 생성을 거부합니다. 가능한 한 원문 재사용 없이 선택합니다.
정답과 변조 종류·고유 원문 수는 packets의 형제 폴더 truth에만 저장합니다.
원본·변조 모두 동일한 필드와 불투명 id를 쓰며 패킷에는 정답이나 변조 이름을 넣지 않습니다.
숫자·식별자·인용 토큰·줄 이동·부정 반전을 각각 20개씩 사용합니다.

채점은 claims_eval.py의 score 하위 명령에 --packets, --truth, --runs, --out을 지정합니다.
--runs에는 판정 회차 폴더 하나 또는 같은 패킷을 판정한 세 회차를 줍니다.
채점기는 재검증 결과를 다시 계산해 일치 여부를 확인하고 재현율·정밀도·오표시율·
quote_not_in_excerpt 비율·3회 판정 일치율·검증 인용 일치율을 고정 표로 출력합니다.
합성 가짜 판정자 결과는 처리 흐름 시험일 뿐 실제 모델 품질 증거가 아닙니다.

L2 공개 사례 패킷은 기존 claims assist --dry-run에서 --case-id를 함께 지정합니다.
목록 밖 사례는 실행 래퍼에서 거부합니다. 허용 사례라도 출처가 다른 저장소인 발췌는 제외합니다.
보고서당 오표시가 3건을 초과하는 결과가 2회 연속이면 실제 호출을 중단하고 좌상 검토로 돌아갑니다.

(보류: 안전장치) 실제 값 대조 생략
