# Step 18 test-suite audit — report

Status: Tiers 1–3 executed and verified, 2026-09-28. Tiers 4–5 analyzed but not yet approved (see "What remains").
This is the closing record `docs/test_audit_plan.md`'s "Done when" section asks for. The full consolidated findings
live in `docs/test_audit_checklist.md`; this file records what was found, what changed, and what remains.

## What was analyzed

13 slices covering the whole suite (~9,900 tests), 3 read-only analysis agents per slice (Code Coverage & Logic
Auditor, Chaos & Mutation Engineer, Style & Maintainability Reviewer) — 39 agents total, run per
`docs/test_audit_plan.md`'s Step 1. All mutation work happened in `rsync -a` copies outside `/workspace`; nothing in
`/workspace` was touched during analysis. No agent saw another's report before finishing.

## What was found

Full detail in `docs/test_audit_checklist.md`. In summary: 6 Tier 1 items (neutrality/bias-evaluation validity —
this is what the project is for), 5 Tier 2 items (data integrity — several invariants are Python-`save()`-only, not
DB-enforced, and confirmed bypassable via `bulk_create`), 5 Tier 3 items (security/privacy-adjacent thin spots,
including one real structural concern: `preview.claim_reusable_check`'s cross-participant guard was defended by
exactly one test), plus Tier 4 (per-slice coverage/mutation gaps) and Tier 5 (style/maintainability) items.

Three items needed an owner decision rather than just a test, resolved via `AskUserQuestion` on 2026-09-28:
- **`snap_to_sentences` never wired into the real rater pipeline** (Tier 1, item 3): left deferred, documented in
  `docs/plan.md` §9 as a deliberate deferral rather than an oversight — it's a pipeline behavior change, not a test
  fix, and belongs with the calibration/analysis work (Steps 15–16) if that's ever resumed.
- **DB-vs-Python enforcement of the Tier 2 invariants** (item 7): kept as Python-only, since `bulk_create` isn't used
  anywhere in the app's own runtime code today (risk is latent, not live) — bypass-regression tests were added
  instead of adding triggers/constraints, so a future reintroduction of `bulk_create` would be caught rather than
  silently reopening the gap.
- **`moderation/replay.py`'s docstring vs. `test_replay_budget.py`'s actual behavior** (item 11): the docstring was
  wrong, not the code — fixed to describe the real (ledger-checked, not look-ahead) budget-stop behavior.

## What changed

Owner approved Tiers 1–3 only ("Agree, just 1-3 for now"); Tiers 4–5 were explicitly not approved this round. All 9
approved items were executed by 9 testing agents (test-files-only, one slice per agent), each verified via a real
rsync-based anti-tautology check (introduce the exact mutant the finding named, confirm the new test fails, revert,
confirm it passes) per `docs/test_audit_plan.md`'s Step 3 rules — no conditional logic in test bodies, exact/strict
assertions, AAA-structured grouped test classes, tunable-derived values only.

| Slice | What was added |
|---|---|
| `moderation/series.py` | `tests/moderation/test_step3_series_unit.py` (new) — direct unit tests of `has_repeated_sentence`, including the `>=3`-word boundary |
| Forum models | `Topic.proposition`'s empty-string `CheckConstraint`, both at the validation layer and bypassing `full_clean` |
| Replay assignments | `make_label_seed` determinism, recomputed independently rather than checked by proxy |
| Replay plan | `pair_id`-grouping order, using transcript ids that aren't alphabetically prefixed by their `pair_id` |
| Preview reuse | the cross-participant draft-check guard, including a decoy-candidate adversarial case |
| Evaluation calibration | per-stratum side balance (previously only pooled-across-strata was checked) |
| Evaluation blinding | an adversarial case where legitimate content contains blinded-field-shaped text |
| Exports | preview/draft-check raw-field suppression, and field-level (not content-scanning) omission |
| LLM rater panel consensus | same-rater dedup guard, and the IoU/intensity/scorability boundaries, all exercised through the real `run_panel` pipeline rather than bare `consensus.py` functions |
| Models | two new bypass-regression test files documenting the confirmed-latent `bulk_create` gaps (Issue, IssueDisposition, InterventionAct cap, cross-run M2M guard, `Message.seq_no` monotonicity) |
| Accounts | a `caplog`-based test proving login/lockout/password-change never log a username or password (parallel to registration's existing test), plus 3 mutation-survivor tests (`_lockout_minutes`'s exact failure-count threshold, its `max(1, ...)` floor, and `LoginForm.clean`'s own `confirm_login_allowed` isolated from Django's `ModelBackend`) |

Also fixed: `moderation/replay.py`'s module docstring (see above), and `docs/plan.md` §9 gained a note documenting
the `snap_to_sentences` deferral decision.

**No source bugs were found or fixed.** Every one of the 9 items was a genuine test/coverage gap in
already-correctly-implemented code — confirmed independently by each agent's own anti-tautology mutation check, not
assumed. The one non-bug design fact worth recording: `_lockout_minutes`' `max(1, ...)` floor is unreachable on the
real HTTP login path, because django-axes' own expired-attempt cleanup deletes the qualifying row at the same instant
the floor would otherwise apply — the new test exercises the function directly rather than through a real lockout,
since the true zero/negative-remaining case is provably unreachable end-to-end.

**Full-suite verification:** `.venv/bin/python -m pytest -q` → 9,900 passed. The only 2 failures
(`test_only_moderation_llm_py_imports_anthropic`, `test_tunables_are_assigned_only_in_tunables_py`) are pre-existing
and unrelated — caused by a stray nested git worktree at `.claude/worktrees/tool-summary-doc/` from a separate,
concurrent session, not by anything in this audit (see checklist §0 for detail). No mutation survivor listed in
Tiers 1–3 remains alive; no regression was introduced anywhere else in the suite.

Nothing from this pass has been committed yet — commits happen only when the owner explicitly asks, per standing
project rule.

## What remains

- **Tiers 4–5** (per-slice coverage/mutation gaps and style/maintainability findings) are analyzed and documented in
  `docs/test_audit_checklist.md` but not approved or scheduled. Owner's call whether/when to act on them.
- **Residual audit-scope gaps**, flagged in the checklist's §0 and not yet acted on:
  - three top-level `tests/` files (`test_accounts_uniqueness.py`, `test_review2_accounts_model.py`,
    `test_accounts_admin.py`) plus `tests/test_tunables.py` were never independently mutation- or style-audited
    (only read incidentally by the accounts coverage agent);
  - `seed_panel.py`/`tests/llm_raters/test_llmr_seed_panel.py` fell between two slices and wasn't audited by anyone;
  - `pytest-cov`/`coverage` aren't installed, so all coverage analysis this round was manual, not measured — adding
    `coverage` to `requirements-dev.txt` would let a future audit get real branch-coverage numbers;
  - one pre-existing flaky test (`test_hundred_kilobyte_inputs_take_well_under_a_second[8-100000]`, a tight
    wall-clock assertion) and one non-recurring `ProcessLookupError` in `tests/worker/test_worker_devsh.py`, neither
    caused by this audit.
- The stray worktree at `.claude/worktrees/tool-summary-doc/` is a different session's in-progress work and not
  this session's to remove.

Per `docs/test_audit_plan.md`'s "Done when" wording, the audit is complete for the approved scope (Tiers 1–3): all
slices went through the three analyses, the owner approved the consolidated checklist for that scope, the approved
changes are made and verified, and the previously-listed Tier 1–3 mutation survivors are confirmed killed. Tiers 4–5
remain open by explicit owner choice, not by omission.
