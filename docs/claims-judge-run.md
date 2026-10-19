# Phase 2 수동 실행

좌상만 보이는 터미널에서 아래 세 줄을 실행합니다. Codex 구현·시험은 실제 모델을 호출하지 않습니다.
작업 디렉터리는 show-me-the-work 저장소입니다. 출력 폴더는 매 회차 새 이름으로 정합니다.

먼저 docs/examples/judge-scope.example.json을 <WORK>/judge-scope.json으로 복사하고
합성 id·출처 접두·<CASES> 아래 저장소 경로·고정 커밋 SHA를 실제 승인 범위로 채웁니다.
예시의 0 SHA는 자리 표시입니다. 이 작업용 파일과 패킷은 공개 저장소에 커밋하지 않습니다.
출력 루트와 범위 파일은 필수 인자이며 개인 경로 기본값은 없습니다.

```powershell
python -X utf8 scripts/claims_eval.py generate --scope-file "<WORK>/judge-scope.json" --out-root "<WORK>/l4-run"
python -X utf8 scripts/claims_eval.py summary --scope-file "<WORK>/judge-scope.json" --packets "<WORK>/l4-run/packets"
python -X utf8 scripts/claims_judge.py --scope-file "<WORK>/judge-scope.json" --packets "<WORK>/l4-run/packets" --out-root "<WORK>" --max-calls 5 --blocklist-file "<WORK>/blocklist.txt"
```

두 번째 표의 출처와 가림 수를 직접 확인한 후에만 세 번째 줄을 실행합니다.
개인 구독 설정과 claude-sonnet-5-5만 허용합니다. Windows에서는 설치된 native claude.exe를 우선 사용하며,
claude-p와 동일하게 CLAUDE_CONFIG_DIR=~/.claude-personal 및 CLAUDE_CODE_PLUGIN_CACHE_DIR=~/.claude/plugins를 지정합니다. 도구는 비활성화되고 세션은 저장하지 않습니다.
사용량 5시간 40% 이상 또는 주간 55% 이상이면 거부합니다. 사용량 자료가 없거나 오래됐으면
기본 거부이며, 좌상이 사용량을 확인하고 명시적으로 --allow-unknown-usage를 줄 때만 허용됩니다.
관제탑이 승인한 문맥 축소는 Phase 2 판정 호출에만 적용하며, 좌상이 명시적으로 --lean-context를 붙일 때 켜집니다.
lean 기본값은 꺼짐입니다. --disable-slash-commands와 --strict-mcp-config는 항상 적용합니다.
lean을 켜면 --system-prompt-file <저장소 판정 지시문>을 추가합니다.
--mcp-config 없이 MCP를 비활성화하고 --tools ""를 유지합니다.
각 호출은 시스템 TEMP 아래 고객 자료·비밀이 없는 새 빈 폴더를 cwd로 사용하고 호출 종료·실패 때 삭제합니다.
--setting-sources·권한 관련 플래그·--bare는 허용하지 않으며 API 키 금지와 구독 사용량 검사는 그대로입니다.
calls.jsonl의 lean_context에 활성 여부를, usage 객체에는 원응답의 input_tokens·output_tokens·
cache_read_input_tokens·cache_creation_input_tokens를 복사합니다. 없거나 음수·비정수이면 null로 기록합니다.
이 옵션의 실제 사용량 감소는 좌상의 명시적 호출로 확인해야 합니다.

보고서 1건의 회차 상한은 기본 4, 최대 8입니다. assist 기본 선별 상한은 40입니다. 패킷 수가 상한을 넘으면 호출 전에 모두 거부합니다.
백그라운드·예약·훅 실행은 금지입니다. stdin과 stdout 모두 TTY여야 합니다.

