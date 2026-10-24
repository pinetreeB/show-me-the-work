# Changelog

## [Unreleased]

### Fixed

- **R2: 출력 리디렉션이 같은 명령의 삭제 대상을 가렸습니다.** 한 구간에서 처음 걸린 탐지 1건만
  돌려주는데 리디렉션 탐지가 삭제 탐지보다 앞에 있어, `rm peer.py 2>/dev/null`은 `/dev/null`만,
  `rm peer.py > own.log`는 `own.log`만 검사했습니다. 이제 리디렉션을 명령 인자에서 떼어 낸 뒤
  명령 자체의 대상과 **모든** 리디렉션 대상을 함께 검사합니다. 단어에 붙은 리디렉션
  (`echo x>peer.py`, `a>b>c`), 리디렉션이 명령 앞에 오는 형태(`>log rm peer.py`), 단어에 붙은
  서브셸 괄호(`(true && rm peer.py)`)도 같은 규칙으로 해석합니다. 다음 단어에서 가져온 리디렉션
  대상도 다시 쪼개므로 `echo x > own.log>peer.py`의 `peer.py`도 검사하고, 단어에 붙은 입력
  리디렉션(`rm peer.py<in`)이 삭제 대상을 가리지 않습니다. 백슬래시로 이스케이프한 `>`
  (`echo safe\>peer.py`)는 bash·PowerShell 모두 글자로 출력하므로 리디렉션으로 보지 않습니다.
  PowerShell의 버리기 관용구 `2>$null`·`*>$null`은 **호스트가 PowerShell 도구라고 알려 준
  경우에만** 파일을 쓰지 않는 것으로 봅니다. bash에서 `$null`은 변수라
  (`null=peer.py; echo x >$null`은 `peer.py`를 씁니다) 그 밖의 셸에서는 이전처럼 해석 불가로
  차단합니다. Claude·Codex 어댑터는 PowerShell 도구 이름을 그대로 R2에 전달합니다.
- **R2·상태 보호: `cd` 뒤 상대경로를 프로젝트 루트 기준으로만 풀었습니다.** `cd src && rm peer.py`는
  `<root>/peer.py`를, `cd .smtw && rm ledger.json`은 `<root>/ledger.json`을 검사해 통과했습니다. 이제
  상대 대상은 루트와 함께 **명령 안의 `cd`/`pushd`/`Set-Location` 목적지**와 **호스트가 알려 준
  셸 작업 폴더**(`shell_cwd`, 어댑터가 hook payload의 `cwd`를 전달) 기준으로도 모두 검사합니다.
  해석을 늘리기만 하므로 보호가 줄어드는 경우는 없습니다. 같은 폴더로 여러 번 옮기는 경우
  (`cd src && cd src` → `src/src`)도 순서대로 따라갑니다. 목적지를 정적으로 알 수 없는 `cd`
  (`cd "$DIR"`, `cd -`, 짝 없는 `popd`)가 있고 상대 파괴 대상이 있으면 fail-closed로 차단합니다.
  Windows에서는 Git Bash 경로(`/c/Users/...`)도 드라이브 경로로 함께 해석합니다.
- **상태 파일 마찰 장치가 R2 밖의 쓰기 명령을 놓쳤습니다.** `install`, `patch`, `curl -o/--output`,
  `wget -O/-P`의 쓰기 대상과 단어에 붙은 리디렉션(`x>>.smtw/ledger.json`)을 이제 `.smtw/**`
  (및 레거시 `.fable-lite/**`) 직접 변경으로 차단합니다. `cd .smtw && echo x >> ledger.json`처럼
  작업 폴더가 바뀐 경우도 같습니다. 쓰기 대상을 고를 때 출력 리디렉션은 인자에서 빼므로
  `install file .smtw/ledger.json > own.log`의 설치 위치를 로그 파일로 착각하지 않습니다.
- **R2: 입력 리디렉션이 명령 인자로 읽혀 대상을 가렸습니다(리뷰 r2).** `ln -sf other.py peer.py <
  /dev/null`·`rsync -a --delete tmp/ src/ < /dev/null`은 실제 목적지 대신 `/dev/null`을 검사했고,
  명령 앞의 입력 리디렉션(`< /dev/null rm peer.py`)이나 앞쪽 변수 대입에 붙은 리디렉션
  (`FOO=a>peer.py true`)은 명령 이름 자체를 가렸습니다. 이제 입력·출력 리디렉션을 모두 인자에서 뗀
  뒤 명령 이름과 대상을 찾습니다. 셸이 표준 입력에서 스크립트를 읽을 때(`bash < script.sh`,
  `bash <<< "…"`)만 입력 리디렉션을 스크립트로 보고, `-c`나 스크립트 파일을 받은 셸에서는 데이터로
  보므로 `bash -c 'rm peer.py' <<< x`의 `-c` 내용도 검사합니다(전에는 here-string·heredoc이 붙으면
  건너뛰었습니다). 파이프로 받은 셸에 꼬리를 붙인 형태(`curl … | sh 2>&1`)와 heredoc 앞의
  리디렉션(`bash 2>/dev/null <<EOF`)도 스크립트 입력으로 봅니다. `bash script.sh <<EOF`는 heredoc
  없는 `bash script.sh`와 똑같이 판정합니다.
- **R2: rsync 옵션 값을 시험 실행으로 오인했습니다(리뷰 r2).** 값을 받는 긴 옵션 목록이 일부뿐이라
  `rsync -a --out-format --dry-run --delete tmp/ src/`의 `--dry-run`(실제로는 `--out-format`의
  값)을 시험 실행으로 보고 통과시켰습니다. rsync 3.4의 값 옵션을 모두 등록했습니다.
- **상태 파일 마찰 장치 보완(리뷰 r2).** `cd .smtw < /dev/null`의 목적지를 읽고, 목적지를 알 수
  없는 `cd`(`cd "$DIR"`, `` cd `pwd`/.smtw ``, `cd -`, 짝 없는 `popd`) 뒤의 상대경로 쓰기는 상태
  폴더 안일 수 있어 차단합니다(절대경로·`~`·변수로 시작하는 경로·`/dev/null`은 통과). 백틱 명령
  치환이 들어간 `cd` 목적지도 `$(…)`처럼 알 수 없는 목적지로 봅니다. `install -Dt .smtw file`처럼
  묶어 쓴 짧은 옵션, `--target=.smtw` 같은 긴 옵션 줄임말, `patch -Ed.smtw`·`--dir=.smtw`도 쓰기
  위치로 읽습니다.
- **상태 파일 마찰 장치: 명령 앞의 리디렉션을 명령 이름으로 읽었습니다(리뷰 r4).**
  `< /dev/null install file .smtw/ledger.json`은 `<`를 명령 이름으로 보아 `install`을 놓쳤습니다.
  이제 리디렉션(`<`, `0<`, `<<<`, `>`, `2>` 등)과 앞쪽 변수 대입(`LC_ALL=C`)을 뗀 뒤 명령 이름을
  찾습니다. 인자 쪽에서도 `<>`·`<&0`처럼 두 토큰으로 쪼개지는 입력 리디렉션의 대상을 설치 위치로
  착각하지 않습니다.
- **상태 파일 마찰 장치: `cd` 뒤 홈 경로 쓰기를 잘못 막았습니다(리뷰 r4, 이 브랜치에서 생긴 회귀).**
  `cd .smtw && echo x >> ~/notes.txt`를 `.smtw/~/notes.txt` 쓰기로 보고 차단했습니다. `cd` 목적지
  기준 해석은 이제 작업 폴더에 따라 달라지는 상대경로에만 적용하고, `~`·변수로 시작하는 경로와
  `/dev/null`·`nul`은 루트 기준 해석(Git Bash 드라이브 경로 변환 포함)만 합니다.
- **R2: `coproc`의 리디렉션 대상을 놓쳤습니다(리뷰 r4 재검토, 이 브랜치에서 생긴 회귀).**
  `coproc rm own.py >peer.py`에서 안쪽 명령의 첫 탐지(`own.py` 삭제)만 남기고 `peer.py` 덮어쓰기를
  버렸습니다. 이제 `coproc` 안쪽 명령의 탐지 결과를 모두 검사합니다.
- **R2·상태 보호: 공백 든 인용 대상 뒤에 붙은 리디렉션을 놓쳤습니다(리뷰 r4 재검토).**
  `echo x >"own file.txt">.smtw/ledger.json`처럼 따옴표 안 공백 때문에 둘로 잘린 대상을 다시 이은
  뒤, 그 뒤에 붙은 리디렉션(`>.smtw/ledger.json`, `>peer.py`)을 다시 쪼개 검사합니다.
- **상태 파일 마찰 장치: 명령 경계와 옵션 값을 잘못 읽었습니다(리뷰 r4 재검토).** 쓰기 대상을 고를
  때 R2와 같은 명령 경계를 써서, 줄바꿈·`&` 뒤의 다른 명령(`install src .smtw/ledger.json` 다음 줄의
  `echo done`)을 설치 위치로 착각하지 않습니다. 괄호·중괄호·`sudo` 같은 앞붙이 안의 명령도 찾습니다.
  `curl`·`wget`은 값을 받는 다른 옵션이 다음 단어를 가져가도 그 단어를 옵션으로 다시 읽어
  `curl --data -o --output .smtw/ledger.json`의 `--output`을 놓치지 않고, `wget --output-doc=…`
  같은 긴 옵션 줄임말도 쓰기 옵션으로 봅니다.
- **R2: 강제 링크(`ln`) 옵션·소스 해석을 보완했습니다(리뷰 r4 재검토).** 옵션을 getopt 방식으로
  읽어 `--for`(`--force` 줄임말)·`--targ=DIR`·`-tDIR`·피연산자 뒤의 옵션을 알아보고, `-S`의 값은
  옵션으로 읽지 않습니다(`-S.f`의 `f`는 강제 옵션이 아니고, `-S --help`의 `--help`는 도움말이
  아닙니다). 기존 파일을 백업 후 바꾸는 `-b`와 확인 후 지우는 `-i`도 같은
  교체로 봅니다. 이름을 알 수 없는 소스(`other/*`, `$DIR`)로 링크를 만들면 링크 폴더 전체를
  검사하므로 `ln -sf other/* src/`가 `src/peer.py`를 바꾸는 경우를 막습니다.
- **R2: `rsync --files-from`과 `--remove-source-files`를 함께 쓸 때 원본 폴더 안 삭제를
  놓쳤습니다(리뷰 r4 재검토).** 목록에 적힌 파일은 재귀 옵션 없이도 원본 폴더 안에서 지워지므로,
  파일 목록을 쓰면 원본 폴더 전체를 검사합니다.
- **Qwen 설치: smtw 표식만 흉내 낸 외부 훅에 trust를 자동 등록했습니다(리뷰 r4 재검토).**
  `Notification`처럼 smtw가 다시 쓰지 않는 이벤트의 훅에 smtw 경로만 넣으면 smtw 훅으로 보고
  넘어갔는데, 그 훅은 병합 뒤에도 남아 신뢰 폴더에서 실행됩니다. 이제 설치가 실제로 교체하는 훅만
  예외로 보고, 나머지는 `--trust` 명시가 필요합니다.
