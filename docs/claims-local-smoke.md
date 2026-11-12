# Local judge D2: one sealed smoke run

This page describes a later operator-authorized run. Implementation tests use
fake openers only; reading this page does not authorize a server connection.

Before the run, obtain the environment owner's start signal and the coordinator's
line identifying the approved window and model. The owner checks the server,
resources, E9 release, and execution limits; this client never starts, stops, or
inspects server processes or GPUs. The agreed start conditions are GPU1 free
memory at least 20,000 MiB, RAM free at least 30 GiB, and no other llama-server;
only the environment owner confirms these. The agreed model is `judge-qwen38-27b`, context
16,384, one slot, reasoning off. The owner's running resource limits still need
to be supplied. Do not reuse the earlier probe's receipt or startup SHA.
With the server set to `--reasoning off`, requests omit `reasoning_format`.

From the repository root, set `SMTW_LOCAL_JUDGE_OUT` to the approved private output
directory, `SMTW_LOCAL_JUDGE_STOP` to the owner's stop-flag path, and
`SMTW_SERVER_START_ARGS_SHA256` to the current startup arguments' 64-hex SHA from
the owner. Confirm that the stop flag does not exist; do not automatically remove
an existing stop signal. Then run this **one line once**, after approval:

```powershell
python scripts/claims_local_smoke.py --provider local --packets tests/fixtures/local-judge-d1/packets --scope-file tests/fixtures/local-judge-d1/scope.json --seal tests/fixtures/local-judge-d1/seal.json --out-root "$env:SMTW_LOCAL_JUDGE_OUT" --max-calls 12 --local-address http://127.0.0.1:8099 --local-model judge-qwen38-27b --local-deadline-seconds 180 --local-input-tokens 14000 --local-output-tokens 1024 --local-json-mode schema --local-protocol openai-compatible --local-seed 42 --local-temperature 0 --local-reasoning off --local-stop-file "$env:SMTW_LOCAL_JUDGE_STOP" --local-server-start-args-sha256 "$env:SMTW_SERVER_START_ARGS_SHA256"
```

The command verifies seal SHA
`eeb47f0d036a666d2e86237c2aa5dd8f75164c53658215c90d5ec4e8e5e27e74`,
all fifteen sealed data files, and twelve exact one-item IDs before opening any
connection. It then performs health, model, props, and full-prompt token checks.
There is no separate manual health command to accidentally run twice. Generation
is sequential, at most twelve requests, with a 180-second per-item deadline and
15-minute run limit. Any local error ends the run without retries or truncation.

To stop, the coordinator creates the agreed stop file. The client interrupts its
wait, attempts to close active I/O, and marks remaining items `not_evaluated`.
After a timeout or disconnect, server compute
termination remains unknown: **hold the next request**, inform the environment
owner through the established coordinator channel, and wait for a new decision.
The client does not send messages or stop the server on its own.

Read `smoke-summary.json` in the printed run directory: the twelve-row comparison
shows expected, normalized, verified, and not-evaluated outcomes. Read
`local_error_count`, `not_evaluated_count`, `quote_not_in_excerpt`, and
`missing_quote_or_ref` together; omitted/error items stay in the denominator.
`binding_ambiguous_capped` counts local `binding_ambiguous` support judgments forced to `not_enough_evidence`, with that reason code retained in normalized verification results.
This cap leaves contradicted/NEI judgments and the team path unchanged; missing references still follow the existing verification rule.
Failures also show `error`, `stage`, `http_status`, and available server/HOLD notices in that summary.
`calls.jsonl` and `run-summary.json` contain identity, code/schema/startup hashes,
token method/counts, usage, timing, and safe failure stages. Raw prompt, response,
error body, and response prefixes are not stored. Accepted normalized judgments
and verified evidence are deliberately retained.

Keep the approved artifacts until 2026-11-30; the coordinator owns deletion.
Four toy items per class do not establish model accuracy or production readiness.
The ordinary collector entry point is `python -m smtw claims judge --provider local`;
the smoke wrapper adds the seal and separate expected-answer comparison gates.
