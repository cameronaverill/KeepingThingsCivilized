# Commit summary (superseded 2026-09-27)

This file used to track a large backlog of built-but-uncommitted work (steps 6c, 8, 9, 10a, 13, 14, the two-position/blocking
redesign, step 19's intervention preview, wave16's playtest feedback, and supporting docs/tooling). The order-dependent
test failure that had been blocking commits (a real Anthropic client built in two transactional tests) was already gone by
the time of this review; five smaller, unrelated test-suite drifts were found and fixed instead (see the commit history
around 2026-09-27).

All of it is now committed, in the batches below (see `git log` for the exact commits and messages):

1. Accounts — no-email MVP (step 6c) + account admin
2. Worker (step 8)
3. Queries, exports, analysis, admin (step 9)
4. Seed topics (step 10a)
5. Replay (step 13)
6. LLM raters, matching, FK cleanup (step 14)
7. Non-political warm-up golden set
8. Forum: two-position model, waiting/blocking, the intervention-preview feature (step 19), wave16's playtest polish, and
   five pre-existing test-suite fixes found during the full-suite check before committing
9. Supporting docs & tooling (schema doc, user-facing-text inventory, README, generator scripts)
10. Planning docs (`docs/plan.md`, `docs/plan_summary.md`, this file)

Nothing is pending as of 2026-09-27. For what's actually being built next, see `docs/plan.md` (the web-search research
feature, section 2/9/18) and `docs/plan_summary.md`.