- **설치기가 링크된 설정 폴더를 따라가 사용자 전역 설정을 바꿀 수 있었습니다.** Codex·Qwen 프로젝트
  설치에서 `<target>/.codex`·`<target>/.qwen`이 심볼릭 링크·junction이거나 프로젝트 밖으로 풀리면
  이제 설치를 거부합니다.

### Changed

- **R2 어휘 확장(KL5 일부 편입).** `rsync`(`--delete*`는 대상 폴더 전체, 재귀 복사는 덮어쓸 위치,
  `--remove-source-files`는 원본), `robocopy`(`/MIR`·`/PURGE`·`/MOV(E)` 포함), `ln -f`/`ln -sf`
  (링크 이름 위치의 기존 파일)를 파괴 대상으로 검사합니다. 원격 소스(`rsync -a host:src/ src/`)도
  로컬 대상 폴더에 쓰므로 같은 규칙으로 보고, 복사될 이름을 알 수 없는 소스(`host:`, `tmp/*`,
  `--files-from`, `-R`)는 대상 폴더 전체를 검사합니다. 내 파일·원격 대상·`-n`/`/L` 시험 실행은
  통과합니다. `tar -x`는 아카이브 안 파일 이름을 정적으로 알 수 없어 계속 알려진 한계로 둡니다.
- **Qwen 프로젝트 설치의 trust 자동 등록을 조건부로 바꿨습니다.** 대상의 기존
  `.qwen/settings.json`에 smtw가 넣지 않은 훅·MCP 서버·도구 명령이 있으면, 폴더를 신뢰 등록하는
  순간 Qwen이 그것들을 실행하게 되므로 자동 등록을 거부하고 `--trust`로 명시하도록 안내합니다.
  그런 설정이 없으면 지금처럼 자동 등록합니다.
- 공개 설계 문서·설치 예시에서 내부 원격 작업 환경 식별 정보(접속 별칭·호스트명·사용자 경로·도구
  버전)를 자리표시자로 바꿨습니다.

## [2.8.2] - 2026-08-08 — Legacy Ledger Recovery

**설치된 하네스가 조용히 죽어 있을 수 있습니다.** 이 저장소 자신이 그 상태였습니다 —
장부의 마지막 기록이 **17일 전**이었고, 그동안 이벤트를 하나도 남기지 못한 채 차단만 하고
있었습니다. v2.8.1 릴리스 작업 전체가 그 상태에서 진행됐습니다.

증상은 조용합니다. 게이트는 정상처럼 동작하고, 진단은 오히려 "활성 턴 0개"라며 안심시킵니다.
`smtw doctor`로 `Ledger health`를 확인하세요. `invalid`면 이 릴리스가 필요합니다.

### Fixed

- **구 스키마 장부가 수리도 마이그레이션도 불가능한 상태로 고착됐습니다.** v2 이전 invocation은
  `status`와 `started_at`이 없습니다. 시각이 없으면 임대 만료를 판정할 수 없으므로 자동으로
  닫는 것은 거부됩니다 — 증명할 수 없는 결과를 "완료"로 적는 것이 바로 이 하네스가 막으려는
  행위이니 옳은 거부입니다. 문제는 **운영자가 명시 승인해도 도달할 수 없었다**는 점입니다.
  정리는 **턴 단위**로 후보를 고르는데 형식 검사는 **남은 장부 전체**를 보기 때문에, 후보에서
  제외된 턴(current identity · unsettled attribution)이 status 없는 행을 그대로 들고 있으면
  apply 전체가 `removed: 0`으로 되돌려졌습니다.

  이제 `--retire-legacy-statusless`로 그 턴들을 **완료가 아니라 '증거 부족 퇴역'**으로 옮깁니다.
  원문 bytes를 먼저 archive에 보관하고, `retired_turns`에 사유와 archive digest를 남깁니다.
  운영자가 영향 invocation 수를 정확히 대야만(`--ack-unverified`) 열리므로 실수로 실행되지
  않습니다. 판정 근거는 경과 시간이 아니라 **스키마 세대**입니다 — 현행 코드는 언제나 `status`를
  기록하므로, status가 없다는 것은 구 스키마 데이터라는 뜻이고 살아 있는 턴은 대상이 될 수
  없습니다.

- **죽은 턴이 "현재 턴"으로 오인되어 영구 보호받았습니다.** 세션 정보 없이 조회하면 agent 이름만
  같으면 사전순 첫 턴을 현재로 골랐습니다. 실측에서 이름이 같은 턴이 12개였고, 선택된 것은 이미
  종료된 과거 세션이었으며, 정작 살아 있는 세션의 턴은 장부에 없었습니다.

- **진단이 스스로를 오진했습니다.** 스키마가 invalid일 때 `doctor`가 실제 39개인 활성 턴을 **0으로
  보고**했습니다(오류 경로에서 기본값을 반환). 이 거짓 값이 조사를 잘못된 방향으로 이끌었습니다.
  또 "다음 안전 명령"으로 안내하던 `migrate --invocation-statuses --check`는 해당 행이 unsafe인
  한 **언제나 실패하는 막다른 명령**이었습니다. 이제 실제 복구 경로를 안내합니다.

- **퇴역 턴이 프롬프트 재생으로 되살아났습니다.** 주변 가드에 `event != "prompt"` 조건이 있어
  프롬프트는 종료 검사를 통과했고, 퇴역한 turn id로 프롬프트가 재생되면 그 턴이 활성으로
  복구되면서 퇴역 기록도 함께 남았습니다. 퇴역은 "결과를 증명할 수 없다"는 운영자의 판단이므로
  재생으로 뒤집히지 않습니다.

- **세션 키가 다르면 퇴역을 찾지 못했습니다.** 교차 조회가 `closed_turns`만 검색해, 옛 세션 키로
  퇴역한 턴이 새 세션 키 조회에서 누락되고 오래된 invocation이 조용히 새 턴에 붙었습니다.
  완료 턴에서는 거부되던 것이 퇴역 턴에서만 뚫려 있었습니다.

- **마이그레이션 뒤 legacy 디렉터리를 지우면 감독이 조용히 꺼졌습니다.** `config.json`은
  transient로 분류돼 이전되지 않으므로, 이전 후에도 활성화는 legacy 디렉터리에 남은 파일에
  매달려 있습니다. 그 상태로 `.fable-lite`를 지우면 `Activation`이 `inactive`가 되는데,
  **장부는 여전히 `healthy`로 보이고 게이트만 사라집니다.** 알아채기 어려운 실패입니다.
  이제 `smtw migrate`가 이 상태를 감지해 `smtw init`을 안내합니다.

  이미 이전을 마친 프로젝트라면 지금 확인하세요:

  ```
  smtw doctor    # Activation: inactive 이면 -> smtw init
  ```

### Changed

- `turn_is_terminal`이 replay 판정의 단일 진입점입니다. 완료와 퇴역을 함께 봅니다.
- CI에 `concurrency`를 추가했습니다. 같은 PR에 새 push가 오면 이전 run을 취소합니다 — 그전에는
  두 run이 같은 Windows 러너를 두고 경쟁해 대기가 배로 늘었습니다. main push는 취소하지 않습니다.

### Notes

- 이 릴리스에 스냅샷 정리(retention) 기능은 없습니다. 고아 스냅샷이 계속 쌓이므로
  (실측: 428개 / 147MB) 다음 릴리스 후보입니다.
- 조사 전말은 `docs/reviews/2026-08-07-ledger-statusless-deadlock.md`에 있습니다.

## [2.8.1] - 2026-08-06 — Container Attribution & PowerShell Binder Coverage

v2.8.0 직후 2차 적대 리뷰 3건(문서 규격 감사 · 100변형 폭격 · 미검토 영역)에서 **41건**이 나왔고,
그 수리를 다시 검증해 **4건**이 더 나왔습니다. 전부 닫습니다. 그중 하나는 Critical이며
**v2.8.0 이전부터 있던 결함**입니다.

리뷰 지적은 게이트 종단으로 **전건 재현한 뒤에만** 채택했습니다. 적대 검증은 3라운드
(100 · 108 · 75변형) 돌렸고 마지막 라운드에서 처음으로 신규 발견이 0건이었습니다.

### Security

- **🔴 상위 디렉터리를 대면 하위의 미정산 파일이 통째로 비껴갔습니다.** 게이트가 canonical
  경로의 **정확 일치만** 비교하고 prefix 관계를 보지 않아, 다른 에이전트가 `shared-dir/peer.py`를
  점유한 상태에서 `rm shared-dir/peer.py`는 차단되지만 **`rm -r shared-dir`는 통과**했습니다.
  `rm -rf` · `Remove-Item -Recurse` · `mv`/`Move-Item`/`Rename-Item` 소스 · `git checkout|restore -- <dir>` ·
  `git rm -r` 전부 해당합니다. 폴더 단위 파괴가 파일 단위보다 피해가 크다는 점에서 실질 위험이
  가장 컸던 갭입니다.
  이제 파괴 대상 중 컨테이너 의미가 있는 경로는 `/` 경계를 보존한 하위 관계로 open invocation
  candidate와 영속 `path_attribution`을 모두 검사합니다. 재귀 복사는 `destination/source` 파생
  경로까지 봅니다. 이름이 겹치는 형제(`shared-dir` 대 `shared-directory`), 읽기 명령, 자기 소유
  디렉터리는 계속 통과합니다.
- **PowerShell 동적 식 차단이 삭제 계열만 비껴갔습니다.** v2.8.0이 도입한 `(`-시작 경로
  fail-closed가 공용 resolver에만 적용돼, `mv (gi peer.py) out`은 차단되는데
  **`Remove-Item (gi peer.py)`는 통과**했습니다. 또 `@(`·`+(`·`&{`·`.{`는 규칙 자체를 비껴갔습니다
  (`Remove-Item @(peer.py)`). 이제 모든 탐지기가 공용 resolver를 쓰고 지정 접두사 전체를
  fail-closed 합니다. 따옴표로 감싼 `'(literal).py'`·`'@(literal).py'`는 정적 파일명으로 유지합니다.
- **PowerShell 공식 바인더 문법을 덮습니다**: `-Name:Value` 콜론 결합, 모호하지 않은 파라미터
  접두사(`-Dest`·`-New`·`-Fo`), 정적 literal의 파이프라인 by-value 바인딩
  (`'mine.py' | Copy-Item -Destination peer.py`, `'Remove-Item peer.py' | Invoke-Expression`),
  그리고 `Tee-Object` 정식 cmdlet 이름(별칭 `tee`만 등록돼 있었습니다). 모호한 접두사는
  fail-closed 하고 switch의 `$true`/`$false` 의미는 보존합니다.
- **옵션 문법 갭**: `sed -ni`·`-Ei`(cluster 안의 실제 `i`) · `find -fprint`/`-fprint0`/`-fls`/`-fprintf`
  (기존 파일을 0바이트로 절단합니다) · `git switch -f`/`--force`/`--orphan` ·
  `git checkout|restore -p`(pathspec 없는 patch 모드) · `cp`/`mv`의 `-tDIR` 붙임형과
  `--target-directory=DIR` · `cp --parents`(상대경로 보존) · `cp`/`mv --context`(선택/무인수).
