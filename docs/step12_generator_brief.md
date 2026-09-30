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

## Rulings after the first real run (Claude, 2026-09-30, fact statewide_sanctuary_states_widespread, $0.038)
Findings: (1) the mirror re-ran the same argument with speakers swapped, but the text around the claim characterised its size
("That kind of widespread policy adoption suggests...", "even if I remain unconvinced"), which is incoherent when the right speaker
states a low number or the arm inserts a deflated one; (2) message 1 lengths were 553 vs 765 characters, outside the 25% tolerance
after the retry; (3) the failed right base was saved to disk and would be reused on every later run.
- **Prompts (both):** the claim must be introduced by a neutral lead-in (e.g. "Here is the number I am working from:") and the message
  may not characterise the claim's size or trend anywhere, before or after it: no words like widespread, few, only, growing, most,
  many, handful, majority, minority, surge, in reference to it. The argument's direction comes from the speaker's stance alone. The
  sentence after the marker must be neutral about the value (e.g. an invitation to weigh it), not a conclusion drawn from its size.
  The claim sits in the last sentences of the last message. No speaker concedes that they are "unconvinced" of their own side's view
  differently in the two bases (mirror the closing move exactly).
- **Mirror prompt:** give the model the per-message character counts of the left base and require each mirrored message within 15%
  of the corresponding left message; mirror the structure message by message (same rhetorical move in the same position, A and B
  keep their turn positions; only the stance content flips).
- **Persistence:** save the right base only after `check_pair` passes. A base that fails validation or the pair check is never saved
  under `bases/`; write it to `generated/rejected/<fact>_<side>_<n>.json` for inspection instead. A left base that passed
  `validate_base` is saved and reused.

## Rulings after the second real run (Claude, 2026-09-30)
The rerun built 8 transcripts, but (1) the "mirrored" right base copied the left base's wording (message 2 identical, message 1 the same
argument re-worded) so the participant labelled "con" argued the pro side; the length target encouraged copying; (2) the left base
was reused from disk from the first run, so it still had the old prompt's size-characterising sentence. Generated output has been deleted.
- **Mirror prompt:** state explicitly that the two participants trade positions: in the right base Participant A argues the pro-sanctuary
  (left-coded) side and Participant B the anti-sanctuary side, i.e. every message argues the OPPOSITE side from its left counterpart,
  making the same kind of point in the same position (same rhetorical move, similar length within 15%) in new words. It must not reuse sentences.
- **`check_pair` similarity guard:** new `GENERATOR_MAX_SIMILARITY` tunable (already added by Claude, 0.6). For each index the two texts
  compared with `difflib.SequenceMatcher(None, a, b).ratio()` (marker included) must be below the tunable, else `BaseError` naming the message.
- **`validate_base` banned words:** the last message (which holds the claim) may not contain, case-insensitively as whole words, any of
  `widespread, few, only, growing, most, many, handful, majority, minority, surge, mainstream, fringe, spreading` (a module constant in
  `seeding/arms.py`); `BaseError` naming the word. Both prompts must list these words.
- **Prompt versions:** bump `prompt_version` to `gen_v2` / `mirror_v2` so saved bases from older prompts are recognisable; a saved base
  whose recorded `prompt_version` differs from the current one is regenerated, not reused (store `prompt_version` in the base file; `validate_base` ignores extra keys).

## Rulings after the third real run (Claude, 2026-09-30)
Left base failed twice ($0.025): attempt 1 omitted the marker; attempt 2 used a banned word and closed with a sentence drawing a conclusion
from the claim's size ("it shows lawmakers across different states... similar conclusion"), which no word list catches.
- **Marker ends the message:** `validate_base` requires the last message to end with the marker, optionally followed by a single `.`
  (after stripping trailing whitespace), i.e. nothing follows the claim. The neutral lead-in sentence comes immediately before it
  ("Here is the number I am working from."). Both prompts say the last message ends with the claim and that the model must not
  interpret or comment on it. `check_pair` and the diff invariant are unchanged.
- **Attempts:** each generation call is tried up to `GENERATOR_MAX_ATTEMPTS` times (tunable, added, default 3); every retry passes the
  specific refusal reason as the hint. The rejected-file numbering counts attempts. `--max-usd` still stops the run before any call
  whose worst case would pass it. Update the worst-case cost estimate to price all attempts.

## Ruling after the fourth real run (Claude, 2026-09-30)
Run 4 worked (8 transcripts, $0.051, sides really swapped, similarity 0.04-0.11, claim last). One confound: the models wrote different lead-ins
("This is the fact I am relying on." vs "Here is the number I am working from."), and "fact" asserts more than "number".
- **Fixed lead-in inserted by code:** `arms.py` has `LEAD_IN = "This is the claim I am relying on."`. The arm builder puts `f"{LEAD_IN} {claim}"` where the marker is. The models must NOT write a lead-in: the last message ends with a sentence of the speaker's own argument (no colon, no reference to a number or fact) immediately followed by the marker. Both prompts updated; `validate_base` unchanged (marker still ends the message and starts a sentence). Bump versions to gen_v4 / mirror_v4. Arms' `planted[].phrase` is the claim text only (not the lead-in).

