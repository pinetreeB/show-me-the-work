from __future__ import annotations

import posixpath
import re
from functools import lru_cache
from urllib.parse import unquote, urlsplit, urlunsplit


def normalize_path(value: str, aliases: dict[str, str] | None = None) -> str:
    return _normalize_path(value, tuple(sorted((aliases or {}).items())))


@lru_cache(maxsize=8192)
def _normalize_path(value: str, aliases: tuple[tuple[str, str], ...]) -> str:
    value = value.strip().strip('`"\'').replace("\\", "/")
    if value.lower().startswith("file://"):
        value = unquote(value[7:]).replace("\\", "/")
        value = re.sub(r"^/+(?=[A-Za-z]:/)", "", value)
    for alias, actual in sorted(aliases, key=lambda x: -len(x[0])):
        alias = alias.replace("\\", "/")
        if value.casefold() == alias.casefold() or value.casefold().startswith(alias.casefold() + "/"):
            value = actual.replace("\\", "/") + value[len(alias):]
            break
    return posixpath.normpath(value).casefold() if value else ""


def matches_path(request: str, actual: str, aliases: dict[str, str] | None = None) -> bool:
    request, actual = normalize_path(request, aliases), normalize_path(actual, aliases)
    if not request or not actual:
        return False
    if request == actual:
        return True
    # An absolute path never falls back to an unrelated file with the same name.
    if request.startswith("/") or re.match(r"^[a-z]:/", request):
        return False
    return actual.endswith("/" + request)


def normalize_url(value: str) -> str:
    try:
        p = urlsplit(value.rstrip('.,;)]}>`'))
        host = (p.hostname or "").lower()
        port = p.port
        if port and not (p.scheme.lower() == "https" and port == 443 or p.scheme.lower() == "http" and port == 80):
            host += f":{port}"
        return urlunsplit((p.scheme.lower(), host, p.path or "/", p.query, ""))
    except ValueError:
        return value


def mapping(values: list[str], *, basename_default: bool = False) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" in value:
            key, actual = value.split("=", 1)
        elif basename_default:
            actual = value
            key = value.replace("\\", "/").rsplit("/", 1)[-1]
        else:
            raise ValueError(f"표기=실경로 형식이 필요합니다: {value}")
        if not key or not actual or key in result and result[key] != actual:
            raise ValueError(f"비어 있거나 중복된 대응: {key}")
        result[key] = actual
    return result