- **Windows cmd의 재귀 삭제 문법이 컨테이너 보호를 비껴갔습니다.** 위 컨테이너 귀속 비교가
  `-r`/`--recursive`/`-Recurse`만 재귀로 인식해 **`rd /s <dir>`·`rmdir /s <dir>`(대문자 `/S` 포함)가
  통과**했습니다. `rm -r`과 같은 작업인데 셸 문법만 달랐습니다. 비재귀 `rd <dir>`은 비어 있지 않으면
  실패하는 명령이라 계속 통과합니다.
- **파이프 by-value 바인딩이 본명만 인식했습니다.** `'rm peer.py' | Invoke-Expression`은 차단되는데
  **별칭 `| iex`는 통과**했고(PowerShell에서는 별칭이 더 흔합니다), 수신 토큰이 정확히 1개일 때만
  바인딩이 발동해 **`| Invoke-Expression -Verbose`처럼 파라미터가 하나만 붙어도 무력화**됐습니다.
  또 비-literal 입력(`Get-Content x.ps1 | iex`)이 eval 계열로 흘러도 fail-closed 되지 않았습니다 —
  `cat x.sh | bash`는 이미 차단하던 것과 같은 성격입니다.
- **`Move-Item`/`Rename-Item`의 소스를 `-Path`로 명명하면 대상이 통째로 사라졌습니다.**
  `Move-Item peer.py out`(위치 인자)은 차단되는데 **`Move-Item -Path peer.py out`은 통과**했습니다.
  명명 소스와 위치 목적지를 섞는 조합에서 positional fallback이 성립하지 않았습니다.
  `-LiteralPath`·콜론 결합·별칭(`mi`·`rni`)도 같았습니다. PowerShell에서 파라미터 명시는 권장
  스타일이라 실사용 형태입니다.
- **콜론 결합 파라미터의 인용 경로가 첫 조각만 취해졌습니다.** `Remove-Item -Path:'peer dir/file.py'`가
  `'peer`를 대상으로 잡아 통과했습니다. 공백 있는 경로는 Windows에서 흔합니다.
- **git patch 모드가 옵션 값·단축 묶음 뒤에 오면 놓쳤습니다.** `git checkout -p`는 차단되는데
  `git restore -s HEAD -p`·`git checkout --detach HEAD -p`·`-pq`는 통과했습니다. 값 옵션을 소비하기
  전에 정확히 `-p`/`--patch` 토큰만 매칭해 `HEAD`를 경로로 기록했습니다. pathspec 없는 patch 모드는
  저장소 전역 대화식 되돌리기라 미정산 파일을 건드릴 수 있습니다.
- **파이프 수신 셸 래퍼가 계속 드리프트했습니다.** unwrap 목록을 손으로 관리해
  `nice`·`setsid`·`ionice`·`stdbuf`·`time`·`busybox`·`toybox`가 빠져 있었습니다. 이제
  `WRAPPER_COMMAND_NAMES`에서 **파생**해 두 집합이 벌어지지 않습니다. `powershell`·`pwsh`·`cmd`도
  파이프 수신 셸로 취급하되 명시적 command/script 옵션은 stdin script로 오인하지 않습니다.

### Fixed

정상 개발 흐름을 막던 오탐을 함께 수리했습니다. 이 도구는 마찰이 커지면 꺼지고, 꺼진 게이트는
미탐 100%이므로 오탐을 미탐보다 우선합니다.

- **`perl -Ilib`·`-Mstrict`가 in-place 편집으로 오인**됐습니다. 옵션 payload 안의 문자 `i`를
  option letter와 구분하지 않았습니다. 실제 `-i`·`-pi`·`-i.bak`는 계속 탐지합니다.
- **`sc stop <서비스>`가 차단**됐습니다. `sc`를 무조건 `Set-Content` 별칭으로 정규화해 cmd의 서비스
  제어와 충돌했습니다. 표준 서비스 제어 동사일 때만 별칭 확장에서 면제합니다.
- **`-WhatIf:$true`가 차단**됐습니다. 면제가 bare `-WhatIf` 토큰만 일치했습니다.
  `-WhatIf:$false`는 계속 실제 파괴 경로로 평가합니다.
- **`bash -C`가 `-c`로 오인**돼 short option 대소문자를 보존하도록 고쳤습니다(수리 중 발견).

### Changed

- 회귀 테스트에 **동형 단언**을 넣었습니다. 같은 입력이 한 탐지기에서 차단되면 다른 탐지기에서도
  차단되는지 검사합니다(`mv (gi …)` 대 `Remove-Item (gi …)`). 이번 Critical의 원인이
  "수리를 한 탐지기에만 적용하고 지나간 것"이었고, 이 프로젝트에서 같은 계열 사고가
  세 번째였기 때문입니다(v2.7.1 어댑터 캐시 · v2.8.0 신원 승격 · 이번 동적 식).
- 수용 하네스 `eval/r2_vocabulary_acceptance.py`가 **179건**으로 확장됐습니다(v2.8.0 시점 93건).
  컨테이너 귀속과 공백 포함 경로를 재는 별도 픽스처가 추가됐습니다.

### Known Limitation

- **positional `Set-Content mine.txt 'data'`·`Out-File mine.txt`가 차단됩니다.** `-Path` 없이 위치
  인자로만 경로를 주면 fail-closed 합니다. `-Path`/`-FilePath` 명시형은 통과합니다. PowerShell에서
  positional은 일상 문법이라 실사용 마찰이 있으며, **v2.8.0부터 존재하던 동작**입니다(이번 릴리스가
  만든 것이 아님을 diff로 확인). 후속 수리 대상입니다.
- 기존 한계는 변동 없습니다: `>` 리다이렉션의 신규 파일 오차단 · `| iex`/`| python`/`| node`
  인터프리터 파이프 수신 · `python -c` 임의 코드 · `robocopy /MIR` · `rsync --delete` · `ln -sf` ·
  `tar -xf` · `Add-Content` · 대상 경로의 따옴표·이스케이프 분할(`rm pe""er.py`).


## [2.8.0] - 2026-08-05 — Destructive-Vocabulary & PowerShell Parser Coverage

R2가 "파괴 형태 아님"으로 흘려보내던 명령들을 닫습니다. 탐지 어휘가 좁아
`unlink`·`shred`·`truncate`·`Clear-Content`·`sed -i`·`git rm`·PowerShell 별칭(`ri`·`mi` …)
등이 **다른 에이전트의 미정산 파일을 대상으로 해도 통과**했습니다. 인코딩·동적 코드가 아닌
리터럴 경로의 정적 단일 명령이라 기존에 선언한 비목표로 면책되지 않습니다.

접근은 **B-Routing**입니다. 파서는 "파괴적이다/아니다"를 최종 판정하지 않고 정적 경로 후보를
**귀속 검사로 라우팅만** 합니다. 파서가 `stat`을 부르지 않으므로 TOCTOU를 새로 들이지 않고,
덮어쓰기 여부를 판정할 필요도 없습니다. 이 설계는 실측이 정했습니다 — 경쟁 에이전트가 파일을
점유한 상태에서도 `rm my_own_file.py`는 통과합니다. 탐지는 차단이 아니라 검사로 보내는 것이고,
실제로 막히는 경우는 남의 미정산 작업물을 덮을 때뿐입니다.

### Security

- **R2가 삭제·절단 명령의 상당수를 "파괴 형태 아님"으로 흘려보내던 문제를 닫았습니다.** 탐지
  어휘가 `rm`·`Remove-Item`·`del`·`rd`·`rmdir`(삭제)와 `Set-Content`·`Out-File`(절단)뿐이어서,
  탐지기가 0건을 내면 `not destructive-shaped`로 **귀속 검사 자체를 건너뛰었습니다**. 그 결과
  `unlink`·`shred`·`truncate`·`Clear-Content`·`dd of=`·`cp /dev/null f`·`mv f /dev/null`·
  `sed -i`·`perl -i`·`git rm`·`Rename-Item`·`New-Item -Force`·정적 `[IO.File]::Delete`/
  `WriteAllText`가 **다른 에이전트의 미정산 파일을 대상으로 해도 통과**했습니다. 인코딩·동적
  코드가 아닌 리터럴 경로의 정적 단일 명령이므로 기존에 선언한 비목표로 면책되지 않습니다.
- **접근 방식은 B-Routing입니다**: 파서는 "파괴적이다/아니다"를 최종 판정하지 않고 정적 경로
  후보를 **귀속 검사로 라우팅만** 합니다. 파서는 `stat`을 호출하지 않으므로 TOCTOU를 새로
  들이지 않고, 덮어쓰기 여부를 판정할 필요도 없습니다 — 남의 미정산 경로일 때만 차단됩니다.
  `cp`/`Copy-Item`은 목적지만, `mv`/`Move-Item`/`Rename-Item`은 소스와 목적지를 모두 대상으로
  잡습니다(sink 목적지면 소스만). 소스를 읽기만 하는 `cp peer.py backup.py`는 통과합니다.
- **직접 호출한 `Invoke-Expression`/`iex`가 우회 경로였습니다.** `eval`은 불투명 래퍼였으나
  PowerShell의 동일 기능은 목록에 없어 `iex 'Remove-Item f'`가 통과했습니다. `WRAPPER` ·
  `OPAQUE_WRAPPER` 양쪽에 등록했습니다(한쪽만 넣으면 첫 membership 검사에서 빠져나가 수리가
  무효가 됩니다).
- **파이프로 스크립트를 받는 셸의 비대칭을 해소했습니다.** `bash < payload.sh`는 차단되는데
  `cat payload.sh | bash`·`curl … | bash`는 통과했습니다. 셸 segment에 유입 구분자를 보존해
  `|` 수신자가 stdin을 스크립트로 읽는 POSIX 셸이면 fail-closed 합니다. `| head`·`| grep` 등
  비실행 수신자와 `| python`·`| node`·`| iex`는 건드리지 않았습니다.
- **PowerShell 표준 별칭이 어휘 전체를 무력화하고 있었습니다.** `Remove-Item peer.py`를
  `ri peer.py`로 **한 단어만 바꾸면 통과**했습니다(`ri`·`rni`·`cpi`·`mi`·`clc`·`sc`·`ni` 전원,
  대소문자 무관). 셸이 PowerShell인 환경에서는 별칭이 오히려 기본 사용 형태입니다. 표준 별칭을
  canonical cmdlet으로 정규화해 기존 탐지기로 보냅니다. `sc.exe` 같은 명시적 실행 파일명은
  별칭 확장에서 제외합니다.
- **쉼표로 묶은 경로 배열도 통과했습니다.** `Remove-Item a.py,peer.py`에서 파서가
  `a.py,peer.py`를 **경로 하나**로 취급해 `peer.py`가 대상이 되지 않았습니다. 이번에 추가한
  어휘뿐 아니라 **기존 `Remove-Item`도 같은 방식으로 뚫렸습니다**(수리 전 커밋 대조로 확인).
  위치 인자와 `-Path`·`-LiteralPath`·`-Destination`의 쉼표 요소를 개별 경로로 펼칩니다.
