# qwen_code 어댑터 설치

qwen-code(0.20.1+ 검증) 네이티브 훅으로 show-me-the-work 감독을 배선합니다.
단일 디스패처 `qwen_hook.py`가 argv[1] 이벤트로 분기합니다.

## 설치

```bash
# 사용자 전역(기본, trust 불요): ~/.qwen/settings.json 의 hooks 키에 병합
python adapters/qwen_code/install.py

# 프로젝트(workspace) 설치: <target>/.qwen/settings.json + trustedFolders.json TRUST_FOLDER 자동 등록
python adapters/qwen_code/install.py --target /path/to/project

# 기존 smtw 소유 훅만 원자 교체(외부 훅·다른 설정 키 보존)
python adapters/qwen_code/install.py --upgrade
```

- 기존 `settings.json`의 다른 키(model, env, mcpServers 등)와 외부 훅은 보존됩니다.
- smtw 소유 훅이 이미 있는데 `--upgrade` 없이 재설치하면 거부됩니다(바이트 보존).
- JSON이 아닌 설정 파일은 건드리지 않고 실패 종료(1)합니다.

## 등록 이벤트

| 이벤트 | 역할 |
|---|---|
| UserPromptSubmit | 턴 시작(프롬프트 분류·팩·intent·goals 요구 기록, 컨텍스트 주입) |
| PreToolUse | R2 파괴명령 게이트 + 계약 게이트 (matcher: run_shell_command\|edit\|write_file\|notebook_edit) |
| PostToolUse | 변경 관측·검증 기록·범위 경고 |
| Stop | 완료 게이트(미검증 변경 턴 차단) |
| SessionStart / SessionEnd | 안전 no-op(확장용 자리) |

## 차단 규약 (qwen-code 0.20.1 실증 기반)

- PreToolUse/R2 차단: **exit 2 + stderr 사유** (stdout `{"decision":"deny"}` 병행).
- Stop 차단: exit 0 + `{"decision":"block","reason":...}`.
- 훅 오류: fail-open(통과, `systemMessage` 안내). 단 `SmtwEnvConflictError`만 fail-closed.

## 주의

- **`timeout` 단위는 밀리초**입니다(qwen 스키마 문서에는 초로 기재돼 있으나 런타임은 ms). 템플릿은 30000(=30초).
- Windows: command에 embedded 따옴표를 넣으면 spawn이 실패합니다. 설치기는 `%SMTW_PYTHON% %SMTW_HOOK%` + env 따옴표 값 형식을 렌더합니다(cmd 확장 시 따옴표 복원 → 공백 경로 안전).
- workspace 훅은 폴더 trust가 있어야 발동합니다(install.py --target이 자동 등록).
- **Stop 훅은 headless `qwen -p` 실행에서는 발화하지 않습니다**(대화형 세션 전용). CI/배치 감독은 PreToolUse/PostToolUse에 의존하세요.
