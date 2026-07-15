# show-me-the-work Antigravity 어댑터 설치 가이드

Antigravity CLI 1.1.2에서 command를 절대 경로로 지정하면 훅이 실제 발동하는 것을 확인했습니다. 상대 명령인 `python ...`은 훅 실행 환경에서 PATH가 해석되지 않아 조용히 미발동하므로 사용할 수 없습니다.

## 1. 절대 경로 치환

`adapters/antigravity/hooks.json`의 두 placeholder를 모두 실제 절대 경로로 바꾸십시오.

- `{PYTHON_EXECUTABLE}`: Python 실행 파일의 절대 경로(예: `C:/Python312/python.exe`)
- `{FABLE_LITE_ROOT}`: show-me-the-work 저장소의 절대 경로(예: `C:/Users/rotat/show-me-the-work`)

치환 뒤 command는 다음처럼 실행 파일과 스크립트가 모두 절대 경로여야 합니다.

```text
"C:/Python312/python.exe" "C:/Users/rotat/show-me-the-work/adapters/antigravity/oma_hook.py" PreToolUse
```

## 2. 설정 파일 설치

프로젝트별 적용은 대상 프로젝트의 `.agents/hooks.json`에 템플릿을 복사하거나 기존 JSON 그룹과 병합하십시오.

```bash
mkdir -p .agents
cp /absolute/path/to/show-me-the-work/adapters/antigravity/hooks.json .agents/hooks.json
```

전역 적용이 필요한 경우 다음 두 위치도 유효합니다.

- `~/.gemini/config/hooks.json`
- `~/.gemini/antigravity-cli/hooks.json`

기존 파일이 있으면 덮어쓰지 말고 최상위 `show-me-the-work` 그룹을 병합하십시오. 전역 훅은 열려 있는 다른 Antigravity 워크스페이스에도 적용될 수 있으므로 프로젝트 로컬 `.agents/hooks.json`을 우선 권장합니다.

## 3. 등록되는 실물 이벤트

템플릿은 Antigravity 1.1.2의 다음 6종 이벤트를 등록합니다.

- `SessionStart`
- `PreInvocation`
- `PostInvocation`
- `PreToolUse`
- `PostToolUse`
- `Stop`

실물 stdin payload의 `conversationId`, `modelName`, `stepIdx`, `artifactDirectoryPath`, `transcriptPath`, `workspacePaths`, `toolCall`을 어댑터 경계에서 수용합니다. `view_file`, `write_to_file`, `run_command`는 각각 코어의 읽기, 편집, 셸 판정으로 정규화하며 `PostToolUse.error`는 실패 결과로 기록합니다.

## 4. fail-open 확인

Antigravity는 `PreToolUse` hook이 0이 아닌 exit code를 반환하면 도구 자체를 차단할 수 있습니다. 이 어댑터는 정상 판정, 차단 결정, 잘못된 JSON, 누락 payload, 알 수 없는 이벤트를 포함한 모든 실행 경로에서 프로세스 exit code 0을 반환합니다. 설치 후에는 `/hooks`에서 6종 이벤트가 보이는지와 절대 경로 치환이 남아 있지 않은지 확인하십시오.

라이브 재판정 근거와 실측 매트릭스는 `docs/reviews/p9-agy-live-hooks.md`의 재판정 섹션에 있습니다.