판정자 --json-schema 인자는 Phase 1 배열 스키마에서 $schema·$id·$comment 메타만 제거하고,
judgments 배열 필드가 필수인 최상위 object로 감싼 뒤 compact 한 줄 JSON으로 전달합니다.
Phase 1 원본 스키마와 재검증기의 배열 계약은 유지합니다.
프롬프트 계약은 v1.2이며 manifest의 judge_contract와 calls.jsonl에
원본 schema_sha256·prompt_sha256·CLI 전달용 cli_schema_sha256을 함께 기록합니다.
이전 v1 패킷은 재생성해야 합니다. 결과의 structured_output.judgments 또는 result 문자열의
judgments를 기존 검증·인용 재검증에 넘깁니다.
참 값인 is_error(비 bool 포함), 정수·bool api_error_status, 또는 subtype이 success가 아닌 응답은 중단하고 stdout을 raw 파일에 그대로 보존합니다.
성공 응답의 api_error_status: null은 정상 처리합니다. subtype이 빠진 CLI 객체는 거부하며, 기존 직접 배열 입력은 유지합니다.
호출 기록의 원인은 result 앞 200자에 가림을 적용한 값만 사용하며 환경값·stderr·예외 문구는 넣지 않습니다.
여러 줄 JSON을 .cmd 인자로 보내지 않습니다. native 경로를 찾지 못한 경우에만 claude-p를 사용합니다.
기존 패킷을 수정하거나 프롬프트·스키마가 바뀌면 새로 생성해야 합니다.
패킷 SHA·출처 목록·가림 패턴·30,000자 한도를 실행 직전에 다시 확인합니다.
부모 ANTHROPIC_*·CLAUDE_CODE_USE_BEDROCK/VERTEX 이름이 있으면 값 조회 없이 거부합니다. 자식 환경은 명시적 허용 목록만 복사합니다.
실제 자격증명 파일이나 값을 읽어 확인하지 않습니다.

calls.jsonl은 지정한 --out-root 아래에 추가 기록만 하며 범위 파일 SHA256을 기록합니다. 원출력, 스키마 통과 JSON, 인용 재검증 결과는
judge-runs/<회차>/에 따로 저장합니다. 판정 결과는 “LLM 참고 표시 — 판정 아님” 구역에만 사용하며,
원래 대조기의 status·요약·종료 코드는 변경하지 않습니다. 잘못된 인용은 표시하지 않습니다.
호출 실패·원출력 형식 오류는 중단하며 자동 재시도하지 않습니다.

L4는 범위 파일에 명시한 저장소와 고정 커밋의 파일 줄을 git show로 읽어
경로:줄 번호와 정확한 인용을 포함한 합성 문장을 만듭니다. 코드·문서 줄을 섞으며,
20자 미만 원문·민감 경로·비밀 패턴이 있는 줄과 발췌는 제외합니다.
원본 100개와 변조 100개를 만들고, 원문 문자열이 같은 줄은 중복으로 셉니다.
서로 다른 원문이 60개 미만이면 생성을 거부합니다. 가능한 한 원문 재사용 없이 선택합니다.
정답과 변조 종류·고유 원문 수는 packets의 형제 폴더 truth에만 저장합니다.
원본·변조 모두 동일한 필드와 불투명 id를 쓰며 패킷에는 정답이나 변조 이름을 넣지 않습니다.
숫자·식별자·인용 토큰·줄 이동·부정 반전을 각각 20개씩 사용합니다.
식별자·단어는 승인된 같은 파일 또는 같은 저장소·고정 revision의 실제 토큰을 사용합니다.
모양이 비슷한 이름을 우선하고 같은 후보군에서는 파일 출처를 먼저 보며, 임의 표지 이름을 만들지 않습니다.
정수는 ±1과 자릿수를 유지하고, 부정은 자연스러운 does not contain 서술로 반전합니다.
변조한 인용이 이미 발췌에 있으면 다른 후보를 고릅니다. 줄 이동은 지목한 줄에 원래 인용이 없어야 합니다.
인위적인 변조 표지가 든 출처와 주변 발췌도 제외합니다. 변조 종류는 truth에만 남습니다.

처음 적은 양을 생성할 때는 두 옵션을 함께 줄 수 있습니다.

```powershell
python -X utf8 scripts/claims_eval.py generate --scope-file "<WORK>/judge-scope.json" --out-root "<WORK>/l4-small" --limit-items 20 --batch-size 1
python -X utf8 scripts/claims_eval.py summary --scope-file "<WORK>/judge-scope.json" --packets "<WORK>/l4-small/packets/batch-001"
```

