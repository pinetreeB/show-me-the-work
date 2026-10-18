# Report claim checker

`smtw claims check` is an on-demand, deterministic advisory checker. It uses only
the Python standard library, calls no model or API, installs no hook, and imports
none of the legacy supervision modules. Reports remain subject to independent
review: matching text cannot establish that a conclusion is correct.

```powershell
python -m smtw claims check --report report.md --agy transcript_slice.jsonl --format json
python -m smtw claims check --report report.md --codex rollout.jsonl --repo ./project --commit "./project=0123456789abcdef"
python -m smtw claims check --report report.md --source-file "guide.md=./snapshots/guide.md@01234567" --path-alias "<SOURCE>=C:/sample/project"
python -m smtw claims check --report report.md --default-file "engine.py=./snapshots/engine.py"
```

`--agy` also accepts a conversation ID under `--agy-brain-root` (default
`~/.gemini/antigravity-cli/brain`). A transcript's adjacent `aux/` takes precedence
over the original `.system_generated/steps` and `.system_generated/tasks` layout.
Pointers are remapped into that root; transcripts never cause commands or URLs to
be executed. Missing pointers, failed reads, truncated output, ambiguous links,
unsupported source formats and damaged text produce `unverifiable` results.

`--agy-auto` searches that brain root for the report's exact path and matching
last successful full-write content. Zero or multiple matching conversations, or
a last partial edit whose final bytes cannot be established, remain INCOMPLETE.

Each `--source-file` is either `report-path=actual-path` or an actual path whose
basename is used for matching. `--repo`, `--commit`, `--source-file` and
`--path-alias` can be repeated. Matching uses normalized paths; absolute paths do
not fall back to unrelated basenames. A bare basename with several different file
candidates remains unverified, even if their contents agree. Current HEAD observations are separately annotated
and cannot verify a historical file claim. Explicit reference commits can supply
missing evidence unless the report explicitly names a conflicting time.

The extractor recognizes file/line references (K1), file counts/sizes (K2), commit
identities (K3), code identifiers (K4), literal quotes (K5), entity-bound values
(K6), internal counts (K7), successful URL reads (K8), document item identifiers
(K9), and direct agent claims of telephone/physical actions (K10, warnings only).
General prose, translations and illustrative code blocks are outside literal
checking. Each extracted atom retains its report line and original token text.

Rev2 also recognizes Korean/English line positions (`12행`, `12~14줄`, `L12`,
`line 12`, `줄 12`), Markdown numbers, and numbered table/list references. A unique
file can be inherited from the containing sentence, parentheses, table row, list
item, or preceding heading. Multiple candidates are never resolved by list order.
Line references bind adjacent quoted snippets or the following code block. Valid
references without quoted content have verdict `range_only`: they are not matches
and do not affect other claims' status. A report with only these neutral references
is INCOMPLETE because it has no substantive checked claim.

Rev3 restricts K1 backtick binding to numbered snippets (`N: content`) and adjacent
code-shaped fragments with at least two code symbols plus whitespace or separators.
Single identifiers, including `name()` and dotted names, remain K4 file-level token
claims. Backticks separated from a position by explanatory prose are not line quotes.
Explicit quotation marks and immediately following code blocks remain checked.

`--default-file "display-path=actual-file"` explicitly supplies one default source
for otherwise unbound line positions and numbered quotations. It does not replace
an explicit or ambiguous file context and does not infer a target from the report
heading or from a unique source. The same source mapping is available for explicit
references to that display path; conflicting `--source-file` mappings are errors.

Prose quotations normalize whitespace, quote styles, Markdown emphasis, backticks
and link formatting on both sides. Code blocks and numbered code quotations retain
their operators and formatting. File-linked identifiers use token boundaries;
unlinked identifiers still require both a code-like shape and explicit code context.
Setting limits are not file sizes. Entity values with multiple possible values
remain unverified.