- **파이프 수신 셸 차단이 래퍼 한 단어로 우회됐습니다.** `curl … | sudo bash`·`| doas bash`·
  `| nohup bash`·`| timeout 5 bash`가 통과했습니다. 수신자 판정 시 이 네 래퍼를 해제합니다.
- **건조 실행은 통과시킵니다**: `Remove-Item -WhatIf`(다른 cmdlet은 이미 인정하는데 이것만
  누락돼 있었습니다)와 `git rm -n`·`--dry-run`·`--cached`. 앞의 둘은 아무것도 지우지 않고
  `--cached`는 인덱스에서만 제거해 워크트리 파일이 남습니다.
- 검증: 경쟁 에이전트가 미정산 파일을 점유한 상태의 게이트 종단 하네스에서 **차단 대상 53/53
  BLOCK · 정상 작업 40/40 ALLOW(미탐 0 · 오탐 0)**. 게이트 1회 평균 22.2 µs로 훅 지연 회귀는
  없습니다. 새 어휘의 `--help`·`--version`도 ALLOW로 고정했습니다.

### Fixed

- **Antigravity 어댑터에만 복원 신원 승격(`legacy_default` → `exact`)이 없었습니다.** 나머지 세
  어댑터에는 모두 있던 수리입니다. 누락 시 유효한 namespaced 계약이 미귀속 편집으로 오인되고
  계약 저자 감사 증거가 Antigravity에서만 기록되지 않습니다.
- **`codex_cli`의 `post_tool_use`·`stop`·`user_prompt_submit`에 ledger 읽기 캐시가 없었습니다.**
  v2.7.1이 어댑터 단위로는 전수했으나 codex 자신의 형제 엔트리포인트는 빠져 있었습니다. 런타임
  실측 uncached 로드: stop 9 → 1, user_prompt_submit 6 → 3, post_tool_use 12 → 2.

### Changed

- **어댑터 커버리지를 테스트가 구조적으로 강제합니다.** 기존 캐시 검사는
  `inspect.getsource()` + 문자열 포함 단언이라 코드를 실행하지 않았고 claude 엔트리포인트만
  봤습니다. 런타임 계측(`_load_ledger_uncached` 호출 횟수)과 어댑터 × 엔트리포인트 매트릭스로
  교체하고, `adapters/` 전수를 열거해 미등록 어댑터·미등록 엔트리포인트가 있으면 실패하는 메타
  테스트를 캐시·신원승격 양쪽에 추가했습니다. 새 어댑터는 등록 없이 통과할 수 없습니다.

### Documentation

- **v2.7.0의 "19종 차단" 기록에 범위를 명시합니다.** 당시 나열한
  `[IO.File]::WriteAllText`·`Invoke-Expression`·`Add-Content`·`Clear-Content`·`Copy-Item`·
  `Move-Item`·`New-Item -Force` 7종은 **`powershell -Command`로 감쌌을 때만** 차단됐고, 셸 자체가
  PowerShell이라 최상위로 호출되는 형태는 통과했습니다(`Out-File`만 양쪽 차단). 원문이
  "all passed through `powershell -Command`"로 범위를 밝히긴 했으나 이어지는
  "0 false negatives"가 일반적 안전 주장으로 읽혔습니다. 이번 릴리스가 bare 형태를 덮습니다.
- **Known Limitation을 명시합니다**(모두 현행 ALLOW로 실측 확인): `>` 리다이렉션의 신규 파일
  오차단(TOCTOU 방어 불가, 기존 확정) · 귀속 없는 일반 copy overwrite · `| iex`/`| python`/
  `| node` 인터프리터 파이프 수신(`fnm env | iex` 같은 정당 관용구와 정적 구분 불가) ·
  `python -c` 임의 코드(기존 비목표) · `robocopy /MIR` · `rsync --delete` · `ln -sf` ·
  `tar -xf` · `Add-Content`(append 정책 정합 필요).
- **대상 경로의 따옴표·이스케이프 분할은 아직 정규화하지 않습니다**(백로그 승격): `rm pe""er.py`·
  `rm "pee"r.py`처럼 셸이 `peer.py`로 합치는 표기를 파서가 그대로 두어 귀속 조회가 빗나갑니다.
  이번 수리 이전부터 있던 파서 일반 결함으로 `rm` 경로 전체에 영향이 있으며, 대상 경로 정규화가
  필요한 별도 건입니다.
- 설계 근거와 실측 기록: `docs/specs/2026-08-05-hardreview-fix.md`.

## [2.7.1] - 2026-08-04 — Adapter Cache Coverage

v2.7.0의 ledger read cache가 `codex_cli` 어댑터에만 적용돼 있었습니다. 감독
대상 프로젝트 대부분은 Claude Code 플러그인, 즉 `claude_code` 어댑터를 타므로
**지연 개선이 실제 사용처에 닿지 않고 있었습니다.** 이 패치가 나머지 세 어댑터를
같은 캐시 범위로 옮깁니다.

### Performance

- **Claude Code · Antigravity · Qwen Code 훅도 ledger 읽기를 캐시합니다.** 대표
  PreToolUse 1회의 uncached ledger load가 Claude Code 24 → 1, Antigravity
  23 → 1, Qwen Code 24 → 1로 줄었습니다(v2.7.0에서 수리한 codex 어댑터의 11회보다
  많았습니다). 동일 8 MB 픽스처(status-less invocation 209건) 기준 p50: Claude
  Code 674.7 → 307.6 ms (54.4%), Antigravity 730.2 → 351.8 ms (51.8%), Qwen
  Code 607.2 → 370.4 ms (39.0%). 세션 등록 후 실제 어댑터 subprocess 재현에서는
  1242~2327 ms → 351~568 ms였습니다.
- Claude Code의 `session_end`는 실제 ledger load가 0회라 캐시 비대상으로 두고,
  누락으로 오인되지 않도록 **0회를 테스트로 고정**했습니다.
- v2.7.0의 정합성 계약(락 내부 fingerprint 재확인, 쓰기 전 무효화)과 비활성
  프로젝트의 shared-core 무import·quiet 반환 계약은 변경하지 않았습니다.

## [2.7.0] - 2026-08-04 — Supervision Friction & Opaque-Wrapper Safety

Closes the supervision friction an operator reported across 19 supervised
projects, and shuts an opaque-wrapper fail-open gap found while investigating
it. Every change shipped regression-first with three independent reviews
(orchestrator gate + adversarial review + independent re-measurement) and a
green five-leg CI.

### Added

- **`smtw maintenance plan|apply`**: explicit lifecycle maintenance for stale
  turns and invocations. `plan` reports safe-auto and manual-only candidates
  with source digest, identity, reason, and estimated bytes; `apply`
  re-verifies the digest (CAS), writes a backup, and excludes the current
  identity, leased open invocations, unsettled attribution, and pending
  transactions. Timestamp-less legacy turns are manual-only and are never
  collected automatically. `doctor` stays diagnosis-only — repair lives in
  `maintenance`.
- **`smtw migrate --invocation-statuses [--check]`**: explicit backfill for
  legacy invocations missing `status`. `--check` is write-free; `apply` refuses
  (exit 1, original and archive untouched) when any row lacks lease evidence.
- **Gate preflight preview**: the first block of a turn now lists every
  remaining requirement (goals → contract → verification → Stop) in one
  message. The preview records no counter and no recover transition. Measured
  on a high-risk mutation turn: consecutive blocks 3 → 1.

### Changed

- **`doctor` ledger health is now honest**: `unreadable` (IO/JSON) and
  `invalid` (schema violation) are separate, with the offending field path, a
  `needs_migration` flag, and the next safe command in both text and JSON. The
  exit code stays 1.
- **Proven-read-only shells skip the goals gate**: R2 always runs first; only a
  fully proven read-only command skips the goals block, and the goals counter
  is untouched. `is_proven_read_only_command` is now the single public SSOT.
- **Manifest journal is compacted**: minimum-field projection plus count (256)
  and byte (1 MB) caps. Full events remain in the agent log, so recovery and
  audit evidence are preserved. Measured on an 8 MB production-shaped ledger:
  journal 3,337,090 → 33,505 bytes.

### Fixed

- **R2 opaque wrappers no longer fail open** (security): a wrapper whose
  payload the shared parser cannot fully prove read-only is blocked as
  `parse_unable_wrapped` instead of passing when no destructive token was
  recognized. Empirically, `[IO.File]::WriteAllText`, `Invoke-Expression`,
  `Add-Content`, `Clear-Content`, `Copy-Item`, `Move-Item`, `New-Item -Force`,
  and `Out-File` all passed through `powershell -Command` before this change;
  all 19 measured cases are blocked now. 30 adversarial injections across 10
  attack families through the real adapter: 30 blocked, 0 false negatives. The
  coordination label `command_parse_unavailable` is unchanged.
- **Time-dependent scorecard fixtures**: two coordination-bounds tests pinned
  2026-07-20 events and fell outside the seven-day window, turning `main` red
  on any later date. Fixtures are now relative to `now(UTC)`.
- **`fable_lite` shim inventory** includes the new `maintenance` module.

### Performance

- **Hook invocations no longer reload the ledger eleven times.** Profiling the
  real adapter entry point (`adapters/codex_cli/pre_tool_use.py`, which does
  not go through `smtw.cli`) showed a single PreToolUse loading the same 8 MB
  ledger up to eleven times, with `backfill_invocation_statuses` deep-copying
  the whole document on each load because 209 invocations lacked `status` —
  2,588,168 `deepcopy` calls, 3.0 s cumulative. `core/ledger.py` now offers an
  opt-in process-scoped read cache (`ledger_read_cache()`) that re-checks an
  `(mtime_ns, size)` fingerprint on every load, re-validates after reading, and
  is invalidated before any write; the backfill returns the original object
  when no row needs a status and otherwise copies only the changed branches.
  Measured on the real adapter path with a production-shaped ledger (8 MB, 209
  status-less invocations): cold p50 1898 → 428 ms (77%), warm 2244 → 356 ms
  (84%), four-way concurrent p50 4009 → 500 ms (88%). Combined with the
  proven-read-only goals exception above, an end-to-end shell hook measured
  1591–2115 ms before this release and 97–111 ms after. `smtw.cli` subcommand
  imports were also made lazy; that is an independent improvement and is **not**
  the cause of the hook speedup.

### Known Limitations

- A harmless redirect that creates a new file (`cmd /c "echo x > newfile"`) is
  still blocked. A pre-execution existence check cannot atomically defend
  against TOCTOU or symlink/junction replacement between the gate and the
  shell, so no exception was added. Use the editor write path instead.
- **Journal compaction is hygiene, not a latency fix.** An 8 MB ledger
  contributes only 30–100 ms to a hook; the latency win came from the read
  cache, not from a smaller file.
- **Half of the remaining hook cost is data, not code.** A ledger carrying
  status-less invocations costs roughly twice one that does not (measured
  1861–4340 ms vs 856–1814 ms before the cache landed). Operators must run
  `smtw maintenance` explicitly to realize that half; this release ships the
  tool, not the cleanup.