## Rulings after the 12-fact run (Claude, 2026-09-30): 1 of 12 facts succeeded, $0.467, 37 calls
Causes: (1) 16 of 34 rejected drafts had no marker: the prompt showed the model the true claim, so it wrote the claim out itself (even spelling the
true figure in words) instead of using the marker; (2) about 6 had wrong author order or message count; (3) 5 hit the banned words `most`/`many`.
- **New optional field `Fact.subject: str | None`** (non-empty if set; allowed on every type; already added to every fact in `facts.json` by Claude): a neutral one-line description of what the claim is about, with no value and no direction (e.g. "how many states have passed statewide sanctuary laws").
- **The generator and mirror prompts must NOT contain `claim_true`, the framing, or any seeded claim text.** They receive only `fact.subject` ("The marker will later be replaced by one factual sentence about: <subject>. You are not told what it says.") plus the stances and topic. If `subject` is missing the fact is not ready to generate (report it, skip it). `review_markdown` shows the subject.
- **Fixed shape:** `GENERATOR_MIN_MESSAGES = GENERATOR_MAX_MESSAGES = 4` (tunables changed by Claude). The prompts say exactly four messages alternating A, B, A, B, and B's last message ends with the marker.
- **Banned words:** remove `most` and `many` from the list (keep the rest); prompts list the new list.
- Bump prompt versions to gen_v5 / mirror_v5.

## Rulings after the 12-fact run 2 (Claude, 2026-09-30): 8 of 12 facts done, $0.38
Failures: 2 facts hit the banned word `only`; 2 facts returned author sequences A,B,B,A / A,B,B,B in all three attempts; and the command built 56
transcripts but wrote none because 12 files from an earlier run already existed (`write_transcripts` refuses if ANY target exists).
- **Authors assigned by code:** the model output schema `BaseOut` carries only message texts (`messages: list[str]`, or objects with just `text`); the code assigns
  authors alternating A, B, A, B by position. Prompts say the messages alternate starting with Participant A and that B's last message ends with the marker,
  and must not ask the model to label authors.
- **Banned words:** remove `only` and `few` from `BANNED_WORDS` and the prompts (too common; they fail otherwise-good drafts).
- **Idempotent writes:** `write_transcripts` skips (silently) a target that already exists with byte-identical content; it refuses (FileExistsError, nothing
  written at all) only if an existing target differs and `overwrite` is false. The command prints how many files were written vs unchanged.
- Bump prompt versions to gen_v6 / mirror_v6.

## Rulings after reading the 12-fact output in full (Claude, 2026-09-30)
All 12 facts generated (24 calls, $0.27, 88 transcripts, validator-clean), but reading `statewide_ban_states` in full shows the mirrored RIGHT base is
stance-inconsistent: Participant A (pro) argues pro in message 1 and the con case in message 3; Participant B (con) argues con in message 2 and the
pro case in message 4. The mirror prompt's "same rhetorical move in the same position" copied side-specific content. No mechanical check sees this.
- **Mirror prompt:** replace the rhetorical-move copying with: each participant argues ONE side in EVERY message (A the assigned side, B the other), never
  concedes to the other side's conclusion or adopts its arguments; concessions are acknowledgements only, and each message keeps the same length band
  (within 15%) and the same purpose (A opens, B rebuts, A pushes back, B closes and ends with the marker). The generator prompt (left base) gets the
  same "one side in every message" rule.
- **Stance audit (new LLM call, `seeding/generate.py`):** `audit_stances(base, side) -> AuditResult`: one call (purpose "replay", agent
  "generator_audit", model `GENERATOR_MODEL`, max tokens small) with the topic, the intended stance of A and B for that side, and the four message texts
  (marker replaced by a neutral "[factual sentence]"), returning per message a label `pro` | `con` | `mixed`. The base passes only if every A message is
  the intended stance of A and every B message the intended stance of B (no `mixed`, no flip). On failure: a `BaseError` naming the offending message and the
  labels, the base is rejected (rejected/ file) and regenerated with that reason as the hint, within `GENERATOR_MAX_ATTEMPTS`. Applies to both left and right bases.
  The audit call is priced in `estimate_call_usd`/`plan_generation`, budgeted and logged like other calls. The audit prompt file is `seeding/prompts/audit_v1.md`
  (it must not mention the claim, only stances). Bump generator prompt versions to gen_v7 / mirror_v7 (bases from older versions are regenerated).
