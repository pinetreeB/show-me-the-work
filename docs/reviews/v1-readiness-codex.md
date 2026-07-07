# v1.0.0 릴리스 준비도 리뷰 - Codex

작성일: 2026-07-07  
관점: 아키텍처 일관성, 릴리스 위생  
결론: **REQUEST CHANGES**. 기능 단위 테스트는 통과하지만, v1.0.0 태그 전에 릴리스 메타데이터와 원출력 정리가 필요합니다.

## v1.0.0 전 반드시 정리할 것

1. **[blocker] 릴리스 버전 선언이 아직 `0.6.1`입니다.**  
   파일: `.claude-plugin/plugin.json:3`, `.claude-plugin/marketplace.json:8`, `README.md:3`, `README.ko.md:3`  
   근거: 플러그인 manifest와 marketplace metadata가 모두 `0.6.1`이고, README에는 v1.0.0 배지나 릴리스 버전 표기가 없습니다. v1.0.0 안정화 릴리스라면 최소한 plugin/marketplace/README가 같은 버전을 가리켜야 합니다.

2. **[blocker] `CHANGELOG.md`가 없습니다.**  
   파일: `CHANGELOG.md` 없음  
   근거: v1.0.0은 첫 안정화 기준점이므로, “무엇이 안정화 범위인지 / 어떤 실험 산출을 근거로 릴리스하는지 / 알려진 제한은 무엇인지”를 태그와 함께 남길 가치가 큽니다. README가 실험 수치를 강하게 주장하므로, CHANGELOG에서 v1.0.0의 release note와 known limitations를 분리하는 편이 안전합니다.

3. **[blocker] README CI 배지는 있는데 repo 안에 대응 workflow가 없습니다.**  
   파일: `README.md:3`, `README.ko.md:3`, `.github/workflows/ci.yml` 없음  
   근거: README 배지는 `actions/workflows/ci.yml`을 가리키지만 `git ls-files .github` 결과가 비어 있습니다. 원격에만 별도 존재하는 상황이 아니라면 릴리스 배지가 깨지거나 신뢰할 수 없는 상태입니다. CI workflow를 추가하거나 배지를 제거해야 합니다.

4. **[blocker] eval A/B 원출력과 hook 로그가 tracked 상태입니다.**  
   파일: `eval/ab/**`, `eval/ab-f1/**`, `eval/ab-repeat/**`, `.gitignore:1-7`  
   근거: `git ls-files eval/ab eval/ab-f1 eval/ab-repeat` 기준 95개 파일, 약 1.6MB가 추적됩니다. `hookdebug.log`, `response.txt`, `ledger.json`, `verification-shot.png` 같은 실행 원출력이 포함됩니다. 용량 자체는 치명적으로 크지 않지만, 안정화 릴리스 본문에 원출력 로그를 대량 포함하면 패키지/리뷰 노이즈가 큽니다. 요약 보고서만 남기고 raw run은 release artifact나 ignored fixture 영역으로 빼는 결정을 릴리스 전에 끝내야 합니다.

5. **[blocker] 기본 pytest 수집이 repo-local 임시 clone까지 집어 실패합니다.**  
   파일: `pyproject.toml` 없음, `pytest.ini` 없음, `tmp/github-repos/**` 현재 로컬 잔재, `.gitignore:4`  
   근거: `python -m pytest tests -q`는 `53 passed`로 통과했습니다. 그러나 루트에서 `python -m pytest --collect-only -q`를 실행하면 ignored `tmp/github-repos` 아래 외부 샘플 테스트까지 수집되어 `tmp/github-repos/fablize/tests/test_gate_robustness.py`의 `sys.exit(0)`로 pytest internal error가 납니다. 깨끗한 clone에서는 재현되지 않을 수 있지만, 릴리스 게이트는 로컬 작업 잔재에 흔들리지 않아야 합니다. `pyproject.toml` 또는 `pytest.ini`에 `testpaths = ["tests"]`를 두는 것이 필요합니다.

6. **[blocker] 릴리스 위생을 잡는 테스트가 부족해 현재 버전 드리프트를 못 잡습니다.**  
   파일: `tests/test_adapters.py:175`, `.claude-plugin/marketplace.json:8`, `.claude-plugin/plugin.json:3`  
   근거: 기존 테스트는 plugin manifest의 이름과 hooks 존재만 검사하고 version/marketplace/README/CHANGELOG 정합성은 검사하지 않습니다. 실제로 `0.6.1` 드리프트가 남아 있으므로 v1.0.0 태그 전에는 작은 release hygiene 테스트를 추가하는 편이 재발 방지에 좋습니다.

## 미뤄도 될 것

