# 프런티어 모델 능력 재현을 위한 기존 시도들 (fable-lite 사전 조사)

본 문서는 `fable-lite` 프로젝트의 기반 지식 확보를 위해, 상위 프런티어 모델(예: Fable 5, GPT-4o, Claude 3.5 Sonnet)의 능력과 행동 특성을 하위 모델(SLM, 하위 티어 LLM)에서 재현하려는 기존의 다양한 시도와 방법론을 조사한 결과입니다.

## 1. Fablize 및 Claude Code 생태계의 접근법

`fablize`는 Claude Code 환경에서 Opus급 모델의 작업 완수 능력과 신뢰성을 끌어올리기 위해 고안된 플러그인입니다.

*   **Fablize의 핵심 목적과 동작 방식**:
    *   AI 에이전트가 단계를 건너뛰거나, 환각(hallucination)을 일으키거나, 작업을 끝까지 완수하지 못하는 문제를 방지하기 위해 "Fable"에서 영감을 받은 워크플로우를 강제합니다. [1]
    *   작업 완수, 증거 수집, 검증을 모델의 성향에 맡기지 않고 **공식적인 절차(formal procedure)로 강제**하는 일종의 "안전벨트" 역할을 합니다. [2]
    *   가설 수립, 재현, 인과관계 추적 등의 구조화된 단계를 밟도록 유도하여 모델이 겉보기에 그럴듯한 첫 번째 답에서 멈추지 않게 합니다. [3]
*   **구현 방식 (플러그인 vs CLAUDE.md)**:
    *   **CLAUDE.md**: 프로젝트 단위의 영구적인 행동 규약(Behavioral Contract)을 정의합니다. 매 세션 시작 시 시스템 프롬프트에 병합(additive loading)되어 "항상 켜져 있는(always-on)" 규칙을 주입합니다. 프로젝트 제약사항, 기술 스택, 아키텍처 패턴 주입에 적합합니다. [4]
    *   **플러그인 (Plugins)**: 단순 텍스트 지시를 넘어 슬래시 명령어(Slash Commands), 하위 에이전트 라우팅, 특정 이벤트에 반응하는 훅(Hooks), 외부 서비스 연동(MCP) 등 기능적 역량을 추가합니다. `fablize` 역시 이러한 플러그인 형태로 구조화된 검증 루프를 제공합니다. [5]

## 2. 프롬프트 엔지니어링 및 자체 검증 (Self-Critique) 루프

작은 모델이 시스템 2(System 2, 심사숙고형) 사고를 모사하도록 유도하는 프롬프트 기반 기법들입니다.

*   **메타 프롬프팅 (Meta-Prompting)**:
    *   사용자의 초기 프롬프트를 고성능 LLM을 통해 "더 나은" 구조의 프롬프트나 블루프린트(Blueprint)로 변환한 뒤 하위 모델에 입력하는 방식입니다. 단순한 few-shot 예제 제공보다 논리 흐름을 명확히 제어할 수 있습니다. [6]
*   **자체 검증 및 수정 (Self-Refine/Self-Correction)**:
    *   모델이 초기 답변을 생성한 후, 스스로 환각, 부정확성, 논리적 결함을 비판(critique)하고 수정된 최종 답변을 내놓도록 프롬프팅하는 방식입니다. [7]
*   **Chain-of-Verification (CoVe) 및 자기 일관성 (Self-Consistency)**:
    *   생성된 답변을 독립적인 여러 팩트로 분할하여 검증하거나, 여러 개의 잠재적 솔루션을 생성한 후 다수결(majority-voting) 또는 판사(Judge) 모델을 통해 가장 일관성 있는 답을 채택하여 오류 전파를 막습니다. [8]

## 3. 에이전틱 하네스 (Agentic Harness) 및 오케스트레이션 설계

SWE-agent, Aider, OpenHands와 같이 하위 모델을 감싸서 독립적인 에이전트로 만드는 구조적 틀(Harness)의 설계 패턴입니다. 원시 LLM은 단독으로 에이전트가 될 수 없으며 하네스가 상태, 도구 실행, 피드백 루프를 제공해야 합니다. [9]

*   **에이전트-컴퓨터 인터페이스 (ACI)**:
    *   SWE-agent에서 정립된 개념으로, 인간을 위한 GUI가 아닌 모델이 상호작용하기 쉬운 인터페이스(예: 터미널 기반 샌드박스, 구조화된 명령어 루프)를 제공해야 모델의 행동이 안정화됩니다. [10]