`--limit-items K`는 기존 생성 순서에서 섞기 전의 앞 K개 항목을 고릅니다.
원본·변조 한 쌍은 2개 항목이며 K는 2 이상의 짝수입니다. 전체 항목 수보다 큰 K는 전체를 선택합니다.
원본 코퍼스의 안전성·다양성 검사는 그대로 수행합니다. 옵션을 생략하면 전체 항목을 출력합니다.

`--batch-size N`은 **항목이 아니라 패킷 N개씩** 나눕니다. N은 양의 정수입니다.
출력은 `packets/batch-001`, `packets/batch-002` 순서이며 각 폴더에 독립 manifest·패킷 SHA가 있습니다.
정답은 각각 `truth/batch-001/answers.json`, `truth/batch-002/answers.json`에 대응합니다.
생성 결과 JSON의 `batches` 목록에 각 패킷 폴더와 정답 경로·개수가 표시됩니다.
항목 제한은 전체에서 짝을 보존하지만, 섞은 패킷을 나눈 각 배치의 원본/변조 수는 다를 수 있습니다.

judge의 `--packets`에는 확인한 배치 폴더 하나를 지정합니다. 각 배치도 기존 `--max-calls` 제한을 따릅니다.
score의 `--packets`와 `--truth` 역시 같은 배치 번호로 지정합니다.
두 옵션을 생략하면 기존 `packets/manifest.json`·`truth/answers.json` 구조를 유지합니다.

채점은 claims_eval.py의 score 하위 명령에 --packets, --truth, --runs, --scope-file, --out-root를 지정합니다. 결과는 지정 출력 루트의 score.json에 씁니다.
--runs에는 판정 회차 폴더 하나 또는 같은 패킷을 판정한 세 회차를 줍니다.
채점기는 재검증 결과를 다시 계산해 일치 여부를 확인하고 재현율·정밀도·오표시율·
quote_not_in_excerpt 비율·3회 판정 일치율·검증 인용 일치율을 고정 표로 출력합니다.
합성 가짜 판정자 결과는 처리 흐름 시험일 뿐 실제 모델 품질 증거가 아닙니다.

L2 패킷의 assist --dry-run에는 --case-id와 --scope-file을 함께 지정해야 합니다.
case id는 범위 파일의 cases 키에서 선택하며 코드에 고정된 선택지는 없습니다.
select-only·verify에서도 둘 중 하나를 지정하면 두 인자가 모두 필요하고 같은 cases 검사를 적용합니다.
범위 인자를 쓰지 않는 select-only·verify는 기존 오프라인 동작을 유지합니다.

```powershell
python -X utf8 -m smtw claims assist --report "<WORK>/report.md" --agy "<WORK>/transcript.jsonl" --repo "<REPO>" --commit "<REPO>=<COMMIT_SHA>" --case-id "<CASE_ID>" --scope-file "<WORK>/judge-scope.json" --dry-run --out "<WORK>/l2-packets" --format json --blocklist-file "<WORK>/blocklist.txt"
python -X utf8 scripts/claims_judge.py --packets "<WORK>/l2-packets" --scope-file "<WORK>/judge-scope.json" --out-root "<WORK>" --max-calls 4 --blocklist-file "<WORK>/blocklist.txt"
```

Codex 전사는 첫 명령의 --agy 대신 --codex를 사용합니다. 두 번째 명령은 좌상이 패킷을 확인한 뒤 실행합니다.
manifest에는 case_id와 원본 범위 파일 바이트의 scope_sha256을 기록하며,
judge와 범위를 지정한 assist --verify는 다른 범위 파일 내용이면 거부합니다.
verify에 지정한 case id도 manifest와 일치해야 합니다.
허용 접두 밖 출처 조각은 패킷 생성 전에 제외하고 manifest의 skipped_out_of_scope에 개수를 기록합니다.
이 값은 중복 제거 전 출처 조각 수이며, 기존 skipped_counts의 항목별 제외 사유와는 단위가 다릅니다.
기존 비밀 가림·민감 경로 제외 규칙은 그대로 적용합니다.
보고서당 오표시가 3건을 초과하는 결과가 2회 연속이면 실제 호출을 중단하고 좌상 검토로 돌아갑니다.

