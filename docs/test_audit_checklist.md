# Step 18 test-suite audit — consolidated checklist (awaiting owner approval)

Status: analysis complete, nothing changed yet. Per `docs/test_audit_plan.md`'s own rule, no test is written or
changed until this checklist is approved. Full per-agent write-ups (coverage.md / mutation.md / style.md, one triple
per slice) live under `$CLAUDE_JOB_DIR/tmp/test_audit/<slice>/` for this session; ask if you want them pulled into
the repo before they're cleaned up with the job.

Method: 13 slices, 3 read-only agents each (Coverage & Logic Auditor, Chaos & Mutation Engineer, Style &
Maintainability Reviewer) = 39 agents total. Mutation agents worked in `rsync -a` copies outside `/workspace`,
introduced real mutants, reran the real suite, and reverted — nothing in `/workspace` was touched at any point
(verified per-agent). No test author saw another agent's report before finishing; this document is my own
consolidation and prioritization, with disagreements settled and reasons recorded where they came up.

Ordering follows `docs/test_audit_plan.md` Step 2: security/neutrality-critical first, then false positives, then
coverage, then style.

## 0. Process notes and residual gaps (read before the tiers below)

- **Accounts scope gap.** `docs/test_audit_plan.md`'s own slice definition names `tests/`, `tests/accounts_registration/`
  and `tests/accounts_auth/` for the accounts slice. My redesigned slice list (against the current ~9,864-test suite)
  only assigned the latter two directories. Three top-level files were never independently audited this round:
  `tests/test_accounts_uniqueness.py`, `tests/test_review2_accounts_model.py`, `tests/test_accounts_admin.py` (plus
  `tests/test_tunables.py`, which is project-wide, not accounts-specific). The accounts coverage agent read them
  incidentally and reported them as "look thorough," but nothing mutation- or style-audited them. Recommend a small
  follow-up pass if you want full parity with the original plan.
- **`seed_panel.py` fell between two slices.** I paired its source file with `tests/evaluation_models/` (slice 10) but
  its real tests live in `tests/llm_raters/test_llmr_seed_panel.py` (slice 11's directory) — a slicing mistake on my
  part, caught by the raters/matching coverage agent. Not audited by anyone. Recommend folding into the same
  follow-up.
- **`pytest-cov`/`coverage` are not installed in `.venv`**, and agents correctly refused to install anything (no
  network access in the sandbox). Every coverage agent did a manual line-by-line audit instead of measuring real
  branch coverage. Recommend adding `coverage` to `requirements-dev.txt` so future audits get real numbers.
- **One pre-existing, unrelated flaky test**: `tests/moderation/test_step3_review2_fuzz.py::test_hundred_kilobyte_inputs_take_well_under_a_second[8-100000]`
  — a tight `< 1.0s` wall-clock assertion that occasionally runs ~1.06s. Not caused by any mutant; worth loosening to
  a relative check.
- **A stray nested git worktree** at `.claude/worktrees/tool-summary-doc/` (from an unrelated session working in
  isolation) pollutes any full-repo `rsync`/scan. Two agents had to work around it by excluding it from their copies.
  Not this session's to delete — flagging so it isn't mistaken for repo corruption.
- **One unrelated `ProcessLookupError`** observed once in `tests/worker/test_worker_devsh.py::test_the_web_servers_child_process_is_stopped_too`,
  during an unrelated mutation run. Didn't recur. Worth a look if it shows up again.

## Tier 1 — Neutrality / bias-evaluation validity (this is what the whole project is for)

1. **[raters_matching, mutation]** Removing the same-rater dedup guard in `evaluation/consensus.py`'s `_cluster`
   survived: one rater's two separate findings on the same dimension can merge into a single `ConsensusFinding`,
   manufacturing a fake `n_raters=2` "two raters agree" signal from one rater's own output. Directly undermines
   "no bias found" trustworthiness. **Fix:** a `run_panel`-level test scripting one rater with two overlapping-span
   findings on the same dimension, asserting they are never merged into one finding.
2. **[evaluation_models, mutation]** The calibration-set builder's per-stratum side balance (`_draw`) has no real
   test: only a pooled-across-all-strata check exists (`test_both_variants_are_represented_though_one_is_rare`), so a
   specific high-intensity stratum can draw 0 items from the rare side while that check still passes — verified
   directly by the agent (seed=2, `abusiveness:planted_high` drew 6/6 from one side). **Fix:** a per-stratum
   variant-balance test, grouping `CalibrationItem` by `stratum` before checking both sides appear.
3. **[raters_matching, coverage+mutation]** `docs/plan.md` §9's "robustness check" (re-running with spans snapped to
   sentence boundaries) is never actually invoked: `build_panel_consensus` always calls `build_consensus` with
   `snap_to_sentences=False`, and nothing overrides it. **Owner decision needed, not just a test:** is this pass
   supposed to be wired into the real pipeline already, or correctly deferred?