- `test_concurrent_same_session_latch_stays_write_once_with_env_roots` failed
  once in a full-suite run and could not be reproduced afterwards (two
  full-suite reruns, five isolated runs, and 25 runs per branch under CPU load
  all passed). Neither a regression nor a pre-existing defect could be
  established; it is tracked rather than dismissed.

## [2.6.2] - 2026-07-25 — Real-use Reliability Hotfix

No new gate semantics. Closes the v2.6.2 real-use reliability handoff: R2
heredoc/opaque-execution safety, goals identity and authoring correctness,
operator health truthfulness, canonical config completion, symlink
attribution, onboarding docs, quarantine/migration durability, and
compatibility/build reproducibility — plus a new qwen-code supervision
adapter. Each PR shipped regression-first with three independent reviews
(orchestrator + adversarial agy + implementer) and a green five-leg CI.

### Added

- **qwen-code supervision adapter** (`adapters/qwen_code/`): a single-dispatcher
  hook adapter that brings show-me-the-work supervision to qwen-code (0.20.1).
  It reuses the Claude Code-compatible payload extraction, maps qwen tool names
  (`run_shell_command`/`edit`/`write_file`/`notebook_edit`) to core canonical
  names, and empirically pins the hook `timeout` to 30000 ms because qwen's
  timeout unit is milliseconds despite documenting "seconds" — a value that
  would otherwise silently kill the hook. Block is exit 2 + stderr; install is
  user-global (`~/.qwen/settings.json`) or workspace with trust registration.

### Fixed

- **R2-07A/B/C shell command-position hardening**: R2 now inspects command
  substitutions in unquoted heredocs while leaving quoted and literal heredoc
  data alone, treats option-bearing shell heredoc stdin as executable script
  input, and follows `builtin command` / `builtin exec` prefixes. Malformed
  executable substitutions remain fail-closed; this is targeted destructive
  command supervision, not an arbitrary-code sandbox.
- **STATE-03 contract authoring migration barrier**: direct contract Edit/Write
  now defers under a held migration lock or active staging, recomputes the
  authority inside the lock, and blocks writes to a path outside the current
  state authority, so a successful write always lands in the final authority
  (INV-03). R2 fail-closed behavior is unchanged.
- **GOALS-03A peer receipt identity inference removed**: goals identity resolves
  via explicit `--identity`, a complete host/session/agent triple, a sole exact
  active identity, or a unique environment match — never a peer's open receipt,
  closing shared-worktree checkpoint overwrites (INV-04).
- **GOALS-03B command-position authoring**: the goals authoring exception now
  reuses the R2 command-position parser and only accepts real executable
  invocations, rejecting `echo`/`printf`/`python -c`/comment/argument
  look-alikes (INV-05).
- **DOCTOR-03A shared ledger schema gate**: `doctor` shares the runtime loader's
  supported-schema set, so an unsupported ledger schema reports `error` instead
  of a healthy exit 0 (INV-06).
- **DOCTOR-03B enum provenance health**: `doctor` derives unsafe provenance
  status from the `ProvenanceStatus` enum rather than hardcoded strings,
  matching runtime Stop safety on the same ledger.
- **CONFIG-03 table-relative dotted keys**: the parse-error-tolerant config
  scanner tracks the current TOML table, recognizing `[tool]` + `smtw.supervision`
  as `tool.smtw.supervision` and staying fail-closed (`DECLARED_INVALID`, no
  legacy fallback) on a malformed canonical config (INV-07).
- **ATTR-03 symlink write-through attribution**: PostTool candidate matching now
  uses both logical and invocation-time resolved keys, so a symlink
  write-through is attributed to its editor instead of external.
- **QUAR-03A/B quarantine durability**: a failed post-write verification cleans
  up (or flags corrupt/unverified) its destination instead of appearing
  complete, and GC orders by creation time / mtime rather than UUID
  lexicographic order.
- **MIGRATION-03 retry-safe archive eviction**: archive eviction removes the
  index entry only after a successful unlink, retains failed victims for retry,
  reports retention outcomes, and preserves the legacy fixed-name archive.
- **HINT-03 `env -S` friction**: shell hints re-tokenize `env -S/--split-string`
  payloads so documented wrappers are not missed (advisory friction only; the
  R2 gate is unchanged).
- **COMPAT-03 interpreter-prefix `-m` detection**: the `fable_lite` shim scans
  only the interpreter option region of `orig_argv`, preserving import identity
  when an application argument mentions `-m fable_lite.cli`.

### Changed

- **DOC-03/04 onboarding and CI matrix docs**: README/README.ko document the
  canonical `pip install .` path (plugin registration alone does not install the
  `smtw` console script) with a checkout-launcher fallback, and the actual
  Ubuntu 3.12/3.13/3.14 + Windows 3.12/3.14 clean-wheel matrix.
- **BUILD-03 setuptools backend pin**: `pyproject.toml` pins
  `setuptools>=69,<84` for reproducible isolated builds, with a documented
  security-update procedure in `CONTRIBUTING.md`.

## [2.6.1] - 2026-07-24 — Safety Boundary & Documentation Stabilization

No new gate semantics. Closes the v2.6.1 stabilization handoff
(`docs/reviews/2026-07-23-sol-v261-stabilization-handoff.md`): the remaining
P0/P1 safety-boundary defects, operator CLI onboarding, bilingual README
truthfulness, CI matrix coverage, and release hygiene. Each PR shipped
regression-first with two independent reviews (orchestrator + adversarial agy)
and a green two-OS (now five-leg) CI.

### Added

- **CI-02 interpreter matrix and tool pins**: CI now runs the full gate on
  Ubuntu Python 3.12/3.13/3.14 and Windows Python 3.12/3.14, and installs
  pytest/ruff/build from pinned `ci/constraints.txt` so runs stay
  reproducible instead of tracking whatever pip resolves that day.
- **CLI-02 operator onboarding**: added secret-safe human/JSON `smtw doctor`
  and concise `smtw status`, explicit non-overwriting `smtw init`, and a
  write-free `smtw migrate --check` with human migration summaries and stable
  healthy/unsafe/action-required exit codes.
- **GOALS-02 identity-aware goals UX**: added canonical `smtw goals plan`, `verify`, and `status` commands with automatic selection of a sole exact active identity, unique host/session environment matching, explicit `--identity` or matching `--host`/`--session-id`/`--agent` selection, and explicit errors for ambiguity, wrong identities, and synthetic-only turns.

### Changed

- **DOC-02 bilingual README realignment**: rebuilt both READMEs around the
  current canonical config and six-state authority model, explicit migration,
  truthful gate failure policies, identity-aware multi-agent operation,
  legacy module execution, performance/privacy limits, and the actual
  cross-platform CI gates. Removed stale single-legacy-state, fixed-contract,
  design-stage alias, and blanket fail-open claims.

### Fixed

- **State layout lock-wait budget (CI flake root cause)**: the ledger
  transaction throttled its outer `.smtw-migration.lock` to a one-second cap
  while the inner ledger lock honored the caller's full budget, so unfair-lock
  contention on loaded runners starved concurrent writers and intermittently
  timed out `main` CI on Windows. The layout barrier now honors the caller's
  full wait budget and the bare default rose from 1s to 5s; latency-sensitive
  R2 deny/quarantine paths keep their explicit zero-wait behavior.
- **COMPAT-01/02 source and legacy execution semantics**: source checkouts now
  prefer their adjacent repository version over stale installed metadata,
  `doctor` exposes module/distribution mismatches, and physical legacy thin
  shims support `python -m fable_lite.<public-submodule>` without loader alias
  failures while ordinary legacy imports retain object identity.
- **N2 recovery guidance**: checkpoint denials now include a complete copyable identity-specific plan command. Following that command writes the checkpoint N2 actually checks, immediately clears the gate for that identity, and cannot borrow a foreign identity's checkpoint; legacy single-agent fallback remains available.
- **Quarantine durability (QUAR-01)**: blocked-command records now reserve
  UUID-suffixed destinations with an exclusive create instead of overwriting a
  name selected by an `exists()` check. UUID collisions retry, POSIX files
  request mode `0600`, and a success is returned only after post-GC
  existence, size, and SHA-256 verification. Backup and verification failures
  remain side effects of the unchanged fail-closed R2 block decision.
- **Truthful truncation records (QUAR-02)**: quarantine headers now record
  original/stored byte counts and SHA-256 digests, UTF-8 encoding, truncation
  state, and complete/incomplete status. The 1 MiB limit applies to stored
  command bytes, UTF-8 code points are never split, `quarantine show` warns
  before partial content, and block messages distinguish complete, partial,
  and failed preservation.
- **CONFIG-02 fail-closed config precedence**: malformed `pyproject.toml` files now recognize equivalent quoted and dotted `tool.smtw` declarations without treating comments or basic/literal string contents as config. A possible canonical declaration is `DECLARED_INVALID` and cannot silently fall back to enabled legacy config.
- **MIGRATION-02 rollback archive rotation**: invocation-status backfills now preserve each distinct source in a SHA-256-named immutable archive with a validated schema-v1 index, reuse identical sources, retain the current rollback source, and bound older archives by count and total bytes. A legacy fixed-name archive no longer prevents a later mixed-version backfill.
- **ATTR-02**: invocation candidates now persist separate project-relative lexical keys for PostTool attribution and filesystem-resolved keys for R2 peer matching. Symlink replacement therefore remains attributed to the declared link while destructive access to its current in-project target still sees the open peer window; out-of-root targets retain the existing R2 policy.
- **HINT-02**: inline-Python state-file hints reuse R2's command-position normalization through `env`, environment assignments, and `command`, and track `pathlib.Path` imports including `Path as P` and `pathlib as pl`.

### Compatibility

- The invocation ledger keeps the resolved `candidate_paths` compatibility projection. New readers prefer `candidate_logical_paths` / `candidate_resolved_paths`, while legacy live entries are migrated on read with the previous physical canonicalization rule.

### Known Limitations

- Inline-Python hints are advisory friction, not an authorization boundary. Static AST inspection intentionally does not decode dynamic `exec`/`eval` payloads such as Base64-wrapped source; R2's independent fail-closed command gate remains authoritative and is unchanged.

## [2.6.0] - 2026-07-22 — canonical Python package (`fable_lite` → `smtw`)

### Changed

- Renamed the canonical Python package from `fable_lite` to `smtw`. Both console scripts (`smtw` and `fable-lite`) now enter `smtw.cli`, and user-facing module commands use `python -m smtw`.

### Compatibility

- `fable_lite` remains as a deprecation shim through v3.x. The package and all public submodules are aliases of the corresponding `smtw` module objects, so legacy imports, `python -m fable_lite`, reload, Windows spawn, and old pickle module paths remain supported.
- The shim emits `DeprecationWarning` once per process with the message `fable_lite is deprecated; import smtw instead`. Default warning handling is supported; explicitly promoting the warning to an error with `-W error::DeprecationWarning` or `PYTHONWARNINGS=error` is intentional failure behavior.
- Running this source checkout while an older global `fable-lite` distribution remains installed is unsupported because distribution metadata can resolve to the older version. Use a clean virtual environment.

