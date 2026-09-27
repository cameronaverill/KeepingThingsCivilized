# Step 20a revision brief — broaden `time_sensitive` to `needs_verification`

Revises the already-committed Step 20a work (commit `088d570`... actually `7d2cb08` is 20b groundwork; the Step 20a
commit is the one titled "Step 20a: Master's time_sensitive flag and the Intervenor's offer_research act"). Read
`docs/plan.md` section 2's "Factual research via web search" entry (2026-09-27 update) for the full design
conversation this responds to — read it before this brief, it explains *why*, this brief only says *what*.

**In one sentence:** `time_sensitive` was scoped only to "the claim's answer might have changed since training";
it's being broadened to also cover "the model might simply be wrong or fabricating this specific detail, regardless
of when it happened" — because both are reasons the existing direct-assertion pipeline (`correct_factual_error`/
`provide_information`, unchanged, still the first choice for anything the model can confidently handle) can be
inadequate for a checkable claim, and the reason is never shown to users, so one broadened boolean covers both.

This is a rename-and-broaden, not a rebuild: the mechanism (a boolean field on `MasterIssue`, a pipeline validation
rule, an `offer_research` act type, prompt guidance) is completely unchanged in shape. Only the field's name and its
prompt-level definition change, plus everywhere that name is referenced.

## 1. Naming

Rename `time_sensitive` to `needs_verification` everywhere it appears:
- `moderation/schemas.py`: `MasterIssue.time_sensitive` → `MasterIssue.needs_verification`.
- `moderation/models.py`: `Issue.time_sensitive` → `Issue.needs_verification` — this is a column rename on an
  existing model, so it needs a new migration (`RenameField`, not a drop-and-add — check the migration this
  introduces doesn't lose data on any existing row; there won't be any real data yet, but write it correctly
  regardless, the same way you would if there were).
- `moderation/pipeline.py`: the `valid_local_ids[i].time_sensitive` reference in `validate_acts` →
  `.needs_verification`; the rejection reason code `not_time_sensitive` → `not_needs_verification` (keep it a single
  reason code, same as before — do not split it into two reasons for the two underlying causes, since the brief
  below says the reason is never surfaced).
- `moderation/prompting.py`: the rendered `<time_sensitive>` tag → `<needs_verification>` (same lowercase
  `true`/`false` rendering rule as before).
- Every test file touching any of the above (`tests/moderation/test_step20a_schemas.py`,
  `test_step20a_taxonomy.py`, `tests/models_moderation/test_modmodels_time_sensitive.py` — rename the file itself to
  `test_modmodels_needs_verification.py`, `tests/pipeline_run/test_pipeline_offer_research.py`,
  `tests/pipeline_agents/test_agents_input.py`'s two rendering tests, and every fixture the original step 20a
  mechanical pass touched — same file list as `docs/step20a_brief.md` item 2 documents, now needing the field
  *name* changed, not re-added). Test names that literally say `time_sensitive` in the function name should be
  renamed to say `needs_verification`, not just have their body's field access changed.
- `moderation/taxonomy.py`'s `offer_research` `DEFINITIONS` entry and `moderation/prompts/intervenor_v1.md`'s
  `offer_research` guidance: no field-name reference to update there (they never named `time_sensitive` directly),
  but see item 2 below for a substance change to both.

## 2. Broadened meaning, in `moderation/prompts/master_v1.md`

The existing `time_sensitive` guidance (added in step 20a, near the `factual_accuracy` rubric) explained one
reason to flag a claim: recency. Add the second reason, side by side, under the same field name
(`needs_verification`): the Master should also set it `true` when it isn't confident a specific checkable detail
(a citation, a named study, a statistic, a precise figure) is accurate, independent of whether it's time-sensitive —
because it might be misremembering or the detail might not exist at all. Keep the existing recency example
(unemployment rate vs. Treaty of Westphalia); add one fabrication-style example in the same worked-example style,
close to this shape: a message asserts "a 2024 Harvard study proved that remote work reduces productivity by 40%" —
this is checkable, not really about recency, and the Master isn't confident enough in the specific citation and
figure to let the existing pipeline assert a correction outright, so it sets `needs_verification: true` (still
reporting the issue with its usual `confidence`/`intensity`, exactly as before — this is an additional flag, not a
replacement judgement).

Say explicitly, since this came up in the owner's design conversation and matters for how the Intervenor treats it:
setting this flag never changes *whether* an issue is reported, and the specific reason (recency vs. fabrication
risk) is never distinguished in the flag itself and must never appear in anything posted to users — only "an
independent check could be requested," never "this might be outdated" vs. "this might be fabricated."

## 3. `moderation/prompts/intervenor_v1.md` — when `offer_research` is chosen vs. the existing act types

Add one clarifying rule, since this is the part of the owner's conversation most likely to be missed: the Intervenor
should prefer `correct_factual_error`/`provide_information` (direct assertion) whenever it's actually confident
enough to use them — `offer_research` is chosen only for an issue where `needs_verification: true`, i.e. only where
the Master itself flagged that the existing pipeline isn't the right tool. Do not weaken or restate the existing
act-type definitions; add this as a short "when to prefer which" note near the `offer_research` entry, pointing back
to the existing definitions rather than duplicating them.

## Tests (testing agent, from this brief)

- Every existing Step 20a test still passes, under the new name, with the new prompt substance covered by the
  existing "prompt contains X" static-text test pattern (extend it for the fabrication-example wording, same
  approach as before — substring/regex on key introduced phrases, no LLM mock).
- A new test: an issue with `needs_verification: true` set for a claim that has nothing to do with recency (a
  fabrication-style scenario) still validates an `offer_research` act citing it, exactly the same as a
  recency-style one did before — the pipeline-level check has no concept of *why* the flag is true, only that it is.
- A new prompt-text test: the intervenor prompt's "when to prefer which" note exists and does not use the words
  "outdated" or "fabricat*" anywhere in a way that would leak the specific reason toward user-facing text (check
  the *acts'* style-rule section already forbids this generally — confirm it still covers this new act type, don't
  duplicate the rule).

## Coordination and out-of-scope reminders

- Same file ownership split as `docs/step20a_brief.md`'s original items 1-2 (taxonomy/schemas + the mechanical
  fixture rename pass) vs. items 3-4c (models/migration, pipeline, prompting.py) vs. items 5-6 (the two prompts) —
  reuse that grouping for this revision; whichever group originally owned a file still owns it here.
- Do not touch `moderation/llm.py`, `moderation/pricing.py`, `moderation/budget.py`, `moderation/worker.py`, or add
  any new `ModerationRun` kind — unchanged from the original brief's boundary; that's still Step 20b.
- Run the full suite before reporting done; this is a rename across an already-integrated feature, so a missed
  reference anywhere will show up as a clean failure (an `AttributeError`/`KeyError` on `time_sensitive`), not a
  subtle one.
