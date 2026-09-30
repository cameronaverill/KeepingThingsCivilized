# Step 12 brief: conversation generator

(The older `docs/step12_brief.md` is the earlier Step 12 about the evaluation models; not this.)
Source of truth: `docs/plan.md` §9 items 3-4 and §14 Step 12. This is the contract for both agents. Use `.venv/bin/python`.
No real API call is ever made in this step: tests use `FakeLLM`, and the command's live mode stays unused until the owner
confirms the Console spend limit (CLAUDE.md).

## Files (disjoint ownership)
- **Building agent** creates/edits only: `seeding/generate.py`, `seeding/arms.py`, `seeding/prompts/generator_v1.md`,
  `seeding/prompts/mirror_v1.md`, `evaluation/management/commands/generate_conversations.py`. Never edits tests.
- **Testing agent** creates only files under `tests/seed_generation/`. Never edits non-test code.
- Already done by Claude (nobody else edits): tunables `GENERATOR_MODEL`, `GENERATOR_MAX_TOKENS`, `GENERATOR_MIN_MESSAGES`,
  `GENERATOR_MAX_MESSAGES`, `GENERATOR_LENGTH_TOLERANCE`, `GENERATOR_TOPIC_TITLE`, `GENERATOR_TOPIC_PROPOSITION` in `config/tunables.py`.
  Read them as `from config import tunables` at call time.
- `seeding/` stays free of Django and anthropic imports **except** `seeding/generate.py`, which may import `moderation.llm`
  (the only gateway) and `moderation.budget`; `seeding/arms.py` stays pure (no Django, no network).

## Concepts and conventions (match the existing golden pairs, see golden/transcripts/sanctuary_*_left.json)
- Side is `left` or `right`. The proposition is the left-coded ("pro") side. In the **left base**, Participant B argues the
  left-coded (pro) side and Participant A the other; in the **right base** the stances are swapped. `stances` is
  `{"Participant A": "con", "Participant B": "pro"}` for left, `{"Participant A": "pro", "Participant B": "con"}` for right.
- The claim is always stated by **Participant B in the last message**, which is the trigger (`trigger_seq` = its `seq`).
- A **base** is `{"side", "fact_id", "messages": [{"seq", "author", "text"}...]}` with `author` alternating starting with
  "Participant A", ending with "Participant B", `GENERATOR_MIN_MESSAGES <= len <= GENERATOR_MAX_MESSAGES`, `seq` 1..n. The
  last message contains the literal marker `[[CLAIM]]` **exactly once** and no other message contains it. The marker
  stands where a full sentence starts (the claim text is a full sentence with its own capital letter); the base text must
  not put the marker inside a quotation or after a colon.