## [2.5.0] - 2026-07-22 — internal unification (`.fable-lite` → `.smtw`)

Internal identifier unification kept deferred at the v2.0 rebrand (design track "v3": `docs/design/smtw-unification-v3.md`, 3-AI). P4 summary: `docs/reviews/2026-07-22-smtw-unification-v3-p4.md`. Released as a **minor** version because it is fully backward compatible: existing (unmigrated) projects keep running unchanged on the legacy tree, this release only adds an explicit `smtw migrate` path and reads both generations, and nothing auto-migrates. The breaking removal of legacy read paths is reserved for a future major (v3/v4).

### Added

- **State layout & explicit migration (Q1)**: `core/state_layout.py` gives a side-effect-free layout verdict (`EMPTY`/`LEGACY`/`NATIVE`/`MIGRATED`/`MIGRATING`/`CONFLICT`); `core/state_migration.py` + `smtw migrate` perform an **explicit** copy→verify→publish migration (isolated staging copy, pre/post source manifest comparison, atomic marker, `rename`-into-absent-target publish). Invariants: exactly one authoritative tree per project, `state_dir()` never writes, no per-file legacy fallback, no silent fallback after publish. Migration **defers** when the legacy tree has an active turn or open invocation (requires session restart); `.fable-lite/config.json` intentionally stays in the legacy location for config-fallback lifetime.
- **Runtime env unification (Q3)**: `core/runtime_env.py` resolves eight semantic keys with `SMTW_` canonical and `FABLE_LITE_` as a read alias by **presence, not truthiness**. Both generations present with differing values is a **fail-closed** `SmtwEnvConflictError` (blocks silent gate-weakening via the legacy name); adapters convert it to `permissionDecision: deny` (PreToolUse) / `decision: block` (other events) rather than absorbing it into health fail-open.
- **Project config unification (Q4)**: precedence `.smtw.toml` > `pyproject.toml` `[tool.smtw]` > `.fable-lite/config.json`, each source resolving to `ABSENT` / `VALID` / `DECLARED_INVALID`. Only an unparseable TOML is corrupt; a missing `[tool.smtw]` section is `ABSENT` and falls through (a removed section cannot DoS supervision into fail-closed). A malformed-pyproject header probe skips comments/strings/`[tool.smtw.fake]`.
- **Blocked-artifact quarantine (B1)**: when R2 blocks a destructive command, the command is backed up to `.fable-lite/quarantine/` as a best-effort side effect so a worker's shell-written artifact is not lost — R2's block decision and reason code are unchanged (a backup failure never flips block to allow). `smtw quarantine list/show/clear` lets the orchestrator review and recover; there is no `apply` (auto-apply is out of scope). Backups are traversal-safe (sanitized agent key, three CLI path guards), live inside the R2-protected state dir, and are bounded by count/size/age GC.

### Changed

- **Consumer SSOT**: every state consumer (ledger, attribution/snapshot, contract/goals/intent, scorecard/coordination/audit, adapter runtime log) now takes the single tree from `state_dir()`; a regression test forbids direct `.fable-lite` literals outside `core/state_layout.py`.
- **R2 / provenance dual protection**: destructive-guard and provenance-exclude protect `.smtw`, the preserved legacy `.fable-lite`, and migration staging/lock by both lexical and resolved path, so a leftover legacy tree is never left unguarded.

### Known Limitations

- **Content-matched marker forgery**: an attacker who already bypasses R2 (arbitrary code execution) can plant a `.smtw` whose marker digest/count/bytes exactly match the legacy tree and have it accepted as authoritative. This causes no data loss (content is identical to legacy) and cannot launder history (a mismatched digest is rejected as `CONFLICT`); a local marker signature adds no trust because the same ACE could rewrite the live target directly, and forcing legacy presence would violate the no-fallback-after-publish invariant. Runtime full-tree rehashing was rejected as prohibitively expensive. Verdict Low / no fix (codex·agy·orchestrator concur).

### Compatibility / migration

- Legacy read paths (`.fable-lite/` whole-tree mode, `FABLE_LITE_*` env aliases, `.fable-lite/config.json` lowest-precedence fallback) remain through v3.x and are removed no earlier than v4, with `smtw doctor` surfacing the active layout/config/env source. **Python module rename `fable_lite` → `smtw` (Q2) is deliberately deferred** to a later minor with a compatibility shim.

## [2.4.1] - 2026-07-21

### Fixed

- **Invocation status hardening (B5)**: `invocations.status` is required again at the v2 schema boundary. Explicitly opted-in status backfill (`FABLE_LITE_AUTO_MIGRATION=1`) closes only omissions proven older than the invocation lease and preserves recent, fully evidenced peer windows as `open`; unclassifiable rows remain unchanged/degraded. Immutable archive and atomic validation remain intact, and ON-but-failing migration emits stage/detail diagnostics instead of looking identical to OFF mode.
- **Release-gate separation**: `auto_migration_enabled()` (ledger v1→v2 schema migration, unlocked in v2.0.0) is receipts-gated and env-independent again, so a clean wheel keeps schema migration on (fixes a wheel-smoke `assert auto_migration_enabled()` regression). The opt-in invocation-status backfill moved to a distinct `status_backfill_enabled()` gate.

## [2.4.0] - 2026-07-21 — stabilization (no new gate semantics)

Stabilization sweep closing the external audit (`docs/reviews/2026-07-20-sol-stabilization-handoff.md`, P0/P1/P2) plus real-world multi-agent friction. Each item shipped regression-first with adversarial (agy) P4 review; full review-debt closure in `docs/reviews/2026-07-21-v24-review-debt-closure.md` (13/13 FIXED or ALREADY_FIXED).

### Fixed

- **R2-01**: the destructive-command parser inspects every chained shell segment (`&&`, `||`, `;`, `|`), not just the head; a benign leading segment no longer masks a destructive tail, operators inside quotes are never split, and any unresolved destructive segment fails closed.
- **R2-02**: `git checkout -f/--force` is blocked as implicit-scope destruction (discards local work) regardless of target attribution; `-b/-B` and non-forced switches stay allowed.
- **R2-03**: peer open-invocation candidate paths are canonicalized to project-relative keys on both write and read, closing a pre-attribution window where an absolute peer candidate diverged from a relative R2 target (backward-compat migration for live ledgers included).
- **R2-04**: state-dir (`.fable-lite`) protection applies to the lexical project-relative first component, so a symlinked state dir cannot route deletes around protection via out-of-root resolution.
- **ACT-01**: on session-root mismatch, the Claude adapter re-checks the mismatched project's opt-in before using it as the active root; unconfigured projects stay stateless (quiet-opt-in contract preserved).
- **CODEX-01 / CODEX-02**: recovered Codex identities are promoted `legacy_default` → `exact`, and contract authorship is recorded after identity resolution, so namespaced-contract authoring is honored on later high-risk edits.
- **SCORE-01**: coordination view first/last times use `occurred_at` min/max instead of journal append order, fixing time inversion under concurrency.
- **HINT-01**: inline Python write-friction detects `Path.rename/replace` destinations and `open` update/binary write modes while leaving read forms unflagged.
- **NEW-02**: `parse_unable_*` fail-closed reasons are mapped in `R2_COORDINATION_REASON_MAP`, so dynamic-command denials (e.g. PowerShell `$var` idioms) are labeled accurately instead of `unresolvable_target` — diagnostic only, gate decision unchanged.
- **Multi-agent goals (B4)**: `goals.py` checkpoints are namespaced per session identity (`goals/<identity>.json`), preventing worker overwrite of the orchestrator's goals; N2 gate checks the active identity; single-identity sessions stay compatible via legacy fallback + on-write migration.

### Changed

- **REL-01**: removed the unused/stale `uv.lock`; `sync_version.py` fails CI if it returns or any release-version surface drifts.
- **DOC-01**: README/README.ko realigned to actual behavior (plugin install + per-project opt-in; blocks up to twice then fail-opens); stale/overclaimed phrasing removed.
- **PKG-01**: added `smtw` / `fable-lite` console scripts and a `version` subcommand.

## [2.3.1] - 2026-07-21

### Fixed

- Made R2 deny auditing fail fast when the ledger lock is busy, preserving the blocking response's one-second budget while leaving deterministic events retryable through the durable outbox. Malformed outbox entries are now rejected before mutation and persist `coordination_degraded` instead of escaping from ledger serialization.
- Made turn bootstrap recovery atomic under the root ledger lock with first-valid-write-wins baseline creation, CAS baseline advancement, explicit missing/ready/degraded states, crash-residue adoption, and live-owner-safe stale-lock recovery. A READY turn is accepted only when its physical baseline exists and its snapshot ID matches the ledger.
- Made coordination projection durable without changing gate decisions: a bounded 256-entry ledger outbox commits with the authoritative transition, drains after unlock through the strict exact-content writer, acknowledges by CAS, retries crash cuts, and reports overflow/schema/I/O damage through `coordination_degraded`.
- Reconciled `COMPLETE_WITH_EXCLUSIONS` PostTool observations by replaying trustworthy turn deltas in memory while filtering every excluded path under the result snapshot's canonical case policy. Excluded peer changes are never attributed to the caller; non-excluded observer contention remains visible.
- Stopped deterministic probe runs from dirtying tracked files. `eval/results/` is ignored and CI writes probe receipts to runner temporary storage.
- Made benchmark RSS accounting phase-local. Timing samples no longer reuse the process-lifetime high-water mark, and the dedicated memory probe polls current RSS during each action so earlier benchmark phases cannot create false SLO failures.

### Changed

- Moved the two reviewed green migration-gate receipts from mutable `eval/results/` output into package data at `core/release_receipts/`. This also fixes clean wheels silently keeping v1 dual-read because `eval/` was never packaged. Source checkouts and wheel installs now evaluate the same W9/W10 evidence; fresh measurements do not self-promote into release approval.

### Known Limitations

- Atomic replace does not include directory fsync durability across sudden power loss. Stale-lock recovery does not yet compare process start time, so PID reuse remains a hardening backlog.
- A tombstone-free stale turn written by a pre-upgrade version cannot always be distinguished from a live child turn.
- Coordination payloads are count-bounded but have no per-entry byte ceiling; bootstrap timestamp validation is conservative at delivery time, and the journal/parser and ledger validators duplicate part of the schema contract.
- `PEER_EXCLUSION` coordination audit, stronger peer-exclusion lease policy, and durable ledger events for cumulative replay remain follow-up work. The W3 replay fix intentionally changes only in-memory attribution; immediate PostTool deltas remain durably recorded as before.

## [2.3.0] - 2026-07-19

### Fixed

