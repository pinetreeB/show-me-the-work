"""Compare offline packet content independently of batching and random excerpt order."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def normalize(folder: Path) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Map item id to excerpt sets; track provenance metadata separately."""
    content: dict[str, set[str]] = {}
    metadata: dict[str, set[str]] = {}
    files = sorted(folder.glob("packet-*.json")) + sorted(folder.glob("*-packets/packet-*.json"))
    if not files:
        raise ValueError("No packet files found")
    for path in files:
        packet = json.loads(path.read_text(encoding="utf-8"))
        for item in packet["data_block"]:
            item_id = item["id"]
            content.setdefault(item_id, set())
            metadata.setdefault(item_id, set())
            for excerpt in item["excerpts"]:
                # Ref and version numbering can change with a shuffle. Revision
                # and observation are provenance, so report them separately.
                body = {key: excerpt[key] for key in ("path", "source", "text")}
                digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                content[item_id].add(digest)
                provenance = {key: excerpt.get(key) for key in ("revision", "observation", "version")}
                metadata[item_id].add(json.dumps(provenance, sort_keys=True, ensure_ascii=False))
    return content, metadata


def compare(left: Path, right: Path) -> dict:
    before, before_meta = normalize(left)
    after, after_meta = normalize(right)
    shared = before.keys() & after.keys()
    changed = sorted(key for key in shared if before[key] != after[key])
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    return {
        "same_content": not (changed or added or removed),
        "items_before": len(before), "items_after": len(after),
        "changed_ids": changed, "added_ids": added, "removed_ids": removed,
        "metadata_changed_ids": sorted(key for key in shared if before_meta[key] != after_meta[key]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.left, args.right)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Do not echo packet contents on malformed-input errors.
        print(json.dumps({"error": type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["same_content"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
