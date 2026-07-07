from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Final, TypeAlias, TypeGuard

from .intent import has_intent
from .ledger import state_dir

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
Decision: TypeAlias = dict[str, JsonValue]

MODIFICATION_RE: Final[re.Pattern[str]] = re.compile(
    r"(고쳐줘|고쳐|고치|수정해줘|수정해|수정|바꿔줘|바꿔봐|바꿔|바꾸|만들어줘|만들|생성|추가해봐|추가해|추가|처리해줘|처리해|해줘|해주세요|fix|change|make|create|add|update|edit)",
    re.IGNORECASE,
)
EDIT_ACTION_RE: Final[re.Pattern[str]] = re.compile(
    r"(고쳐|고치|수정|바꿔|바꾸|만들|생성|추가|처리|fix|change|make|create|add|update|edit)",
    re.IGNORECASE,
)
PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w가-힣])(?:[./\\\w-]+[/\\])?[\w.-]+\.(?:py|js|ts|tsx|jsx|md|json|toml|yml|yaml|html|css|scss|sql|ps1|sh|txt)(?![\w가-힣])",
    re.IGNORECASE,
)
PRONOUN_RE: Final[re.Pattern[str]] = re.compile(
    r"(이거|저거|그거|여기|이\s*부분|저번에\s*말한\s*거|[가-힣]+(?:는|던)\s*거)",
    re.IGNORECASE,
)
DELEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"(알아서|적당히|어떻게\s*좀|느낌대로|니가\s*판단해서|네가\s*판단해서)",
    re.IGNORECASE,
)
SKIP_PHRASE_RE: Final[re.Pattern[str]] = re.compile(r"(그냥\s*해|묻지\s*말고)")
QUICK_RE: Final[re.Pattern[str]] = re.compile(
    r"(왜|뭐|무엇|알려|설명|분석|조회|검색|찾아|확인만|가능한가|가능해|\?)",
    re.IGNORECASE,
)
CONCRETE_HINTS: Final[frozenset[str]] = frozenset(
    {
        "api",
        "ui",
        "로그인",
        "결제",
        "프로필",
        "관리자",
        "버튼",
        "색상",
        "라벨",
        "페이지",
        "화면",
        "메인화면",
        "텍스트",
        "파일",
        "함수",
        "기능",
        "테스트",
        "컴포넌트",
        "설정",
        "문서",
        "테이블",
        "컬럼",
        "마이그레이션",
        "서버",
        "포트",
        "번호",
        "라우팅",
        "로직",
        "쿼리",
        "성능",
        "coverage",
        "데이터베이스",
        "readme",
    }
)
STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "이거",
        "저거",
        "그거",
        "여기",
        "부분",
        "말한",
        "거",
        "뭔가",
        "이상해",
        "이상한",
        "작동",
        "안하는데",
        "에러",
        "에러나는거",
        "에러났던거",
        "이전에",
        "저번에",
        "깔끔하게",
        "어울리게",
        "기존",
        "알아서",
        "적당히",
        "어떻게",
        "좀",
        "느낌대로",
        "니가",
        "네가",
        "판단해서",
        "고쳐줘",
        "고쳐",
        "고치",
        "수정",
        "바꿔줘",
        "바꿔",
        "바꾸",
        "만들어줘",
        "만들",
        "생성",
        "추가",
        "추가해",
        "처리해줘",
        "처리해",
        "해줘",
        "해주세요",
    }
)


def evaluate_ambiguity(payload: Mapping[str, object]) -> Decision:
    root = _project_root(payload)
    prompt = _prompt(payload)
    requested_paths = _requested_paths(payload, prompt)

    signals: list[str] = []
    if _target_missing(prompt, requested_paths):
        signals.append("missing_target")
    if _pronoun_reference(prompt):
        signals.append("pronoun_reference")
    if DELEGATION_RE.search(prompt):
        signals.append("delegation")
    if _ultra_short(prompt):
        signals.append("ultra_short")

    score = len(signals)
    # 모호성 점수화는 가재코드의 채점임계 게이팅 방법론 차용
    never_flag = _never_flag(root, prompt, requested_paths)
    ambiguous = score >= 2 and not never_flag
    return {
        "ambiguous": ambiguous,
        "ambiguity_score": score,
        "signals": signals,
        "message": (
            "의도 확인 필요: 모호성 신호 2개 이상"
            if ambiguous
            else "absolute no-flag condition matched"
            if never_flag
            else "의도 확인 불필요: 모호성 신호 2개 미만"
        ),
    }


def _project_root(payload: Mapping[str, object]) -> str:
    value = payload.get("project_root") or payload.get("cwd")
    return value if isinstance(value, str) and value else "."


def _prompt(payload: Mapping[str, object]) -> str:
    value = payload.get("prompt")
    return value if isinstance(value, str) else ""


def _requested_paths(payload: Mapping[str, object], prompt: str) -> list[str]:
    value = payload.get("requested_paths")
    if _str_sequence(value):
        paths = [item for item in value if item.strip()]
        if paths:
            return paths
    return [match.group(0) for match in PATH_RE.finditer(prompt)]


def _str_sequence(value: object) -> TypeGuard[Sequence[str]]:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes) and all(isinstance(item, str) for item in value)


def _never_flag(root: str, prompt: str, requested_paths: list[str]) -> bool:
    if not prompt.strip():
        return True
    if SKIP_PHRASE_RE.search(prompt):
        return True
    if requested_paths:
        return True
    if (state_dir(root) / "goals.json").exists():
        return True
    if has_intent(root):
        return True
    return _quick_mode(prompt)


def _quick_mode(prompt: str) -> bool:
    if QUICK_RE.search(prompt) and not EDIT_ACTION_RE.search(prompt):
        return True
    if not _has_modification(prompt):
        return True
    return bool(QUICK_RE.search(prompt) and not _imperative_suffix(prompt))


def _has_modification(prompt: str) -> bool:
    return bool(MODIFICATION_RE.search(prompt))


def _imperative_suffix(prompt: str) -> bool:
    return bool(
        re.search(
            r"(해줘|해주세요|해라|줘|고쳐|바꿔|바꿔봐|만들어|추가해|추가해봐|처리해줘|please|fix|make|add|update|edit)\s*[.!]*$",
            prompt,
            re.IGNORECASE,
        )
    )


def _target_missing(prompt: str, requested_paths: list[str]) -> bool:
    return _has_modification(prompt) and not requested_paths and not _has_concrete_object(prompt)


def _pronoun_reference(prompt: str) -> bool:
    return bool(PRONOUN_RE.search(prompt))


def _ultra_short(prompt: str) -> bool:
    compact = re.sub(r"\s+", "", prompt)
    return len(compact) < 15 and _has_modification(prompt)


def _has_concrete_object(prompt: str) -> bool:
    lowered = prompt.casefold()
    if any(hint in lowered for hint in CONCRETE_HINTS):
        return True
    stripped = MODIFICATION_RE.sub(" ", prompt)
    stripped = DELEGATION_RE.sub(" ", stripped)
    tokens: list[str] = re.findall(r"[A-Za-z_][A-Za-z0-9_-]*|[가-힣]{2,}", stripped)
    meaningful = [token for token in tokens if token.casefold() not in STOPWORDS]
    return any(_ascii_identifier(token) for token in meaningful)


def _ascii_identifier(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", token))