- `resume_turn` bootstrap retry treated `COMPLETE_WITH_EXCLUSIONS` as failure (strict `is COMPLETE` comparison), defeating the F3 peer-activity rescue exactly in its target scenario — concurrent multi-agent boot on a fresh store. Bootstrap success now uses the shared completion helper, and `begin_invocation` no longer drops `candidate_paths` registration on the recovery path. Reproduced 100% by two independent probes before the fix.
- `turn_not_started` recovery is now explicit: an invocation that successfully full-bootstraps a missing baseline records `baseline_status=ready`, `provenance_incomplete=false`, `provenance_status=complete`, and an empty status reason in one ledger write, instead of depending on a later PostToolUse to accidentally overwrite the stale state (which a PostToolUse fail-open could leave behind forever). Failed bootstraps keep the conservative `turn_not_started` state unchanged.
- Windows long-path atomic writes: on hosts with `LongPathsEnabled=0` (the Windows default), session-registry warning paths past the 260-character boundary made `os.replace` fail and silently degraded PostToolUse into a health fail-open, losing scope warnings. `atomic_write` now normalizes to absolute paths and applies the `\\?\` extended-path prefix when either side crosses the boundary, preserving the open→flush→fsync→replace contract. Verified on the reproducing host with default temp paths (704 tests green without workarounds); 259/260/262-boundary and deep-warning regressions added.
- Probe runner now forces UTF-8 on the child pytest process so Korean failure tails are no longer mojibake; PASS/FAIL stays returncode-based.

### Added

- Scorecard coordination journal: a separate append-only `.fable-lite/scorecard/coordination.jsonl` (existing `gates.jsonl`, its closed `ReasonCode` enum, and default CLI output are unchanged). This wave records two categories — `r2_deny` (all eight destructive-guard block sites mapped onto four closed reason codes, plus the pre-resolution parse branch) and `turn_bootstrap` (`entered`/`recovered` pairs closed per actor and turn). Recording is laundering-resistant by construction: the R2 verdict itself stays fail-closed on the raw identity, while the journal entry is written only after `resolve_active_invocation()` settles the real identity, as fire-and-forget I/O that can never affect the gate decision (a read-only journal directory does not bypass R2). Remaining categories are reserved in the enum but not yet recorded.
- Scorecard CLI views: `python -m fable_lite scorecard --view sessions|agents|coordination` — `sessions` is the unchanged default; `agents` compares actors side by side; `coordination` renders the new journal. Root-level cross views stay a CLI read-time join; the core `SessionIdentity` model is unchanged and nothing new is auto-pushed into Stop output (quiet policy preserved).
- READMEs: project-scope plugin install path for true zero cost outside supervised projects (`claude plugin install ... --scope project`, per-project disable via `.claude/settings.local.json`), and the Antigravity host-status wording updated to the current facts (official-schema hooks.json parses and loads on host 1.1.2+; host execution of config-path hooks still unconfirmed).

### Known Limitations

- Turn bootstrap baseline persistence, ledger transition, and coordination observation use separate transactions to avoid lock re-entry; coordination IDs enforce one recovered observation per actor and turn, but the three operations are not a single atomic transaction.
- Coordination `--days`/`--session` filters apply to `entered` and `recovered` events independently; the CLI prints a hint when a counterpart may fall outside the selected window.

## [2.2.0] - 2026-07-17

### Changed

- **Claude Code adapter is now opt-in per project** ("Quiet Opt-in", spec: `docs/specs/v2.2-quiet-optin.md`). Hooks engage only when `<root>/.fable-lite/config.json` declares `{"schema_version": 1, "supervision": true}` (strict boolean). Inactive projects take a stdlib-only fast path: zero core imports, zero file writes, output exactly `{}`. Exact-home sessions are hard-off even with a config; projects under the home directory keep full supervision.
- Project root resolution no longer trusts the drifting tool `cwd`: `CLAUDE_PROJECT_DIR` → write-once session registry latch (atomic, TTL-GC'd, cleaned by SessionEnd) → upward config search fallback. This eliminates the cd-drift false blocks (R1/Stop) observed in live sessions.
- Message diet: informational systemMessages (`observed N change(s)`, `provenance incomplete`, `recorded verification`, `Stop gate allow`, home advisory) are silenced while ledger recording is preserved. Block reasons stay visible; scope warnings dedupe to once per turn; health warnings (fail-open, corrupt registry/config, root mismatch) dedupe to once per session and are never silenced. Scorecard line is display-opt-in.
- Quick mode keeps full PostToolUse/PostToolUseFailure observation and atomically promotes the turn to normal before the first mutation-capable tool runs (promotion is exception-safe; if it cannot be persisted the tool is denied rather than fail-opened). Only provably read-only turns skip the heavy Stop reconcile, and they never claim "clean verified".
- PreToolUse denials emit `hookSpecificOutput.permissionDecision: "deny"` instead of the deprecated top-level `decision: "block"`.
- Test harness: suite is hermetic against shared plugin-data state (`SMTW_TEST_FORCE_ENABLE=1` isolates per test); verified green on two heterogeneous runner environments plus a hostile shared-state environment.

## [2.1.1] - 2026-07-16

### Fixed

- Home-directory sessions (project root exactly equal to `$HOME`/`%USERPROFILE%`) now explicitly skip provenance instead of looping on `scope_too_large`. A home directory's scan volume structurally exceeds any budget (observed 11x: `.claude` alone is 2.8 GB, 61% of it the agent's own session logs), so provenance was blocking every turn and passing only via the fail-open cap without ever verifying anything (observed 9 cap-allows, 0 resolutions). Home root is now reported as `unsupported`/`home_root` with an actionable message (open the session from the project folder), snapshot scans are skipped entirely, and only the `scope_too_large`/`incomplete` provenance blocks are bypassed — investigation, design, and verification gates stay active. Exact home match only (case-folded, path-normalized); projects under the home directory keep full supervision.

## [2.1.0] - 2026-07-15

### Added

- Added an opt-in design gate (default OFF). Enable it with `FABLE_LITE_DESIGN_GATE=1` or a project `design/gate.config` `{"enabled": true}` (project config wins over the environment variable). When enabled, UI-domain or UI-file-creation turns run `design_lint` on changed lines — raw hex/rgb/hsl colors, raw px spacing, and Tailwind arbitrary design literals — with DESIGN-OPS exception boundaries (token source files, `0`/`1px` hairlines, percentages, `currentColor`, SVG internals, and a path+rule+reason+expiry allowlist). Stop blocks a UI-touching turn until the lint passes and a render-verification tool call was observed, with an independent two-block fail-open counter. `fable_lite check --design` runs the lint on demand regardless of the toggle. Editing `design/gate.config` invalidates a prior pass, so an agent cannot launder an allowlist entry to bypass the gate. Adds the `design-review` pack and extends `verification-grounding` with design observation checks.

## [2.0.2] - 2026-07-15

### Fixed

- Fixed provenance false positives that structurally blocked Stop on home-directory, large-repo, and remote-mutation sessions. Shell effects are now classified as proven-read-only, proven-remote-only, or local-or-unknown (default), and a `git status` observation no longer marks a turn mutation-capable on its own.
- Hardened the config self-exemption path: `.fable-lite/provenance-config.json` is always tracked by the snapshot scanner, and git-tracked source paths are force-observed regardless of config `exclude`, so an agent cannot launder a backdoor by excluding a path and committing it.
- Fixed shell tokenization so a mid-word `#` no longer swallows subsequent operators; a change command after `#` is classified local-or-unknown, not read-only.
- Recognized loopback aliases (`127.1`, `0.0.0.0`, integer and short-form IPs, trailing-dot hostnames) so local ssh/scp edits are not misclassified as remote-only.
- Restored fail-closed handling when a post-mutation turn baseline is missing, preventing a concurrent observer from absorbing an unverified change.
- Attributed a deleted path to the current turn only when it existed in the turn baseline, so a pre-turn deletion no longer over-blocks a read-only turn.
- Replaced tracked-path discovery with `git ls-tree HEAD` plus staged additions and applied Windows case-folding, removing an intent-to-add false positive and a casing-based evasion.

## [2.0.1] - 2026-07-14

### Added

- Added Session Quality Scorecard: an append-only gate journal, bounded per-session ledger cache, privacy-preserving CLI summaries, and optional Stop allow summaries for Claude Code, Codex CLI, and Antigravity payloads.
- Added bounded provenance scans for 10,000 tracked entries, 256 MiB, and cooperative full/incremental deadlines. Oversized scopes now return an explicit advisory-only `scope too large` state without committing partial snapshots.
- Added conservative remote mutation epochs for direct `ssh` and local-to-remote `scp`. A separately started successful verification, including a local-only check, must cover the remote epoch; satisfying it does not prove that remote state was observed as clean. Local redirects, pipelines, downloads, command chains, substitutions, and unsafe SSH options do not use this relaxation.
- Expanded CI with Ruff, version synchronization, fresh W9 receipts, wheel build/install smoke, and a Windows nightly/tag workflow for randomized W9, W10, the eight-process Stop race, tag/version matching, and receipt artifacts.

### Fixed

- Removed the `quick`-mode exemption for non-document changes. Fresh successful verification is now required for code and other non-document artifacts in every task mode.
- Replaced substring-only verification recognition with shell-aware tokenization so output-only commands such as `echo pytest`, `printf`, `Write-Output`, comments, and print-only inline Python cannot unlock Stop.
- Tightened high-risk R1 contracts so `evidence` is required, must be a list of non-empty strings, and cannot be replaced with missing, scalar, empty, or whitespace-only values.
- Applied soft provenance exclusions at every path depth so nested dependency and cache directories do not trigger home-root scan blowups.
- Preserved frozen verification epochs and conservative local-mutation handling in oversized scopes, including SSH options that can create local files.
- Cleared the current Ruff findings without adding runtime dependencies.

### Changed

- Updated both READMEs to describe v2 state paths, Scorecard, the current deterministic probe result, the actual host-support matrix, and the limits below.

### Known Limitations

- Stop still fail-opens after two blocks. The harness supervises normal work discipline; it is not a complete defense against a deliberately evasive model.
- Files outside the project root and database or network side effects are not directly observed. Remote epochs prove only that a later verification command ran after the remote mutation, not that remote state was independently observed as clean.
- Full reconciliation near the 10,000-entry/256 MiB envelope can take several seconds. Deadlines are cooperative and cannot preempt one blocked OS call.
- Promise-only completion (`PRB-01`) and independent per-gate toggles (`PRB-11`) remain manual/unimplemented.
- Antigravity conformance is validated by payload injection; live firing on host 1.1.1 remains unconfirmed.

## [2.0.0] - 2026-07-13

### Added - Change provenance

- Renamed the public product and plugin from `fable-lite` to `show-me-the-work` (`smtw`): the project grew from transferring Fable 5 working discipline into evidence-based AI work supervision. Internal `.fable-lite/`, `FABLE_LITE_*`, Python module, CLI launcher, and package identifiers remain compatible in v2.0.

- Added stdlib-only filesystem provenance with BLAKE2b-256 manifests, metadata fast-paths, full Stop reconciliation, Windows casefold collision handling, non-follow symlink/reparse safety, generation rebase, canonical multi-adapter replay, and per-turn verification covers.
- Added the 200-case W9 golden corpus and Git/non-git plus Claude Code/Codex/Antigravity replay receipts.
- Added the W10 1k/10k benchmark (`5` warm-ups + `30` measurements) with optional 50k/2GiB stress, percentile/read/stat/RSS metrics, atomic JSON receipt, and a two-receipt release migration guard.

### Release gate status

