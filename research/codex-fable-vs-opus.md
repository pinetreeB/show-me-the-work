# Claude Fable 5 vs Opus 4.8/4.6 리서치

작성일: 2026-07-06  
목적: Fable 5의 차이를 하위 Claude 모델에서 프롬프트/하네스로 재현할 수 있는 영역과, 모델 가중치/제품 계층 차이라 재현하기 어려운 영역으로 나눈다.

## 결론 요약

1. 공식 포지셔닝상 Fable 5는 단순한 Opus 4.8 튜닝판이 아니라, Opus보다 높은 "Mythos-class" 계층의 일반 공개 버전이다. Anthropic은 Mythos-class를 Opus보다 높은 능력 티어라고 설명하며, Fable 5와 Mythos 5는 같은 underlying model이고 차이는 safeguards라고 밝힌다. 근거: https://www.anthropic.com/news/claude-fable-5-mythos-5, https://www-cdn.anthropic.com/d00db56fa754a1b115b6dd7cb2e3c342ee809620.pdf
2. 가장 큰 공식 성능 차이는 "긴 자율 작업, 에이전틱 코딩, 복잡한 도구 루프, 비전/문서/과학"에서 난다. SWE-bench Pro 80.0/80.3 vs Opus 4.8 69.2, FrontierCode Diamond 29.3 vs 13.4, CursorBench 72.9 vs GPT-5.5 64.3, Terminal-Bench 2.1은 Mythos 88.0/Fable 84.3 vs Opus 4.8 82.7이다. 근거: Claude Fable/Mythos system card, section 8: https://www-cdn.anthropic.com/d00db56fa754a1b115b6dd7cb2e3c342ee809620.pdf
3. Fable 5의 API/제품 차이는 능력뿐 아니라 "always-on adaptive thinking", Fable 전용 refusal/fallback 처리, 30일 데이터 보존, 2배 가격, 더 낮은 캐시 최소 길이, safety classifier이다. 근거: https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5, https://platform.claude.com/docs/en/about-claude/pricing
4. 하위 모델에서 재현 가능한 것은 "작업 규율"이다: evidence-grounded progress, periodic self-verification, verifier subagent, async subagents, memory files, compaction, task budgets, skills, stop gates. 하지만 Fable의 latent capability 자체, 특히 hard long-horizon coding에서의 성공률, 비전/과학/취약점 발견 능력, medium effort에서도 이전 모델 xhigh를 넘는 원시 추론 효율은 프롬프트만으로 동일 재현하기 어렵다. 이 분류는 공식 수치와 문서화된 행동 차이에 기반한 추정이다.

## 1. 공식 자료가 밝힌 구조적 차이

### 1.1 Mythos-class의 의미

- Anthropic 발표문은 Fable 5를 "Mythos-class model made safe for general use"로 소개하고, footnote에서 Mythos-class가 Opus class 위에 있는 능력 티어라고 정의한다. 근거: https://www.anthropic.com/news/claude-fable-5-mythos-5
- System card는 Claude Mythos 5와 Claude Fable 5를 "two configurations of a new large language model"로 설명한다. Mythos 5는 Project Glasswing의 검증된 파트너용이고, Fable 5는 동일 underlying model weights에 cybersecurity/biology safeguards를 더한 일반 공개 버전이다. 근거: https://www-cdn.anthropic.com/d00db56fa754a1b115b6dd7cb2e3c342ee809620.pdf
- 즉 Fable vs Mythos의 차이는 주로 deployment/safeguard 차이이고, Fable vs Opus의 차이는 모델 계층과 가중치 수준의 차이다.

### 1.2 훈련과 안전 조치

