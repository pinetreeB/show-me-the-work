# Codex 문서 규격 감사 — v2.8.0 (188ef0d)

## 요약: 확인한 명령 37개 / 문서 대조한 옵션·인수 문법 285개 / 갭 13건

- 대상은 `main@188ef0d09e049b9ac4df18b0107df65514948d44`의 `core/destructive_guard.py` 2,302줄 전체다. 감사 시작 시 대상 blob과 작업 파일의 Git blob SHA는 모두 `36de5fc3f492716e0183d27f040b389eee91b60b`였다.
- 37개는 과제에 명시된 35개 명령과 구현에 추가 등록된 `git switch`, `git read-tree`를 합친 수다. 표준 별칭은 정식 명령과 한 명령으로 셌다. `Tee-Object`는 `tee`의 정식 명령 누락으로 감사 중 발견했으므로 등록 명령 수에는 더하지 않았다.
- 285개는 단·장 옵션 동의어를 한 문법군으로 묶되, 값 결합/분리, 선택 인수, `--`, 단축 묶음, 위치 인수, PowerShell의 파라미터 별칭·고유 접두사·콜론 결합·파이프라인 바인딩을 서로 다른 문법군으로 센 값이다. 명령별 개수와 실제로 펼친 문법은 아래 대조표에 남겼다.
- 갭 13건은 공통 원인 기준이다. 같은 원인이 여러 명령에 걸친 경우 한 건으로 묶고 각 명령의 종단 재현을 모두 적었다.
- 모든 `ALLOW/BLOCK`은 파괴 명령을 실행하지 않고 `eval/r2_vocabulary_acceptance.py`의 `_identity`/`record_event` 경쟁 픽스처를 재사용해 `evaluate_r2_destructive_gate()`에 명령 문자열만 전달한 결과다. `beta`의 `candidate_paths`에 해당 경로를 기록하고 `alpha`가 명령을 시도하는 상황으로 측정했다.

### 결론

| 등급 | 건수 | 핵심 |
|---|---:|---|
| Critical | 1 | 디렉터리·Git pathspec의 하위 peer 파일을 후보로 확장하지 않아 재귀 삭제·이동·복원이 통과한다. |
| High | 11 | GNU 선택/결합 옵션, `find` 파일 출력, PowerShell 바인더 문법·정식 cmdlet, Git 무경로 파괴 모드가 빠졌다. |
| Medium | 1 | GNU `tee`가 문서상 파일명인 `-`를 stdout 표기로 오인한다. |

## 갭

### [G1] 디렉터리·pathspec의 하위 항목 — 재귀 삭제·복사·이동·복원

