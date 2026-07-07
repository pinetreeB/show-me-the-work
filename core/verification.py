from __future__ import annotations

import re

# 검증 명령으로 인정하는 신호. claude_code·codex_cli·antigravity 3개 어댑터가 공유한다
# (v1 릴리스 심사 H1/H2/H3 — 한쪽 어댑터에서만 고쳐지고 다른 쪽엔 이식 안 되는 회귀를
# core로 이전해 구조적으로 막는다). python -c "assert ..." 한 줄 검증(E1b F4)·
# 스크립트 재실행(E1c F1)·bash·PowerShell·컴파일 언어 빌드/테스트 러너(v1 릴리스 심사 H3)를 포함한다.
TEST_TERMS = (
    "pytest", "python -m pytest", "python -c", "python3 -c", "unittest",
    "npm test", "npm run test", "yarn test", "pnpm test", "jest", "vitest",
    "go test", "cargo test", "node --test", "node:test", "deno test", "rspec", "phpunit",
    "make test", "make check", "ctest", "mvn test", "gradle test", "gradlew test",
    "dotnet test", "tox", "rake test", "invoke-pester",
)

# 스크립트 재실행 패턴(E1c F1에서 관측: `python demo.py`로 수정 전후 검증했는데 미인식).
# 인터프리터 + 스크립트파일. bash/sh/zsh 추가(H3 — 이 프로젝트가 직접 감시하는
# SHELL_TOOLS의 한 축인 Bash 자체의 스크립트 재실행이 빠져 있던 것을 보강).
TEST_SCRIPT_RE = re.compile(
    r"\b(?:python3?|node|ruby|deno|bun|go run|php|bash|sh|zsh)\s+[^\s|;&]*\.\w+",
    re.IGNORECASE,
)

# 스크립트 재실행처럼 보여도 검증이 아닌 명령 — 이게 있으면 검증으로 인정하지 않는다.
NON_VERIFY_TERMS = (
    "migrate", "makemigrations", "install", "setup.py", "collectstatic",
    "build", "deploy", "runserver", "serve", "start", "manage.py",
)

# tool_output 텍스트에서 실패를 시사하는 신호. 하나라도 있으면 성공 신호 유무와
# 무관하게 실패로 판정한다(보수적 — 애매하면 실패 쪽으로 기운다).
FAIL_SIGNALS = ("failed", "error", "traceback", "assertionerror", "exception", "fatal", "not ok")

# 실패 신호가 없을 때만 확인하는 성공 신호.
OK_SIGNALS = ("passed", "verify_ok", "success", " ok\n", " ok ", "all tests", "✓")


def is_verification_command(command: str) -> bool:
    """이 셸 명령이 검증(테스트/빌드확인) 명령으로 인정되는지 판정한다."""
    lowered = command.lower()
    if any(term in lowered for term in TEST_TERMS):
        return True
    if any(term in lowered for term in NON_VERIFY_TERMS):
        return False
    return bool(TEST_SCRIPT_RE.search(command))


def text_indicates_success(text: str) -> bool:
    """exit_code/success 필드가 없을 때(nested headless 세션 등, E1b F4에서 관측)
    stdout/stderr 텍스트만으로 성공 여부를 보수적으로 판정하는 폴백.
    판정 불가(텍스트 없음, 성공/실패 신호 둘 다 없음)면 실패로 둔다."""
    lowered = text.lower()
    if not lowered:
        return False
    if any(signal in lowered for signal in FAIL_SIGNALS):
        return False
    return any(signal in lowered for signal in OK_SIGNALS)