1. **[nice-to-have] 설치형 Python 패키지 메타데이터는 plugin-only 릴리스라면 미룰 수 있습니다.**  
   파일: `fable_lite/__main__.py:1`, `fable_lite/cli.py:9`, `pyproject.toml` 없음  
   판단: `python -m fable_lite`는 repo 루트 또는 `PYTHONPATH`가 잡힌 테스트 환경에서는 동작합니다. 다만 `pip install .`, console script, build metadata는 없습니다. v1.0.0을 “Claude Code 플러그인 릴리스”로만 정의하면 설치형 배포는 v1.0.1 이후로 미뤄도 됩니다. 단, 위 blocker의 pytest 설정을 위해 최소 `pyproject.toml`을 만드는 선택은 별도입니다.

2. **[nice-to-have] Claude/Codex/Antigravity 어댑터의 bridge 공통화 여지가 큽니다.**  
   파일: `adapters/claude_code/common.py:26`, `adapters/codex_cli/common.py:29`, `adapters/antigravity/oma_hook.py:17`, `adapters/claude_code/post_tool_use.py:43`, `adapters/codex_cli/post_tool_use.py:20`  
   판단: Claude와 Codex는 각각 `common.py`를 갖고 있고 JSON read/emit/project_root/tool parsing이 유사합니다. Antigravity는 `oma_hook.py` 한 파일 안에 read/emit/fail-open/tool parsing/core 호출을 모두 재구현합니다. v1.0.0 기능 리스크라기보다 유지보수 리스크이므로, 테스트가 통과하는 현재 상태에서는 릴리스 후 `adapters/common_bridge.py` 같은 내부 유틸로 수렴해도 됩니다.

3. **[nice-to-have] 어댑터별 verification command 인식 규칙이 완전히 같지 않습니다.**  
   파일: `adapters/claude_code/post_tool_use.py:11`, `adapters/codex_cli/post_tool_use.py:10`, `adapters/antigravity/oma_hook.py:113`  
   판단: Claude는 `python demo.py`류 스크립트 재실행까지 인식하고, Codex/Antigravity는 더 좁은 `TEST_TERMS` 기반입니다. 실제 adapter별 payload 차이 때문에 완전 통합은 설계가 필요하지만, 장기적으로는 core 또는 공유 bridge의 정책 함수로 맞추는 편이 좋습니다.

4. **[nice-to-have] Antigravity hooks timeout 표기가 다른 어댑터와 다릅니다.**  
   파일: `adapters/claude_code/hooks.json:9`, `adapters/codex_cli/hooks.json:10`, `adapters/antigravity/hooks.json:8`  
   판단: Claude/Codex는 `timeout: 10`, Antigravity는 `timeout: 5000`입니다. 스키마 단위가 다르면 문제가 아니지만, 문서에 단위 차이를 적거나 내부 상수로 설명하면 릴리스 독자가 혼동하지 않습니다.

5. **[nice-to-have] 기능 테스트 분포는 양호하지만 release/install/e2e 축은 얇습니다.**  
   파일: `tests/test_adapters.py:37`, `tests/test_codex_adapter.py:63`, `tests/test_antigravity_adapter.py:51`, `tests/test_fable_lite_cli.py:99`, `tests/test_eval_runner.py:58`  
   판단: 3어댑터, core 계약, CLI, eval runner는 단위 테스트가 있습니다. `python -m pytest tests -q` 결과도 `53 passed`입니다. 부족한 축은 `pip install`/manifest version/README badge/CHANGELOG/CI workflow/e2e smoke를 릴리스 게이트로 묶는 테스트입니다.

## 증거

- `git status --short --ignored`: 현재 새 untracked 리뷰 파일 외에 `.claude/`, `.codegraph/`, `.omo/`, `.omx/`, `.pytest_cache/`, `tmp/`, `__pycache__/`류가 ignored 상태입니다.
- `test -f CHANGELOG.md`, `test -f pyproject.toml`, `test -f pytest.ini`: 모두 없음.
- `git ls-files eval/ab eval/ab-f1 eval/ab-repeat | wc -l`: 95.
- `git ls-files -z eval/ab eval/ab-f1 eval/ab-repeat | xargs -0 du -cb | tail -1`: 약 1.6MB.
- `python -m pytest tests --collect-only -q`: 53개 project test 수집.
- `python -m pytest tests -q`: 53 passed.
- `python -m pytest --collect-only -q`: 현재 로컬 `tmp/github-repos/**`까지 수집되어 external test의 `SystemExit: 0`로 internal error.

## 릴리스 전 권고 순서

1. `plugin.json`, `marketplace.json`, README 버전 표기를 v1.0.0으로 맞추고 `CHANGELOG.md`를 추가합니다.
2. `.github/workflows/ci.yml`을 추가하거나 README CI 배지를 제거합니다.
3. `eval/ab*` raw output을 보존할지 결정합니다. 보존한다면 “실험 fixture/증거”로 명시하고, 아니라면 요약 보고서만 남기며 `.gitignore`에 raw run 패턴을 추가합니다.
4. `pyproject.toml` 또는 `pytest.ini`로 `testpaths = tests`를 고정합니다.
5. release hygiene 테스트를 하나 추가해 manifest/marketplace/README/CHANGELOG 버전 드리프트를 자동 검출합니다.