Evidence comes from linked read-tool outputs, never user messages, final answers,
write arguments, generated echo output, report rereads or post-write rereads of
agent-authored files. agy batched results are ordered across intervening system
messages and cross-checked against paths/URLs. Task completion messages supply
control metadata only; task content must come from the corresponding log. Reads
after the report's last linked write serve only explicit current-state claims.
Literal conditional success markers (`&& echo DONE` or `; if ($?) {echo DONE}`)
do not discard the preceding read. The marker is removed from evidence content;
unconditional or dynamic echo commands remain excluded. Successful browser/MCP
navigate, read, snapshot and fetch results can establish URL visits when the result
identifies the page. Search results and argument-only URLs cannot establish visits.

Plain text/code and `.docx` paragraphs and tables are supported. Unsupported Word
regions (headers, notes, text boxes, revisions) prevent an absence conclusion when
the quote cannot be found in the supported body. Logs decode as UTF-8 then CP949;
replacement characters prevent conclusions that depend on the damaged region.

Line counts share one implementation: newline count (`wc -l`), content lines, and
view-file lines (including the final empty display line). An unspecified `N줄`
matches either content lines or display lines. File byte counts are never inferred
from transcript-output sizes. KB/MB are decimal; KiB/MiB are binary; no rounding
tolerance is added.
An explicitly displayed empty final view-file line also establishes content lines
as `Total Lines - 1`, including partial views. Linked PowerShell content counts and
`wc` measurements retain their distinct count conventions.
K2 uses transcript observations whenever that file was read in the eligible evidence
window. Missing measurements, failed reads or unresolved explicit times remain
unverified; current files, source snapshots and fixed commits cannot replace those
observations. Source-file/fixed-commit fallback is available only without a read.

A missing SHA is `unverifiable` with `repo_scope_unknown` unless the report explicitly
identifies one supplied `--repo` in the same sentence, line/table row or paragraph.
Repository paths, quoted names and names labelled `repo`/`repository`/`저장소` are
recognized; merely providing CLI repository candidates does not prove scope.
When scope is unique, object lookup uses that repository. A successful object/read
observation remains positive evidence; lookup failure is never proof of absence.

Exit codes are **0 PASS**, **1 FAIL**, **3 INCOMPLETE**, **2 execution error**.
`[미확인]`, `[추정]` and `추정:` affect the containing sentence/cell only; they do not
change individual verdicts. Such marked failures make the result INCOMPLETE,
except literal quotations and file/line references (K5/K1), which are not exempt.
No extracted claims is INCOMPLETE. K10 warnings never change status.

JSON includes `status`, `claims`, `timed_out`, `unchecked_count`,
`author_marked_review`, `warnings`, `summary`, and `elapsed_seconds`. Claims expose
`report_line`, `text`, `check_type`, `verdict`, `author_marked`, `exempt`, `source`,
`reason`, and `reason_code`. Codes distinguish missing reads (`no_read_of_file`),
partial ranges (`partial_range`), ambiguous bindings (`binding_ambiguous`), missing
fixed-commit files (`commit_missing_file`), historical references without matching
evidence (`historical_no_commit`), damaged text (`decode_damage`), missing auxiliary
files (`pointer_missing`), pending tasks (`async_incomplete`), and timeouts
(`timed_out`). Text output supplies the same locations and search scopes in Korean.
`verdict_reason_counts` adds a deterministic nested summary, keyed first by verdict
and then by `reason_code`; each verdict subtotal equals the corresponding `summary`
count, including neutral `range_only` claims.
`--time-limit` defaults to 60 seconds and covers source/transcript/repository work;
already discovered failures survive a timeout. Auxiliary logs are read lazily and
streamed when searched.

The conservative parser does not evaluate shell programs or JavaScript. It
recognizes literal Codex `exec_command`, web and MCP wrappers; dynamic compound calls and
ambiguous result batches need independent review. `line N: text` output from a
command naming exactly one file is accepted as a partial read, never as proof of
whole-file absence. Numeric claims without an explicit entity and semantic item
equivalence are intentionally unverified or unextracted. Precision/recall and
runtime must be measured on independently labelled reports before operational use.
