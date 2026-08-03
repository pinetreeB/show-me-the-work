# friction-fix W3 독립 검증 결과 (P4)

- 일자: 2026-08-03 · 담당: qwen(우하)
- 대상: `C:\Users\example\fable-lite` 브랜치 `worker/friction-w3-r2-opaque-whitelist`
- 검증 대상 상태: **현재 HEAD `5177b99`**(지시의 `85c5f23` 이후 W0 scorecard fix 병합 포함). 지시대로 checkout 없이 현재 상태 그대로 검증. 읽기·임시 스크립트 실행만 수행, 추적 파일 무변경.
- 재현 스크립트: `tmp/friction-fix/_pwsh_bypass.py`, `tmp/friction-fix/_adversarial_r2.py`, 신규 `tmp/friction-fix/_w3_independent_verify.py` (실제 R2 게이트 `evaluate_r2_destructive_gate` + 공유 분류기 `is_proven_read_only_command` 사용)

## (a) 갭 폐쇄 대조표 — 이전 fail-open 19건 전건

이전에 allow되던 파워셸/.NET 돌연변이 19건이 **전건 `parse_unable_wrapped`로 차단**됨(parse·R2 게이트 모두). **갭 폐쇄 확인.**

| # | 명령 | 이전 판정 | 현재 parse | 현재 R2 게이트 |
|---|---|---|---|---|
| 1 | `pwsh -NoProfile -Command @"[IO.File]::WriteAllText(...)"@` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 2 | `pwsh -NoProfile -Command @"[System.IO.File]::WriteAllText(...)"@` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 3 | `pwsh -NoProfile -Command @"$t=Get-Content; Set-Content..."@` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 4 | `powershell -NoProfile -Command "[IO.File]::WriteAllText(...)"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 5 | `powershell -NoProfile -Command "Set-Content out.txt data"` | allow | block `truncate_redirect[parse_unable_wrapped]` | **block** |
| 6 | `powershell -NoProfile -Command "Add-Content out.txt data"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 7 | `powershell -NoProfile -Command "New-Item -Force out.txt"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 8 | `powershell -NoProfile -Command "Remove-Item out.txt"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 9 | `powershell -NoProfile -Command "Invoke-Expression 'Remove-Item...'"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 10 | `powershell -NoProfile -Command "& { Remove-Item out.txt }"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 11 | `powershell -NoProfile -Command "Get-ChildItem \| Remove-Item"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 12 | `powershell -NoProfile -Command "Copy-Item a b"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 13 | `powershell -NoProfile -Command "Move-Item a b"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 14 | `powershell -NoProfile -Command "Clear-Content out.txt"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 15 | `powershell -NoProfile -Command "Out-File out.txt"` | allow | block `truncate_redirect[parse_unable_wrapped]` | **block** |
| 16 | `powershell -NoProfile -Command "sc out.txt x"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 17 | `powershell -NoProfile -Command "del out.txt"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 18 | `powershell -NoProfile -Command "rm out.txt"` | allow | block `os_remove[parse_unable_wrapped]` | **block** |
| 19 | `powershell -NoProfile -Command "echo x \| Out-File out.txt"` | allow | block `truncate_redirect[parse_unable_wrapped]` | **block** |

## (b) false positive 검사 — 일상 read-only 명령 20건+

**직접 명령(4-pane 워커가 실제로 보내는 형태) 23건 전부 R2 게이트 `allow` (과잉 차단 없음).**

| 명령 | R2 게이트 | 명령 | R2 게이트 |
|---|---|---|---|
| `git status` | allow | `git status --short` | allow |
| `git diff` | allow | `git log --oneline -5` | allow |
| `git rev-parse --show-toplevel` | allow | `python --version` | allow |
| `python -m pytest` | allow | `python -m pytest tests/` | allow |
| `python -m ruff check core` | allow | `pytest tests/` | allow |
| `python -c "print('hi')"` | allow | `ls` / `ls -la` | allow |
| `pwd` | allow | `cat README.md` | allow |
| `rg --no-config -n friction README.md` | allow | `Get-Content README.md` | allow |
| `Get-ChildItem` / `-Recurse .` | allow | `Select-String "friction" README.md` | allow |
| `Test-Path .fable-lite` | allow | `dir` / `echo hello` | allow |

**화이트리스트 wrapper(허용 경로) 6건 전부 allow:** `cmd /c "dir"`, `cmd /c "echo hello"`, `pwsh -NoProfile -NonInteractive -Command "Get-Content README.md"`, `powershell.exe ... -Command "Get-ChildItem -Recurse . | Select-String -Pattern friction"`, `bash -c "printf safe"`, `sh -c "echo safe"`.

## (c) 실사용 영향 판정 — 4-pane 워커 평소 작업

- **직접 명령 = 워커의 실제 경로.** 어댑터 3종 모두 `tool_command(payload)`가 `payload["command"]`의 **원문 그대로** R2 게이트에 전달(`adapters/codex_cli/common.py:114`, `adapters/claude_code/common.py:93`, `adapters/qwen_code/common.py:129`). 워커가 `git status`처럼 직접 입력하면 `git status` 문자열이 게이트에 도달 → **allow**. 평소 작업(조회·테스트·리팩토링)은 막히지 않음.
- **차단되는 것은 이전에 돌연변이가 통과하던 19건 + 명시적 wrapper 안의 비화이트리스트 명령** — 전부 정당한 차단.
- **좁은 한계점(false positive 1종):** `cmd /c "git status"`, `bash -c "python --version"` 같은 **wrapper로 감싼** read-only 명령은 `parse_unable_wrapped`로 차단됨(화이트리스트가 `git rev-parse`/`git branch --show-current`와 cmd/powershell/posix read-only 집합만 허용). 워커가 직접 명령을 wrapper 없이 보내는 정상 패턴에서는 발생하지 않으나, 어떤 도구가 명령을 `cmd /c`/`bash -c`로 감싸 전달하면 마찰이 생길 수 있음. (스펙상 리다이렉트·동적 wrapper는 전부 block하는 설계와 일치 — 알려진 제한.)
- **회귀:** W3+R2 관련 테스트 5파일 `tests/test_friction_w3_opaque_whitelist.py tests/test_v261_r2_command_parser.py tests/test_destructive_guard_new_02.py tests/test_multiagent_f4_stabilization_r2_01.py tests/test_remote_mutation.py` → **297 passed** (GREEN). 어댑터 빠른 경로(`task_mode=="quick"`에서 proven-read-only만 block 생략)는 명령을 추가로 차단하지 않음.

## GO / NO-GO

**GO**

- 이전 fail-open 갭 19건 전건 차단(fail-closed) — 보안 갭 폐쇄 확인.
- 일상 read-only 명령 23건 + 화이트리스트 wrapper 6건 전건 allow — 과잉 차단 없음.
- 4-pane 워커 평소 작업(직접 명령 경로)은 막히지 않음.
- W3+R2 회귀 297건 GREEN.
- 유일한 좁은 한계점: wrapper로 감싼 read-only 명령(`cmd /c "git status"` 등)은 차단 — 정상 직접 명령 패턴과 무관하며 스펙 설계와 일치. GO를 막지 않음.

## 검증 환경
- 추적 파일 무변경 확인(`git status --short` = 사전 dirty 상태 `.codex/config.toml`·`+$_.Value)`·spec 파일만). 임시 스크립트는 `tmp/friction-fix/`(gitignore)에만 존재.
- 실측은 현재 HEAD `5177b99` 기준. 지시 커밋 `85c5f23` 이후 W0 머지분이 포함되어 일부 결과가 그보다 앞선 상태 기준일 수 있음.