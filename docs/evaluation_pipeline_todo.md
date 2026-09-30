# Evaluation pipeline — remaining work

> **Superseded in part (2026-09-30).** The evaluation was redesigned (`docs/plan.md` §9 and §14 Steps 11-16): seeded factual
> errors, paired left/right arms, one LLM judge, no abusiveness, no span-consensus or human calibration set. Items below that
> assume the two-judge panel, the calibration set or human rating (including "Your time as a human rater") are retired or
> **future work**: a human panel to calibrate severity, score how appropriate interventions are, and spot-check the judge.
> The scenario audit (Step 17), the pre-registration and the web-search items still apply.

## Summary

The forum itself (Phase A, `docs/plan.md` §14 Steps 1–10) is fully built and running, including the web-search
"factual background" feature. Everything below is Phase B: the separate machinery that would produce a report on
whether the moderator treats both political sides equally (`docs/plan.md` §9, §14 Steps 11–17, plus the web-search
feature's own evaluation lens from §9 item 6). None of it is needed for the site to work for real users; it exists
only to answer the neutrality question with evidence instead of assertion.

What finishing it would actually take, in plain terms:
- **Your time as a human rater.** Step 15 needs at least two people to label a ~100-message-per-dimension
  calibration set by hand (Step 12 built the table and builder, not the rubric-reading or the labeling itself).
  There is no way to skip this with more engineering — it's the ground truth the LLM raters get checked against.
- **Your own scenario audit (Step 17).** A hard gate, by your own request: reading through the golden transcripts
  and mechanical series critically (balance, planted problems, difficulty tiers) before any result from them can be
  trusted. This is reading and judgment, not code.
- **A statistics decision (Step 16's pre-registration).** Before running the real evaluation, someone has to fix
  the number of matched pairs per condition from a power calculation, the agreement thresholds, and the
  positive-control pass criterion — and commit to them *before* seeing results, or the analysis isn't meaningful.
  I can draft this, but the thresholds are calls only you can sign off on.
- **New code**, comparatively small: rubric finalization, a calibration-report script, the frozen-evidence capture/
  replay tooling for the web-search feature, and a production-monitoring report for click-through/source patterns.

Given that, the realistic bottleneck isn't code — it's your rater and audit time. **Steps 15–16 are deferred
indefinitely per your 2026-09-28 decision** (see memory `calibration-deferred`); nothing else here is blocked by
that, and nothing here blocks the live product or deployment.

## TODO

- [ ] **Step 11 — Finalize the rubrics.** `rubrics/factual_accuracy_v1.md` and `rubrics/abusiveness_v1.md` exist as
  drafts; finalize the wording with you and write the human rater guidelines from the same text. *Only feeds the
  evaluation pipeline (confirmed: not used by the live Master/Intervenor prompts) — reasonable to defer alongside
  Step 15/16 if you'd rather not do it now either.*
- [ ] **Step 15 — Calibration report and controls.** *(DEFERRED, 2026-09-28.)* Human–human agreement, LLM-vs-human
  agreement and systematic offsets, judge symmetry by side, the human-only benchmark, the positive control (a
  deliberately biased moderator must be detected), the noise floor from replicates and identical pairs. Needs Step
  11's rubrics and two human raters through the annotation page.
- [ ] **Step 16 — Pre-registered analysis.** *(DEFERRED — blocked by Step 15's thresholds.)* Write
  `analysis/prereg.md` (headline scheme, metrics, intensity/agreement thresholds, pair counts from a power
  calculation) *before* looking at results, then the real per-dimension detection/action/false-positive/data-quality
  tables. Also blocked by Step 17 until that's signed off.
- [ ] **Step 17 — Your scenario-scrutiny audit.** *(Owner task, hard gate.)* Review the golden transcripts and
  mechanical series against the checklist in §19 (balance, planted problems and sources, difficulty tiers, side
  effects) on a freshly generated review pack; decide what to keep, edit, replace or add. No result from Step 16
  may be finalized until this is recorded.
- [ ] **Web-search feature: frozen-evidence replay tooling** (§9 item 6). Capture real search results once, freeze
  them, and build the replay path that reuses the Intervenor's swap test on frozen evidence through matched claim
  pairs. Not started — no capture/freeze/replay code exists yet for this feature.
- [ ] **Web-search feature: production monitoring** (§9 item 6). An ongoing report (not a one-time audit) on
  whether live retrieval comes back asymmetric by side — source diversity, verdict severity, inconclusive rate —
  and whether click-through on "Provide factual background" differs by side. Not started.
- [ ] **Web-search feature: baseline-deviation samples** (§9 item 6, §18). A bounded set of paired samples comparing
  a claim researched through this app's pipeline against the same question asked of Claude's web search directly,
  to check whether this specific design deviates from that baseline. Not started; deliberately narrower than general
  search-neutrality (see §9 item 6 for why that's out of scope).
- [ ] **Surface "where you agree / disagree" (`identify_agreement_disagreement`, `restate_positions`).**
  *(DE-PRIORITIZED, owner decision 2026-09-28; the mechanics are mostly in place, the trigger is not.)* Today a user
  essentially never sees such a note: on your real `db.sqlite3` the site has only ever produced six other act types.
  What exists:
  - **The content is already generated on every run.** The Master fills `discussion_map`
    (`moderation/schemas.py: DiscussionMap`: `agreements[]` and `disagreements[{summary, kind: factual|normative}]`,
    `prompts/master_v1.md` "The discussion map"), and the pipeline stores it in
    `moderation_moderationrun.discussion_map` (JSON) for every run, including runs with no issues.
  - **The act types exist end to end:** `taxonomy.py` (definitions), `schemas.ActType`,
    `moderation_interventionact.act_type` (choices), the Intervenor prompt's act list, and the validators (an act with
    empty `source_issue_ids` is allowed for exactly these acts).
  - **The Intervenor receives the map** as background (`prompting.render_intervenor_input`, `<discussion_map>`).
  - **Why it never fires:** (1) `pipeline.py` calls the Intervenor only when the Master produced at least one valid
    issue, otherwise the run ends `no_intervention` / "no valid issues"; (2) nothing in the prompt says when to prefer
    these acts, and they are never shown as the map itself; (3) the act also has to survive the label-naming rule
    (`label_check`), which forbids "Participant A" and "the other side" wording, so a summary of two positions is hard
    to phrase.
  - **Missing:** a trigger (for example a periodic Intervenor call on a message count, or on `enforce_process` runs),
    prompt guidance on when to use the acts, a way to display the stored map, and a neutrality check of the summaries
    (equal weight and detail per side, equal frequency by side), which would need its own place in the evaluation
    plan (§9).

## Related open design notes (not scheduled, no code to write yet)

These are decisions to make later, not build steps — listed here because they live in the same evaluation-pipeline
space and should be picked up if any of the above is ever resumed (`docs/plan.md` §18):
- Differential uptake and requester-favoring bias in the web-search feature (who clicks, does the note read more
  favorably to whoever clicked).
- Whether to restrict who may click "Provide factual background" (symmetric access today; a "gotcha" dynamic is a
  named, unmitigated risk).
- Domain scope for `web_search` (`allowed_domains`/`blocked_domains`) — left open on purpose for now.
