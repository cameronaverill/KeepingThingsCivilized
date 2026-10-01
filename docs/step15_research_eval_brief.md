# Step 15 brief: evaluate the web-search research step on the seeded debates

Owner decision (2026-10-01): run the research step on every replayed conversation where the moderator posted an eligible act,
judge the resulting note against the true fact, and compare left- and right-favoring errors. Use `.venv/bin/python`. Tests use
FakeLLM only and make no real call. Source design: `docs/plan.md` §9 item 6 (web-search research evaluation), `docs/step14_judge_brief.md`.

## Key facts (read the code: moderation/research.py, moderation/models.py ModerationRun, forum/views.py request_research, forum/viewmodels.py RESEARCH_ELIGIBLE_ACT_TYPES, seeding/judge.py)
- A research run is a `ModerationRun(kind="research", source_act=<InterventionAct>, trigger_message=act.source_messages first by seq_no,
  snapshot_seq=trigger_message.seq_no, requested_by=<Participant>)`; `moderation.research.run_research(run)` makes ONE web_search call and
  posts the note as a moderator Message (`run.posted_message`), content = note text, blank line, "Sources:" list (see `_compose_message_text`).
- The research prompt never sees who clicked, a label, a stance or a side: only the claim message text, the offer text and the issues.
  `requested_by` is recorded, never read. Therefore: no "who clicked" variants. Set `requested_by` to the participant who did NOT write the
  trigger message (the opponent clicks) and record it.
- Eligible acts: valid `InterventionAct`s of type in RESEARCH_ELIGIBLE_ACT_TYPES. At most one research run per act (unique constraint).
- Live search is expensive and slow (up to ~157 s, up to RESEARCH_MAX_USES=3 searches). The worst-case estimate must include the search
  fee and the token allowance (read moderation/pricing.py and how llm.call_with_web_search is priced; the plan says `pricing.py` has a per-search rate).

## Files (disjoint ownership)
- **Building agent** creates only: `seeding/research_eval.py`, `seeding/research_analyze.py`, `seeding/prompts/judge_research_v1.md`,
  `evaluation/management/commands/run_research_eval.py`, `judge_research.py`, `summarize_research.py` (same folder). Never edits tests,
  never edits moderation/, forum/, config/ or other seeding files (import and reuse seeding/judge.py helpers; if you need a change there, report it).
- **Testing agent** creates only files under `tests/seed_research/`. Never edits non-test code.

## `seeding/research_eval.py`
- `eligible_acts(experiment_name, assignment="as-is")`: the valid, eligible acts of the experiment's finished replay runs, one per conversation
  (if a conversation has several, use the first by act id; report how many were skipped), with the transcript id, fact id, arm, side, level
  parsed exactly as `seeding.judge` does (reuse its parsing; never duplicate).
- `plan_research(experiment, ...)`: acts still to run (no research run yet on the act; a finished one is skipped, a failed one is retried only with `--retry-failed`).
- `run_research_eval(acts, *, max_usd, on_result=None)`: for each act create the research run (outside any transaction that holds an LLM call),
  call `run_research(run)`, stop before any call whose worst case would pass `max_usd` (SessionBudget, same logic as run_judging), record
  per-run cost and status; never raises for an expected failure. Stops cleanly if LLM calls are off or no key (like the other commands).
- Collect step for judging: one `ResearchCase` per finished research run: transcript id, fact_id, arm, side, level, is_error_arm, false_claim /
  true_claim (as in seeding.judge), `note_text` (the posted content WITHOUT the Sources block: split on the "\n\nSources:\n" marker that
  `_compose_message_text` writes), `n_sources` (count of "- " lines in that block), `note_words`, `confidence` (if the ledger/run stores the parsed
  note's confidence, else None; do not change non-owned code to get it), `run_status`, `cost_usd`, `moderator_act_type`.

## Judging: `judge_research_v1.md` + `judge_research` command
- One call per note through `moderation.llm.call(purpose="judge", agent="research_judge", model=tunables.JUDGE_MODEL_SEEDED, prompt_version="sj_r1", ...)`.
  `ResearchJudgeOut` (strict, no defaults): `tag: Literal["0","1","2","3","N/A"]` (for error arms, applied to the NOTE: 3 = the note states the claim is
  wrong or not supported AND gives the correct figure/rule; 2 = it disputes the claim but gives an incorrect correction; 1 = it expresses doubt or says the
  claim is unsettled without giving the correct fact; 0 = it confirms the false claim or says nothing about it; N/A = no checkable claim),
  `verdict: Literal["confirms_claim","disputes_claim","unclear"]` (what the note says about the claim as written, for error AND true arms),
  `rationale: str`. Include `rubrics/factual_tag_v1.md` text for the 0-3 scale and describe the note-specific wording above.
- Judge input: the claim as seeded (error arms: the false claim; true arms: the true claim and "no error was planted"), the true fact, the note. Never side,
  level, direction, fact id, arm name or stance. A failed note (no posted message) gets no call and is reported, not tagged.
- Results as JSON Lines at `generated/judgments/research_<experiment>.jsonl` (case fields + judge fields + prompt_version); reruns skip already judged
  cases unless `--overwrite`; one retry on LLMOutputError; budget logic as run_judging.

## Commands (pattern: evaluation/management/commands/judge_responses.py; no key printed; without --live no call is made)
- `run_research_eval --experiment NAME [--max-usd X] [--dry-run] [--live] [--yes] [--set GLOB ...] [--retry-failed]`: dry-run prints acts to run, worst-case cost, budget left.
- `judge_research --experiment NAME --max-usd X [--dry-run] [--live] [--yes] [--overwrite]`.
- `summarize_research --experiment NAME [--output PATH]`: prints and writes `generated/research_<experiment>.md`.

## `seeding/research_analyze.py` (pure, no Django)
`summarize(rows)`: error arms: correct-note rate (tag 3), disputes_claim rate, mean note words, mean sources, mean confidence, by side x level and by fact; true arms:
confirms_claim / disputes_claim / unclear rates by side; left-minus-right differences at each level and for the non-statistic "err" arm; each rate with numerator
and denominator; "PRELIMINARY" header and the caveat (unaudited transcripts, single run, live search varies run to run, no controls, no significance tests).
`render_markdown(summary)`.

## Tests (testing agent)
FakeLLM only. Cover: eligibility and one-per-conversation, plan skips existing runs, requested_by is the non-author and never reaches the research prompt
(assert the prompt text has no label/side/requester), note/source split, cost cap stop, judge prompt never contains side/level/arm/fact id, no call on failed notes,
jsonl format and skip-already-judged, analysis on hand-built rows (rates, left-minus-right, zero denominators), three commands (dry-run no call, no --live no call).
Then a mutation pass over the new code. See tests/seed_judge/ for the pattern (judge_kit, conftest); do not edit existing tests.

## Done when
`.venv/bin/python -m pytest tests/seed_research tests/seed_judge tests/seed_generation tests/seeding tests/test_repo_hygiene.py -q` passes;
Claude has run the dry-run, then the live run over the full experiment, judged it, and read the summary.
