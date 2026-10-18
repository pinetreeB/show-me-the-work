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
Line references bind adjacent quoted snippets or the following code block, including
one intervening blank line. Two blank lines or intervening prose break that link.
A proposal or recommendation immediately before the fence also leaves the reference
as `range_only`; proposed code is not a quotation of the existing source. Valid
references without quoted content have verdict `range_only`: they are not matches
and do not affect other claims' status. A report with only these neutral references
is INCOMPLETE because it has no substantive checked claim.

Rev3 restricts K1 backtick binding to numbered snippets (`N: content`) and adjacent
code-shaped fragments with at least two code symbols plus whitespace or separators.
Single identifiers, including `name()` and dotted names, remain K4 file-level token
claims. Backticks separated from a position by explanatory prose are not line quotes.
Explicit quotation marks and immediately following code blocks remain checked.
Comma-separated references such as `engine.py:2, 5-7` create one K1 atom per
position/range. A standalone backtick position such as `5줄`, or `5줄에서/의/은`,
is not a file line count. Counts require size context: `총`, `전체`, parentheses,
`줄짜리`, a line-count column, or a number directly following the file name.
Date-shaped eight-digit numbers are not short SHAs without an explicit commit,
SHA or hash prefix. Quote delimiters are paired before the eight-character K5
threshold is applied, preventing short quotations from reversing quote boundaries.

`--default-file "display-path=actual-file"` explicitly supplies one default source
for otherwise unbound line positions and numbered quotations. It does not replace
an explicit or ambiguous file context and does not infer a target from the report
heading or from a unique source. The same source mapping is available for explicit
references to that display path; conflicting `--source-file` mappings are errors.

Prose quotations normalize whitespace, quote styles, Markdown emphasis, backticks
and link formatting on both sides. Ellipses (`…` or `...`) split a prose quotation
into nonempty fragments that must occur in order, without reusing the same source
characters. Ellipsis matching requires at least one normalized fragment of eight
characters; weaker fragments keep the literal comparison. Code blocks and numbered code quotations retain
their operators and formatting. File-linked identifiers use token boundaries;
unlinked identifiers still require both a code-like shape and explicit code context.
Inherited paths do not turn general words, tool names or repository names into code
claims. A K4 existence claim in a directly related negative or recommendation
context remains `unverifiable` with `negated_context`, rather than disappearing.
This includes the listed polite Korean endings and adjacent English negations;
the context is evaluated for each identifier, not spread across the sentence.
Ordinary factual wording such as an added function, a call without errors, or use
under an `if` condition does not suppress the claim. Context words come from prose outside paths and quoted tokens; a filename
such as `config.py` cannot supply its own code context. Without a supplied transcript, a repository search miss for an unlinked
identifier remains unverified; search scopes list only the inputs actually searched.
Setting limits are not file sizes. Entity values with multiple possible values
remain unverified.
Numbers described as file contents are entity values, not dimensions of that file.
JSON array/object values are compared structurally; incomplete values or duplicate
JSON keys remain unverified. Prose substring matching is applied before truncating
the search window, including quotations near the beginning of a long source line.

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
An explicit command claim such as `` `SYNTAX_OK` (exit 0) `` can use the actual
conditional marker as control evidence. Marker/exit metadata never establishes a
file identifier or quotation. This also applies to syntax-check commands without
file-content output. An asynchronous marker requires its linked completed task log;
missing/failed results and post-write self-reads retain their exclusion rules.
Self-modification detection recognizes only the enumerated forms.
Recognized shell writes include in-place sed/perl edits, git apply/am, patch, tee,
PowerShell Set-Content/Out-File and output redirection.
Shell wrappers are recognized at each command position (line start or after `;`,
`&&`, `||`, `|`, or `&`), including `sudo`, `env`, `exec`, `time`, and `nohup`
prefixes. Supported command flags are bash/sh letter bundles containing `c`
(`-c`, `-lc`, `-ec`, `-xc`), cmd `/c` or `/k`, and PowerShell/pwsh `-c` or
`-Command` (including `-Command:`), without case sensitivity. Quoted command
arguments and shell argument lists are unwrapped to at most three nested levels.
Bash/sh consume values for `-o`, `-O`, `+o`, `+O`, `--rcfile`, and `--init-file`;
other `+[a-z]+` bundles and `--long` options are skipped, and the first non-option
stops wrapper detection. PowerShell/pwsh skip an unlisted `-option` and its immediately
following non-option value, while `-File`/`-f` stops wrapper detection.
`zsh`, `dash`, and `ksh` are not shell wrappers, and nesting beyond three levels is
ignored without a warning. Quoted `env` assignments and quoted executable paths
such as `& "C:\Program Files\PowerShell\7\pwsh.exe"` are not recognized as wrappers.
Other quoted text remains masked when identifying redirection and patch commands.
PowerShell `-EncodedCommand` is not decoded and remains outside this support.
Reads of explicit write targets after the write cannot serve as independent evidence.
Null-device redirection, descriptor duplication and arrows are not file writes;
patch applicators must occur in command position, not in filenames or quoted text.
Only actual patch applicators and variable-target redirections may exclude all
subsequent file reads; warnings report how many reads this exclusion affected.
The patch forms include `git -C <directory> apply/am`, and variable redirections
include `$env:` paths. Variable targets in `-Path`, `tee $p`, or `WriteAllText`
remain unresolved and do not trigger global exclusion.
Other unresolved writes do not erase the rest of the conversation's evidence.

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
Linked view-file exports and completed measurement logs are loaded on demand.
An empty or whitespace-only final display line establishes `Total Lines - 1`;
missing or differently bound auxiliary files cannot establish a measurement.
If the last display row was not obtained, a count of `Total Lines - 1` is
`unverifiable` with `partial_range`. A compatible linked content-line measurement
can resolve it. For this partial-view boundary, a completed same-file `wc -l`
measurement matching the claim can also resolve it (`linked_line_measurement`);
this records a command-count comparison, not proof of the unseen final row.
An isolated `wc -l` measurement does not establish an unspecified content-line count,
and a known nonempty last row is never overridden by this fallback.
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
K8 URL failures retain their individual verdicts and are also warnings: they make
the report INCOMPLETE, never FAIL by themselves. A separate substantive failure
still makes the report FAIL. Unexpected checker exceptions produce ERROR and exit
2 (JSON when requested); KeyboardInterrupt is not swallowed. Numeric extraction
requires a leading digit and never treats a hexadecimal token's `b` as a byte unit.
Comma-only file metrics or arithmetic operands remain `unverifiable` with
`malformed_number`; checking continues for the other atoms in the report.

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