- System card에 따르면 Mythos/Fable 5는 공개 웹, 공개/비공개 데이터셋, 다른 모델로 생성한 synthetic data의 proprietary mix로 학습됐고, deduplication/classification 등 data cleaning/filtering 후 pretraining, post-training, fine-tuning을 거쳤다. 근거: system card section 1.1, https://www-cdn.anthropic.com/d00db56fa754a1b115b6dd7cb2e3c342ee809620.pdf
- Fable 5의 novel safeguards는 cybersecurity, biology/chemistry, distillation attempt를 감지하는 classifiers다. Claude 앱에서는 감지 시 Opus 4.8로 fallback되고, Messages API에서는 기본적으로 HTTP 200의 `stop_reason: "refusal"`과 category를 반환한다. 자동 fallback은 별도 설정 또는 일부 surface에서만 제공된다. 근거: https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5
- System card는 frontier LLM development 관련 safeguard도 별도로 설명한다. 이 영역은 일반적인 cyber/bio fallback과 달리 prompt modification, steering vectors, PEFT 같은 방식으로 효과를 제한할 수 있다고 설명한다. 영향 추정은 약 0.03% traffic, 0.1% 미만 조직에 집중된다고 제시한다. 근거: system card section 1.5, https://www-cdn.anthropic.com/d00db56fa754a1b115b6dd7cb2e3c342ee809620.pdf
- Anthropic은 Fable/Mythos 등 Mythos-class 모델에 30일 데이터 보존을 요구하고 zero data retention을 지원하지 않는다고 문서화했다. 근거: https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5

### 1.3 가격, 컨텍스트, output, effort

- Fable 5/Mythos 5 가격은 input $10/MTok, output $50/MTok이다. Opus 4.8/4.7/4.6은 input $5/MTok, output $25/MTok이므로 Fable은 rate card 기준 2배다. 캐시 write/hit도 같은 비율로 2배다. 근거: https://platform.claude.com/docs/en/about-claude/pricing
- Fable 5/Mythos 5는 1M token context window와 최대 128k output tokens를 제공한다. Opus 4.8도 1M context와 128k max output을 지원하므로, Fable과 Opus 4.8의 차이는 "context 크기 자체"보다는 그 context를 오래, 덜 drift하며 활용하는 능력 쪽이다. 근거: https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5, https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-8
- Fable 5/Mythos 5는 adaptive thinking이 항상 켜져 있고 `thinking: {"type":"disabled"}`를 지원하지 않는다. Opus 4.8도 adaptive thinking만 지원하지만, request에 `thinking: {"type":"adaptive"}`를 넣지 않으면 thinking 없이 실행된다. Opus 4.6은 adaptive thinking을 지원하지만 optional이고, manual `budget_tokens`는 deprecated 상태로 아직 허용된다. 근거: https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking
- Effort는 모델이 token을 얼마나 적극적으로 쓰는지 조절하는 behavioral signal이다. `xhigh`는 Fable 5, Mythos 5, Opus 4.8, Opus 4.7, Sonnet 5에서 지원되며, Opus 4.6은 현재 문서상 `xhigh` 대상에 없다. 근거: https://platform.claude.com/docs/en/build-with-claude/effort

## 2. 벤치마크 격차

아래 수치는 별도 표기가 없으면 Anthropic Fable/Mythos 5 system card section 8 기준이다. Fable과 Mythos가 모두 있는 경우, classifier가 영향을 주는 벤치마크에서는 Fable 점수가 낮아질 수 있다. 근거: https://www-cdn.anthropic.com/d00db56fa754a1b115b6dd7cb2e3c342ee809620.pdf

| 평가 | Fable/Mythos 5 | Opus 4.8 | 해석 |
| --- | ---: | ---: | --- |
| SWE-bench Pro | Fable 80.0, Mythos 80.3 | 69.2 | 큰 격차. hard multi-file coding에서 raw capability 차이가 강함. |
| SWE-bench Verified | Fable 95.0, Mythos 95.5 | 88.6 | 기존 verified task도 유의미한 격차. |
| Terminal-Bench 2.1 | Mythos 88.0, Fable 84.3 | 82.7 | Fable은 20.9% trials에서 safety refusal/fallback이 발생해 Mythos보다 낮음. |
| FrontierCode Diamond | Fable 29.3 | 13.4 | long-horizon autonomous coding에서 2배 이상. GPT-5.5는 5.7로 보고됨. |
| FrontierCode Main | Fable 46.3 | 34.3 | Fable이 모든 effort에서 선두, medium effort도 타 모델 최고 effort 초과라고 system card가 설명. |
| FrontierSWE | Fable mean@5 rank 2.12 | Opus 4.8 rank 3.26 | 20시간 ultra-long engineering tasks. 순위 지표라 낮을수록 좋음. |
| ProgramBench | Mythos 84-93 | Opus 4.8 79-88 | binary reconstruction 특성상 Fable 별도 점수는 classifier 때문에 미보고. |
| CursorBench | Fable 72.9 at max effort | 직접 Opus 수치 미표기 | Cursor production harness 기준 Fable이 이전 최고 기록보다 높고 GPT-5.5 64.3보다 +8.6. |
| BrowseComp | Mythos single 88.0, multi-agent 93.3 | Opus 4.8 single 84.3, multi-agent 88.5 | web/research agent에서 multi-agent harness가 모델보다 또 다른 큰 축. |
| DeepSearchQA | Mythos 94.2 F1 | Opus 4.8 93.1 F1 | 심층 검색은 격차가 작음. |
| GDP.pdf strict pass | Fable 29.8 | Opus 4.8 22.5 | dense professional PDF/vision 문서에서 격차. |
| AutomationBench | Fable 17.4 | Opus 4.8 15.5 | 실제 업무 API workflow 벤치에서는 소폭 우위. |

