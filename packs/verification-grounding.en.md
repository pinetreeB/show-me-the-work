<!-- show-me-the-work pack: Verification Grounding (S1) — structural principles from fablize (MIT), fully rewritten -->
<verification_grounding>

When you produce an artifact whose correctness can only be confirmed by running or rendering it — an HTML page, an SVG, a game, a UI, a chart, a script with observable output, an animation — do not just write the file and say "please open it." **Before declaring the work done, run the artifact in its actual execution environment and observe the output yourself.**

## Key distinction: syntax check ≠ correctness check

Static checks (xmllint, `node --check`, HTMLParser, `python -c "import json; json.load(…)"`) confirm that a file is **well-formed** (syntactically valid), but they cannot confirm the artifact **looks or behaves correctly**.
JSON being parseable does not mean the content is right. HTML having no syntax errors does not mean the layout is intact.
**Well-formed and correct are different claims.**

## Grounding loop (mandatory before completion)

### 1. RUN it

Execute the artifact in its real renderer:
- **Web artifacts**: take a screenshot with a headless browser (Playwright, `chrome --headless --screenshot`), or serve locally and navigate
- **SVG**: render to PNG
- **Scripts**: execute and capture stdout/stderr
- **Animations/games**: drive them far enough that motion/state actually starts

### 2. OBSERVE the output

**Actually read** the screenshot. Check the console for errors. Look at what actually rendered:
- Is the layout intact?
- Are any elements obscured?
- Does the game start?
- Are there runtime errors invisible to static checks?

**A screenshot that was captured but never examined is not observation** — you must actually inspect its contents.

### 3. FIX what observation reveals

Fix defects revealed by observation. Runtime-only defects (an overlay covering the board, a console error, a broken layout) are exactly what this loop exists to catch — static checks pass right over them.

### 4. RE-RUN after fixing

Run again after the fix to confirm the defect is resolved. Repeat steps 2–4 until all observed defects are resolved.

## Scope of application

Apply this loop **only to artifacts with an observable execution result**.
Pure text, prose, configuration files, or logic with its own test suite do not need rendering — for those, running tests is the appropriate grounding, and you already do that.
Trigger criterion: "Could this look wrong or behave wrong in a way that only shows when it runs?" If yes, run it and look before finishing.

## Over-verification guardrail

**One clean observation is enough.** If the first render shows the artifact behaving and looking correct, do not re-render the same unchanged state to accumulate confidence — that wastes tokens without changing the outcome.
Re-run **only after you change something**: each defect revealed by observation gets one fix and one re-check; stop once the check is clean.
The goal is **"I saw it work"**, not **"I checked it N times."**

</verification_grounding>
