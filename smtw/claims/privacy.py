"""Local privacy filters. Never render blocklist entries or matched text."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import unicodedata


def compact(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in value if not char.isspace() and unicodedata.category(char) != "Cf")


@dataclass(frozen=True)
class Blocklist:
    entries: tuple[str, ...] = field(repr=False)
    sha256: str = field(repr=False)

    def matches(self, text: str) -> bool:
        value = compact(text)
        return any(entry in value for entry in self.entries)

    def metadata(self) -> dict:
        return {"count": len(self.entries), "sha256_prefix": self.sha256[:8]}


def load_blocklist(path: Path | None, *, required: bool = False) -> Blocklist | None:
    if path is None:
        if required:
            raise ValueError("--blocklist-file is required for work-document sources")
        return None
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise ValueError("blocklist file is unavailable or invalid") from None
    entries = tuple(dict.fromkeys(compact(line) for line in text.splitlines() if compact(line)))
    if not entries:
        raise ValueError("blocklist must contain at least one name")
    return Blocklist(entries, hashlib.sha256(raw).hexdigest())


def needs_blocklist(scope) -> bool:
    return bool(scope and (any(scope.work_roots.values()) or scope.checkpoints))


def business_document(text: str) -> bool:
    value = unicodedata.normalize("NFKC", text).casefold().replace("\\", "/")
    return bool(re.search(
        r"(?<![\w])(?:one\s*drive[^/\r\n]*|documents)(?:/|\ufffd)[^\"'<>\r\n]*?"
        r"\.(?:docx|xlsx|pdf|hwp)(?![a-z0-9])", value))


def privacy_reason(text: str, blocklist: Blocklist | None) -> str:
    if blocklist and blocklist.matches(text):
        return "skipped_blocklist"
    if business_document(text):
        return "skipped_business_doc"
    return ""


def validate_packet_privacy(folder: Path, manifest: dict, blocklist: Blocklist | None) -> None:
    metadata = blocklist.metadata() if blocklist else None
    if manifest.get("blocklist") is not None and manifest["blocklist"] != metadata:
        raise ValueError("blocklist differs from packet generation; regenerate packets")
    # Check ALL packets before the first call and again immediately before every call.
    for entry in manifest["packets"]:
        text = (folder / entry["file"]).read_text(encoding="utf-8")
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for key, child in value.items():
                    yield key
                    yield from strings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from strings(child)
        decoded = "\n".join(strings(json.loads(text)))
        if privacy_reason(text + "\n" + decoded, blocklist):
            raise ValueError("packet privacy gate refused the entire run")