4. **[raters_matching, mutation]** The exact-threshold IoU-merge boundary and the exact-threshold intensity-disagreement
   boundary (both explicitly named risk areas) are unguarded at the real `run_panel` level — both mutants survived.
   Their only real coverage is on the bare `consensus.py` functions, in a different slice's test directory.
5. **[raters_matching, coverage+mutation]** The third of three named adjudication triggers — disagreement on
   scorability — has zero coverage through the real pipeline (`rate_target` → `_store_findings` →
   `build_panel_consensus`); only unit-tested on hand-built `Finding` rows elsewhere.
6. **[moderation_prompts, coverage+mutation, converging]** `moderation/series.py` (sentence/repetition/unanswered-question
   detection, used by the mechanical evaluation series) has **zero direct unit tests** — confirmed by the coverage
   agent — and its "3+ words" repeated-sentence boundary mutant (`>=3` → `>3`) **survived**, confirmed by the mutation
   agent, because the golden-transcript cross-check oracle has no word-count filter at all.

## Tier 2 — Data integrity (confirmed by coverage AND mutation agents independently)

7. **[models, coverage+mutation, converging]** Several invariants `docs/plan.md` describes as enforced are
   Python-only (`save()`-level), not DB-enforced, and confirmed bypassable via `bulk_create`/direct M2M writes:
   - `Issue`: "an issue on a moderator message must be rejected"
   - `IssueDisposition`: "only a valid issue can have a disposition"
   - `InterventionAct`: the `MAX_ACTS_PER_INTERVENTION` cap
   - `InterventionAct.source_issues`/`source_messages`: the cross-run/cross-conversation guard (an `m2m_changed`
     signal that only fires on `.add()`/`.set()`, never on a direct through-table write)
   Only `ModerationRun`'s trigger-message rule has real SQLite-trigger backing; everything else in this list doesn't.
   **This needs an owner decision**: add triggers/constraints, or accept these as Python-only with a documented
   bulk-write ban, or add explicit bypass-regression tests. Given `bulk_create` isn't used anywhere in the app's own
   runtime code today, the risk is latent, not live — but worth a decision either way.
8. **[models, coverage]** `Issue.quote == content[start:end]` exact match, `Message.seq_no` monotonicity (only
   *uniqueness* is DB-enforced), and `Topic.proposition`'s non-empty `CheckConstraint` (confirmed **zero** test
   coverage at any level, any layer) share the same pattern.
9. **[replay_warmup, mutation]** `make_label_seed`'s documented determinism (sha256 of experiment|transcript|assignment)
   is only checked via proxies (not-None, differs-by-assignment) — never recomputed independently to confirm it's a
   pure function of its inputs. Matters for the golden-transcript methodology's reproducibility guarantee specifically.
10. **[replay_warmup, mutation]** `plan_runs`' pair_id-grouping sort key is currently indistinguishable from sorting by
    transcript id alone, because every real/fixture transcript id happens to be prefixed by its `pair_id`. Latent,
    not live against real data.
11. **[replay_warmup, coverage]** Owner decision needed: `moderation/replay.py`'s own docstring claims a budget stop
    "leaves complete pairs and both label assignments together," but `test_replay_budget.py` directly contradicts
    this (stops mid-pair in a real test). Stale docstring, or a missing invariant?

