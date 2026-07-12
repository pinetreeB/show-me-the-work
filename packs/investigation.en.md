<!-- show-me-the-work pack: Systematic Investigation Protocol (S3) — structural principles from fablize (MIT), fully rewritten -->
<!-- compliance.py marker contract: model output from turns where this pack is injected will be parsed for adherence -->
<systematic_investigation_protocol>

When handling debugging, incident analysis, or failures of unknown cause, follow these 6 steps in order.

## Step 1: Reproduce

Before reading any code, **execute the failing case yourself** and observe the actual output with your own eyes.
A hypothesis formed without reproduction is speculation. Record the reproduction result (error message, stack trace, observed output).
If reproduction is impossible — environment differences, non-deterministic failure, etc. — state "reproduction not possible" explicitly and proceed to Step 2.

## Step 2: Formulate competing hypotheses (minimum 3)

For a single symptom, formulate **at least 3 hypotheses with distinct root causes**.
The most prominent signal in the logs is not necessarily the root cause — it is merely one hypothesis among several.
Do not rush toward the first "plausible" cause that comes to mind. It may be correct, but until verified, it remains a hypothesis.

Number each hypothesis in the following format:
```
Hypothesis 1: [cause description]
Hypothesis 2: [cause description]
Hypothesis 3: [cause description]
```

## Step 3: Gather evidence per hypothesis

For each hypothesis, **define what evidence would confirm or refute it first**, then collect that evidence.
Read the relevant code paths end to end — do not stop midway with "this part is probably it."
When citing evidence, use the following prefix:
```
Evidence: [filename:line] [observed fact]
```
Update your confidence (high/medium/low) per hypothesis as evidence accumulates.

## Step 4: Trace the causal chain

Do not stop at the first plausible cause.
Ask one level deeper: "How did this cause produce this symptom?"
Check: "If I only remove the visible trigger, does the defect remain latent?"
A fix that makes the test pass is not necessarily a fix that removes the defect — verify that you have reached the root of the causal chain.

## Step 5: Verify before and after

**Before** modifying code, confirm the root cause with evidence.
**After** the fix, demonstrate that the failure mode itself is gone — not merely that the triggering condition no longer occurs in this environment, but that the underlying defect has been resolved.

## Step 6: Report rejected hypotheses

Include **both adopted and rejected hypotheses** in your report.
For rejected hypotheses, use the following prefix:
```
Rejected: [Hypothesis N] — [rejection rationale and refuting evidence]
```
Omitting rejection reports forces others (or your future self) to re-investigate the same hypotheses.

---

### Report style: body for humans, markers for the gate

Split the final report into **two layers**. Your reader may not be a developer.

1. **Body (first, in plain language)** — explain what was wrong, what changed, and what it means for the reader.
   Keep code identifiers, file paths, line numbers, and regexes out of the body. If a technical term is unavoidable, attach a one-line gloss.
   (e.g. "The gate saw two file names and mistook the request for a big multi-part task, so it kept blocking the first steps" — no class name needed in that sentence.)
2. **Investigation record (bottom, compact)** — the marker block exists to satisfy the gate. Compress each hypothesis/evidence/rejection to **one line each**.
   The gate only checks that the markers exist. Do not turn the whole report into a technical document just to fill markers — that is the most common failure.

However technical the investigation was, if the reader has to ask "so what actually happened?", the report has failed.

When following this protocol, your output **must** include these markers:
- `Hypothesis 1:`, `Hypothesis 2:`, `Hypothesis 3:` — numbered hypotheses (**required: minimum 3**)
- Lines starting with `Evidence:` — evidence citations (**required: at least 1** · recommended: 1 per hypothesis)
- Lines starting with `Rejected:` — rejection reports (**required: at least 1** · recommended: one per non-adopted hypothesis)

These markers are automatically parsed by the pack compliance gate (N1) to determine protocol adherence.
**Exemption**: an answer-only turn that changes no files (you only answered a question) may reply in plain prose without markers — the gate won't demand them either. Markers are mandatory for turns that **investigated and then actually modified something**.
That said, if you did real investigation (reproduction, code tracing) without changing files, recording the markers is still **recommended** — it becomes the starting point when a later turn makes the fix, and it prevents the bad habit of retroactively inventing the investigation in the fix turn.
The parser recognizes both English markers (`Hypothesis N:` / `Evidence:` / `Rejected:`) and Korean markers (`가설 N:` / `증거:` / `기각:`) — bilingual.
When filling in the markers, don't lead with meta-commentary like "Here is my response organized with the required markers" or "Reorganizing to include the investigation pack markers" — that framing is itself unnecessary filler. Start directly with the hypotheses.
Output that contains only conclusions without markers is judged as non-compliant.

</systematic_investigation_protocol>
