# Step 21 brief: a clarity dimension and the agreement/disagreement note

Owner decisions (2026-10-01): add the clarity dimension; add the "where you agree / disagree" note, fired after every 4th user message
(N = 4); base the new prompts on v1 (not v2); the note is displayed as an ordinary moderator message; neutrality tests are for factual
items only for now, and neutrality tests for clarity and for the agreement note are a post-MVP TODO (write that TODO, do not build the tests).
Use `/workspace/.venv/bin/python` with this worktree (`/workspace/.claude/worktrees/dimensions`, branch `dimensions-clarity-map`) as the working
directory. Work only here, never in `/workspace`, never commit, never push, no real API calls (FakeLLM only). Source of truth for the design:
`docs/plan.md` (§5 taxonomy, §6 pipeline, §14); the single list of categories is `moderation/taxonomy.py` (a test fails if a prompt omits a value).

## Part 1: the clarity dimension
- `taxonomy.DIMENSIONS["clarity"] = {"issue_type": "unclear_statement", "coverage": "flagged_only"}` plus a one-sentence `DEFINITIONS["clarity"]`
  (same style as `abusiveness`). `unclear_statement` therefore gets an intensity (0 to 4, only 1 to 4 are reported) like the other two.
- New rubric `rubrics/clarity_v1.md`, same layout as `rubrics/abusiveness_v1.md` (0 = clear, 1 = slightly vague, 2 = ambiguous, 3 = hard to
  respond to, 4 = unintelligible, each with examples). It must say: score the statement, never the speaker or side; register, dialect, formality,
  non-native phrasing and typos that do not change the meaning are 0; a clear statement the reader disagrees with is 0.
- New prompts `moderation/prompts/master_v3.md` and `intervenor_v3.md`, copied from `master_v1.md` / `intervenor_v1.md` (NOT v2) and edited:
  three scored issue types, the clarity rubric embedded inline the way the two existing rubrics are, "report `unclear_statement` only for intensity
  1 to 4". Intervenor v3: for a clarity issue prefer `request_clarification` (a question) over `clarify_argument` (a restatement); use
  `clarify_argument` only when sure of the meaning. All other wording stays as in v1. `load_prompt("master")` picks the highest version, so v3 becomes live.
- Pipeline: `validate_issues`/`_store_issues` already key off `taxonomy.dimension_for`; make sure `unclear_statement` with a missing or
  out-of-range intensity is handled exactly like the other two dimensions. `Issue.dimension` choices come from the taxonomy: generate the
  (choices-only) migration with `makemigrations`.

## Part 2: the agreement/disagreement note, every N-th user message
- New tunable `AGREEMENT_MAP_EVERY_N_USER_MESSAGES = 4` in `config/tunables.py` (comment: the note fires after every N-th user message in a
  conversation; 0 or None turns it off). Read it by attribute at call time. Only this one line may be added to tunables by this step.
- A live run is **summary due** when: `run.kind == "live"`, the number of user messages in the conversation with `seq_no <=` the trigger's `seq_no`
  is a positive multiple of N, and the Master's `discussion_map` has at least one agreement or disagreement. Replay runs, research runs and preview
  checks are never summary due (so the seeded-error evaluation is unchanged).
- When due, the Intervenor is called even if there are no valid issues (today the run ends `no_intervention` with "no valid issues"; keep that for runs
  that are not due). Pass a flag through `prompting.render_intervenor_input` (new keyword `summary_due=False`, rendered as a small block the prompt
  explains). Intervenor v3 instructs: when a summary is due, include exactly one `identify_agreement_disagreement` act (addressee "all", subject
  "both", `source_issue_ids` may be empty) built ONLY from the discussion map: each agreement, each disagreement, and whether each disagreement is about
  facts or values, in impersonal wording ("One position holds ...; another holds ..."), the same weight and detail for each position, one to three
  sentences, never "Participant A/B", "the other side" or viewer-relative words (the existing label rules and `label_check` still apply).
  The act counts toward the 3-act limit; if the Intervenor leaves it out the run proceeds normally (no error).
- Preview: a draft check never fires the note. When a live run is summary due and a preview's stored outputs exist (`preview.claim_reusable_check`),
  reuse the stored Master output but call the Intervenor fresh (the stored one has no summary act).
- No new tables or columns: `ModerationRun.discussion_map`, `InterventionAct.act_type` and the existing acts are reused. The note is an ordinary moderator
  message in the conversation; no template or view change. Do not edit `docs/user_facing_text.md` or the how-it-works page; if a sentence there should
  change, say so in your report.

## Docs (building agent)
`docs/plan.md` (short edits: the taxonomy/dimensions list, the pipeline step that decides summary due, a line recording the owner's N = 4 decision),
`docs/plan_summary.md`, `docs/codebase_summary.md` (the "room to grow" items for these two now built), `docs/evaluation_pipeline_todo.md` (replace the
"Surface where you agree / disagree" item with what was built and what remains), README tunables table, `docs/database_schema.md` regenerated with
`scripts/make_schema_doc.py` (and `.mmd` by hand if the choices changed it). **Post-MVP TODO** (put it in `docs/plan.md` §18 post-MVP list and in
`docs/evaluation_pipeline_todo.md`): neutrality tests for the clarity dimension (matched clear/vague pairs mirrored left and right; dialect-swapped
pairs; faithfulness of `clarify_argument` restatements) and for the agreement note (equal weight and detail per position, order of positions,
hedging, factual-versus-normative labelling by side, omission of one side's points, a deliberately slanted positive control). Not built now.

## Files (disjoint ownership)
- **Building agent:** `moderation/taxonomy.py`, `moderation/schemas.py` (only if needed), `moderation/pipeline.py`, `moderation/prompting.py`,
  `moderation/agents.py`, `moderation/preview.py`, `moderation/models.py` + its new migration (choices only), `moderation/prompts/master_v3.md`,
  `moderation/prompts/intervenor_v3.md`, `rubrics/clarity_v1.md`, the one tunables line, and the docs above. Never edits tests.
- **Testing agent:** only files under `tests/`. Never edits non-test code.

## Tests (testing agent; FakeLLM only; a mutation pass over the new code at the end)
Taxonomy/prompt consistency (every taxonomy value appears in v3; each dimension has a rubric file); clarity issues stored with dimension `clarity`
and a validated intensity, missing or out-of-range intensity handled like the other dimensions; summary-due cadence (N = 4: due at user messages 4, 8,
12 and not at 3, 5, 6, 7; only user messages counted, moderator messages ignored; off when 0 or None; the tunable read at call time via monkeypatch;
live only, never replay/research/preview; not due when the map is empty); the Intervenor is called with no valid issues exactly when due and the
posted message contains the `identify_agreement_disagreement` act; not called when not due and no issues; a missing summary act is not an error;
the act text passes `label_check` and an act text with "Participant A" or "the other side" is rejected as for any act; preview never fires the note and
a due live run reuses the preview's Master output but not its Intervenor output; the migration check is clean. Update existing tests that assume
`unclear_statement` has no intensity or that the default prompt is v1. The highest-version helper in `tests/pipeline_agents/` already follows the latest file.

## Done when
`.venv/bin/python -m pytest -q -p no:cacheprovider` passes (this worktree has no local `tunables.py` edits, so expect no known failures),
`manage.py check` and `makemigrations --check --dry-run` are clean, and the testing agent's mutation pass has no surviving mutant the tests should catch.
Afterwards Claude re-runs the 88 seeded debates on v3 (summary off in replay) to confirm the factual-error behaviour is unchanged.