- Gates 1-10 and 12-13 in `docs/design/v2-provenance.md` section 17 are covered by the W1-W9 implementation and green W9 receipt: no tool-name-only events, parser-miss loss, no-op false positives, stale verification, Git/non-git drift, migration corruption, concurrency loss, adapter replay mismatch, Windows collision overwrite, or premature migration trigger; runtime remains stdlib-only.
- Gate 11 is green under the rev3 3-AI SLO reconsensus: representative 1k retains 200ms/1,000ms metadata/full p95 budgets, while extreme 10k uses measured 1,000ms/6,000ms budgets after a 1.48s isolated Windows lower-bound probe; native scanning remains rejected and the workload was not reduced.
- The final receipt records independent 1k and 10k hard gates, clean fast-path reads of 0 bytes, and aggregate green status. With the green W9 receipt, `record_event()` now permits the one-shot v1 ledger migration path.

## [1.2.0] - 2026-07-12

### Added — Evidence integrity, host conformance, and release SSOT

- P0 Evidence Integrity adds conservative Antigravity verification-result parsing, monotonic ledger sequences with verification epochs, and a self-locating Codex installer whose four hooks work from projects outside the fable-lite checkout.
- Three-host Stop conformance now replays real Claude Code Stop, Codex CLI Stop, and Antigravity AfterAgent payload shapes for the shared two-block cap, fresh-verification recovery, and N1-marker recovery. Antigravity coverage is payload injection only, not proof that its host hook engine fires.
- `.claude-plugin/plugin.json` is the version SSOT. The stdlib-only `scripts/sync_version.py` synchronizes marketplace metadata, `pyproject.toml`, and both README badges; `--check` reports drift without writing, and release hygiene compares every version surface.
- The classifier corpus distinguishes bare `next.js`/`node.js`/`vue.js` technology names from explicit file references and no longer treats `생성` alone as an observable artifact request. Real filenames and HTML/page/game/chart/SVG/UI targets retain their existing behavior.

### Fixed — P4 review hardening

- Applied nine review fixes: explicit Antigravity result signals now outrank ANSI-stripped text fallback; support checks reuse epoch-aware verification; docs changes do not advance the verification epoch; agent-log locks verify ownership; malformed tool names fall through to the next authoritative payload source; Codex installation refuses to overwrite an existing hooks file; failed Codex verification blocks Stop; fresh Antigravity verification allows Stop; and externally installed Codex hooks exercise the unverified-change block path.
- Antigravity payload handling now covers the observed `tool_response` result shape and string boolean signals while preserving conservative fallback when explicit result fields are absent.

### Known Limitations

- Antigravity 1.1.1 did not invoke the configured hook process in six live installation attempts. Its adapter remains tested through deterministic payload injection until the host exposes a working live hook engine; see `docs/reviews/p9-agy-live-hooks.md`.
- Moving the Stop block-counter update into the shared ledger transaction remains deferred to v2.0. Current per-file lock ownership is hardened, but multi-process Stop-counter serialization is outside v1.2 scope.

## [1.1.3] - 2026-07-08

### Changed — N1 markers only on turns that change files (user feedback)

- The N1 investigation-marker gate now applies only to turns that actually modified files. Answer-only turns ("why doesn't this work?") reply in plain prose — no `가설/증거/기각` block demanded. Rationale: the pack's purpose is "investigate before you modify"; forcing markers on a turn that modifies nothing is friction, not discipline.
- Ledger change/verification records are now reset per prompt (turn-scoped). Without this, one early edit kept `changed=True` for every later question turn in the session — an adversarial review (Critical-1) caught that the relaxation would have been ineffective before it shipped.
- Packs (ko/en) document the exemption, and still recommend recording markers for investigation-only turns so a later fix turn doesn't retro-invent its investigation.
- Known accepted limitation (documented, backlog): file edits made via raw shell commands are not counted as changes, so such turns bypass N1 — fable-lite's threat model is carelessness, not an adversarial model deliberately dodging its own gates.
- pytest 101 (4 new: exemption x2, per-turn reset, changed-turn still blocks), probes 15/15.

## [1.1.2] - 2026-07-08

### Changed — report style guidance (user feedback)

- Investigation packs (ko/en) gained a "Report style: body for humans, markers for the gate" section: the final report must lead with a plain-language body (no code identifiers/paths/line numbers; readers may be non-developers), with the marker block compressed to one line each at the bottom. Rationale: the N1 gate only parses marker existence, but models were letting the marker format drag the whole report into a technical document — forcing non-developer users to ask follow-up questions.
- N1 block message now carries the same hint ("markers are one compact line each at the bottom; keep the body in plain language"), so a blocked model rewrites toward a readable report instead of a more technical one.
- Gate logic unchanged: pytest 97, probes 15/15.

## [1.1.1] - 2026-07-08

### Fixed — briefing false positives (real-world defect)

- Worker boot/role-injection prompts ("[부팅] ... read MEMORY.md and X.md ... then stand by") were classified as multi-story work: the two memory file paths alone tripped `needs_goals`, so the PreToolUse gate blocked the worker's first Bash/Edit calls twice and wasted 1-2 minutes per boot (observed live on a Sonnet worker pane).
- New briefing detection: a boot marker ("[부팅]", "세션 부팅", ...) or a stand-by closing ("대기하라", "standby", ...) suppresses `needs_goals`/multi-story/artifact signals — but only when no imperative action follows. Imperative-suffix matching keeps rule phrases ("...만 수정", "sentinel 파일 생성.") from counting as actions, while "[부팅] ... auth.py 고쳐줘" still gates (adversarial-review bypass guard).
- `is_debug` and `risk_flags` are intentionally NOT suppressed inside briefings: a boot prompt carrying a dangerous command still hits the R1 contract gate.
- Path mention counting no longer counts version strings (`v1.1.0`) or bare domains (`google.com`) as file paths.
- Word-boundary matching for multi-story terms: `and` no longer matches inside `commands`/`sandbox`; `여러분`, `12개월`, `multiply` no longer false-positive.

### Evidence

- Reproduced both reported boot prompts before the fix (`needs_goals=True`, spurious verification pack), confirmed suppression after (`needs_goals=False`, no packs), and confirmed the enumeration regression probe (PRB03) still blocks real multi-story prompts.
- pytest 92 (10 new classification tests), deterministic probes 15/15, live-payload hook E2E: boot prompt → Bash allowed; multi-story prompt → Edit blocked.

## [1.1.0] - 2026-07-07

### Added — Intent Gate ("알" gate)

- Ambiguity scoring (`core/ambiguity.py`): signal-count score (0-4) with a conservative threshold of 2 and hard never-flag guards (questions, explicit paths, existing goals/intent, "그냥 해"). Scoring→threshold-gating methodology adapted from [Yeachan-Heo/gajae-code](https://github.com/Yeachan-Heo/gajae-code) (MIT).
- Intent-interview packs (`packs/intent-interview.ko.md` / `.en.md`): at most 3 questions, one at a time, multiple-choice first; explicit-assumption path (`--assumed`) when the user says "just do it".
- `fable_lite intent set/show/clear` CLI writing `.fable-lite/intent.json`; a new prompt auto-clears the previous intent.
- PreToolUse edit-lock: when a prompt is flagged ambiguous, Edit/Write/patch tools are blocked until `intent.json` exists (2-block cap, fail-open; Bash/read tools stay allowed so investigation can proceed).
- Self-locating launcher `fable-lite-cli.py` + absolute-path command injection: hooks compute their own install location and embed a copy-paste-runnable command into block reasons and injected context, so recovery works in plugin installs without `pip` and without relying on model path-hunting.

### Evidence

- Ambiguity corpus (41 real-style Korean prompts, adversarially authored by a different model): accuracy 100% — 0 false positives, 0 false negatives after a targeted fix round (initial run: FP 0, FN 7).
- Live nested-session E2E: ambiguous prompt → questions first and Edit genuinely blocked; clear prompt → zero added friction; "그냥 알아서" → assumption declared and recorded with `--assumed`; after the launcher fix, the model copy-executed the injected command verbatim and produced a standard-schema `intent.json` with no workarounds.

### Fixed

- BLOCKER (found in live E2E): `python -m fable_lite` failed with `ModuleNotFoundError` from arbitrary project directories under plugin installs — resolved via the self-locating launcher plus injected absolute-path commands.
- Verification OK-signal matching now uses word-boundary regex (`\bok\b`, case-insensitive): recognizes leading `ok ...` and `OK: ...`, with a regression test proving `broken` does not false-positive.

### Known Limitations

- Value-dump verification output with no pass/ok token (e.g. `add(2,3) = 5`) is conservatively treated as unverified — the safe direction (gate gets stricter, never looser).
- `확인질문 N:` marker adherence varies turn-to-turn; the gate is deterministic on `intent.json` file existence, markers are guidance.

## [1.0.0] - 2026-07-07

### Stabilized

- Core discipline engine: Korean-first prompt classification, investigation compliance markers, evidence-gated Stop checks, scope warnings, high-risk contract gating, ledger persistence, and goals checkpoints.
- Three adapter surfaces: Claude Code, Codex CLI, and Antigravity thin wrappers around the shared core behavior.
- Orchestrator CLI: `python -m fable_lite check` and `python -m fable_lite brief`, including task-card based delegation checks.
- Evaluation surface: deterministic probe runner, smoke harness, A/B methodology documents, and summarized review reports.

### Evidence

- Controlled A/B evaluation: fable-lite ON won 5/5 tasks against OFF in the blind-judged report.
- Live 3-CLI observation: Claude Code, Codex CLI, and Antigravity adapter paths were exercised as practical hook surfaces.
- Natural-compliance experiment: pack-only behavior failed to reliably enforce investigation markers, while the Stop gate recovered runs with concrete evidence.

### Known Limitations

- Verification recognition is centered on Python, JavaScript, shell, and common build/test runner patterns. Other ecosystems may need additional adapter policy.
- Evaluation sample sizes are intentionally small; the reports support release readiness, not statistical generalization.
- Sonamu Bot deployment assumes a host with Python available on `PATH` because hooks are stdlib Python scripts.
- fable-lite reproduces procedural discipline only. It does not claim to reproduce Fable 5 model-weight capabilities.

## [0.6.x] - 2026-07

- Added orchestrator `check` and `brief` commands, including task-card integration.
- Added Codex CLI and Antigravity adapters alongside the Claude Code adapter.
- Added deterministic probe runner and JSON result output.

## [0.5.x] - 2026-07

- Hardened adapter payload parsing, fail-open behavior, N1 investigation compliance wiring, and Stop gate sharing.
- Added regression tests for realistic nested tool payloads and malformed hook inputs.

## [0.4.x] - 2026-07

- Added compliance, scope, and high-risk contract modules.
- Added bilingual investigation marker parsing and pack alignment checks.

## [0.3.x] - 2026-07

- Added ledger, verification state, and goals checkpoint CLI behavior.
- Added tests for Stop blocking limits and verification recording.

## [0.2.x] - 2026-07

- Added Korean prompt classification patterns and initial pack routing.
- Added Claude Code hook scaffold and plugin manifest wiring.

## [0.1.x] - 2026-07

- Project bootstrap, research notes, initial architecture, and v1 contract drafts.
