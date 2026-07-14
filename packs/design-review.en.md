<!-- show-me-the-work pack: Design Review (v1.0) — the single source of truth (SSOT) for rules and tokens is
     ~/.claude/DESIGN-OPS.md. This pack does not restate that content; it carries only a summary + warning signals
     (for detail, see "DESIGN-OPS §N"). Structural principles follow the existing show-me-the-work pack family
     (verification grounding, investigation, completion). -->
<!-- Stop gate target: this pack is injected together with the verification-grounding pack. The "evidence" of design
     compliance is the recorded call to a render-verification tool (playwright/chrome-devtools), and the Stop gate
     judges that record. -->
<design_review>

If this pack is injected, this task has already been classified as UI artifact creation/modification (.html/.tsx/.vue/.svelte/.css, or domain=UI). Before touching UI, **read the rules/tokens SSOT `~/.claude/DESIGN-OPS.md`** — what follows is only a summary and warning signals; the authoritative source for any judgment is that file. If the project has `design/tokens.*` (§2), inherit those tokens; if not, the bootstrap (§2.0) must come first.

## 1. Global floor — warning signals when you break it (see DESIGN-OPS §1)

Below is the immutable floor for all Korean-language UI. The rule text and numbers live in §1 (SSOT); here we list only the **signals that you are in violation**:

- **Font (§1.1)**: body Pretendard, code monospace. Signal — loading a new arbitrary web font is a violation.
- **Type ceiling (§1.2)**: hero / largest heading ≤ 2.7rem (a project token may redefine this). When size is ambiguous, choose the **smaller**; large decorative type is rejected by default. "The font is so big it looks tacky" is real feedback rejected twice in this family — distrust the urge to scale up.
- **Line breaking (§1.3)**: global `word-break: keep-all` + heading `text-wrap: balance`. Signal — **a line break in the middle of a word** is always a violation.
- **Removing AI-smell (§1.4)**: Signal — more than one gradient per hero, decorative emoji, purple (#7C3AED-ish) as the sole brand color, generic stock copy/hero. But this is not a final automatic verdict — filter it once, and leave the final call to a **human (intervention ③)** (this pack §2-c).
- **Color (§1.5) · spacing (§1.6) · dark mode (§1.8)**: Signal — raw hex in CSS color properties, raw px in spacing, hardcoded dark-mode colors. **Reference tokens only.** The exception boundary (token source files, `0`, hairlines, chart colors, allowlist, etc.) is defined by DESIGN-OPS §2.
- **Contrast · accessibility (§1.7)**: WCAG AA (body 4.5:1, large text 3:1) or better. Signal — do not let contrast alone stand in for accessibility. It also covers keyboard navigation, `focus-visible`, `prefers-reduced-motion`, touch targets ≥44px, and semantic markup.
- **Motion (§1.9)**: 200–300ms ease by default. Signal — excessive animation, ignoring `prefers-reduced-motion`.
- **Render verification (§1.10)**: UI artifacts get actually run → observed → fixed → re-run. **A static check is not observation** (this pack §3).

## 2. The verdict method differs per item — most cannot be claimed "checked" statically (see DESIGN-OPS §4)

The 8 items of the §4 checklist split into three verdict methods. **This distinction is the core of this pack** — for most items you cannot claim a pass by looking at the file alone:

- **(a) Static — judged by lint/AST**: ① zero hardcoded tokens (§4-1) · ⑧ component-state *spec* exists · ⑥ dark-mode *token reference*. Checkable from the file alone (`fable_lite check --design` catches these — implementation is Phase 2).
- **(b) Judged only after rendering + observing**: ② heading ≤ ceiling (computed style — static CSS misses `clamp()`, inheritance, responsive) · ③ zero mid-word breaks (line boxes) · ④ WCAG AA + a11y (axe, per state) · ⑥ dark-mode *actual contrast* · ⑦ responsive overflow (per viewport) · ⑧ per-state render (hover·focus·disabled·loading·empty·error). **These items are decided only by rendering and looking with your eyes.**
- **(c) Human eye required — not an automatic gate**: ⑤ AI-smell (§1.4). With no threshold or reference image, and given a judging AI's self-pass bias, this is not decided by a binary automatic verdict. Filter once by the §1.4 observable criteria; the final call is a human (intervention ③) saying "good / this is off."

## 3. The evidence of a finished mockup is a render-verification tool call — not words

The (b) items are confirmed in the **OBSERVE step** of the co-injected `verification-grounding` pack. Once the mockup is built, do not just write the file and say "the rules are followed":

- With a render-verification tool (playwright / chrome-devtools), actually render and observe screenshots at the **mobile 375 · desktop 1280 viewports, light/dark themes**.
- **Verify with your eyes** that computed type is within the ceiling, there are no mid-word breaks, contrast is AA or better, and per-state (hover/focus/disabled/loading/empty/error) renders are intact.
- **Do not declare in words** "I used tokens only · I matched the contrast." A static check and a self-declaration are not observation. Phrases that simulate confidence without observation ("should be fine," "should be AA") are banned — if you didn't verify it, say you didn't.

The point of this pack is not to repeat the rules — it is to **make the actual compliance with DESIGN-OPS visible through rendering.**

---

### Gate contract (design Stop gate · opt-in)

The design gate is project opt-in (OFF by default — turn it on with the env var `FABLE_LITE_DESIGN_GATE=1` or `enabled: true` in `design/gate.config`). When it is on and a turn changed a UI file (`ledger.design_required` AND `design_touched`), the Stop hook judges two things:
- ① `fable_lite check --design` (layer A, static lint) fails → block ("design rule violation: `<file:line>`. See DESIGN-OPS §N").
- ② no recorded call to a render-verification tool (layer B) → block ("UI change with no render observation. Confirm with a screenshot before finishing").

Consecutive blocks are capped at 2, then pass through (fail-open). Bash-only, investigation-only, and question-only turns are not blocked. On pass, the check result and screenshot paths are recorded as completion evidence in the ledger.
Note: the layer-A static lint (`design_lint`) and this Stop design-block are Phase-2 items that work only after show-me-the-work is re-activated — until then this pack acts only as soft discipline injection, so compliance is on you even when no gate catches it.
This gate looks only at **the tool-call record**, not your "claim sentences" — talking your way around it does not work. Follow this pack's §1–§3 yourself and you will never trip it.

</design_review>