- 문서 근거: GNU [`rm`](https://www.gnu.org/software/coreutils/rm)은 `-r/-R/--recursive`가 디렉터리와 그 내용을 재귀 삭제한다고 정의한다. GNU [`cp`](https://www.gnu.org/software/coreutils/manual/html_node/cp-invocation.html)는 `-R/-r/--recursive`, GNU [`mv`](https://www.gnu.org/software/coreutils/manual/html_node/mv-invocation.html)는 디렉터리 이동을 정의한다. MS Learn의 [`Remove-Item`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/remove-item?view=powershell-7.5), [`Copy-Item`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/copy-item?view=powershell-7.5), [`Move-Item`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/move-item?view=powershell-7.5), [`Rename-Item`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/rename-item?view=powershell-7.5)도 컨테이너와 `-Recurse` 동작을 명시한다. Git [`rm`](https://git-scm.com/docs/git-rm), [`checkout`](https://git-scm.com/docs/git-checkout), [`restore`](https://git-scm.com/docs/git-restore) 및 [pathspec 정의](https://git-scm.com/docs/gitglossary)는 디렉터리 pathspec이 그 아래 경로에 적용됨을 전제로 한다.
- 구현: `core/destructive_guard.py:355-365`, `:483-493`, `:752-764`, `:793-805`는 명령에 적힌 부모 문자열과 `destination/basename(source)`만 만든다. `:2281-2301`은 그 정확한 canonical key만 peer 후보와 비교하며 부모 대상과 peer 하위 경로의 prefix 관계를 검사하지 않는다.
- 게이트 실측:
  - `rm -r shared-dir` → **ALLOW** (`peer=shared-dir/peer.py`, 기대 BLOCK)
  - `Remove-Item -Recurse shared-dir` → **ALLOW** (동일 peer, 기대 BLOCK)
  - `cp -R mine-dir shared-dir` → **ALLOW** (`peer=shared-dir/mine-dir/peer.py`, 기대 BLOCK)
  - `Copy-Item -Recurse mine-dir shared-dir` → **ALLOW** (동일 peer, 기대 BLOCK)
  - `mv shared-dir moved-dir` / `Move-Item shared-dir moved-dir` / `Rename-Item shared-dir renamed-dir` → 모두 **ALLOW** (`peer=shared-dir/peer.py`, 기대 BLOCK)
  - `git checkout -- shared-dir` / `git restore -- shared-dir` / `git rm -r shared-dir` → 모두 **ALLOW** (`peer=shared-dir/peer.py`, 기대 BLOCK)
- **Critical / 수리 방향:** 파괴되는 source/pathspec 컨테이너는 peer candidate의 prefix까지 비교하고, 재귀 복사 destination은 공식 배치 규칙에 따라 하위 상대 경로를 보존해 비교해야 한다.

### [G2] `cp`·`mv` — 필수 인수를 붙인 `-tDIRECTORY`

- 문서 근거: GNU [`cp`](https://www.gnu.org/software/coreutils/manual/html_node/cp-invocation.html)와 [`mv`](https://www.gnu.org/software/coreutils/manual/html_node/mv-invocation.html)의 형식은 `-t, --target-directory=DIRECTORY`다. POSIX [`getopt()`](https://pubs.opengroup.org/onlinepubs/9799919799/functions/getopt.html)는 필수 short-option 인수가 같은 토큰의 나머지 문자로 붙을 수 있음을 정의한다. 로컬 GNU coreutils 8.32도 `cp -tpeer-dir --help`, `mv -tpeer-dir --help`에서 `peer-dir`를 target directory로 해석했다(복사는 실행되지 않고 operand 부족으로 종료).
- 구현: `core/destructive_guard.py:724-733`은 정확히 `-t` 다음 토큰과 `--target-directory=...`만 처리한다. `-tpeer-dir` 또는 마지막에 `t`가 오는 short cluster는 일반 옵션으로 버려 destination을 잃는다.
- 게이트 실측: `cp -tpeer-dir mine.py` / `mv -tpeer-dir mine.py` → 모두 **ALLOW** (`peer=peer-dir/mine.py`, 기대 BLOCK). 대조군 `-t peer-dir`, `--target-directory peer-dir`, `--target-directory=peer-dir`는 모두 BLOCK했다.
- **High / 수리 방향:** GNU short-option cluster를 해석해 `t` 뒤 나머지 문자를 필수 인수로 취하고, 나머지가 없을 때만 다음 토큰을 소비한다.

### [G3] `cp`·`mv` — 인수가 없거나 선택적인 `--context`를 필수 분리 인수로 오인

- 문서 근거: 로컬 GNU coreutils 8.32 `cp --help`는 `--context[=CTX]`, `mv --help`는 `-Z, --context`로 정의한다. GNU [`cp`](https://www.gnu.org/software/coreutils/manual/html_node/cp-invocation.html)와 [`mv`](https://www.gnu.org/software/coreutils/manual/html_node/mv-invocation.html) 문서도 `--context`의 선택/무인수 형식을 설명한다.
- 구현: `core/destructive_guard.py:649-651`이 `--context`를 필수 값 옵션에 넣고 `:734-737`에서 무조건 다음 토큰을 소비한다. 첫 source가 옵션 값으로 사라져 source/destination 쌍을 만들지 못한다.
- 게이트 실측: `cp --context mine.py peer.py` / `mv --context peer.py moved.py` → 모두 **ALLOW** (`peer=peer.py`, 기대 BLOCK). `cp --context=system_u mine.py peer.py`는 BLOCK해 결합형과 무인수형의 불일치도 확인했다.
- **High / 수리 방향:** `cp --context`와 `mv --context`는 다음 토큰을 소비하지 말고, `cp --context=CTX`만 같은 토큰의 선택 값을 해석한다.

### [G4] `cp --parents` — destination 상대 경로 보존

- 문서 근거: GNU [`cp`](https://www.gnu.org/software/coreutils/manual/html_node/cp-invocation.html)는 `--parents`가 source의 전체 이름을 destination 아래에 사용한다고 정의한다. 즉 `cp --parents a/b/mine.py shared-dir`의 sink는 `shared-dir/a/b/mine.py`다.
- 구현: `core/destructive_guard.py:752-764`는 항상 source의 basename만 붙여 `shared-dir/mine.py`까지만 후보로 만든다.
- 게이트 실측: `cp --parents a/b/mine.py shared-dir` → **ALLOW** (`peer=shared-dir/a/b/mine.py`, 기대 BLOCK).
- **High / 수리 방향:** `--parents`가 유효할 때 source의 정규화된 상대 경로 전체를 destination 뒤에 결합한다.

### [G5] GNU `sed` — `i`가 첫 글자가 아닌 short-option cluster

- 문서 근거: GNU sed 4.9 [`Command-Line Options`](https://www.gnu.org/software/sed/manual/html_node/Command_002dLine-Options.html)는 `-i[SUFFIX]`를 정의하고, `-Ei`는 `-E -i`, `-iE`는 suffix `E`로 서로 다르게 해석한다고 설명한다. 같은 문서는 `-ni`가 출력 억제와 in-place를 함께 켜 파일을 비울 수 있다고 경고한다.
- 구현: `core/destructive_guard.py:834-843`은 토큰이 정확히 `-i`이거나 문자열이 `-i`로 시작할 때만 in-place로 본다. `-ni`, `-Ei`처럼 `i`가 cluster 뒤에 있는 문법은 `:854-855`에서 일반 옵션으로 버린다.
- 게이트 실측: `sed -ni 's/x/y/' peer.py` / `sed -Ei 's/x/y/' peer.py` → 모두 **ALLOW** (`peer=peer.py`, 기대 BLOCK).
- **High / 수리 방향:** GNU의 optional suffix 모호성 규칙을 그대로 구현해 cluster 안 `i`를 판독하되, `i` 앞 옵션과 `i` 뒤 suffix의 의미를 문서대로 구분한다.

### [G6] GNU `find` — 파일을 생성·절단하는 출력 action

- 문서 근거: GNU findutils 4.10 [`Print File Name`](https://www.gnu.org/software/findutils/manual/html_node/find_html/Print-File-Name.html)는 `-fprint file`이 기존 파일을 0바이트로 절단하고, `-fprint0`, `-fprintf`, `-fls`도 이름을 받은 파일 출력 action으로 정의한다.
- 구현: `core/destructive_guard.py:1178-1208`은 `-delete`와 `-exec/-execdir/-ok/-okdir`만 분류한다. 네 file-output action과 그 다음 파일 인수를 소비하는 분기가 없다.
- 게이트 실측:
  - `find . -fprint peer.py` → **ALLOW**
  - `find . -fprint0 peer.py` → **ALLOW**
  - `find . -fls peer.py` → **ALLOW**
  - `find . -fprintf peer.py '%p\n'` → **ALLOW**
  - 모두 `peer=peer.py`, 기대 BLOCK이다.
- **High / 수리 방향:** 네 action의 바로 다음 파일 인수를 truncate sink로 라우팅하고, `-fprintf`는 뒤의 format 인수까지 정확히 소비한다.

### [G7] GNU `tee` — 파일명 `-`

- 문서 근거: 현재 GNU [`tee`](https://www.gnu.org/software/coreutils/manual/html_node/tee-invocation.html)는 operand `-`를 stdout 표기가 아니라 파일명으로 취급한다고 명시한다.
- 구현: `core/destructive_guard.py:1022-1023`은 `token == "-"`를 항상 target에서 제외한다. `--` 이후에도 같은 제외가 적용된다.
- 게이트 실측: `tee -` / `tee -- -` → 모두 **ALLOW** (`peer=-`, 기대 BLOCK).
- **Medium / 수리 방향:** GNU `tee`의 operand 위치에 온 `-`를 정적 파일 target으로 유지한다.

### [G8] PowerShell — `-Parameter:Value` 결합 문법

- 문서 근거: MS Learn [`about_Parameters`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parameters?view=powershell-7.5)는 `-Name Value`와 `-Name:Value`를 모두 유효한 parameter-value 형식으로 정의한다.
- 구현: `core/destructive_guard.py:541-545`, `:571-582`, `:681-691`, `:923-939`는 토큰 전체가 정확한 parameter 이름과 같고 값이 다음 토큰에 있을 때만 소비한다. `:`를 기준으로 parameter와 값을 분리하지 않는다.
- 게이트 실측:
  - `Copy-Item -Path:mine.py -Destination:peer.py` → **ALLOW** (`peer=peer.py`, 기대 BLOCK)
  - `Move-Item -Path:peer.py -Destination:moved.py` → **ALLOW** (`peer=peer.py`, 기대 BLOCK)
  - `Rename-Item -Path:peer.py -NewName:renamed.py` → **ALLOW** (`peer=peer.py`, 기대 BLOCK)
  - `New-Item -Path:peer.py -Force:$true` → **ALLOW** (`peer=peer.py`, 기대 BLOCK)
- **High / 수리 방향:** cmdlet detector 앞에서 `-Name:Value`를 PowerShell binder 규칙대로 정규화하되 switch의 `$true/$false`를 보존한다.

### [G9] PowerShell — 모호하지 않은 parameter 이름 접두사

- 문서 근거: MS Learn [`Adding Aliases, Wildcard Expansion, and Help to Cmdlet Parameters`](https://learn.microsoft.com/en-us/powershell/scripting/developer/cmdlet/adding-aliases-wildcard-expansion-and-help-to-cmdlet-parameters?view=powershell-7.5)는 런타임이 parameter 이름의 고유한 부분 일치를 수행한다고 설명한다. 따라서 각 parameter set에서 모호하지 않은 `-Dest`, `-New`, `-Fo` 같은 접두사가 유효하다.
- 구현: `core/destructive_guard.py:681-699`, `:923-947`은 `-destination`, `-newname`, `-force`의 정확한 철자만 특별 처리하고 나머지 `-...`를 버린다. named source와 접두사 destination을 섞으면 positional fallback도 성립하지 않는다.
- 게이트 실측:
  - `Copy-Item -Path mine.py -Dest peer.py` → **ALLOW** (`peer=peer.py`, 기대 BLOCK)
  - `Move-Item -Path peer.py -Dest moved.py` → **ALLOW** (`peer=peer.py`, 기대 BLOCK)
  - `Rename-Item -Path peer.py -New renamed.py` → **ALLOW** (`peer=peer.py`, 기대 BLOCK)
  - `New-Item -Fo peer.py` → **ALLOW** (`peer=peer.py`, 기대 BLOCK)
- **High / 수리 방향:** cmdlet별 parameter metadata로 현재 parameter set에서 유일한 접두사만 정식 이름에 resolve하고, 모호한 접두사는 fail-closed한다.

### [G10] PowerShell — pipeline으로 바인딩되는 Path/Command

- 문서 근거: MS Learn [`about_Pipelines`](https://learn.microsoft.com/en-gb/powershell/module/microsoft.powershell.core/about/about_pipelines?view=powershell-7.4)와 [`Copy-Item`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/copy-item?view=powershell-7.5), [`Move-Item`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/move-item?view=powershell-7.5), [`Rename-Item`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/rename-item?view=powershell-7.5)은 Path의 pipeline 입력을 문서화한다. [`Invoke-Expression`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.utility/invoke-expression?view=powershell-7.5)은 Command가 pipeline 입력을 받으며 문자열을 pipe하는 예를 제공한다.
- 구현: `core/destructive_guard.py:1896-1970`은 `|` 양쪽 segment를 독립 파싱한다. pipeline 수신 특례는 `:1881-1893`의 stdin shell뿐이다. `:654-710`과 `:1038-1071`은 왼쪽 literal을 수신 cmdlet의 Path/Command로 전달하지 않는다.
- 게이트 실측:
  - `'mine.py' | Copy-Item -Destination peer.py` → **ALLOW** (`peer=peer.py`, 기대 BLOCK)
  - `'peer.py' | Move-Item -Destination moved.py` → **ALLOW** (`peer=peer.py`, 기대 BLOCK)
  - `'peer.py' | Rename-Item -NewName renamed.py` → **ALLOW** (`peer=peer.py`, 기대 BLOCK)
  - `'Remove-Item peer.py' | Invoke-Expression` → **ALLOW** (`peer=peer.py`, 기대 BLOCK)
- **High / 수리 방향:** 정적 literal pipeline 값은 receiver의 공식 pipeline parameter로 전달하고, 파괴 cmdlet의 필수 Path/Command가 pipeline에만 의존하면서 정적으로 해석되지 않으면 fail-closed한다.

### [G11] PowerShell `Tee-Object` — 정식 cmdlet 이름과 FilePath/LiteralPath 문법

- 문서 근거: MS Learn [`Tee-Object`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.utility/tee-object?view=powershell-7.5)는 정식 명령, `[-FilePath] <String>`, `-LiteralPath <String>`, `-Append`, 그리고 Windows 표준 별칭 `tee`를 문서화한다. `-Append`가 없으면 기존 내용을 교체한다.
- 구현: `core/destructive_guard.py:993-1035`는 `_command_name(tokens[0]) == "tee"`만 허용한다. alias map `:104-117`에도 `tee-object` 정규화가 없어 정식 cmdlet은 탐지기에 도달하지 않는다.
- 게이트 실측:
  - `'x' | Tee-Object -FilePath peer.py` → **ALLOW**
  - `'x' | Tee-Object peer.py` → **ALLOW**
  - `'x' | Tee-Object -LiteralPath peer.py` → **ALLOW**
  - 모두 `peer=peer.py`, 기대 BLOCK이다. 대조군 `Tee-Object ... -Append`의 ALLOW는 append 정책과 맞는다.
- **High / 수리 방향:** `Tee-Object`를 정식 명령으로 등록하고 FilePath 위치 인수·`-FilePath`·`-LiteralPath/-LP`를 sink로, `-Variable`과 `-Append`를 비절단 경로로 구분한다.

### [G12] `git switch` — `-f/--force`와 `--orphan`

- 문서 근거: Git [`switch`](https://git-scm.com/docs/git-switch)는 `-f/--force`를 `--discard-changes`의 alias로 정의하고, `--orphan`은 모든 tracked file을 제거한다고 설명한다.
- 구현: `core/destructive_guard.py:29`, `:431-435`는 `--discard-changes` 한 철자만 implicit destructive로 분류한다. `-f`, `--force`, `--orphan`은 모두 `None`으로 끝난다.
- 게이트 실측: `git switch -f main` / `git switch --force main` / `git switch --orphan newroot` → 모두 **ALLOW** (`peer=peer.py`, 기대 BLOCK).
- **High / 수리 방향:** `-f`, `--force`, `--discard-changes`, `--orphan`을 문서상의 동일 파괴 범주로 등록한다.

### [G13] `git checkout/restore -p|--patch` — pathspec 생략

- 문서 근거: Git [`checkout`](https://git-scm.com/docs/git-checkout)과 [`restore`](https://git-scm.com/docs/git-restore)는 `-p/--patch`에서 pathspec을 생략할 수 있고, 그 경우 변경 hunk를 대화식으로 선택해 되돌릴 수 있다고 정의한다.
- 구현: `core/destructive_guard.py:346-349`는 positional이 하나도 없으면 즉시 비파괴로 반환한다. `checkout`의 option parser `:368-450`도 patch 상태를 별도 파괴 모드로 전달하지 않고, `restore`에는 대응 분기가 없다.
- 게이트 실측: `git checkout -p`, `git checkout --patch`, `git restore -p`, `git restore --patch` → 모두 **ALLOW** (`peer=peer.py`, 기대 BLOCK).
- **High / 수리 방향:** `-p/--patch`가 있고 pathspec이 없으면 implicit scope로 차단하고, pathspec이 있으면 기존 정적 대상 라우팅을 유지한다.

## 오탐 위험 (문서상 무해한데 막힐 수 있는 형태)

아래 역시 파괴 명령은 실행하지 않고 게이트만 측정했다. `peer=peer.py`가 아닌 예시는 peer 후보와 무관한 자기 파일을 사용했다.

### [F1] PowerShell `WhatIf`와 공통 parameter alias `wi`

- [`about_CommonParameters`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_commonparameters?view=powershell-7.6)는 `-WhatIf`가 명령을 실행하지 않고 영향만 표시하며 alias가 `wi`라고 명시한다.
- `Set-Content -WhatIf -Path peer.py -Value x`, `Out-File -WhatIf -FilePath peer.py` → **BLOCK**.
- `Remove-Item -wi peer.py`, `Clear-Content -wi peer.py`, `Copy-Item -wi mine.py peer.py`, `Move-Item -wi peer.py moved.py`, `Rename-Item -wi peer.py renamed.py`, `New-Item -wi -Force peer.py`, `Set-Content -wi -Path peer.py -Value x`, `Out-File -wi -FilePath peer.py` → 모두 **BLOCK**.
- 원인은 help/no-op 검사가 exact `-whatif` 위주이고(`core/destructive_guard.py:457-460`, `:539-540`, `:679-680`, `:923-924`), Set/Out detector에는 그 검사 자체가 없기 때문이다.

### [F2] `Set-Content`·`Out-File`의 위치 FilePath와 `-LP`

- MS Learn [`Set-Content`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/set-content?view=powershell-7.5)는 Path가 position 0이고, [`Out-File`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.utility/out-file?view=powershell-7.5)는 FilePath가 position 0이다. LiteralPath의 parameter alias는 `LP`다.
- `Set-Content mine.py x`, `sc mine.py x`, `Out-File mine.py`를 `peer=peer.py` 상황에서 재면 모두 **BLOCK** (`parse_unable_missing_path_flag`). 자기 파일만 쓰는데도 named path가 없다는 이유로 막힌다.
- `Set-Content -LP mine.py -Value x`, `Out-File -LP mine.py`도 exact `-literalpath`만 인식하므로 같은 위험이 있다. 반면 full `-LiteralPath mine.py`는 ALLOW했다.
- 구현 위치: `core/destructive_guard.py:563-584`.

### [F3] Git의 전역 옵션·dry-run·읽기 전용 또는 worktree 비변경 모드

- Git 공식 문서상 `git -C <path> status`는 읽기 작업인데 `git -C . status` → **BLOCK** (`command_parse_unavailable`). 구현이 `tokens[1]`을 바로 subcommand로 보고 앞의 global option은 모두 fail-closed한다(`core/destructive_guard.py:419-426`).
- [`git clean`](https://git-scm.com/docs/git-clean)의 `-n/--dry-run`은 제거를 하지 않지만 `git clean -n`, `git clean --dry-run` → **BLOCK**.
- [`git stash`](https://git-scm.com/docs/git-stash)의 `list`, `show`, `create`는 worktree를 정리하지 않지만 세 명령 모두 **BLOCK**. [`git read-tree`](https://git-scm.com/docs/git-read-tree)의 `-n/--dry-run`도 `git read-tree -n HEAD` → **BLOCK**.
- `git restore --staged peer.py`, `git reset peer.py`, `git reset --soft HEAD`, `git reset --mixed HEAD`도 worktree 파일 자체를 바꾸지 않는 문법인데 **BLOCK**한다. 다만 index/HEAD 공유 정책까지 R2가 보호할지는 별도 정책 결정이 필요하다.
- 원인은 `reset/clean/stash/read-tree` 전체를 옵션·submode 확인 없이 implicit scope로 묶은 `core/destructive_guard.py:27-31`, `:429-430`이다.

### [F4] Perl option payload 안의 문자 `i`

- Perl [`perlrun`](https://perldoc.perl.org/perlrun)은 `-i[extension]` 외에도 `-Mmodule`을 정의한다. `perl -Mstrict -pe 's/x/y/' peer.py`는 파일을 in-place 편집하지 않는데 게이트는 **BLOCK**했다.
- `core/destructive_guard.py:883-886`이 모든 short-option 토큰의 payload에 문자 `i`가 포함되기만 하면 in-place로 판정하기 때문이다. module 이름, include 관련 payload와 실제 option letter를 분리해야 한다.

### [F5] 파일 operand가 없는 GNU `tee` 선택 인수

- GNU [`tee`](https://www.gnu.org/software/coreutils/manual/html_node/tee-invocation.html)의 `--output-error[=MODE]`는 mode가 생략 가능하다. `tee --output-error`는 파일 operand가 없어 stdout만 쓰지만 게이트는 **BLOCK** (`parse_unable_pipeline`)한다.
- `core/destructive_guard.py:1009-1015`가 `--output-error=...`만 인식하고 bare 선택 인수는 알 수 없는 long option으로 처리한다.

### [F6] 기존 destination을 보존하는 no-clobber 문법

- 기존 peer destination이 실제로 존재한다는 전제에서 GNU `cp -n/--no-clobber`, `mv -n/--no-clobber`, PowerShell `Out-File -NoClobber`는 덮어쓰지 않는다. 게이트는 `cp -n mine.py peer.py`, `mv -n mine.py peer.py`, `Out-File -NoClobber -FilePath peer.py`를 모두 **BLOCK**했다.
- 보존 옵션을 정책에서 ALLOW할지는 파일 존재 확인의 TOCTOU와 source 이동 여부를 함께 검토해야 한다. 감사 결과는 현재 detector가 옵션을 구분하지 않는다는 사실까지만 확정한다.

### [F7] `--` 뒤 option-like 자기 파일

- POSIX/GNU의 `--`는 뒤 토큰을 operand로 만든다. 그러나 remove detector는 `--` 뒤에도 `-` 또는 `/`로 시작하는 토큰을 계속 옵션으로 버린다(`core/destructive_guard.py:468-478`). 따라서 자기 파일 `-scratch.py`를 대상으로 한 `rm -- -scratch.py`, `unlink -- -scratch.py`는 target 부재 fail-closed 차단 위험이 있다.

## 대조 완료 — 갭 없음 확인한 명령·옵션 목록

아래 수는 요약의 285개 문법군 산출 근거다. `G#` 표시는 그 문법군의 나머지는 덮지만 해당 번호의 예외가 있음을 뜻한다. `covered`는 정적 literal target에 한정한 결과다.

### POSIX/GNU/Perl — 75개 문법군

| 명령 | 수 | 공식 문법을 펼쳐 대조한 범위 | 구현 대조 |
|---|---:|---|---|
| `rm` | 7 | `FILE...`; force/prompt; dir; recursive; root 보존; filesystem 경계; `--` | exact literal·묶음 옵션은 covered. 재귀 하위는 G1, option-like operand는 F7. |
| `unlink` | 3 | 단일 `FILE`; `--`; help/version | exact literal은 covered. F7 가능. |
| `shred` | 8 | `FILE...`; force; iterations; size; remove mode; exact; random source; zero/verbose와 `--` | 파일 operand는 covered. option value를 target으로 과수집할 마찰 여지는 남음. |
| `truncate` | 7 | `FILE...`; no-create; io-blocks; `-r/--reference`; `-s/--size`; `-sSIZE/-rFILE`; `--opt=VAL`; `--` | `core/destructive_guard.py:587-623`에서 covered. |
| `dd` | 7 | `name=value`; `of=FILE`; input/block/count/seek 계열; `conv`; `iflag`; `oflag`; help/version | 비-append `of=`는 covered. `oflag=append`는 현 append 정책상 ALLOW. |
| `cp` | 14 | 세 synopsis; `SOURCE... DIRECTORY`; `-t DIR`; `-tDIR`; long `=VAL`; 선택 인수; recursive; parents; symlink dereference; preserve/backup; no-clobber/update; `-T`; `--`; help/version | 일반 source/destination과 `--`는 covered. G1, G2, G3, G4 및 F6. |
| `mv` | 12 | 세 synopsis; `SOURCE... DIRECTORY`; `-t DIR`; `-tDIR`; no-target-directory; backup/suffix; no-clobber/update; context; strip slash; exchange; `--`; help/version | 일반 source와 destination 양쪽은 covered. G1, G2, G3 및 F6. |
| `sed` | 9 | script positional; `-eSCRIPT/-fFILE`; `-i[SUFFIX]`; long `=SUFFIX`; short cluster 순서; quiet/regexp mode; sandbox/line length; `--`; help/version | `-i...`가 첫 option이면 covered. G5. |
| `perl` | 8 | program file/`-e`; `-i[extension]`; short cluster; `-I`; `-M/-m`; switch value 결합; `--`; help/version | 실제 `i` option이 명확한 일반형은 covered. F4. |

### Git — 63개 문법군

| 명령 | 수 | 공식 문법을 펼쳐 대조한 범위 | 구현 대조 |
|---|---:|---|---|
| `git rm` | 8 | `file...`; pathspec; `-r`; force; dry-run; cached; ignore-unmatch; sparse; `--` | literal 파일, `-n/--dry-run/--cached`는 covered. 디렉터리는 G1. |
| `git checkout` | 12 | branch; pathspec; `--`; `-b/-B`와 붙인 값; force/no-force; short cluster; detach; orphan; ours/theirs/merge/conflict; overlay; pathspec-from-file; patch | branch-create/force/literal path는 covered. pathspec-from-file fail-closed. G1, G13. |
| `git restore` | 9 | pathspec; source; staged/worktree; overlay; ours/theirs/merge/conflict; ignore-unmerged; recurse-submodules; pathspec-from-file; patch | literal path와 file-fed pathspec fail-closed는 covered. G1, G13; staged-only는 F3. |
| `git reset` | 7 | soft/mixed/hard/merge/keep; pathspec; patch; intent-to-add; recurse-submodules; pathspec-from-file; `--` | 전체 implicit block. 안전 미탐은 없지만 F3 마찰. |
| `git clean` | 6 | dry-run; force; interactive; directories; ignored/exclude; pathspec/`--` | 전체 implicit block. 안전 미탐은 없지만 dry-run은 F3. |
| `git stash` | 8 | list/show/drop/pop/apply/branch/clear/create/store/push; pathspec; include-untracked/all; keep-index/staged/patch; pathspec-from-file; message; quiet/index; `--` | 전체 implicit block. 파괴 submode는 안전, list/show/create는 F3. |
| `git switch` | 8 | branch; create/force-create; detach; guess; force/discard; merge/conflict; recurse-submodules; orphan | `--discard-changes`만 covered. G12. |
| `git read-tree` | 5 | tree-ish; merge/reset/prefix; index-output/empty; dry-run; sparse/index-only options | 전체 implicit block. 안전 미탐은 없지만 dry-run은 F3. |

공통 Git global option(`-C`, `-c`, `--git-dir`, `--work-tree`, `--namespace`, `--config-env`)의 subcommand 앞 문법도 확인했으며 현재는 안전 우선 fail-closed다(F3).

### `tee`·`find`·`xargs`·셸 — 48개 문법군

| 명령 | 수 | 공식 문법을 펼쳐 대조한 범위 | 구현 대조 |
|---|---:|---|---|
| GNU `tee` | 7 | `FILE...`; append; ignore interrupts; pipe mode; output-error 선택 값; short cluster; `--`/`-` | 일반 non-append FILE은 covered. G7, F5. |
| GNU `find` | 8 | 전역 `-H/-L/-P/-D/-O`; 시작점; 값 있는 tests; 논리 연산자; delete; exec/execdir; ok/okdir; 파일 출력 actions | delete와 exec 계열은 covered. G6. 누락된 비파괴 test-value는 보수 차단 가능. |
| GNU `xargs` | 11 | arg-file; null/delimiter; EOF; replace; max-lines/args/chars/procs; interactive/verbose; exit/logical options; short attached 값; long `=VAL`; `--`/기본 echo | `core/destructive_guard.py:1088-1152`가 문서형 option을 건너뛰고 정적 payload를 재귀 파싱한다. 확인 범위에서 안전 갭 없음. |
| `bash` | 8 | `-c string`; short cluster; `-s`; `--`; init/rc option value; `-o/-O` value; script positional; stdin/heredoc | 정적 `-c`, script wrapper, stdin shell은 covered/fail-closed. |
| POSIX `sh` | 6 | `-c command_name`; option cluster; `-s`; `--`; command_file; stdin | [POSIX sh](https://pubs.opengroup.org/onlinepubs/009604599/utilities/sh.html) 형식은 covered/fail-closed. |
| `zsh` | 8 | `-c`; clustered options; `-s`; `--`; named option `-o`; script; stdin; sh/ksh emulation | [Zsh Invocation](https://zsh.sourceforge.io/Doc/Release/Invocation.html) 형식은 covered/fail-closed. |

### PowerShell — 91개 문법군

모든 행에 공통으로 [`about_Command_Syntax`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_command_syntax?view=powershell-7.5)의 위치 인수·배열, [`about_Parameters`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parameters?view=powershell-7.5)의 분리/콜론 결합, parameter alias, 고유 접두사, pipeline 바인딩, common parameter를 대조했다.

| 명령 | 수 | 공식 문법을 펼쳐 대조한 범위 | 구현 대조 |
|---|---:|---|---|
| `Remove-Item` | 9 | Path pos0; LiteralPath/PSPath/LP; comma array; pipeline Path; Recurse; Filter/Include/Exclude; Stream; Force; WhatIf/wi와 aliases `del/erase/rd/ri/rm/rmdir` | exact/alias/array/식 fail-closed는 covered. G1, F1, F7. |
| `Clear-Content` | 9 | Path pos0; LiteralPath aliases; comma array; pipeline; Filter/Include/Exclude; Stream; Force; Credential; WhatIf/wi와 `clc` | literal 대상은 covered. F1. |
| `Set-Content` | 10 | Path pos0; LiteralPath aliases; Value pos1/pipeline; encoding/byte stream; Filter/Include/Exclude; Stream; Force; PassThru/NoNewline; Credential; WhatIf/wi와 `sc` | named full Path는 covered. F1, F2. |
| `Out-File` | 10 | FilePath pos0; LiteralPath/LP; InputObject pipeline; Encoding; Append; Force; NoClobber; width/depth; formatting switches; WhatIf/wi | named full path는 covered. F1, F2, F6. |
| `Copy-Item` | 11 | Path pos0/pipeline; Destination pos1; LiteralPath aliases; comma array; Recurse/Container; Filter/Include/Exclude; Force; PassThru; remote session; WhatIf/wi; aliases `copy/cpi/cp` | 일반 literal source/destination과 alias는 covered. G1, G8, G9, G10, F1. |
| `Move-Item` | 10 | Path pos0/pipeline; Destination pos1; LiteralPath aliases; comma array; Filter/Include/Exclude; Force; PassThru; Credential; WhatIf/wi; aliases `mi/move/mv` | 일반 literal source/destination과 alias는 covered. G1, G8, G9, G10, F1. |
| `Rename-Item` | 8 | Path pos0/pipeline; NewName pos1; LiteralPath aliases; Force; PassThru; Credential; WhatIf/wi; aliases `ren/rni` | 일반 literal source/new name과 parent 결합은 covered. G1, G8, G9, G10, F1. |
| `New-Item` | 9 | Path pos0; Name; ItemType/Type; Value; Force; Target; Credential; WhatIf/wi; alias `ni` | exact `-Force`와 literal Path/Name은 covered. G8, G9, F1. |
| `Add-Content` | 10 | Path pos0; LiteralPath aliases; Value pos1/pipeline; encoding/byte stream; Filter/Include/Exclude; Stream; Force/PassThru; NoNewline; Credential; WhatIf/wi와 alias `ac` | [공식 문서](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/add-content?view=powershell-7.5)상 append이며 현재 append 비차단 정책과 일치. overwrite 문법은 확인되지 않음. |
| `Invoke-Expression` | 5 | Command pos0; `-Command`; pipeline Command; WhatIf 비지원; alias `iex` | positional/named literal payload와 alias는 wrapper로 covered/fail-closed. G10. |

표준 command alias 집합은 MS Learn [`about_Aliases`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_aliases?view=powershell-7.6)와 각 cmdlet의 Notes를 대조했다. 구현의 `core/destructive_guard.py:104-117` 및 직접 command set은 `ac`, `clc`, `copy`, `cpi`, `cp`, `del`, `erase`, `mi`, `move`, `mv`, `ni`, `rd`, `ren`, `ri`, `rm`, `rmdir`, `rni`, `sc`, `iex`, `tee`를 덮는다. `tee`의 정식 이름 역방향 누락은 G11이다.

### .NET `System.IO.File` — 8개 문법군

| 명령 | 수 | 공식 문법을 펼쳐 대조한 범위 | 구현 대조 |
|---|---:|---|---|
| [`File.Delete`](https://learn.microsoft.com/en-us/dotnet/api/system.io.file.delete?view=net-10.0) | 1 | `Delete(path)` | `[IO.File]`/`[System.IO.File]`, 대소문자, 첫 literal path는 covered. 동적 식은 fail-closed. |
| [`File.WriteAllText`](https://learn.microsoft.com/en-us/dotnet/api/system.io.file.writealltext?view=net-9.0) | 3 | contents; encoding; span overload의 path-first 계약 | 첫 literal path는 covered. |
| [`File.WriteAllBytes`](https://learn.microsoft.com/en-us/dotnet/api/system.io.file.writeallbytes?view=net-10.0) | 1 | bytes overload | 첫 literal path는 covered. |
| [`File.WriteAllLines`](https://learn.microsoft.com/en-us/dotnet/api/system.io.file.writealllines?view=net-10.0) | 3 | enumerable/array; encoding; path-first 계약 | 첫 literal path는 covered. |

구현은 `core/destructive_guard.py:190-197`, `:808-817`에서 네 method만 case-insensitive로 제한하고 첫 인수가 literal이 아니면 fail-closed한다. 확인 범위에서 추가 안전 갭은 없었다.

## 못 본 영역 정직 고백

- 파괴 명령 자체는 경계상 실행하지 않았다. 따라서 실제 GNU/PowerShell/Git 명령의 파일시스템 결과와 gate 결과를 한 프로세스에서 연동하는 end-to-end 실행 검증은 하지 않았다. 대신 공식 문서·로컬 `--help` 문법과 gate 종단 판정을 분리해 확인했다.
- 로컬 문법 기준은 GNU coreutils 8.32, GNU sed 4.9, GNU findutils 4.10.0이고, PowerShell은 MS Learn 7.5/7.6, Git은 현재 git-scm 공식 문서, .NET은 링크한 9.0/10.0 문서다. PowerShell 5.1 전용 차이와 향후 버전 추가 parameter는 전수 비교하지 않았다.
- PowerShell provider별 dynamic parameter와 사용자 정의 alias/function은 보지 않았다. 표준 cmdlet reference에 정적으로 열거된 parameter set과 표준 alias만 감사했다.
- glob, 환경변수, subexpression, splatting, scriptblock, symlink/junction, NTFS alternate data stream의 실제 확장 결과는 정적 literal 문법 감사 밖이다. 구현이 이런 입력을 fail-closed하는지는 확인했지만 각 동적 결과를 창작해 폭격하지 않았다.
- 현재 어휘 밖의 `robocopy /MIR`, `rsync --delete`, `ln -sf`, `tar -xf`, `gsed`, 임의 interpreter 코드는 보지 않았다. 이번 과제의 등록 어휘 범위를 지켰다.
- `find`의 모든 비파괴 predicate 의미나 shell의 모든 실행 의미를 재구현 관점에서 검증하지 않았다. destructive target 추출에 영향을 주는 인수 소비, action, wrapper payload 문법을 전수 대조했다.

[CODEX-AUDIT-DONE]
