# D1 synthetic smoke v1

These twelve independent toy items contain no corpus or customer material.
`packets/` holds twelve fixed one-item packets; `answers.json` holds the separate
fixed expected verdict, truth, evidence adequacy, and exact quote anchor.
Do not send answers, truth, adequacy, group IDs, or this README to a model.

`seal.json` pins all fifteen data files and the unchanged judge contract. Its
SHA256 is `eeb47f0d036a666d2e86237c2aa5dd8f75164c53658215c90d5ec4e8e5e27e74`.
The test pins that seal independently. Do not regenerate or retune these files
after seeing smoke responses; a change requires a separately reviewed version.

The classes are supported 4, contradicted 4, and not_enough_evidence 4. Quote
validation proves that an anchor exists in the supplied excerpt, not logical
entailment. The complete two-line toy example is a file-local absence example,
not evidence of repository-wide absence. The four insufficient-evidence items
keep unavailable lines, unspecified revision, incomplete search, and ambiguous
file identity separate from factual truth.

D1 uses fake responses only. Passing the sealed-answer plumbing test is not a
model accuracy measurement. The proposed later smoke is two passes of these
same twelve items, with at most 24 requests after separate execution approval.
Server identity, protocol, request limits, reasoning settings, cancellation,
resource availability, and the execution window remain environment-owner inputs.
Team agreement is a diagnostic, not ground truth or a quality acceptance gate.
Four items per class cannot meet the design's 20 independent items per class
quality threshold. D1 does not establish runtime latency or available GPU memory.
