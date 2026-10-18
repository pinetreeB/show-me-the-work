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
The following uncertainty codes retain the extracted claim and never count as a match:

- `binding_ambiguous`: multiple line references and quotations lack a definite one-to-one correspondence; explicit `각각`/`respectively` lists can be checked in order. A candidate range beyond the known file boundary, or a quote absent from every observed candidate range, remains a definite mismatch; an unobserved candidate preserves uncertainty.
- `reported_claim`: a number or value attributes an earlier claim to an explicit third party and precedes a contrast such as `실제`/`actually`; first-person and the current report's own statements remain checkable. Literal K1/K5 quotations also remain checkable.
- `inherited_binding`: a range or value disagrees with a file inherited from a section or paragraph that also names another file; a single-file section remains checkable.
- `found_outside_scope`: a name missing from linked reads occurs in code at a supplied repository's designated commit (HEAD only when no commit was supplied). The source records commit, file, line, and whether the hit is a definition or code mention; documentation, string, and comment-only hits do not count.
- `repository_search_incomplete`: repository lookup failed or could not honor the claim's time, so absence cannot be confirmed.
- `proposed_name`: the atom names a proposed, hypothetical, or future addition rather than an existing definition.
- `item_token_only`: a review/item label followed only by a backtick token, number, or date does not establish an item description; explicit file/ordinal identifier row mappings remain supported.
- `self_assigned_item_id`: an item ID missing from the target may be a report-local label, so the full item claim remains unverifiable. If the target contains at least two IDs from the same prefix family and only the requested ID is missing, K9 remains `absent`.

A single literal directly following a list of candidate references is represented as
one K5 candidate-group atom, while each K1 keeps only its own range claim. The group
matches when any candidate's observed range contains the quote and mismatches once
when every candidate range is fully observed and rejects it. Missing, partial, prose-separated,
placeholder, or unordered multi-quote bindings remain `binding_ambiguous`; distinct
unordered quotations remain distinct unverifiable atoms.

K9 emits one atom for each structural item section. Its description spans from the
item ID through the line before the next item boundary, including continuation
lines. Backtick tokens inside that description continue through their normal K4/K6
extraction paths and do not multiply K9 atoms.

Before a K4/K6 missing-name verdict, fixed-string `git grep` searches the supplied
repository scope. A hit lowers the verdict to unverified; it does not verify a
definition or value. This does not relax explicit value mismatches, missing
definitions in a file, or literal quotation mismatches. Search errors also remain
unverified. Past/present factual statements about added or defined names continue
to be checked for absence.
Repository-only hits intentionally remain `found_outside_scope`; `token_match` is
reserved for a token observed in directly linked file content.
General `before`/`after` wording does not skip repository search. Only an explicit
revision that conflicts with the configured repository commit is skipped, and the
skip reason is recorded in the claim source.

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

- FIX8a2: `quote_mismatch`는 개별 후보 및 같은 파일의 인접·겹친 후보 연결 범위를 모두 대조한다. 각각의 개수 불일치도 후보 전체 부재는 확정하며, 괄호 안 인용은 바깥 참조 묶음과 구분한다.
- FIX8a2: 긴 줄은 전체 원문을 비교하며 K1의 결합 후보 인용 중 하나라도 일치하면 `quote_match`; 출처 괄호만으로 계측 대상을 정한 경우 `inherited_binding`, 명시된 남의 날조 서술은 `reported_claim`으로 보존한다. 식별자 안 `-1B` 등은 크기 원자가 아니다.

- FIX8c Phase 0: K1/K5 `quote_mismatch`는 읽은 모든 판 × 모든 후보 범위(인접 연결 포함)에 인용이 없고 각 관측이 완전할 때 확정한다. `source.quote_check`에 판 수·각 출처·검사 행렬을 남긴다.
- FIX8c: 판별 줄 위치/범위가 다르면 판 전체로 대조 범위를 넓히고 `whole_version_fallback`으로 공개한다. 어느 판에라도 정확한 인용이 있으면 기존 `observation_conflict` 미확정을 유지하며, 부분·실패 관측으로 전체 부재를 단정하지 않는다.

- Phase1 §0: 열린 후보 묶음의 K5는 원래 파일 전체·모든 판에서 인용을 검사한다. K1 줄 결합 및 복수 인용의 any-of 지지는 binding_ambiguous로 남기며 확실한 범위 초과는 보존한다.
- Phase1 §0: 괄호 출처의 계측 면제는 다른 대상 후보가 있을 때만, 보고 주체 판별은 동사형만 인정한다. 괄호 속 인용은 자체 참조가 없으면 바깥 참조 묶음 전체를 사용한다.
- Phase1 §0: 저장소 보조 검색은 소스 확장자 화이트리스트와 docs/reviews 제외를 적용한다. 데이터·문서·패치 파일은 found_outside_scope 근거가 아니다. K9의 같은 계열 항목이 하나뿐인 경우는 자체 라벨과 구별하지 못하는 알려진 한계다.

- Phase1 A: paraphrased_quote는 코드 인용의 한 호출 내 말줄임(R-ell), 실제 클래스 소유 속성의 한정자(R-qual), 같은 들여쓰기 수준의 타입 주석 생략(R-ann)을 구문으로 확인했을 때의 unverifiable이다. match나 PASS 근거가 아니다.
- R-ell은 고정 이름·값·조각 순서를 보존하고 서로 다른/중첩 호출의 인자를 합치지 않는다. X(...)만 있거나 의미 있는 조각이 3자 미만이면 적용하지 않는다. R-qual은 직접 소유한 속성, R-ann은 동일 수준의 동일 RHS에만 적용한다. 현재 구조 검증은 Python 구문으로 해석 가능한 코드에 한정한다.

### Phase1 assist — 오프라인 선별

`smtw claims assist --report report.md --codex transcript.jsonl --select-only --format json`은 판정을 변경하지 않고 참고 후보를 출력한다. `--aggregate`는 갈래별 총 후보/선별 건수와 제외 사유만 남긴다. 기본 `--limit 20`, 초과는 `deferred_count`이며 0은 집계 전용이다. 우선순위는 observation_conflict, paraphrased_quote, 숫자·인용 미추출 문장, range_only, 나머지 순서다.

pointer_missing/no_read_of_file/partial_range는 명시 SHA `--commit 저장소=SHA`가 있을 때만 후보가 된다. command_result_missing/binding_ambiguous/malformed_number/measurement_missing/repo_scope_unknown/commit_missing_file 및 match/absent/mismatch 원자는 제외한다. 미추출 여부는 같은 줄의 개별 문장/표 셀 단위로 대조한다. 선별 성공 종료 코드는 0이며 기존 check의 상태/요약/종료 코드를 바꾸지 않는다. 모델 실행기는 없다.