해석:

- Fable의 차이는 "모든 과제에서 압도"가 아니라 "hard tail에서 크게 벌어짐"에 가깝다. 쉬운/중간 난도 업무에서는 Opus 4.8이 이미 충분하고, harness/skill의 영향이 모델 교체보다 클 수 있다.
- Terminal-Bench처럼 classifier가 끼는 과제에서는 Fable 체험이 underlying Mythos 능력보다 낮아질 수 있다. 즉 실제 Fable 앱/API 사용자는 "Fable weights + safeguards + fallback behavior"를 함께 평가해야 한다.
- Opus 4.6은 2026-02 발표 당시 1M context beta, adaptive thinking, effort, compaction을 도입하며 이미 큰 도약이었다. 하지만 Fable system card의 head-to-head 표는 대부분 Opus 4.8 기준이라, Fable vs Opus 4.6의 직접 최신 수치 비교는 제한적이다. Opus 4.6 공식 발표 근거: https://www.anthropic.com/news/claude-opus-4-6

## 3. 커뮤니티/실사용 관찰

주의: 아래는 공식 벤치마크가 아니라 Reddit/벤더 블로그/제3자 평가 관찰이다. 표본 편향, 프롬프트 차이, harness 차이, Fable fallback 여부 미확인 문제가 있으므로 "정황 증거"로만 본다.

### 3.1 긍정 관찰

- Anthropic Fable 페이지의 고객 코멘트는 Fable 5가 장기 agent 작업, 더 적은 correction, 자기 검증, 복잡한 multi-agent Claude Code workflow, spreadsheet suite에서 25-30% 빠른 완료 등을 보였다고 소개한다. 근거: https://www.anthropic.com/claude/fable
- Reddit 실사용자 중 일부는 Opus 4.8이 세부를 놓치고 revision에서 빙빙 돈 Reaper DAW/WALTER 테마 작업을 Fable이 첫 시도에 대부분 처리했고 revision도 정확했다고 보고했다. 근거: https://www.reddit.com/r/claude/comments/1u40cpp/fable_5_vs_opus_48_is_the_difference_actually/
- ClaudeCode Reddit의 한 사용자는 Fable이 4.8보다 "quicker to get moving", 덜 장황하고, 다음 단계 제안이 더 간결하다고 관찰했다. 반면 다른 댓글은 "큰 차이 없음, 느림, redundant comments"라고 반박한다. 근거: https://www.reddit.com/r/ClaudeCode/comments/1u1nn0n/ok_human_answers_only_how_is_fable_compared_to/
- Reddit의 architecture/design 대화 관찰에서는 Fable이 Opus보다 더 "to the point"이고 intent를 조금 더 잘 이해한다는 반응이 있었다. 근거: https://www.reddit.com/r/ClaudeCode/comments/1u1z5u0/20_hours_with_fable_5_benchmarks_say_10_points/

### 3.2 부정/제한 관찰