## Tier 3 — Security/privacy-adjacent thin spots

12. **[pipeline_agents_preview, mutation]** `preview.claim_reusable_check`'s participant-ownership filter (stops one
    participant reusing another's cached draft-check) is defended by **exactly one test**. The single most
    concerning point of failure found in the whole audit, structurally — a real cross-participant leak vector one
    test edit away from going unnoticed.
13. **[analysis_exports_admin, coverage+mutation, converging]** The preview/draft-check raw-field suppression in
    exports (`moderation/queries.py`'s `_preview_call_ids`) is bypassable within `tests/exports/`'s own scope,
    confirmed by both agents. Not a live hole — `tests/preview_backend/` covers the real guarantee — but the file
    whose entire job is export privacy has a blind spot on this exact path.
14. **[evaluation_models + analysis_exports_admin, coverage]** Both slices' privacy/blinding adversarial tests use
    sentinel strings with no vocabulary overlap with real message content — this proves "doesn't leak in this
    fixture," not "omitted at the field level." **Fix:** at least one adversarial case per slice where legitimate
    content coincidentally contains blinded-field-shaped text (a username-looking word, an export identity string).
15. **[accounts, coverage]** No `caplog`-based test proves login/lockout/password-change never logs a username or
    password — registration alone has this test (`test_the_logs_never_hold_a_password`). Given this project's
    explicit "never log usernames/passwords" rule (recently reinforced by this session's own logging pass), this is
    the single biggest gap in the accounts slice.
16. **[accounts, mutation]** Three confirmed survivors: (a) `_lockout_minutes`' failure-count threshold can flip
    `>=`→`>` undetected, because every test uses a loose tolerance window instead of an exact value; (b) the
    `max(1, ...)` floor preventing a 0/negative lockout-minutes display has no test forcing zero/near-zero remaining
    time; (c) `LoginForm.clean`'s own `confirm_login_allowed` (inactive-account) call is dead-on-tested-paths, because
    Django's `ModelBackend` already filters inactive users first — nothing isolates this layer's own guarantee.

## Tier 4 — Other confirmed coverage gaps and mutation survivors, by slice

- **[research_worker]** No test drives a research run through the real worker end-to-end with the real (unstubbed)
  `run_research` — `RESEARCH_MODEL`/`RESEARCH_MAX_TOKENS`/`RESEARCH_MAX_USES`/`RESEARCH_MAX_SOURCES_SHOWN` never
  proven to reach the real call. Injection-safety escaping (`_escape`) and `build_research_input` are never tested
  against actual special characters or multi-issue input. The unclassified-exception ("internal_error") path in
  `run_research` has zero coverage. **Mutation confirmed one real survivor:** the retry-once-on-`LLMOutputError`
  contract in `_call_research` has no test that would notice if the retry were removed entirely.
- **[forum_views, coverage, echoes a real shipped bug]** 4 of 7 ineligible act types for the "Provide factual
  background" button are never individually tested (only 3 of 7 are sampled), and `request_information` — the type
  that famously shipped missing from the eligible set — is still never taken through the full click→run-created path.
  Same failure pattern that caused the real bug this session already fixed once.
- **[forum_views, mutation]** Two minor survivors: the view-layer self-block guard is fully redundant with an
  identical service-layer check (defense-in-depth, untested in isolation); `_preview_mode`'s `can_post` gate is only
  half-tested (closed-conversation case only, not the "open but message-cap-full" case).
- **[forum_services, coverage]** A real plan/code mismatch: `docs/plan.md` promises literal "thread full" text for a
  third joiner; that text doesn't exist anywhere in the code. Actual (well-tested) behavior: a third person silently
  gets their own new waiting conversation, per the later step-7c ruling. **The plan line is stale and should be
  corrected**, not the code.
- **[forum_services, coverage]** The `not_saved` rejection branch (a `Message.save()` `ValidationError`) is declared
  in `EXTRA_CODES` and named in a test docstring but never actually triggered by any test.
- **[forum_services, mutation]** Two minor survivors, both "redundant defense-in-depth untested in isolation": NFC
  normalization in `count_message_chars` (every caller already normalizes first); a participant-count filter in
  `_waiting_candidates` redundant with the conversation's `status` field.
- **[seed_readme, coverage+mutation, converging]** `seed_topics.py`'s title-length boundary (`TITLE_MAX`) has zero
  tests — confirmed by both agents independently, unlike every other length-limited field which has full boundary
  coverage. **Mutation additionally found:** the per-entry write loop has no test forcing a mid-loop DB failure, so
  "a failure on entry 3 rolls back entries already written in this run" is completely unverified.
- **[seed_readme, coverage]** `tests/readme_check/` is strong on static claims (paths exist, tunable values match)
  but never actually *runs* a documented command to verify its claimed behavioral output — e.g. `README.md`'s precise
  claims about `manage.py check --deploy`'s and `manage.py budget`'s output are never checked by executing them (both
  are read-only, cheap to test for real).
- **[analysis_exports_admin, coverage]** `PreviewModeAdmin`/`PreviewCheckAdmin` are registered in Django admin but
  completely excluded from the admin contract test suite (access control, no-spend, no-N+1, truncated-display
  privacy methods — all untested for these two models).
- **[analysis_exports_admin, mutation]** One fixture gap: the hand-built known-answer test fixture has no row with
  `acted=True, issue_valid=False`, so a real semantic bug in `metrics.action` was only caught by the randomized
  property-based oracle, not by any hand-built example test.

## Tier 5 — Style/maintainability (representative highlights; full detail in each slice's `style.md`)

- **[pipeline_agents_preview]** `pipeline_run_kit.py` and `pipeline_agents_kit.py` implement the same five
  scripted-output builders under different names with **opposite defaults** (`interv_d()` defaults to "intervene",
  `intervenor_out()` defaults to "no_intervention") — a real footgun for anyone copying a call between directories.
- **[moderation_guard]** `rows()`/`only_row()`/`refused_rows()` copy-pasted across 8+ files instead of living in the
  shared testkit; two incompatible `clock` fixture contracts share one name across 4 files.
- **[moderation_prompts]** `test_step20a_schemas.py` is now almost entirely duplicate of `test_step20a_revision_schemas.py`
  (the rename landed; recommend deleting the older file). Five files independently reimplement the same
  spike-transcript-folder fixture.
- **[evaluation_models]** A helper named `refused` is reimplemented with **completely different signatures and
  exceptions** in two files — the sharpest naming collision found in the audit.
- **[raters_matching]** A test whose own name promises it checks against `config/tunables.py` actually hardcodes
  `(0.5, 2)` instead of importing it. The rent-control sentence `"Rent control always lowers rents..."` is duplicated
  verbatim across 8 files under 3 different local names.
- **[forum_views]** `flat()`, `home_cards()`, `user_topic()` and several other helpers are each redefined with
  different behavior across different files in the same slice.
- **[seed_readme]** The stated helper-placement convention ("helpers live in `seedtopics_kit.py`") is contradicted by
  every single test file in the slice, each defining its own private helpers instead.
- **[accounts, models, forum_services, replay_warmup, pipeline_agents_preview]** — each has its own list of
  duplicated fixtures/helpers and a few magic numbers standing in for tunables; full detail in each `style.md`.

## Recommended next step

Please review and tell me which tiers to act on. My suggestion: **Tiers 1–3 first** (neutrality validity, data
integrity, security/privacy thin spots) — these are the ones with real consequences if left alone. Tiers 4–5 are
real but lower-stakes; happy to batch them into the same testing-agent pass or defer them, your call. Once you
approve a scope, I'll route it to testing agents under the strict rules in `docs/test_audit_plan.md` (no conditional
logic in tests, exact assertions, AAA structure, no tautologies), and a separate builder agent fixes any source bug
a strengthened test reveals — classified by me as code bug vs. test bug vs. contract ambiguity, same as every other
step this project has followed.
