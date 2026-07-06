# Hugging Face 자산 발굴 및 분석 보고서: Fable 5 모사/재현 시도

본 문서는 `fable-lite` 프로젝트의 두 번째 리서치 과제로, Hugging Face 플랫폼에 존재하는 Fable 5 (및 Claude 시리즈) 모사 및 재현 관련 자산을 발굴하고 분석한 결과입니다. (주의: 증류(Distillation) 관련 에셋은 다운로드하지 않고 분석만 수행했습니다.)

## 1. 모델 발굴 (Models)
**발굴 각도**: "fable", "fable-5", "mythos", "claude-fable" 키워드 탐색 (파인튜닝/Distill)

*   **배경**: `claude-fable-5` (일반 공개용 Mythos 클래스) 및 `claude-mythos-5` (연구용, 안전장치 미적용)의 공식 가중치는 Anthropic의 비공개 자산이므로 Hugging Face에 존재하지 않습니다.
*   **발굴된 파인튜닝/증류 모델**: 
    *   **Qwable 시리즈**: Qwen 계열(예: Qwen2.5) 등 오픈소스 모델을 기반으로, Fable 5의 출력 궤적(traces)을 사용하여 파인튜닝(SFT)을 진행한 모델들이 존재합니다. 이는 모델 카드의 설명에서 'Fable 5의 추론 및 에이전틱 행동을 모사'하기 위해 증류(Distillation) 기법을 사용했음을 명시하고 있습니다.
    *   **접근법**: 강력한 오픈소스 베이스 모델에 고품질 에이전트 궤적을 주입하는 '합성 데이터 기반 파인튜닝'.
*   *URL 예시 (개념적/검색 결과 기반)*:
    *   `https://huggingface.co/models?search=fable-5`
    *   `https://huggingface.co/models?search=mythos`

## 2. 데이터셋 발굴 (Datasets)
**발굴 각도**: Fable/frontier 모델 출력 수집 데이터셋, agentic trajectory 데이터셋, behavioral cloning(행동 복제)

*   **발굴된 자산**: 
    *   **`Glint-Research/Fable-5-traces`**: 행동 복제(Behavioral Cloning)를 목적으로 Fable-5 에이전트 모델에서 추출한 깊은 다중 턴(multi-turn) 계획, 도구 사용(tool-use) 논리, Chain-of-Thought(CoT) 행동 기록이 포함된 대표적인 데이터셋입니다.
    *   **Agentic Trajectories / Computer Use Traces**: Claude Opus 및 Fable 등을 활용하여 긴 추론 사슬과 터미널 환경에서의 도구 사용 기록을 캡처한 고품질 합성 궤적 데이터셋들이 다수 존재합니다. 이는 하위 모델에 '에이전트로서의 워크플로우'를 가르치는 데 사용됩니다.
    *   **접근법**: API를 통해 프런티어 모델의 작업 수행 과정을 단계별로 기록하여, 단순한 Q&A가 아닌 '작업 수행 절차' 자체를 학습 데이터로 구축.
*   *URL 예시*:
    *   `https://huggingface.co/datasets/Glint-Research/Fable-5-traces` (검색을 통해 확인된 대표 사례)
    *   `https://huggingface.co/datasets?search=agentic+trajectory`
    *   `https://huggingface.co/datasets?search=behavioral+cloning`

## 3. Spaces 및 블로그 발굴 (Spaces/Blogs)
**발굴 각도**: Fable 스타일 시스템 프롬프트 재현, 프롬프트 팩

*   **발굴된 자산**: 
    *   **프롬프트 팩 및 시스템 프롬프트**: 구체적으로 "Fable 스타일"로 포장된 단일 프롬프트 팩보다는, Claude 모델들의 에이전틱 성능을 극대화하기 위한 '프롬프트 플레이북(Prompt Playbook)'이나 방법론(예: 범위 초과 방지, 분석/작성/검토 단계 분리)을 다루는 논의가 주를 이룹니다. 특히, 너무 구체적인 단계적 지시(step-by-step)보다는 '목표와 제약조건'을 명확히 주어 모델이 스스로 계획하게 하는 시스템 프롬프팅이 Fable 5에 적합하다는 분석이 있습니다.
    *   *발굴 실패 (Honest Note)*: "Fable 5의 시스템 프롬프트를 완벽히 리버스 엔지니어링하여 배포한 단일 프롬프트 팩" 형태의 스페이스나 블로그는 뚜렷하게 발굴되지 않았습니다. 대신 방법론적인 팁과 에이전트 하네스 설계 가이드(예: ClawBody 개발자 도구의 성격/페르소나 프롬프트 슬롯 제공 등)가 주류입니다.
*   *URL 예시*:
    *   `https://huggingface.co/spaces?search=claude+prompt`
    *   `https://huggingface.co/blog?search=system+prompt`

## 4. 접근법 평가 및 라이선스/위험 (ToS) 분석

*   **접근법 비교 (프롬프트 팩 vs 파인튜닝/Distill vs 하네스)**:
    *   **파인튜닝/Distill**: 실효성이 가장 높음. 복잡한 도구 사용과 자기 검증 패턴을 하위 모델의 가중치에 직접 각인시킬 수 있으나 법적 리스크가 큽니다.
    *   **프롬프트 팩**: 합법적이나, 모델의 기본 체급이 낮으면 복잡한 지시를 무시하는 'Instruction Fade-out' 현상으로 인해 실효성이 떨어집니다.
    *   **하네스 (에이전트 래퍼)**: 중간 단계로, 외부 스크립트를 통해 모델의 행동을 제약하고 검증 루프를 강제하여 실효성을 끌어올릴 수 있습니다 (예: Fablize).
*   **라이선스 및 위험 (Anthropic ToS 상의 Distillation 문제)**:
    *   **명시적 금지**: Anthropic의 서비스 약관(ToS)은 자사 모델의 출력을 사용하여 다른 AI 모델을 훈련, 개선 또는 증류(Distillation)하는 것을 명시적으로 금지합니다.
    *   **시스템적 방어 (Guardrails)**: Fable 5 출시 당시, 대규모 증류 시도나 데이터 추출 행위가 감지될 경우 모델이 조용히 출력 품질을 저하시키거나 구형 모델(Opus 4.8 등)로 폴백(fallback)하는 방어 메커니즘이 내장되어 논란이 된 바 있습니다. 이후 경고 알림 기능이 추가되었고, 노골적인 위반에 대한 법적 대응도 이뤄지고 있습니다.
    *   **결론적 위험성**: Hugging Face에 업로드된 `Fable-5-traces` 기반의 파인튜닝 모델이나 데이터셋은 모두 ToS 위반의 파생물(Derivative work)일 가능성이 매우 높습니다. 따라서 `fable-lite` 프로젝트에서 이러한 증류 데이터를 직접 사용하여 모델을 학습시키거나 배포하는 것은 치명적인 법적 위험(Compliance Risk)을 수반합니다. 합법적이고 안전한 접근은 **'하네스 설계' 및 '자체적인 시스템 프롬프트 제어(Rule Injection)'**에 집중하는 것입니다.

---
*본 조사는 증류 데이터 및 모델의 실제 다운로드나 실행 없이, 공개된 메타데이터 및 분석 문서(검색 결과)만을 바탕으로 작성되었습니다.*