- 같은 Reddit thread에서 일부 사용자는 대부분 task에서 Opus가 충분하고, Fable은 token을 더 빨리 쓰거나 frontend output이 Opus와 비슷하거나 나빴다고 보고했다. 근거: https://www.reddit.com/r/claude/comments/1u40cpp/fable_5_vs_opus_48_is_the_difference_actually/
- 4-5일짜리 long-horizon autoresearch류 작업에서는 Fable도 hands-off가 아니며 계속 steering이 필요했다는 관찰이 있다. 이는 공식 "days-long" 포지셔닝과 충돌하는 반례가 아니라, 실제 harness/목표/검증 루프 없이는 모델만 바꿔도 장기 자율성이 자동으로 보장되지 않는다는 경고로 보는 편이 맞다. 근거: https://www.reddit.com/r/ClaudeCode/comments/1u1z5u0/20_hours_with_fable_5_benchmarks_say_10_points/
- CodeRabbit의 code review 벤치마크는 Fable 5가 actionable coverage는 Opus 4.8과 거의 같지만 precision은 낮고, comment 수가 많아 reviewer workload를 키울 수 있다고 보고했다. 수치: actionable precision Fable 32.8% vs Opus 35.5%, full precision Fable 19.4% vs Opus 26.5%, Fable comments 253. 근거: https://www.coderabbit.ai/blog/fable-5-model-review
- Tessl의 917 shared scenarios agent skill eval은 Fable 5가 Opus 4.8보다 overall +0.9점(92.9 vs 92.0)에 그쳤고, relevant skill 추가 효과는 양쪽 모두 약 +17점이었다고 보고했다. 또한 Fable은 약 940개 시나리오 중 26개를 refusal로 처리했고 Opus는 완료했다고 한다. 이는 "좋은 skill/harness가 모델 업그레이드보다 큰 경우"의 강한 근거다. 근거: https://tessl.io/blog/claude-fable-5-vs-opus-48-the-mythos-hype-meets-reality/

### 3.3 관찰 행동 차이 정리

| 행동 축 | 관찰된 Fable 우위 | 반례/주의 |
| --- | --- | --- |
| 장기 자율작업 지속성 | 공식/고객 코멘트는 days-long, multi-day, fewer turns를 강조. FrontierCode/FrontierSWE에서 hard tail 우위. | Reddit 일부는 4-5일 작업에서 여전히 steering 필요하다고 보고. Harness 없이는 재현 불안정. |
| 계획 수립/자기검증 | 공식 prompting guide는 Fable이 highest effort에서 reflect/validate 성향이 강하고, verifier subagent 권장. | 자기검증만으로 충분하다는 뜻은 아님. fresh-context verifier가 더 낫다고 공식 문서도 권장. |
| 지시 이행 정밀도 | 공식 guide는 instruction-following 개선, Tessl은 instruction-following 89.3 vs 88.0 소폭 우위. | Tessl에서 skill 효과(+27 instruction-following)가 model 효과보다 훨씬 큼. |
| 컨텍스트 관리 | 1M context, memory files, compaction, long-running focus 개선 공식 주장. | Opus 4.8도 1M/128k 지원. Context engineering이 없으면 context rot은 여전히 문제. |
| 실수 패턴 | 더 proactive하고 덜 질문한다는 관찰, 더 깊은 설계 hole 포착. | 과감함이 loose cannon, over-refusal, noisy code review, unrequested action으로 나타날 수 있음. |

## 4. 재현 가능 vs 재현 불가 가설 분류

아래 분류는 Fable-lite 구현 방향을 위한 가설이다. "공식 확인"이 아니라, 위 근거들을 종합한 engineering hypothesis로 표시한다.

### 4.1 프롬프트/하네스 규율로 상당 부분 재현 가능

| 차이 | 재현 가설 | 근거/구현 힌트 |
| --- | --- | --- |
| 장기 작업에서 중간 보고를 사실 기반으로 제한 | 가능성이 높음 | Fable prompting guide는 progress claim을 tool result에 근거하라고 지시하면 fabricated status를 크게 줄인다고 설명. 하위 모델에도 evidence ledger, claim audit를 강제할 수 있음. 근거: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5 |
| 자기검증/테스트 실행 습관 | 가능성이 높음 | interval-based self-check, fresh-context verifier subagent, test-first gate는 모델 외부 절차. 하위 모델도 tool loop와 stop condition으로 강제 가능. |
| 덜 묻고 더 진행하는 autonomous posture | 부분 가능 | "pause only for destructive/irreversible/scope-changing/missing authority" 같은 checkpoint instruction은 하위 모델에서도 효과적. 단, 하위 모델은 잘못된 확신으로 scope creep 가능성이 커 verifier 필요. |
| 병렬 subagent 활용 | 가능 | Multi-Agent BrowseComp/ProgramBench에서 harness가 성능/latency를 크게 개선. 이는 모델 가중치보다 orchestration 효과. 근거: system card section 8.15, https://www-cdn.anthropic.com/d00db56fa754a1b115b6dd7cb2e3c342ee809620.pdf |
| memory file/lessons 활용 | 가능 | Fable guide가 markdown memory system을 권장하지만, memory 자체는 외부 상태 관리. 하위 모델에도 SSOT 인덱스, lesson file, retrieval rule을 붙이면 재현 가능. |
| skills/작업별 규약 준수 | 가능성이 매우 높음 | Tessl eval에서 relevant skill이 Fable/Opus 양쪽 모두 약 +17 overall을 만들었고, 모델 업그레이드 효과보다 컸다. 근거: https://tessl.io/blog/claude-fable-5-vs-opus-48-the-mythos-hype-meets-reality/ |
| fallback/refusal 감지와 재시도 | 가능 | API의 `stop_reason: "refusal"`/fallback handling은 애플리케이션 레벨 정책으로 구현 가능. 단 Anthropic 내부 classifier와 동일한 정확도는 불가. |
| output brevity/working style | 가능 | 공식 guide는 짧은 brevity instruction으로 Fable 행동을 잘 조절할 수 있다고 한다. 하위 모델에서도 "결과 먼저, 근거만" 규율은 효과적일 가능성이 높음. |

