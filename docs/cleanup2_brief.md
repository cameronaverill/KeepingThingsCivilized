# Cleanup 2 brief: retire the old golden-transcript scaffolding (owner approved 2026-10-01)

Work only in `/workspace/.claude/worktrees/delete-old-scaffolding` (branch `delete-old-scaffolding`, from `main` after cleanup 1). Use
`/workspace/.venv/bin/python` with this folder as the working directory. Never touch `/workspace` itself, never commit, never push, no real API calls.
The seeded-error pipeline (`seeding/`, `replay`, the six commands in `evaluation/`) and the live site must behave exactly as before.

## Delete
- The `spike` command (`moderation/management/commands/spike.py`, 906 lines) and everything only it used.
- The mechanical series: `moderation/series.py`, and anything only it or the golden generator used (check `scripts/`, `golden/`).
- `golden/results/`, `golden/warmup/`, and all but THREE files of `golden/transcripts/` (42 files today). Keep one matched left/right sanctuary pair
  (two files that replay with label swap) and one other transcript, so the `replay` tests still have real fixtures. Pick files already used by tests
  where possible.
- `tests/warmup_set/`, and any test that only exercises the deleted code or the deleted golden files (inventory checks, counts, series, spike).
- `moderation/prompts/master_v2.md`, `moderation/prompts/intervenor_v2.md`, `docs/intervenor_v2_draft.md` (v3 replaced them; the loader then
  picks v3). Leave the v1 and v3 prompts alone.
- Move `docs/plan_v1.md` ... `plan_v4.md` to `docs/archive/` with `git mv`, and fix every reference to those paths (including the one sentence in
  `CLAUDE.md`: update the path only, nothing else in that file).

## Hidden dependency (do this first)
Several tests, including the seeded-error tests (`tests/seed_generation/`), check transcript files with
`moderation.management.commands.spike.validate_transcript(path, data, known_ids=None)`. First check whether `moderation/replay.py` already has its own
validator; if not, move `validate_transcript` and the small helpers it needs (and `compute_features` only if replay or arms need it) into a new pure module
`moderation/transcripts.py`, and point the tests and any non-test code at it BEFORE deleting `spike.py`. Behaviour must be identical.

## Keep (do not touch)
`moderation/replay.py`, `analysis/metrics.py`, the `Experiment` kinds (paired, series, replay, warmup, observational), the LLM purposes `spike` and
`golden` (ledger history), `BUDGET_SPIKE_USD_TOTAL` and the budget code that reads it, `evaluation/`, `seeding/`, the two older rubrics, `.githooks`.
Remove tunables that ONLY the deleted code read (grep first), with their comments; do not remove anything else.

## Docs (building agent)
README (the `spike` command row and any golden/spike mentions; the layout section), `docs/plan.md` (short "removed on 2026-10-01" edits where the plan
describes golden transcripts, the spike or the series as existing), `docs/plan_summary.md`, `docs/codebase_summary.md` (the folders table row for `golden/`),
`docs/evaluation_pipeline_todo.md`, `docs/eval_pipeline_summary.md` if it mentions them. Do not edit `docs/neutrality.md` or `docs/step*_brief.md`.

## Files (disjoint ownership)
- **Building agent:** everything above except files under `tests/`. Never edits tests.
- **Testing agent:** only files under `tests/`: delete the dead tests, repoint tests to `moderation/transcripts.py`, fix tests that listed deleted files,
  commands, tunables or the golden inventory (`tests/readme_check`, `tests/test_repo_hygiene.py`, `tests/replay/`, `tests/exports/`, `tests/admin_site/`,
  `tests/pipeline_agents/`, `tests/test_tunables.py`), and add a small test that `moderation/transcripts.py` validates the three kept transcripts and that
  `spike` is no longer a registered command. Never edits non-test code.

## Done when
`/workspace/.venv/bin/python -m pytest -q -p no:cacheprovider` passes in ONE pass (the README path test may fail only on `generated/` and `results/`, which exist
in `/workspace` but not in a worktree; ignore that one), `manage.py check` and `makemigrations --check --dry-run` are clean, a `grep -rn "spike\|golden\|warmup"`
over non-doc code finds only the kept purposes/tunable/fixtures, and `replay --dry-run` on the three kept transcripts still works.