- Claim text is inserted as `claim.rstrip(".")` (the base supplies the sentence's punctuation).

## `seeding/arms.py` (pure)
- `MARKER = "[[CLAIM]]"`.
- `validate_base(base: dict) -> None`: raises `BaseError(ValueError)` on any violation above (count range, authors,
  seq, marker count/location, empty texts, a message over `MAX_MESSAGE_CHARS`).
- `check_pair(left_base, right_base) -> None`: raises `BaseError` unless same `fact_id`, same message count, same authors
  sequence, and for each index the two texts' lengths (characters, marker counted as its own length) differ by at most
  `GENERATOR_LENGTH_TOLERANCE` of the longer one; also each base individually valid.
- `arm_specs(fact) -> list[dict]`: for a ready `Fact`, the arms in this fixed order: for side left then right: the true arm,
  then the side's seeds. Each spec: `{"side", "arm", "claim", "seed"}` with `arm` = `"true"`, `"l1"`, `"l2"`, `"l3"` for a
  statistic's seeds, `"err"` for a non-statistic's single seed; `claim` = `fact.claim_true` for true arms else
  `seed.false_claim`; `seed` = the `Seed` or None. A statistic gives 8 specs, a non-statistic 4. Uses
  `seeding.seeds.build_seeds` (left seeds pair with the left base, right seeds with the right base).
- `build_transcripts(fact, left_base, right_base) -> list[dict]`: runs `check_pair`, then for each spec builds a replay
  transcript dict:
  - `id` = `f"{fact.id}_{side}_{arm}"` (must match `[a-z0-9_]+`), `pair_id` = `f"{fact.id}_{'true' if arm == 'true' else arm}"`,
    `variant` = side, `description` (plain text saying which arm), `topic` = `{"title": GENERATOR_TOPIC_TITLE,
    "proposition": GENERATOR_TOPIC_PROPOSITION}`, `stances` per the convention, `trigger_seq` = last seq,
    `messages` = the base messages with the marker replaced (only the last message changes), each with `planted`
    (`[]` except the last message of an error arm).
  - The error arm's last message `planted` = one item: `{"dimension": "factual_accuracy", "phrase": <the inserted claim text
    as it appears, without the trailing period>, "intensity": <1|2|3 from the arm, 2 for "err">, "correction":
    fact.claim_true, "fact_id", "side", "level": <int or None>, "direction": <str or None>}`. (`intensity` only satisfies the replay
    schema; the real severity is `level`.)
  - Extra top-level key `seed`: `{"fact_id", "arm", "side", "level", "direction", "false_claim": <or None>}`.
  - **Diff invariant** (asserted inside `build_transcripts`, `BaseError` if broken): within one side, every arm's texts equal
    the base with the marker replaced by that arm's claim text, and all messages other than the last are byte-identical across arms.
  - Each transcript must pass the replay validator `moderation.management.commands.spike.validate_transcript` (call it in tests).
- `write_transcripts(transcripts, directory) -> list[Path]`: writes `<id>.json` (indent 2, UTF-8, trailing newline), refusing
  to overwrite an existing file unless `overwrite=True`. Default output directory `generated/transcripts/` (create it).
- No randomness; same input gives byte-identical output.

## `seeding/generate.py` (LLM calls, through the gateway only)
- Pydantic output schema `BaseOut`: `messages: list[MessageOut]` where `MessageOut` is `{author: Literal["Participant A","Participant B"], text: str}` (strict, extra forbidden, no defaults).
- `generate_base(fact, side, *, left_base=None, session, conversation_hint=None) -> tuple[dict, LLMResult]`: builds the prompt from `seeding/prompts/generator_v1.md` (left base, or when `side == "right"` and `left_base` is given, from `seeding/prompts/mirror_v1.md` with the left base text as the template to mirror: swap the two stances, keep message count, order, structure and lengths; the marker stays in the last message). The prompt states the topic, the stances, the fact's `framing` and the claim's role, and tells the model the marker `[[CLAIM]]` must appear once in Participant B's last message and that neither participant may state any number, law or other checkable fact about the claim except through the marker (other messages must not contain the claim's figure or paraphrase it). Calls `moderation.llm.call(purpose="replay", agent="generator", model=tunables.GENERATOR_MODEL, max_tokens=tunables.GENERATOR_MAX_TOKENS, prompt_version="gen_v1"|"mirror_v1", output_schema=BaseOut, session=session, temperature=None)`. Converts to a base dict (seq 1..n), calls `validate_base`. If validation fails, raises `BaseError`; the caller may retry once (see below).
- `estimate_call_usd(fact, side, left_base=None) -> Decimal`: worst-case cost of one generation call using `budget.estimate_input_tokens` and `budget.reservation_usd` (see `evaluation/llm_rater.py:206-213` for the pattern).
- `plan_generation(facts) -> list[Item]` and `run_generation(facts, *, max_usd, session budget, existing bases skipped) -> Report`: for each ready fact, generate the left base, then the mirror right base; on `BaseError` or `LLMOutputError`, retry that call once (`attempt=2`); after `check_pair` failure regenerate the right base once; then `build_transcripts` and return them plus a report (counts, cost, failures). Before each call stop if `spent + worst_case_usd > max_usd` (copy `run_panel`'s logic in `evaluation/llm_rater.py:600-630`). Never call inside `transaction.atomic`. Bases are saved as `generated/bases/<fact_id>_<side>.json` and reused on later runs unless `overwrite`.
- Only facts with `ready()` are used; a not-ready fact is skipped and reported.

## `evaluation/management/commands/generate_conversations.py`
Copy the pattern of `evaluation/management/commands/run_raters.py` (`_parse_max_usd`, `_remaining_budget`, `_dry_run`, `_run`, `--live`, `--yes`, `override_settings(LLM_ENABLED=...)`). Options: `--max-usd` (required unless `--dry-run`), `--dry-run` (prints number of facts, calls, worst-case cost, budget left, writes nothing and calls nothing), `--live`, `--yes`, `--facts id1,id2` (subset), `--overwrite`, `--output-dir`. Without `--live` no LLM call is made (all calls are refused as disabled and reported); with `--live` it needs `ANTHROPIC_API_KEY`, prints the worst case, and asks for a typed "yes" unless `--yes`. It never reads or prints the key.

## Prompts
Plain markdown files, versioned by the `prompt_version` string. Neutral wording; each participant argues civilly and equally
well; no insults, no other factual claims that could themselves be wrong (the generator must avoid stating any other
statistic, law or date); equal length of A's and B's turns. The prompt never asks for a "biased" or "one-sided" conversation.

## Tests (testing agent; from this contract)
Pure `arms.py`: validate_base (each rule), check_pair (each rule, tolerance boundary), arm_specs counts/order (8 and 4), build_transcripts
(ids, pair_id, variant, stances, trigger_seq, planted content, diff invariant, only the last message differs within a side, marker gone, validator passes
via `spike.validate_transcript`), write_transcripts (files, no-overwrite, determinism), and every ready shipped fact builds with a
stub base (88 transcripts). `generate.py` with `FakeLLM` (see `tests/llm_raters/conftest.py`): prompts contain the topic, stance and marker
instructions and never contain `variant`/`pair_id`/other-arm claims (the true claim only where the prompt is allowed to say what the fact is); retry once on invalid base;
budget stop; mirror uses the left base; `llm.call` args (purpose "replay", agent "generator", model tunable). The command: `--dry-run` writes nothing and
makes no call; missing `--max-usd` errors; without `--live` no call is made; `--facts` subset; no key is read or printed. A test that `seeding/arms.py` imports neither django nor anthropic.

## Done when
`.venv/bin/python -m pytest tests/seed_generation tests/seeding tests/test_repo_hygiene.py -q` passes; the testing agent's mutation
pass finds no uncaught mutant that should have been caught; a `--dry-run` shows the worst-case cost for all 12 facts.

## Rulings after the first build (Claude, 2026-09-30)
- **Marker position:** `validate_base` requires the marker to start a sentence: either the very start of the message, or preceded (ignoring spaces) by `.`, `!` or `?`. Reject any other position (this replaces the narrower quote/colon rules, which stay implied).
- **No digits in a base:** `validate_base` rejects any ASCII digit `0-9` anywhere in a base's message texts (the generator must write any number of its own in words, and the claim's figure appears only through the marker). The prompts must say so.
- **`generate_base(..., attempt=1)`** and the retry hint are accepted as built. Prompts may include `claim_true` and the fact's framing (kept).
- **Step 11 purity test:** `tests/seeding/test_review_rubric_module.py::TestPurity` is updated (tests only) to allow the new `arms.py`, `generate.py` and `prompts/` files, and to exempt only `seeding/generate.py` from the no-django/no-moderation import ban; `arms.py` stays pure.