### 4.2 모델 가중치/제품 기능 수준이라 완전 재현 불가 또는 제한적

| 차이 | 재현 가능성 | 근거/해석 |
| --- | --- | --- |
| Mythos-class raw reasoning/coding capability | 완전 재현 불가 | SWE-bench Pro, FrontierCode, FrontierSWE 같은 hard benchmark gap은 같은 harness에서 난 모델 성능 차이다. 프롬프트로 일부 보완은 가능해도 동일 분포 성능은 어렵다. |
| Vision/document/table/CAD raw perception | 대체로 불가 | GDP.pdf, Blueprint-Bench, BenchCAD 등에서 Fable/Mythos가 Opus보다 크게 앞선다. crop/tool 보조는 가능하지만 시각 인식 latent capability 차이는 남는다. |
| Medium effort에서도 이전 모델 xhigh를 넘는 효율 | 제한적 | Fable guide는 lower effort Fable이 prior models xhigh를 종종 넘는다고 설명한다. 하위 모델은 effort를 올려 흉내낼 수 있지만 cost/latency와 실패율이 달라진다. 근거: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5 |
| Always-on adaptive thinking 기본값 | Opus 4.8은 부분 가능, Opus 4.6은 설정 필요 | Opus 4.8도 adaptive thinking은 지원하지만 request에서 명시해야 한다. Fable은 끌 수 없다. 제품 기본동작은 하네스로 감쌀 수 있으나 모델 내부 calibration은 동일하지 않다. 근거: https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking |
| Fable/Mythos safeguards, fallback, 30일 보존 | 동일 재현 불가 | 외부 policy router로 유사 동작은 만들 수 있지만 Anthropic 내부 activation probe/classifier, fallback semantics, data-retention product policy는 모델 외부 제품 계층이다. |
| Cyber/bio frontier capability | 의도적으로 재현 금지/불가 | System card의 cyber benchmark는 safeguards-off Mythos 기준이며, Fable은 해당 과제에서 Opus 4.8 fallback에 가깝게 동작하도록 설계됐다. Fable-lite가 이 영역을 모방하는 것은 안전/정책상 범위 밖으로 두는 것이 맞다. |
| Novel scientific hypothesis / drug design 성능 | 불가에 가까움 | Anthropic은 Mythos 5가 내부 과학자 선호도 80% 등 과학 연구 능력을 보였다고 주장하지만 상세 결과는 일부 미공개다. 하위 모델에서 workflow만으로 동일 창발을 보장할 수 없다. 근거: https://www.anthropic.com/news/claude-fable-5-mythos-5 |

### 4.3 회색지대: prompt/harness로 "체감"은 흉내 가능하나 성능 보장은 불가

