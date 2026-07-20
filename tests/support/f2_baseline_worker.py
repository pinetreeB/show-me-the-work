from __future__ import annotations

import json
from pathlib import Path
import sys
import time

from core.agent_log import ledger_transaction
from core.provenance_snapshot import snapshot_id_for
from core.provenance_store import initialize_turn_baseline, load_turn_baseline
from core.provenance_types import EntryKind, ManifestEntry, Snapshot


def _candidate(root: Path, label: str) -> Snapshot:
    entry = ManifestEntry(
        path="app.py",
        canonical_key="app.py",
        file_type=EntryKind.REGULAR,
        size=1,
        mtime_ns=1,
        mode=0o644,
        digest=f"digest:{label}",
    )
    entries = (entry,)
    return Snapshot(
        root=root,
        entries=entries,
        reparse_observations=(),
        issues=(),
        snapshot_id=snapshot_id_for(entries),
        scope_policy_id="policy:f2-worker",
        generated_patterns=(),
    )


def main() -> int:
    root = Path(sys.argv[1])
    label = sys.argv[2]
    (root / f"ready-{label}").write_text("ready", encoding="ascii")
    deadline = time.monotonic() + 30
    while not (root / "go").exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("parent did not release workers")
        time.sleep(0.005)
    with ledger_transaction(str(root)) as transaction:
        outcome = initialize_turn_baseline(
            root,
            "host:session:agent",
            "turn",
            True,
            _candidate(root, label),
            transaction,
        )
    winner = load_turn_baseline(root, "host:session:agent", "turn")
    assert winner is not None
    print(json.dumps({"outcome": outcome.value, "winner": winner.snapshot_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