(보류: 안전장치) 실제 값 대조 생략


### 작업 문서 발췌 범위
사례 항목은 기존 접두 목록 또는 source_prefixes, work_roots, checkpoint 객체를 받습니다.
work_roots는 절대 경로 목록, checkpoint는 파일 하나의 절대 경로입니다.
실제 경로가 든 범위 파일은 비공개 작업 폴더에 두십시오. 저장소 예시는 합성 경로입니다.
작업 문서는 보고서가 파일 경로 또는 파일명을 직접 언급하고 전사에 읽기 결과가 있어야 합니다.
체크포인트는 보고서 언급 조건만 면제하며 전사 읽기 조건은 유지합니다.
작업 트리 직접 읽기나 작업 문서의 git 출처는 허용하지 않습니다.
정규화한 경로의 고객 데이터·비밀·환경·키/토큰·세무/법무/계약·민감 메모리 경로는
부분 일치로 하드 제외하며 skipped_hard_exclude는 제거한 출처 관측 수입니다.
발췌는 항목당 최대 3개, 각각 최대 1,500자입니다.
manifest와 calls.jsonl의 sent_sources는 항목 id별 표시 경로·실제 전송 시작/끝 줄을 기록합니다.


### 인용 정규화와 v1.2 전환
기존 v1.1 패킷은 다시 생성해야 합니다. 프롬프트 SHA와 버전이 다르면 judge가 거부합니다.
supported와 contradicted 모두 근거가 되는 한 줄 전체를 줄 번호 접두 없이 인용합니다.
주장이 줄 번호를 명시하면 먼저 번호를 비교하고, 내용이 다른 줄에 있으면 contradicted로
실제 내용이 있는 줄 전체를 인용합니다. 200자를 넘는 줄밖에 없으면 근거 부족으로 처리합니다.
결정적 후처리는 부분 문자열을 포함하는 유일한 줄 전체로 evidence_quote를 정규화하고,
raw_evidence_quote에 원본을 보존합니다. 서로 다른 여러 줄에 맞는 인용은 표시하지 않습니다.
검증 인용 재현성은 verified_quote_agreement_3(정규화)와
raw_verified_quote_agreement_3(원본)을 함께 출력합니다.


### 선별 다양성
같은 보고서 줄의 여러 원자는 한 항목으로 합치고 경로·범위·식별자 후보를 보존합니다.
observation_conflict를 먼저 선별하고, 나머지 갈래를 우선순위 순으로 한 개씩 순환합니다.
따라서 한 갈래의 후보가 많아도 다른 갈래가 기본 상한 40 안에 들어올 수 있습니다.
branch_counts는 합친 후보 수, selected_branch_counts는 실제 선별 수입니다.
복수 갈래인 줄은 우선순위가 가장 높은 대표 갈래로 집계하며 branches에 전체 갈래를 남깁니다.


### 단일 셸 읽기 출처
다음 제한은 assist 패킷 발췌에만 적용합니다. smtw claims check의 수집·귀속·판정은
75512be 동작을 유지하며, 패킷 제외 때문에 원자 판정이나 요약·상태가 바뀌지 않습니다.
셸 출력은 단일 단순 읽기 명령과 유일한 파일 인자가 확인될 때만 패킷 발췌로 사용합니다.
cat, type, Get-Content(선택적 -LiteralPath/-Path/-Raw), wc(-l/-c), stat(-c %s), head/tail(-n 정수)의
닫힌 문법만 인정합니다. git show <7~40자리 고정 SHA>:<파일>도 단일 읽기로 인정하며,
선택적 -C <폴더>와 표시 전용 꼬리 2>&1, | Out-String, | Select-Object -First N만 허용합니다.
N은 양의 정수이고 -First 출력은 부분 판입니다. 다른 옵션·파일 인자·실행 명령이 붙으면 거부합니다.
그 밖의 셸 연결·파이프·개행·치환·글로브·여러 파일·스크립트 래퍼·알 수 없는 옵션은
파일 근거에서 제외합니다. 출력 속 File Path/FILE 헤더도 이를 우회하지 못합니다.
발췌용 파서는 거부한 명령의 출력 본문을 파일에 귀속시키기 전에 제외합니다.
대조기에는 opaque_version 판을 추가하지 않으며 기존 관측판 수와 판정 규칙을 유지합니다.
대조기에서 읽힌 출력이라는 사실만으로 패킷 발췌가 허용되지는 않습니다.
명령의 모든 토큰과 작업 디렉터리에 결합한 경로를 하드 제외 규칙으로 검사합니다.
기존 패킷은 다시 생성해야 합니다. 프롬프트 v1.2와 별도로 source_policy=single-read-v1 계약을
추가해 수정 전 패킷은 judge 사전 검사에서 거부합니다.

