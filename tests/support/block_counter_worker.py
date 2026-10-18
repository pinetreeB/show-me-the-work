from __future__ import annotations

import json
from multiprocessing.connection import Client, Connection
import os
import sys


def _send(channel: Connection, status: str, **details: str) -> None:
    channel.send_bytes(json.dumps({"status": status, **details}).encode("utf-8"))


def main() -> int:
    if os.environ.get("BLOCK_COUNTER_WORKER_EXIT_BEFORE_CONNECT"):
        print("forced exit before counter barrier", file=sys.stderr)
        return 23
    channel = Client((sys.argv[1], int(sys.argv[2])), authkey=bytes.fromhex(sys.argv[3]))
    try:
        channel.send_bytes(sys.argv[4].encode("ascii"))
        _ = channel.recv_bytes()
        from core.contract import evaluate_pretool_contract
        from core.verify_state import evaluate_stop

        actions = {"stop": evaluate_stop, "pretool": evaluate_pretool_contract}
        result = actions[sys.argv[5]](json.loads(sys.argv[6]))
    except Exception as exc:
        _send(channel, "error", error_type=type(exc).__name__, message=str(exc))
    else:
        _send(channel, "ok", decision=str(result["decision"]))
    finally:
        channel.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
