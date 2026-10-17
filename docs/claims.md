# Report claim checker

`smtw claims check` is an on-demand, deterministic advisory checker. It uses only
the Python standard library, calls no model or API, installs no hook, and imports
none of the legacy supervision modules. Reports remain subject to independent
review: matching text cannot establish that a conclusion is correct.

```powershell
python -m smtw claims check --report report.md --agy transcript_slice.jsonl --format json
python -m smtw claims check --report report.md --codex rollout.jsonl --repo ./project --commit "./project=0123456789abcdef"
python -m smtw claims check --report report.md --source-file "guide.md=./snapshots/guide.md@01234567" --path-alias "<SOURCE>=C:/sample/project"
```

`--agy` also accepts a conversation ID under `--agy-brain-root` (default
`~/.gemini/antigravity-cli/brain`). A transcript's adjacent `aux/` takes precedence
over the original `.system_generated/steps` and `.system_generated/tasks` layout.
Pointers are remapped into that root; transcripts never cause commands or URLs to
be executed. Missing pointers, failed reads, truncated output, ambiguous links,
unsupported source formats and damaged text produce `unverifiable` results.

Each `--source-file` is either `report-path=actual-path` or an actual path whose
basename is used for matching. `--repo`, `--commit`, `--source-file` and
`--path-alias` can be repeated. Matching uses normalized paths; absolute paths do
not fall back to unrelated basenames. A bare basename can have several candidates;
disagreement remains unverified. Current HEAD observations are separately annotated
and cannot verify a historical file claim. Explicit reference commits can supply
missing evidence unless the report explicitly names a conflicting time.

The extractor recognizes file/line references (K1), file counts/sizes (K2), commit
identities (K3), code identifiers (K4), literal quotes (K5), entity-bound values
(K6), internal counts (K7), successful URL reads (K8), document item identifiers
(K9), and direct agent claims of telephone/physical actions (K10, warnings only).
General prose, translations and illustrative code blocks are outside literal
checking. Each extracted atom retains its report line and original token text.

Evidence comes from linked read-tool outputs, never user messages, final answers,
write arguments, generated echo output, report rereads or post-write rereads of
agent-authored files. agy batched results are ordered across intervening system
messages and cross-checked against paths/URLs. Task completion messages supply
control metadata only; task content must come from the corresponding log. Reads
after the report's last linked write serve only explicit current-state claims.

Plain text/code and `.docx` paragraphs and tables are supported. Unsupported Word
regions (headers, notes, text boxes, revisions) prevent an absence conclusion when
the quote cannot be found in the supported body. Logs decode as UTF-8 then CP949;
replacement characters prevent conclusions that depend on the damaged region.

Line counts share one implementation: newline count (`wc -l`), content lines, and
view-file lines (including the final empty display line). An unspecified `N줄`
matches either content lines or display lines. File byte counts are never inferred
from transcript-output sizes. KB/MB are decimal; KiB/MiB are binary; no rounding
tolerance is added.

Exit codes are **0 PASS**, **1 FAIL**, **3 INCOMPLETE**, **2 execution error**.
`[미확인]`, `[추정]` and `추정:` affect the containing sentence/cell only; they do not
change individual verdicts. Such marked failures make the result INCOMPLETE,
except literal quotations and file/line references (K5/K1), which are not exempt.
No extracted claims is INCOMPLETE. K10 warnings never change status.

JSON includes `status`, `claims`, `timed_out`, `unchecked_count`,
`author_marked_review`, `warnings`, `summary`, and `elapsed_seconds`. Claims expose
`report_line`, `text`, `check_type`, `verdict`, `author_marked`, `exempt`, `source`,
and `reason`. Text output supplies the same locations and search scopes in Korean.
`--time-limit` defaults to 60 seconds and covers source/transcript/repository work;
already discovered failures survive a timeout. Auxiliary logs are read lazily and
streamed when searched.

The conservative v1 parser does not evaluate shell programs or JavaScript. It
recognizes literal Codex `exec_command` wrappers; dynamic compound calls and
ambiguous result batches need independent review. `line N: text` output from a
command naming exactly one file is accepted as a partial read, never as proof of
whole-file absence. Numeric claims without an explicit entity and semantic item
equivalence are intentionally unverified or unextracted. Precision/recall and
runtime must be measured on independently labelled reports before operational use.
