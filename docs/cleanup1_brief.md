# Cleanup 1 brief: delete the retired rating stack (owner approved 2026-10-01)

Work only inside the worktree `/workspace/.claude/worktrees/delete-old-eval` (branch `delete-old-evaluation`, from tag
`before-deleting-old-evaluation-pipeline-material`). Use `/workspace/.venv/bin/python` with this folder as the working directory.
Never touch `/workspace` itself (another session is running research jobs there), never commit, never push, no API calls.
Nothing here changes behaviour of the site, the replay, or the seeded-error pipeline.

## What is being deleted (the old two-judge / span-consensus design, replaced by `seeding/`)
Code: `evaluation/llm_rater.py`, `matching.py`, `consensus.py`, `calibration.py`, `blinding.py`, `targets.py`, `schemas.py`,
`evaluation/prompts/rater_v1.md`, `evaluation/models.py` (all 9 models: Rater, Panel, PanelMember, Rating, Finding, ConsensusFinding,
ConsensusFindingMember, IssueFindingLink, CalibrationSet, CalibrationItem, Annotation), commands `seed_panel` and `run_raters`.
Tunables in `config/tunables.py`: `JUDGE_MODELS`, `SPAN_MATCH_MIN_IOU`, `INTENSITY_DISAGREEMENT_THRESHOLD`, `CALIBRATION_*`, `RATER_*`
(first grep that nothing outside the files above still reads them; keep `JUDGE_MODEL_SEEDED`, `JUDGE_SEEDED_MAX_TOKENS`).
Tests: `tests/llm_raters/`, `tests/evaluation_models/`, `tests/rater_matching/` and any other test that only exercises the deleted code.

## What must stay
- The `evaluation` Django app itself (label `evaluation`, in INSTALLED_APPS) as the home of the six seeded-error commands:
  `generate_conversations`, `judge_responses`, `summarize_pilot`, `run_research_eval`, `judge_research`, `summarize_research`.
  Keep `evaluation/__init__.py`, `apps.py`, `management/`, and migrations 0001 and 0002 (history must still replay).
- Everything in `moderation/`, `forum/`, `accounts/`, `seeding/`, `analysis/`, `golden/`, `moderation/replay.py`, `spike`. Do not touch
  `analysis/metrics.py`, golden, warmup, series or spike (phase 2 is a separate, undecided step).
- LLM purposes `replay` and `judge` (the new judge uses `judge`).

## Database
Add migration `evaluation/migrations/0003_delete_retired_rating_models.py` (generate with `makemigrations evaluation` after emptying
`models.py`; name it that) that deletes the 9 models. The tables were empty in the dev database. Remember: after this, `evaluation/models.py`
is an empty module (a docstring saying the app now only holds commands is enough).

## Docs to update (building agent)
README.md (remove `seed_panel` and `run_raters` rows and mentions), `docs/plan.md` (mark the old machinery as removed where §9/§14 say it will
be retired; short edits only), `docs/plan_summary.md`, `docs/evaluation_pipeline_todo.md`, `docs/codebase_summary.md` (the paragraph about old
evaluation tables, and the tables list), `docs/database_schema.md` and `.mmd` (regenerate with `scripts/make_schema_doc.py` if that is how they are
made; else edit by hand), `docs/eval_pipeline_summary.md` if it mentions them. Do not edit `docs/neutrality.md`, `docs/plan_v*.md` or old step briefs.

## Files (disjoint ownership)
- **Building agent:** everything above except files under `tests/`. Never edits tests.
- **Testing agent:** only files under `tests/`: delete the old test directories; update tests that listed the deleted files, commands or
  tunables (e.g. `tests/readme_check/test_readme_commands.py` PROJECT_COMMANDS and the README checks, `tests/test_tunables.py` if it names the
  removed tunables, import-rule tests that name `evaluation/llm_rater.py` as the only gateway user: rewrite them to the current rule, which is
  that only `moderation/llm.py` imports `anthropic`, and `seeding/generate.py`, `seeding/judge.py`, `seeding/research_eval.py` plus moderation
  reach the gateway; and `tests/evaluation_models/test_evalmodels_app_shape.py`'s intent). Never edits non-test code.

## Done when
`/workspace/.venv/bin/python -m pytest -q -p no:cacheprovider` passes except the known failures that come from the owner's local
`config/tunables.py` edits (not present in this worktree, so expect none), `manage.py check` and `makemigrations --check --dry-run` are clean,
`grep -rn "llm_rater\|evaluation.models\|run_raters\|seed_panel" --include=*.py --include=*.md .` finds nothing outside history docs,
`--dry-run` of the six seeded-error commands still works against an empty experiment name (no crash on import), and the testing agent's
check that the deleted dirs left no dangling imports is clean.
