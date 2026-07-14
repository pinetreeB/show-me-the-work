from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from typing import TypeAlias

from .design_gate import design_domain, design_gate_enabled
from .risk_terms import risk_flags

JsonValue: TypeAlias = str | int | bool | list[str]
JsonObject: TypeAlias = dict[str, JsonValue]

DEBUG_REGEXES = (
    re.compile(r"(버그|고쳐|고치|에러|오류|실패|깨져)"),
    re.compile(r"안\s*(되|돼|됨|됐|될|되지|되는|되네|되나)"),
    re.compile(r"\b(debug|bug|error|fix)\b", re.IGNORECASE),
)
ARTIFACT_PATTERNS = (
    "페이지",
    "화면",
    "렌더",
    "html",
    "svg",
    "게임",
    "차트",
    "만들어",
    "만들어줘",
    "build",
    "render",
    "page",
)
CREATION_ARTIFACT_PATTERNS = (
    "이미지",
    "문서",
    "보고서",
    "파일",
    "컴포넌트",
    "템플릿",
    "프로젝트",
    "앱",
    "스크립트",
    "설정",
    "image",
    "pdf",
    "file",
    "component",
    "template",
    "project",
    "app",
    "script",
    "config",
)
CREATION_ARTIFACT_SUBSTRINGS = tuple(
    pattern for pattern in CREATION_ARTIFACT_PATTERNS if not pattern.isascii()
)
CREATION_ARTIFACT_REGEXES = tuple(
    re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(pattern)}(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )
    for pattern in CREATION_ARTIFACT_PATTERNS
    if pattern.isascii()
)
ARTIFACT_REGEXES = (re.compile(r"\b(?:ui|game|chart)\b", re.IGNORECASE),)
MULTI_STORY_PATTERNS = (
    "그리고",
    "고치고",
    # 열거 연결어미+쉼표("만들고, ...도 하고, ...") — 진행형("하고 있어")은 쉼표가 없어 매칭되지 않는다
    "고, ",
    "그리고 또",
    "및",
    "동시에",
)
MULTI_STORY_REGEXES = (
    re.compile(r"\band\b", re.IGNORECASE),
    re.compile(r"\bmulti(?!ply|plex)", re.IGNORECASE),
    re.compile(r"여러\s"),
    re.compile(r"(?<![\d일이삼사오육칠팔구십])2개"),
    re.compile(r"두 개"),
)
BOOT_MARKER_RES = (
    re.compile(r"\s*\[?부팅\]?"),
    re.compile(r"\s*세션 부팅"),
    re.compile(r"\s*부팅 절차"),
    re.compile(r"\s*역할 부여"),
    re.compile(r"\s*role briefing", re.IGNORECASE),
)
WAIT_PATTERNS = (
    "대기하라",
    "대기해라",
    "대기하세요",
    "대기해",
    "대기 해",
    "standby",
    "await further",
)
KOREAN_ACTION_STEMS = "고쳐|고치|수정|구현|보완|개선|만들|추가|작성|생성|연동|해결|처리|통합|설정|적용|변경|업데이트|삭제|제거|배포|리팩터링?"
KOREAN_ACTION_SUFFIXES = (
    "해|하라|해라|해줘|해 줘|해주세요|하세요|합시다|해야|부탁|바람|요망"
)
KOREAN_ACTION_ADVERBS = "좀|꼭|먼저|바로|다시"
IMPERATIVE_ACTION_RES = (
    re.compile(
        rf"({KOREAN_ACTION_STEMS})(?:\s*(?:{KOREAN_ACTION_ADVERBS})\s*)?({KOREAN_ACTION_SUFFIXES})"
    ),
    re.compile(r"(만들어|바꿔|고쳐)(줘|라| 줘|주세요)?"),
    re.compile(r"고치고\b"),
    re.compile(
        r"\b(fix|implement|build|create|add|update|modify|refactor|resolve|integrate|apply|deploy|delete|remove|write)\b",
        re.IGNORECASE,
    ),
)
ENGLISH_GENERATION_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:creat(?:e(?:s|d)?|ing)|generat(?:e(?:s|d)?|ing))(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
KOREAN_NOMINAL_GENERATION_RE = re.compile(r"생성\s*(?:필요|좀)\s*(?:[.!?]|$)")
PATHLIKE_TLDS: set[str] = {"com", "net", "org", "io", "kr", "dev"}
BARE_TECHNOLOGY_NAMES: frozenset[str] = frozenset({"next.js", "node.js", "vue.js"})
MENTIONED_PATH_RE: re.Pattern[str] = re.compile(
    r"[\w./\\-]+\.(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]+"
)


def _text(payload: Mapping[str, object]) -> str:
    value = payload.get("prompt")
    return value if isinstance(value, str) else ""


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def _is_debug(text: str) -> bool:
    return any(pattern.search(text) for pattern in DEBUG_REGEXES)


def _contains_regex(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _contains_creation_target(text: str) -> bool:
    return _contains_any(text, CREATION_ARTIFACT_SUBSTRINGS) or _contains_regex(
        text, CREATION_ARTIFACT_REGEXES
    )


def _is_generation_request(text: str) -> bool:
    return (
        ("생성" in text and _contains_regex(text, IMPERATIVE_ACTION_RES))
        or ENGLISH_GENERATION_RE.search(text) is not None
        or (KOREAN_NOMINAL_GENERATION_RE.search(text) is not None)
    )


def _has_boot_marker(text: str) -> bool:
    return any(pattern.match(text) for pattern in BOOT_MARKER_RES)


def _is_briefing(text: str) -> bool:
    has_signal = _has_boot_marker(text) or _contains_any(text, WAIT_PATTERNS)
    return has_signal and not _contains_regex(text, IMPERATIVE_ACTION_RES)


def _has_file_boundary(text: str, match: re.Match[str], candidate: str) -> bool:
    if "/" in candidate or "\\" in candidate:
        return True
    if (
        text[match.start() - 1 : match.start()] == "`"
        and text[match.end() : match.end() + 1] == "`"
    ):
        return True
    before = text[max(0, match.start() - 8) : match.start()].rstrip()
    after = text[match.end() : match.end() + 8].lstrip()
    return (
        before.endswith("파일")
        or after.startswith("파일")
        or re.search(r"\bfile\W*$", before, re.IGNORECASE) is not None
        or re.match(r"^file\b", after, re.IGNORECASE) is not None
    )


def _mentioned_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in MENTIONED_PATH_RE.finditer(text):
        candidate = match.group(0)
        extension = candidate.rsplit(".", maxsplit=1)[1].casefold()
        is_domain = (
            extension in PATHLIKE_TLDS
            and "/" not in candidate
            and "\\" not in candidate
        )
        is_bare_technology = (
            candidate.casefold() in BARE_TECHNOLOGY_NAMES
            and not _has_file_boundary(text, match, candidate)
        )
        if not is_domain and not is_bare_technology:
            paths.append(candidate)
    return paths


def classify_prompt(payload: Mapping[str, object]) -> JsonObject:
    prompt = _text(payload)
    requested_paths = _mentioned_paths(prompt)
    is_debug = _is_debug(prompt)
    is_briefing = _is_briefing(prompt)
    is_generation_request = _is_generation_request(prompt)
    has_generation_target = bool(requested_paths) or _contains_creation_target(prompt)
    is_artifact = (
        _contains_any(prompt, ARTIFACT_PATTERNS)
        or _contains_regex(prompt, ARTIFACT_REGEXES)
        or (is_generation_request and has_generation_target)
    ) and not is_briefing
    is_multi = (
        _contains_any(prompt, MULTI_STORY_PATTERNS)
        or _contains_regex(prompt, MULTI_STORY_REGEXES)
    ) and not is_briefing
    risks = risk_flags(prompt)
    packs: list[str] = []
    root_value = payload.get("project_root")
    root = Path(root_value) if isinstance(root_value, str) else Path.cwd()
    domain = design_domain(prompt, requested_paths)
    design_required = domain == "UI" and design_gate_enabled(root)

    if is_debug:
        packs.append("investigation")
    if is_artifact:
        packs.append("verification-grounding")
    if is_multi:
        packs.append("completion")
    if design_required:
        packs.append("design-review")

    mode = "quick"
    if is_debug or risks:
        mode = "deep"
    elif is_artifact or is_multi:
        mode = "normal"
    if is_multi and (is_debug or is_artifact or risks):
        mode = "deep"
    if is_briefing and mode == "quick":
        mode = "normal"

    needs_goals = False if is_briefing else is_multi or len(requested_paths) >= 2
    message = "[smtw] 한국어 라우팅 완료 / routing complete."
    if is_briefing:
        message = "[smtw] briefing 감지 / routing complete."
    return {
        "mode": mode,
        "packs": packs,
        "risk_flags": risks,
        "needs_goals": needs_goals,
        "requested_paths": requested_paths,
        "briefing": is_briefing,
        "domain": domain,
        "design_required": design_required,
        "message": message,
    }
