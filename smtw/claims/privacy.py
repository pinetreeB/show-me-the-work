"""Local privacy filters. Never render blocklist entries or matched text."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import warnings


def compact(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = "".join(char for char in value if not char.isspace()
                    and unicodedata.category(char) != "Cf" and char not in "-_.·")
    # Compatibility/halfwidth consonants normalize to leading jamo, including
    # consonants used as finals. Resolve those positions before NFC composition.
    value = unicodedata.normalize("NFD", value)
    finals = (1, 2, 4, 7, 0, 8, 16, 17, 0, 19, 20, 21, 22, 0, 23, 24, 25, 26, 27)
    clusters = {(1,19):3, (4,22):5, (4,27):6, (8,1):9, (8,16):10,
                (8,17):11, (8,19):12, (8,25):13, (8,26):14, (8,27):15, (17,19):18}
    output, index = [], 0
    def vowel(at):
        return at < len(value) and 0x1161 <= ord(value[at]) <= 0x1175
    while index < len(value):
        lead = ord(value[index]) - 0x1100
        if not (0 <= lead < 19 and vowel(index + 1)):
            output.append(value[index])
            index += 1
            continue
        middle = ord(value[index + 1]) - 0x1161
        index += 2
        tail = 0
        if index < len(value):
            number = ord(value[index])
            if 0x11A8 <= number <= 0x11C2:
                tail = number - 0x11A7
                index += 1
            elif 0x1100 <= number < 0x1113 and not vowel(index + 1):
                tail = finals[number - 0x1100]
                if tail:
                    index += 1
                    if index < len(value) and 0x1100 <= ord(value[index]) < 0x1113 and not vowel(index + 1):
                        pair = (tail, finals[ord(value[index]) - 0x1100])
                        if pair in clusters:
                            tail = clusters[pair]
                            index += 1
        output.append(chr(0xAC00 + (lead * 21 + middle) * 28 + tail))
    return unicodedata.normalize("NFC", "".join(output))


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
    lines = (line.split("#", 1)[0] for line in text.splitlines())
    entries = tuple(dict.fromkeys(value for line in lines if (value := compact(line))))
    if any(len(entry) < 2 for entry in entries):
        warnings.warn("blocklist contains entries shorter than two characters", UserWarning, stacklevel=2)
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
