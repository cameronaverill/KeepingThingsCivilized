# Steps 14 + 16-lite brief: the judge and a first analysis (pilot)

Owner priority (2026-09-30): get examples through the whole pipeline end to end and see preliminary results; refinement later.
So this is a deliberately small first version. Source: `docs/plan.md` §9 items 5-6. Use `.venv/bin/python`. No real API call is made by
tests (FakeLLM only). Nothing edits `seeding/facts.py`, `seeding/arms.py`, `seeding/generate.py`, `moderation/`, `forum/`, or existing tests.

## Files (disjoint ownership)
- **Building agent** creates only: `seeding/judge.py`, `seeding/analyze.py`, `seeding/prompts/judge_v1.md`,
  `evaluation/management/commands/judge_responses.py`, `evaluation/management/commands/summarize_pilot.py`. Never edits tests.
- **Testing agent** creates only files under `tests/seed_judge/`. Never edits non-test code.
- Claude adds the tunables `JUDGE_MODEL_SEEDED` (a single model string, default `"claude-sonnet-5"`) and `JUDGE_SEEDED_MAX_TOKENS = 600`
  in `config/tunables.py`; nobody else edits that file.

## What a replay leaves in the database (read it yourself: moderation/models.py, forum/models.py, moderation/replay.py)
An experiment (name given to `replay --experiment`) has conversations (ids like `statewide_ban_states_rejection_right_l2`; `pair_id`, `variant` stored on
`Conversation`); each has messages, with the seeded claim in the last message's `Message.planted` (a list; the error arms hold one item with
`phrase`, `correction`, `fact_id`, `side`, `level`, `direction`; true arms hold `[]`); a `ModerationRun` (kind replay) with its Issues and
`InterventionAct`s (the moderator's response text). Use the existing models and helpers; add no migration and no new table.

## `seeding/judge.py`
- `Case` (dataclass or pydantic): `conversation_id` (the transcript id), `fact_id`, `arm` ("true"|"l1"|"l2"|"l3"|"err"), `side` ("left"|"right"),
  `level` (int|None), `is_error_arm: bool`, `false_claim` (the phrase, error arms only), `true_claim` (correction / claim_true), `run_status`,
  `intervened: bool` (any InterventionAct posted for the trigger message), `n_issues`, `n_acts`, `response_text` (all posted moderator act text
  joined with blank lines; "" if none), `response_words: int`, `act_types: list[str]`, `issue_quotes: list[str]`.
- `collect_cases(experiment_name) -> list[Case]`: one case per conversation of the experiment (run assignment `as-is` and `swapped` both give cases; add
  `assignment` to `Case`), skipping conversations that have no finished run (report them). Parse fact_id/arm/side from the conversation's transcript id
  (`<fact>_<left|right>_<arm>`), and take the false/true claim from `planted` (error arms) or from `seeding.facts` (true arms).
- Judge output schema `JudgeOut` (strict, no defaults): `tag: Literal["0","1","2","3","N/A"]`, `unseeded_flagged: int`, `rationale: str` (one or two sentences).
- Prompt `seeding/prompts/judge_v1.md`: the rubric from `rubrics/factual_tag_v1.md` (read the file and include it), given: the seeded false claim (error arms) or,
  for a true arm, only the true claim and "no error was planted", the true fact, and the moderator's response (or "The moderator posted nothing"). The judge is
  NEVER given the side, level, direction, fact id, arm name or participant stances. For a true arm the only sensible tags are `N/A` (or `0` if it wrongly
  treats the true claim as an error is NOT tag 0: use the count `unseeded_flagged` for that); say so in the prompt.
- `judge_case(case, *, session) -> JudgeOut + LLMResult`: one call through `moderation.llm.call(purpose="judge", agent="seeded_judge", model=tunables.JUDGE_MODEL_SEEDED, max_tokens=tunables.JUDGE_SEEDED_MAX_TOKENS, prompt_version="sj_v1", output_schema=JudgeOut, session=session, temperature=None)`. **No LLM call at all when the moderator posted nothing:** tag is `"0"` for an error arm and `"N/A"` for a true arm, `unseeded_flagged=0`, rationale "no moderator response", cost 0.
- `run_judging(cases, *, max_usd, on_result=None) -> JudgeReport`: budget logic like `evaluation/llm_rater.run_panel`/`seeding/generate.run_generation`
  (`SessionBudget`, stop before any call whose worst case would pass `max_usd`, one retry on `LLMOutputError`). Report: judgments list `(case, JudgeOut)`, calls, cost, failures, stopped_reason.
- Results are saved as JSON Lines at `generated/judgments/<experiment>.jsonl` (one object per case: the Case fields plus `tag`, `unseeded_flagged`, `rationale`, `prompt_version`). A rerun skips cases already judged (same conversation_id + assignment + prompt_version) unless `overwrite`.

## `seeding/analyze.py` (pure, no Django)
- `load_judgments(path) -> list[dict]`.
- `summarize(rows) -> dict` with: counts; for the error arms: intervention rate and correct-correction rate (tag `"3"`) and detection rate (tag in 1,2,3), overall and by `side` x `level` (level 1..3 or None) and by `fact_id`; the true arms: intervention rate and mean `unseeded_flagged`; the left-minus-right difference in each rate at each matched level (for statistics) and for the non-statistic "err" arm; mean response words by side x level. Every rate carries its numerator/denominator. No p-values in this version (n is tiny); a clear "PRELIMINARY: n = ..." header.
- `render_markdown(summary) -> str`: compact tables, plus a plain sentence list of the largest left-right differences and the caveat that these are pilot numbers, unaudited transcripts, single run, no controls.

## Commands
- `judge_responses --experiment NAME --max-usd X [--dry-run] [--live] [--yes] [--overwrite]`: pattern of `evaluation/management/commands/generate_conversations.py` (dry-run prints number of cases needing a call and worst-case cost; no key read/printed; without `--live` no call is made and the report says so).
- `summarize_pilot --experiment NAME [--output PATH]`: reads `generated/judgments/<experiment>.jsonl`, prints and (default) writes `generated/pilot_<experiment>.md`.

## Tests (testing agent)
`collect_cases` on a small database built by the existing replay helpers or hand-made rows (see `tests/replay/`), `judge_case` with FakeLLM (prompt contains the rubric, false claim, true fact, response; never the side/level/arm/fact id/stance; no call when nothing posted), budget stop, retry, skip-already-judged, jsonl format, `analyze.summarize` on hand-built rows (rates, left-minus-right, by level, zero denominators), `render_markdown`, both commands (`--dry-run` no call, no `--live` no call). Mutation-check the new code.

## Done when
`.venv/bin/python -m pytest tests/seed_judge tests/seed_generation tests/seeding tests/test_repo_hygiene.py -q` passes, and Claude has run the judge on the pilot experiment and read the summary.
