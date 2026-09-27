# Step 20a brief — Master's `time_sensitive` flag and the Intervenor's `offer_research` act

First half of the web-search research feature (`docs/plan.md` section 2, "Factual research via web search"; section 9
item 6; section 18's post-MVP list). This half adds no new infrastructure: no new `ModerationRun` kind, no new LLM
gateway call shape, no `web_search` tool, no worker changes, no UI. It only teaches the Master to flag a claim it
can't be confident is current, and the Intervenor to *offer* (never perform) a check — a small, low-risk schema and
prompt change that fits the existing pipeline exactly as it runs today.

The second half (the actual research call, a new `ModerationRun` kind, worker wiring, the "Provide factual
background" control) is **Step 20b**, deliberately separate, because it requires extending `moderation/llm.py` (the
money-safety gateway) to support a fundamentally different call shape — Step 20a must be done, reviewed and merged
first. Do not touch `moderation/llm.py`, `moderation/pricing.py`, `moderation/budget.py`, `moderation/worker.py`, or
add any new `ModerationRun` kind in this step.

A **building agent** implements every numbered item. A **separate testing agent** writes tests for every numbered
item from this brief (not from reading the builder's diff) and never edits non-test code. Ambiguities come back to
Claude, not to each other.

## Correction to `docs/plan.md` (read this first)

`docs/plan.md` originally described this as "a new not-scorable reason, alongside `contested`/`unverifiable`/
`needs_context`". That was wrong and has been corrected in the plan: `NOT_SCORABLE_REASONS` (`moderation/taxonomy.py`)
and `not_scorable_reason` (`evaluation/schemas.py`) belong **only** to the rater/calibration schema used by the human
and LLM judge panel (section 9) — `MasterIssue` (`moderation/schemas.py`) has no not-scorable concept at all. A claim
the Master judges not worth reporting is simply never turned into an issue. So `time_sensitive` cannot reuse that
mechanism; it is a new, ordinary, **required** boolean field on `MasterIssue` instead (see item 2 below).

## 1. `moderation/taxonomy.py`

Add `"offer_research"` to the `ACT_TYPES` tuple (append it; do not reorder the existing ten) and a `DEFINITIONS`
entry for it, in the same one-sentence-with-boundary-rule style as its neighbours:

```
"offer_research": (
    "Offer the participants an independent factual check on a claim whose true answer might have changed, or only "
    "become known, after the model's training; it takes no position on the claim itself and does not perform any "
    "check — it only offers one, unlike provide_information or correct_factual_error, which state something as fact."
),
```

Do not add a new issue type, dimension, disposition, or "not scorable" reason. `ISSUE_TYPES`, `NOT_SCORABLE_REASONS`,
`DIMENSIONS` are unchanged.

**Correction found while building (2026-09-27): those tests do NOT extend automatically.** `tests/moderation/
step3_testkit.py` has its own hand-frozen `ACT_TYPES` set (a plain Python set literal, not an import from
`moderation.taxonomy`), and `test_step3_prompt_files.py`'s "every act type has a definition" / "every prompt mentions
every act type" coverage is parametrized against that frozen copy. Add `"offer_research"` to `ACT_TYPES` in
`tests/moderation/step3_testkit.py` too (same file has `ISSUE_TYPES`, `DECISIONS`, `TONES`, `NOT_SCORABLE_REASONS`
frozen the same way — leave those alone, only `ACT_TYPES` changes for this item), so the existing parametrized tests
actually pick up the new value instead of silently ignoring it.

## 2. `moderation/schemas.py`

Add one new required field to `MasterIssue`:

```python
time_sensitive: bool  # true only if the claim's true answer could have changed, or only become knowable, after
                       # training; meaningless (always false) for issue types other than possible_factual_error /
                       # unsupported_claim, but still required on every issue since the API schema forbids defaults
```

No validator needed (it's a plain bool). Add `"offer_research"` to the `ActType` Literal (keep it in the same order
as `taxonomy.ACT_TYPES` — the existing `assert ActType.__args__ == taxonomy.ACT_TYPES` line already checks this, do
not weaken or remove that assert).

**This is a breaking change to every existing `MasterIssue`-shaped payload in the test suite** (canned `FakeLLM`
responses, any hand-built dict passed to `MasterOutput`/`MasterIssue` in a test). Because the API's structured-output
schema forbids default values, Pydantic will reject any existing payload missing the field. Before writing anything
new:
1. `grep -rl '"issue_type"' tests/ moderation/ golden/` (and similarly for Python dict literals shaped like a
   `MasterIssue`, e.g. `issue_type=` keyword arguments) to find every place a Master issue is constructed as a fixture.
2. Add `"time_sensitive": False` (or `time_sensitive=False`) to every one, unless a specific test is about to be
   rewritten anyway to exercise `time_sensitive=True`.
3. Run the full existing suite (`.venv/bin/python -m pytest -q`) and confirm it is back to fully green from this
   mechanical pass alone, **before** adding any new code or new tests. Report the before/after failure count to
   Claude if anything surprising turns up (e.g. a fixture format this brief didn't anticipate).

Test: `MasterIssue(..., )` without `time_sensitive` raises a validation error; a full payload with `time_sensitive`
set both `True` and `False` round-trips; `ActType` accepts `"offer_research"` and rejects an unknown string exactly
as it does today for the other nine act types.

## 3. `moderation/models.py` — `Issue.time_sensitive`

Add a boolean column to `Issue`: `time_sensitive = models.BooleanField(default=False)`. Not nullable, no blank
option — it is always known (copied straight from the Master's output, `False` for issue types where it's
inapplicable). One new migration in `moderation/migrations/` (next number after `0005_preview_mode_and_check.py`,
so `0006_...`).

No change to `InterventionAct` is needed: `act_type` choices are already `_choices(taxonomy.ACT_TYPES)`, so
`offer_research` becomes a selectable value automatically once item 1 lands.

Test: migration applies cleanly on a fresh database and is reversible; an `Issue` row defaults to
`time_sensitive=False`; existing model-shape tests (`tests/models_moderation/`) still pass with the new column
present.

## 4. `moderation/pipeline.py` — store `time_sensitive`, and validate `offer_research`'s source

**4a. Storing the flag.** Wherever the pipeline currently builds an `Issue` row from a `MasterIssue` (the Master
issue-storing loop — find it near where `issue_type`, `quote`, `confidence`, `intensity` are copied across), copy
`time_sensitive` straight across unchanged. No validation rule needed on this value itself (a `bool` from a `_Strict`
Pydantic model can't be malformed).

**4b. Validating `offer_research`'s source, in `validate_acts`.** An `offer_research` act must cite at least one
valid issue, and every issue it cites must have `time_sensitive=True` — this is how the contract "the Intervenor may
only *offer*, never invent, a research need" is enforced in code, not just trusted from the prompt. Add one new
rejection reason, `not_time_sensitive`, alongside the existing list in the module docstring's "Rejection reason
codes" and in the `validate_acts` branch chain (`moderation/pipeline.py:340-369`):

- An `offer_research` act with an empty `source_issue_ids` is rejected `not_time_sensitive` (reuse this single reason
  for "no source" and "source not time-sensitive" — both mean the same thing: nothing time-sensitive backs this
  offer). Place this check **after** `bad_source_issue` in the branch chain (an unknown issue id must still be
  caught as `bad_source_issue` first, exactly as today), so the order becomes: `decision_no_intervention` →
  `empty_text` → `bad_label` → `names_participant` → `bad_source_issue` → `bad_source_message` → **`not_time_sensitive`
  (new, `offer_research` only)** → `act_cap`.
- The check: `elif act.type == "offer_research" and not (act.source_issue_ids and all(valid_local_ids[i].time_sensitive for i in act.source_issue_ids)): reason = "not_time_sensitive"`.
  This only ever runs for `offer_research` acts; every other act type is completely unaffected by this item.

Test: an `offer_research` act citing only `time_sensitive=True` issues is stored valid; one citing a mix of
`time_sensitive=True` and `time_sensitive=False` issues is rejected `not_time_sensitive`; one with no source issues
at all is rejected `not_time_sensitive`; one citing an issue id the Master never reported is still rejected
`bad_source_issue` (existing behaviour, confirm it still wins over the new check for that case); every other act
type (`provide_information`, `request_information`, etc.) is completely unaffected by an issue's `time_sensitive`
value — cite a `time_sensitive=True` issue from a `correct_factual_error` act and confirm it validates exactly as it
does today, with no new rule silently applying to it.

## 4c. `moderation/prompting.py` — the Intervenor must actually see the flag (found while building, 2026-09-27)

`render_intervenor_input` renders each issue via `_render_issue`, whose fixed field tuple is currently
`("message_id", "issue_type", "confidence", "intensity", "quote", "explanation")` — it does not include
`time_sensitive`. Without this, the Intervenor's rendered input never shows the flag at all, so it has no way to
know which issues are time-sensitive and the whole point of item 4b's `not_time_sensitive` validation (rejecting an
`offer_research` act not backed by a real flagged issue) would just mean the Intervenor's offers get silently
rejected with no way for it to learn why. Add `"time_sensitive"` to that tuple (position: after `intensity`, before
`quote`, matching the field's place in `MasterIssue` itself) so `<time_sensitive>...</time_sensitive>` appears in
every rendered issue block. Render the bool as lowercase `true`/`false` (consistent with how `intensity=None` already
renders as the lowercase word `none`, not Python's `None`), not Python's default `True`/`False` capitalization.

Test: `render_intervenor_input` (and `render_master_input`'s `already_raised` rendering, which reuses the same
`_render_issue`) includes a correctly-cased `<time_sensitive>` tag with the issue's real value, for both `True` and
`False`; existing rendering tests for the other fields are unaffected.

## 5. `moderation/prompts/master_v1.md`

Add instruction and a worked example near the existing "Rubrics behind the intensity numbers" / rubric text for
`factual_accuracy` (the natural home, since only `possible_factual_error` and `unsupported_claim` issues can carry
`time_sensitive: true`). Substance to convey (exact wording is the builder's judgement, matching the file's existing
style):
- After forming the usual judgement about a factual claim, additionally ask: does this claim concern a specific,
  checkable current fact, statistic or event whose true answer could have changed, or only become knowable, after
  this model's training? If so, set `time_sensitive: true` on that issue. This is independent of `confidence` and
  `intensity` — report those exactly as usual; `time_sensitive` is an extra signal, not a replacement judgement, and
  it never changes whether an issue is reported at all.
- Set `time_sensitive: false` for every issue type other than `possible_factual_error`/`unsupported_claim`, and for
  a factual issue whose answer is timeless (a historical date, a mathematical fact, a settled scientific constant).
- One worked example pair, close to this shape: "the national unemployment rate fell last month" (checkable,
  changes constantly, `time_sensitive: true`) versus "the Treaty of Westphalia was signed in 1648" (checkable,
  answer cannot change, `time_sensitive: false`).
- State explicitly that this field carries no position on whether the claim is actually still true — the Master is
  not being asked to guess whether it's now outdated, only whether it *could* be, which it usually cannot know.

No test can call the real LLM. As with the wave16 prompt-precision items: (1) assert the prompt file contains the
new guidance as static text (substring/regex on the key phrases introduced, not a brittle exact match), (2) extend
whatever "prompt mentions every taxonomy value" test already exists to also check for `time_sensitive` guidance
text. Do not write a test that mocks an LLM to "prove" the model now behaves differently.

## 6. `moderation/prompts/intervenor_v1.md`

Add `offer_research` to the "## Act types" list, using the same definition as `taxonomy.py` (item 1) — point back to
it rather than inventing separate wording that could drift. Add a short rule near the existing act-type guidance:
`offer_research` may be chosen only when at least one issue given to the Intervenor has `time_sensitive: true`
(mirrors the code-level rule in item 4b — the prompt should say this even though the pipeline also enforces it, so
the model doesn't waste an act slot on a rejected offer); it takes no position on whether the claim is actually
right or wrong, and its `text` must read as an offer, not a claim or a request — contrast with `request_information`
(which asks the participant for a source) and `provide_information`/`correct_factual_error` (which state something).
Example phrasing in the same impersonal style as the file's existing examples: "The claim in message 4 involves a
current figure that may have changed; an independent check could be requested." Apply the same style rules already
in the file (no "you"/"your", no participant labels, tone chosen by severity not side).

Same test approach as item 5: static text assertions only, no LLM mock.

## Coordination and out-of-scope reminders

- `moderation/taxonomy.py`, `moderation/schemas.py`, `moderation/models.py`, `moderation/pipeline.py`, and the two
  prompt files are each touched by exactly one item above; the builder owns all of them for this step.
- The mechanical fixture pass (item 2) touches test files across the suite, but it is **not** the testing agent's
  job — the builder does it, because it is a prerequisite for the suite to run at all, not new test coverage. The
  testing agent's own new tests (items 2-6) come after that pass is green.
- Do not touch `moderation/llm.py`, `moderation/pricing.py`, `moderation/budget.py`, `moderation/worker.py`,
  `moderation/agents.py`'s call shape, or add any new `ModerationRun` kind, `LLMCall` purpose, or tunable — all of
  that is Step 20b, not this step.
- Do not add a UI control, a URL, a view, or anything user-facing — `offer_research` acts post and render exactly
  like any other moderator act today (same template, same features computation); there is nothing to click yet.
- Run only the tests relevant to your own files as you go; Claude runs the full suite before this step is
  considered done.