- "intent를 더 잘 이해한다": 좋은 task brief, 목적/제약/성공 기준, examples, negative constraints로 하위 모델에서도 체감 개선 가능. 그러나 애매한 요구에서 스스로 핵심을 잡는 latent ability는 차이가 남는다.
- "덜 장황하고 바로 실행한다": system prompt와 stop rules로 재현 가능. 하지만 하위 모델은 빠른 실행이 shortcut/검증 누락으로 바뀔 수 있어 evidence gate가 필요하다.
- "실수를 스스로 죽인다": self-critique prompt보다 fresh verifier, tests, static analysis, diff review가 더 재현성 높다. 모델 내부 반성 성향은 일부만 모방 가능.
- "context를 오래 유지한다": 1M context가 있더라도 context rot은 문서상 존재한다. 하위 모델은 summary, memory, retrieval, context editing으로 보강 가능하지만 긴 context raw recall 자체는 모델 차이가 남는다. 근거: https://platform.claude.com/docs/en/build-with-claude/context-windows

## 5. Fable-lite 구현 가설

Fable-lite가 Opus 4.8/4.6 또는 다른 하위 모델 위에서 Fable 5의 작업 체감을 재현하려면, 모델을 바꾸려 하기보다 "작업 운영체제"를 만들어야 한다.

1. Task contract: 목표, 비가역 금지, scope boundary, success criteria, stop condition을 첫 메시지에서 구조화한다.
2. Evidence ledger: 모든 진행/완료 claim은 tool result/file diff/test output에 매핑한다. 근거 없는 claim은 "미검증"으로 표시한다.
3. Long-run loop: plan -> implement -> verify -> critic -> fix -> final gate를 반복하고, plan-only로 끝나는 것을 금지한다.
4. Fresh verifier: self-review가 아니라 별도 context verifier 또는 재실행 가능한 QA gate를 둔다.
5. Async subagents: 독립 검색/리뷰/테스트는 병렬화하되, leader가 통합 검증을 책임진다.
6. Memory/skills: repo별 규칙, 과거 실패, 사용자 선호를 file wiki/skills로 관리한다. Tessl 결과상 task skill은 모델 교체보다 큰 개선을 만들 수 있다.
7. Refusal/fallback router: Fable 스타일을 모방하려면 refusal/fallback 상태를 명시적으로 감지하고, fallback model 여부를 final에 남긴다.
8. Cost-aware effort policy: 일상 작업은 Opus 4.8/4.6 high 또는 medium, hard-tail 작업만 xhigh/max/Fable escalate로 라우팅한다.

추정: Fable-lite로 가장 잘 재현될 부분은 "성실함, 멈추지 않는 루프, 검증 습관, 병렬화, 메모리 활용"이다. 가장 안 될 부분은 "난도가 높은 단일 모델 추론 품질, 시각/과학/장기 코딩 benchmark 상한, 적은 prompting으로 세부 의도를 읽는 능력"이다.

## 6. 출처 목록

- Anthropic announcement, Claude Fable 5 and Claude Mythos 5: https://www.anthropic.com/news/claude-fable-5-mythos-5
- Anthropic system card PDF, Claude Fable 5 & Claude Mythos 5: https://www-cdn.anthropic.com/d00db56fa754a1b115b6dd7cb2e3c342ee809620.pdf
- Claude Platform docs, Introducing Claude Fable 5 and Claude Mythos 5: https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5
- Claude Platform docs, Prompting Claude Fable 5: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5
- Claude Platform docs, Effort: https://platform.claude.com/docs/en/build-with-claude/effort
- Claude Platform docs, Adaptive thinking: https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking
- Claude Platform docs, Context windows: https://platform.claude.com/docs/en/build-with-claude/context-windows
- Claude Platform docs, Pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Claude Platform docs, What's new in Claude Opus 4.8: https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-8
- Anthropic announcement, Claude Opus 4.6: https://www.anthropic.com/news/claude-opus-4-6
- Anthropic Fable product page: https://www.anthropic.com/claude/fable
- Reddit, Fable 5 vs Opus 4.8 real-world use: https://www.reddit.com/r/claude/comments/1u40cpp/fable_5_vs_opus_48_is_the_difference_actually/
- Reddit, Fable compared to Opus models: https://www.reddit.com/r/ClaudeCode/comments/1u1nn0n/ok_human_answers_only_how_is_fable_compared_to/
- Reddit, 20 hours with Fable 5: https://www.reddit.com/r/ClaudeCode/comments/1u1z5u0/20_hours_with_fable_5_benchmarks_say_10_points/
- CodeRabbit, Claude Fable 5 model review: https://www.coderabbit.ai/blog/fable-5-model-review
- Tessl, Claude Fable 5 vs Opus 4.8: https://tessl.io/blog/claude-fable-5-vs-opus-48-the-mythos-hype-meets-reality/
