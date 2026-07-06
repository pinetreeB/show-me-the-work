# fable-lite

`fable-lite`는 Fable 5의 모델 가중치를 복제하지 않고, 조사·검증·완료 규율을 Claude Code 훅으로 강제하는 한국어 우선 하네스입니다.

## v1 범위

- 한국어 프롬프트 분류: `버그 고쳐줘`, `안돼`, `에러`, `페이지 만들어줘` 같은 요청을 조사/검증 팩으로 라우팅합니다.
- 단일 상태 디렉토리: 대상 프로젝트의 `.fable-lite/` 아래에 `ledger.json`, `goals.json`, `contract.json`만 사용합니다.
- Stop gate: 코드/산출물 변경 후 성공한 검증 증거가 없으면 최대 2회 완료를 차단합니다.
- N1 준수 게이트: 조사 출력에서 `가설 1:`, `가설 2:`, `가설 3:`, `기각:`, `증거:` 마커를 파싱합니다.
- N3 scope guard: 요청 범위 밖 파일 변경을 PostToolUse 단계에서 경고합니다.
- R1 high-risk contract: 인증, 결제, DB 마이그레이션 등 high-risk 편집은 `.fable-lite/contract.json`이 있어야 진행됩니다.

## 설치 형태

Claude Code 플러그인 manifest는 `.claude-plugin/plugin.json`에 있으며, 훅 정의는 `adapters/claude_code/hooks.json`에 있습니다. 훅은 모두 Windows native Python 명령만 사용합니다.

## 목표 체크포인트 CLI

```powershell
python goals/goals.py plan --root . --goal "페이지 만들기" --story "관리자 페이지 렌더" --verify-cmd "python -m pytest"
python goals/goals.py verify --root . --story "관리자 페이지 렌더" --evidence "pytest green"
python goals/goals.py status --root .
```

## 출처

검증 접지, 분해/증거 게이트, 조사 루프, 조기종료 방지의 절차 구조는 MIT 라이선스의 `fivetaku/fablize`에서 검증된 아이디어를 참고했습니다. 문장과 코드는 새로 작성했습니다.
