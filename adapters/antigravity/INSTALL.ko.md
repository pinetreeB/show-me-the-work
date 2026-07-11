# fable-lite Antigravity (OmA) 어댑터 설치 가이드

> ⚠️ **실호스트 발동 미확인 (2026-07-12 실측)**: Antigravity CLI 1.1.1에서 아래 안내를 포함한 6가지 설치 조합을
> 라이브로 실측한 결과 **훅이 한 번도 발동하지 않았습니다** — 현행 agy의 실물 훅 이벤트는 이 어댑터가 쓰는
> `BeforeModel/BeforeTool/AfterTool/AfterAgent`가 아니라 `PreToolUse/PostToolUse/PreInvocation/PostInvocation/Stop`
> 계열이며, `/hooks` UI로 실물 등록을 해도 훅 프로세스가 실행되지 않았습니다 (엔진 미발동 판정).
> 상세 증거와 재판정 절차: `docs/reviews/p9-agy-live-hooks.md`.
> 본 어댑터는 **payload 주입 테스트 레벨로만 검증**된 상태입니다. agy 훅 엔진이 발동하는 버전이 확인되면
> 이벤트 매핑 개편과 함께 본 가이드가 갱신됩니다.

이 문서는 fable-lite 코어를 Antigravity CLI(OmA) 환경에 통합하는 방법을 설명합니다.

## 주의: 메인 설정 훼손 방지

OmA의 글로벌 설정 파일인 `~/.gemini/config/plugins/oh-my-antigravity/hooks.json`을 직접 수정하는 것은 권장하지 않습니다. 대신 대상 프로젝트의 로컬 디렉토리 내에 `.gemini/hooks.json` 파일을 구성하여 안전하게 훅을 주입하는 방식을 사용하십시오.

## 설치 방법

1. **절대 경로 치환 (수동)**
   fable-lite 저장소의 `adapters/antigravity/hooks.json` 파일을 열고, 템플릿의 `{FABLE_LITE_ROOT}` 부분을 사용자가 실제 클론한 fable-lite의 절대 경로(예: `C:/Users/rotat/fable-lite`)로 직접 치환해야 합니다. 이 작업은 현재 수동으로 진행해야 합니다.
   
2. **로컬 프로젝트에 적용**
   fable-lite 하네스를 적용하려는 대상 프로젝트의 경로로 이동하여, 아래와 같이 로컬 훅 설정 파일을 복사합니다.

   ```bash
   mkdir -p .gemini
   cp /path/to/fable-lite/adapters/antigravity/hooks.json .gemini/hooks.json
   ```

   > ⚠️ **로컬 훅 등록 주의**: OmA는 프로젝트 로컬의 훅을 로드할 때 `.gemini/hooks.json` 파일을 사용합니다. 다른 경로(예: `.omg/...`)를 사용하면 훅이 조용히 무시되므로 주의하십시오. 만약 대상 프로젝트에 이미 사용 중인 `.gemini/hooks.json` 파일이 존재한다면 덮어쓰지 말고 JSON 내용을 수동으로 병합(Merge)하십시오.

3. **작동 확인**
   해당 프로젝트 디렉토리에서 Antigravity CLI를 구동하면 `oma_hook.py`가 자동으로 호출되어 페이로드를 Python 코어 판정 로직으로 전달합니다.