단일 Get-Content를 정확히 감싼 Count/Length 계측은 숫자 계측에만 사용하며 패킷 발췌로 쓰지 않습니다.
조건부 성공 표식과 연결된 완료 로그도 제어 정보로만 남기며 파일 원문 근거가 되지 않습니다.
skipped_ambiguous_command는 파일 읽기로 확정하지 못해 버린 셸 출력 관측 수입니다.


### 호출 격리 기본값
--strict-mcp-config와 --disable-slash-commands는 lean 여부와 무관하게 항상 적용합니다.
lean-context는 system-prompt-file과 빈 임시 cwd만 추가합니다.
자식 환경은 실행 경로·운영체제·임시 폴더·개인 홈·언어 설정 이름의 허용 목록만 복사합니다.
개인 구독 설정 폴더 두 이름은 기존처럼 고정합니다.
ANTHROPIC_* 또는 CLAUDE_CODE_USE_BEDROCK/VERTEX 이름이 있으면 값을 조회하지 않고 거부합니다.
전역 tests/conftest.py는 모든 시험의 실제 judge._launch 진입을 실패시킵니다.


### 충돌·경로·인용 보완
서로 다른 충돌 문장/경로는 같은 보고서 줄에 있어도 분리합니다. 같은 충돌 원자의 중복만 합칩니다.
이 예외로 충돌 판 A/B가 다른 문장의 발췌 때문에 상한을 넘겨 사라지는 것을 방지합니다.
직접 파일 언급 뒤의 한글 조사와 문장 끝 마침표를 인정합니다.
경로 제외는 구성 요소와 디렉터리 범주 단위이며 judge_contract.py, syntax.md 같은 일반 파일은 허용합니다.
tokens.txt, pw.md, accounting 및 같은 민감 이름 계열은 제외합니다.
심볼릭 링크와 Windows 짧은 이름은 내용 읽기 없이 실제 경로를 해석해 다시 검사합니다.
해석하지 못한 짧은 이름·네트워크/장치 경로는 근거로 허용하지 않습니다.
정규화 인용은 정확 일치 줄을 먼저 보존하고, 승격 결과가 가림 표지를 포함하거나 200자를 넘으면 표시하지 않습니다.


### 비공개 이름 차단과 업무 문서 경로
assist --dry-run과 judge에 동일한 --blocklist-file을 지정합니다.
한 줄에 이름 하나를 적고 한글/영문 표기는 각각 등록합니다. 대소문자·공백·전각은 비교할 때 무시합니다.
실제 목록은 비공개 작업 폴더에만 둡니다. 저장소 예시는 합성 이름뿐입니다.
manifest와 호출 기록에는 유효 이름 건수와 파일 SHA 앞 8자만 기록합니다. 이름·목록 본문은 출력하지 않습니다.
work_roots 또는 checkpoint를 쓰는 범위에서는 목록이 필수이며 파일 없음·빈 목록·변경은 거부합니다.
assist는 claim·앞뒤 문맥·원문 발췌·표시 경로 적중 항목을 통째 제외하고 skipped_blocklist로 집계합니다.
클라우드 동기화/Documents 폴더 아래 docx/xlsx/pdf/hwp 경로도 항목에서 제외합니다.
경로에 깨진 글자가 있어도 감지하며 skipped_business_doc로 집계합니다.
judge는 모든 패킷을 첫 호출 전 및 매 호출 직전에 검사하고, 하나라도 적중하면 전체 호출을 거부합니다.
무시·사람 승인·강제 통과 옵션은 없습니다. privacy_policy=blocklist-v1 이전 패킷은 재생성해야 합니다.