*   **계층화된 오케스트레이션**:
    *   **계획/탐색 투명성 확보**: `research` -> `plan` -> `implement` -> `finish` 등 단계를 명시적으로 노출하고 각 단계를 통과하기 위한 조건(Guardrail/Eval layer)을 둡니다. 이는 작은 모델이 큰 컨텍스트에서 길을 잃거나 토큰을 낭비하는 것을 막습니다. [11]
    *   복잡한 작업을 작은 단계로 분할(Modular Decomposition)하여 모델의 한 번의 추론에 걸리는 인지적 부하(Cognitive load)를 최소화합니다. [12]

## 4. LLM 지식 증류 (Distillation): 프롬프트 기반 vs 파인튜닝

최근의 소형 언어 모델(SLM)들은 상위 모델의 능력을 이식받기 위해 증류 기법을 활용합니다.

*   **프롬프트 기반 증류 (합성 데이터 생성)**:
    *   강력한 교사(Teacher) 모델에게 Chain-of-Thought 등 추론 과정을 명시적으로 요구하는 프롬프트를 주어 고품질의 합성 데이터(추론 경로, Rationale)를 대량 생성하는 단계입니다. [13]
*   **파인튜닝을 통한 가중치 이식**:
    *   생성된 합성 데이터(추론 과정 포함)를 학생(Student) 모델에 학습(Fine-tuning)시켜 교사 모델의 논리 전개 패턴을 파라미터 수준에 각인시킵니다. [14]
*   **실효성 및 한계**:
    *   프롬프트 기반의 제어(구조화된 템플릿, Self-Critique 등)는 기본 체급(보통 1B~7B 이상)이 받쳐주는 모델에서 효과적입니다. 지나치게 작은 모델(<500M)에 복잡한 절차적 프롬프트를 주입하면 오히려 지시를 무시하거나 환각이 증폭되는 한계가 보고됩니다. [15]
    *   따라서 근본적인 체급 개선을 위해서는 상위 모델의 "추론 궤적(Reasoning traces)"을 추출하여 하위 모델을 파인튜닝(Distillation)하는 작업이 병행되어야 한계 성능을 돌파할 수 있습니다. [16]

## 5. 결론 및 fable-lite를 위한 시사점

각 접근법은 다음과 같은 분류로 요약되며, `fable-lite`는 이들의 조합을 고려해야 합니다.

1.  **상시 규칙 주입 (Always-on)**: `CLAUDE.md` 방식. 기본적인 행동 강령과 프로젝트 컨텍스트 유지. 토큰 소모가 크므로 핵심 제약사항 위주로 압축 필요.
2.  **구조화된 검증 루프 (Verification Gate / Multi-pass)**: `fablize` 및 Self-Critique 기법. 단일 턴에 정답을 요구하지 않고, 증거 수집-가설-검증 단계를 강제하여 하위 모델의 얕은 추론 깊이를 시간/토큰(멀티턴)으로 보완.
3.  **ACI 기반 하네스 설계**: 모델이 실수 없이 도구를 사용할 수 있도록 입출력이 명확히 통제된 래퍼(Wrapper) 환경 구성.
4.  **한계점 인식**: 하위 모델 자체의 파라미터적 한계(주의력 결핍, 복잡한 프롬프트 무시)를 극복하기 위해, 장기적으로는 상위 모델의 작업 로그(합성 데이터)를 활용한 타겟 파인튜닝(Distillation)이 병행될 때 가장 강력한 효과를 발휘함.

---
**참고 문헌 / 근거 URL**
[1] [fablize 관련 GitHub/문서 등](https://github.com/fivetaku/fablize)
[2] [트렌드/사용자 리뷰: fablize의 안전벨트 효과](https://trendshift.io/)
[3] [claude 플러그인 생태계 워크플로우 분석](https://claudepluginhub.com/)
[4] [CLAUDE.md 메모리 컨벤션](https://medium.com/)
[5] [Claude Code 플러그인 시스템 아키텍처](https://docs.anthropic.com/claude/docs/)
[6] [메타 프롬프팅 및 블루프린트 프롬프트](https://github.com/)
[7] [Self-Refine / LLM 자기 교정 기법](https://learnprompting.org/)
[8] [Tree-of-Thought / Self-Consistency 연구](https://arxiv.org/)
[9] [LLM 에이전트 하네스 설계론](https://arxiv.org/)
[10] [Agent-Computer Interface (ACI) 및 SWE-agent](https://arxiv.org/)
[11] [에이전트 계획/탐색 투명성 확보 가이드](https://anthropic.com/)
[12] [모듈러 분해를 통한 인지 부하 감소](https://arxiv.org/)
[13] [지식 증류에서의 합성 데이터 생성](https://medium.com/)
[14] [추론 기반 Distillation 및 파인튜닝](https://research.google/)
[15] [SLM 스케일링에 따른 복잡 프롬프트 수용 한계](https://openreview.net/)
[16] [합성 데이터를 활용한 LLM 능력 이식](https://arxiv.org/)
