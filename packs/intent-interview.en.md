<!-- show-me-the-work pack: Intent Gate (v1.1) — replaces the "understanding intent from an ambiguous
     instruction" part of Fable-style discipline with a procedure instead of a capability.
     Structural principles follow docs/design/intent-gate.md (frozen design). -->
<!-- PreToolUse target: if ledger.intent_required=true and .fable-lite/intent.json does not exist,
     Edit/Write/MultiEdit/NotebookEdit are blocked (capped at 2 blocks, fail-open). Bash is not gated. -->
<intent_gate>

This pack being injected means this request has already been judged ambiguous. You do not need to re-judge whether it's ambiguous. Your job right now is not to start the work — it's to pin down intent first.

## 1. Decide what to ask — skip anything already clear

Of the 3 slots below, **do not ask about anything the prompt already makes clear.** Only genuinely uncertain slots are worth asking about:
- **Goal**: what outcome is actually wanted
- **Scope**: what's OK to touch (which files/features/areas)
- **Non-goals**: what must NOT be touched / doesn't need to be done

## 2. Ask one at a time (one question, one answer) — multiple-choice first, no meta-commentary

### Decision rule
1. Is there still an uncertain slot among the 3 above, and have you asked fewer than 3 questions so far? If not (everything's clear, or you've already asked 3), go to step 3.
2. Pick **just one** of the uncertain slots, ask it as `Confirm N:`, and end the turn on that question (a sentence ending in a question mark). Do not list several at once — one at a time.
3. Once the user answers, go back to 1 and re-evaluate.

Start the question directly — no meta-commentary first ("Let me ask you something," "I need to confirm a few things," etc.):
```
Confirm N: [the question]
```
**Prefer multiple-choice** so a non-developer can answer easily — like "Should I go with A, or B?" Use an open-ended question only when multiple-choice genuinely doesn't fit.

## 3. Once you have what you need, don't restate it — just run intent set

Don't paraphrase the answers back ("Got it," "So to summarize...") — that's filler in itself. As soon as you have answers to whatever you needed to ask (or didn't need to ask anything to begin with), **call the exact absolute-path `fable-lite-cli.py` command shown in the hook context or block message**. Fill only the `--goal`, `--scope`, and optional `--non-goal` values. When you are working directly from the repository root, `python -m smtw intent set ...` is also valid, but in plugin-installed projects prefer the displayed absolute-path command.
`--scope` can be a file path (globs are fine, e.g. `"src/auth/**"`), or — if a specific file still isn't identified — the natural-language area the user described (e.g., `"the login form"`).
Running this command is the confirmation itself — until it runs, intent stays unconfirmed and Edit/Write-class tools keep getting blocked. Bash isn't blocked before intent is confirmed, so looking at the code first (to ask fewer questions) is fine too.

## 4. If the user says "just do it" / "use your judgment": declare your assumptions and proceed

Don't ask again. Instead, explicitly state the assumption(s) you're adopting:
```
Assumed N: [the assumption you're adopting]
```
Then record it with the `--assumed` flag and proceed:
```
<the exact intent set command shown in context/block message> --assumed
```
The record must clearly show what was assumed, so it can be corrected later if it turns out wrong — never slide past this silently without an `Assumed N:` line.

---

### Example (full flow — a lower-tier model should follow this pattern exactly)

**User**: "Can you fix this thing"

**Assistant** (turn 1 — asks exactly one question, then ends the turn):
```
Confirm 1: Which file/feature does "this thing" refer to? (e.g., the login form we were just discussing, or somewhere else?)
```

**User**: "The login form — nothing happens when I click submit"

**Assistant** (turn 2 — scope is now clear, only one non-goal question remains):
```
Confirm 2: Should I fix just the behavior, or is it OK to also touch the design/styling?
```

**User**: "Just the behavior. Don't touch the design"

**Assistant** (turn 3 — answers are all in, so no verbal recap — call the tool directly):
```
<use the displayed absolute-path fable-lite-cli.py command> --goal "Fix unresponsive submit button on login form" --scope "login form" --non-goal "style/design changes"
```
(From here, proceed with the investigation pack's reproduce-and-diagnose procedure.)

---

### Gate contract (PreToolUse)

Attempting Edit/Write/MultiEdit/NotebookEdit while `.fable-lite/intent.json` doesn't exist gets blocked by PreToolUse with the reason "intent has not been confirmed" — this clears only once you complete step 3 (finish questioning, then `intent set`) or step 4 (declare assumptions, then `--assumed`). Blocking is capped at 2 times before it passes through (no infinite trap). Bash is not covered by this gate — investigate and reproduce freely even before intent is confirmed.

</intent_gate>
