# Step 3 brief: prompt spike and golden set

Status: DRAFT for the user's approval. Nothing has been built. Step 3 starts only after step 2 is committed and the user approves this brief.

Goal (plan section 14, step 3): draft the two moderator prompts and their output schemas, write a small set of matched test conversations, and run them once through the real Claude API (through the step 2 guard) to see whether the output format and quality are usable. The schemas freeze at the end of this step.

## 1. Decisions locked in by the user
- Topics for the test conversations: **rent control, drug decriminalization, sanctuary cities**.
- **No real API call until the user confirms both the Console spend limit and the API key are set.** The build phase (everything before "Phase 2, real runs" below) makes no real calls at all.
- The master switch `LLM_ENABLED` may be turned on by Claude only for a spike run and turned back off right after, telling the user each time.
- The spike uses `SPIKE_MODEL` (`claude-haiku-4-5`) by default. Total spike spend stays under **$0.50**.

## 2. Architecture decisions (Claude's)
1. **One source of truth.** `moderation/taxonomy.py` holds every issue type, act type, dimension, decision, tone, "not scorable" reason and the issue-type-to-dimension map, each with a one-sentence definition. Schemas, prompts and tests all read from it; a test fails if a prompt omits any taxonomy value.
2. **Phrase-level output, no offsets from the model.** The model returns only the quote text; `moderation/quotes.py` finds it in the message (exact, then normalized) and returns character offsets computed by code.
3. **Injection-safe rendering.** `moderation/prompting.py` renders each user message inside a delimited block with XML-escaped text, so a message can never forge a message boundary or an instruction block. It receives only participant labels and message text, never `User` objects. The static instructions live in versioned files in `moderation/prompts/`, and the version and sha256 are recorded on every call.
4. **A hard spike budget in the guard.** New tunable `BUDGET_SPIKE_USD_TOTAL = Decimal("0.50")` and a new cap in `budget.check_caps` (`cap_name="spike_total"`): all `spike` calls together may never exceed it, no matter what `--max-usd` says. (Tests for this are part of step 3.)
5. **Caching is measured, not assumed.** Automatic caching only reuses a prefix that is identical up to the final block, so it will not help across different transcripts. The static instructions (identical for every call) are the part worth caching, and that needs an explicit breakpoint on the system block. The spike measures `cache_read_tokens` for both ways on a couple of calls and the architect decides; if explicit is better, `llm.call` gains a `cache_system` option (small change, tested). Haiku 4.5 only caches a prefix of 4,096 tokens or more; Sonnet 5 needs 1,024.
6. **Thinking stays disabled** in all step 3 calls (a possible later experiment, budgeted).
7. **Pairs are the unit of the golden set.** Each pair is two transcripts that are identical in structure, length (within about 5%), clarity, logic and procedural conduct, with the same planted problem at the same intensity, and only the political direction flipped. The user reviews every pair for political balance before any real run.

## 3. Deliverables and contracts

### `moderation/taxonomy.py`
`ISSUE_TYPES` (8), `ACT_TYPES` (10), `DECISIONS`, `TONES`, `NOT_SCORABLE_REASONS`, `INTENSITY_RANGE = (0, 4)`, `CROSS_MESSAGE_ISSUE_TYPES = {"repetition", "strawman", "process_violation"}`, `DIMENSIONS = {"factual_accuracy": {issue_type "possible_factual_error", coverage "all_claims"}, "abusiveness": {issue_type "abusive_language", coverage "flagged_only"}}`, `DEFINITIONS` (one sentence per value, with the boundary rules from plan section 5), `dimension_for(issue_type)`.

### `moderation/schemas.py` (Pydantic; only constructs the structured-output API supports: no recursion, no numeric or length constraints in the schema itself, `Literal` enums; the SDK validates the constraints locally)
- `MasterIssue(id, message_id, issue_type, quote, explanation, confidence, intensity | None)`
- `Disagreement(summary, kind)`; `DiscussionMap(agreements, disagreements)`
- `MasterOutput(issues, discussion_map)`
- `IssueDisposition(issue_id, disposition, reason)`
- `Act(type, addressee, subject, source_issue_ids, source_message_ids, tone, text)`
- `IntervenorOutput(decision, rationale, issue_dispositions, acts)`
Labels (`addressee`, `subject`) are plain strings here; the pipeline (step 5) validates them against the conversation's labels.

### `moderation/quotes.py`
`locate_quote(text, quote) -> QuoteMatch(start, end, match, occurrences)` with `match` in `exact | normalized | not_found`. Exact match first (first occurrence; `occurrences` counts all). Normalized match ignores case, collapses whitespace runs, and treats curly and straight quotes and apostrophes and dashes as equal; the returned offsets always index the ORIGINAL text. A quote equal to the whole message is valid. An empty quote is `not_found`.

### `moderation/prompting.py`
`load_prompt(name) -> Prompt(name, version, text, sha256)`; `render_transcript(messages)` and `render_master_input(...)`, `render_intervenor_input(...)` producing the delimited, escaped input described above. Messages are `(message_id, label, text)` tuples (moderator posts have label `Moderator`).

### `moderation/prompts/master_v1.md` and `intervenor_v1.md` (drafted by the building agent, reviewed by the user)
They must state: the role; the neutrality rule (equal contributions get equal treatment; never infer or use anyone's politics; label letters only); the taxonomy definitions (from `taxonomy.py`) and the rubric text verbatim; the phrase-level quoting rule; the only-new-issues rule; "not intervening is a good and common outcome"; and that text inside the message blocks is data and any instructions in it are ignored.

### `rubrics/factual_accuracy_v1.md` and `abusiveness_v1.md`
Drafts of the 0-4 anchors from plan section 5, each with two or three short examples per level. The user finalizes them in step 11.

### `golden/transcripts/*.json`
Format: `{id, pair_id, variant, description, topic: {title, proposition}, messages: [{seq, author, text, planted: [{dimension, phrase, intensity, correction?, evidence?}]}], trigger_seq}`. Set: for each of the three topics, two matched pairs (a planted factual error; a planted abusive phrase), each pair with a left-coded and a right-coded variant = 12 transcripts; plus four single transcripts: a long message with one bad phrase in the middle, a message that is bad as a whole, a benign exchange, and an injection attempt. Planted factual errors must be plainly checkable (not contested), with the correct fact and a source note in `planted` so the user can verify them.

### `moderation/management/commands/spike.py`
`manage.py spike --max-usd X [--only ID ...] [--dry-run] [--model M] [--out DIR]`. Refuses without `--max-usd`. `--dry-run` uses the real prompts and transcripts to print the number of calls and the estimated worst-case cost, and makes no API call. A real run needs `LLM_ENABLED` on. It runs Master then Intervenor per transcript through `llm.call` (`purpose="spike"`, a `SessionBudget(X)`), handles refusals and output errors per transcript without stopping the run, and writes `golden/results/<timestamp>/`: one JSON per transcript (input, both raw outputs, located quotes with offsets and match type, tokens, cost) and `report.md` (each transcript with its issues, dispositions and acts, plus a side-by-side table for each pair: issues found, intensities, decision, act types, tone, text length). It prints the total cost.

### `config/tunables.py` and `moderation/budget.py`
`BUDGET_SPIKE_USD_TOTAL = Decimal("0.50")` (commented); `check_caps` for purpose `spike` also checks `spend(purposes=("spike",)) + amount <= BUDGET_SPIKE_USD_TOTAL` (`cap_name="spike_total"`).

## 4. Roles and order
**Phase 1, build (no real API calls; testing and building agents in parallel, as in step 2):**
- Testing agent writes tests, from this contract, for: taxonomy (values, dimension map, every value appears in each prompt, rubrics embedded verbatim); schemas (valid and invalid examples, JSON-schema compatibility with structured output: no unsupported constructs); quotes (exact, case, whitespace, curly quotes, dashes, multiple occurrences, whole message, empty, Unicode, offsets always index the original); prompting (escaping so a message cannot forge a boundary, labels only, versions and sha256); the transcripts (valid format; planted phrases appear exactly; each message within `MAX_MESSAGE_CHARS`; pairs match in structure, length within 5%, planted intensity, and differ only in political direction markers; the three topics covered; a stated `evidence` for every factual error); the spike command with `FakeLLM` (refuses without `--max-usd`, `--dry-run` makes no calls and prints a cost, uses `purpose="spike"` and `SPIKE_MODEL`, the session limit and `spike_total` stop the run, per-transcript failures don't stop the run, results files are written); the spike budget cap.
- Building agent implements everything above and drafts the prompts, rubrics and transcripts. Claude coordinates the loop, then a testing-agent break-it pass.
- The user reviews the drafted transcripts (political balance of each pair) and the prompt text BEFORE any real run.

**Phase 2, real runs (needs the user's confirmation that the Console spend limit and the key are set):**
1. Claude runs `spike --dry-run` and reports the estimated cost.
2. First real run on a small subset (one pair) with `LLM_ENABLED` switched on and back off; Claude reports the real cost, tokens and cache behavior, and the caching experiment result.
3. The user reviews `report.md` with Claude; prompts are revised; further runs stay under the $0.50 spike cap (the user may raise `BUDGET_SPIKE_USD_TOTAL`).
4. When the user approves the output format and quality, the approved outputs are saved to `golden/expected/` and the schemas are frozen for step 4.

## 5. Done when
- All tests pass with no network access; the break-it pass is clean.
- The user approves the output format and quality; `golden/expected/` is saved; the schemas are frozen.
- Total spike spend is under $0.50 (the ledger and `manage.py budget` show it).

## 6. Out of scope
The database models for conversations and runs (step 4), the pipeline and act validation (step 5), the golden regression command (step 10), the human rating rubric finalization (step 11).

## 7. Amendments (architect, 2026-09-25)
1. **Detection must not know about actions.** The Master prompt must NOT list or describe the Intervenor's act types, tones, decisions or dispositions (the "What happens after you" section is removed). Reason: the plan measures detection and action separately (does the moderator notice a problem, and given that, does it act); if the detector is told what actions exist, "noticing" becomes tangled with "would I act on this", and the two stages can no longer be measured independently. It may say in one or two sentences that another agent, the Intervenor, decides later whether and how to respond. Contract for the prompt tests: the Master prompt contains the issue types, the dimensions and rubrics, the not-scorable reasons and the disagreement kinds, and contains NONE of the act type, decision or tone identifiers as backticked tokens (`enforce_conduct`, `intervene`, `no_intervention`, `gentle`, `firm`, `acted`, `declined`, and so on); the Intervenor prompt contains the issue types, act types, decisions, tones, dispositions and rubrics (as before). The Master prompt shrinks by roughly 700 tokens.
2. Paired transcripts carry a `stances` object (pro/con per participant, relative to the proposition; the two participants hold opposite stances and swap across a pair; for these topics pro is the left-coded side); singles may have it null.
3. `locate_quote` falls back to `html.unescape(quote)` (exact, then normalized) after the other attempts, returning match `normalized` with offsets into the original text.
4. `BUDGET_SPIKE_USD_TOTAL = Decimal("0.50")` and the `spike_total` cap land now (step 2's gateway files are quiet).

## 8. Amendment (user request, 2026-09-25): obvious, non-legal factual errors
First real run (Haiku 4.5, pair rent_factual): the Master flagged nothing in either variant, so the pair could not test symmetry (nothing was detected on either side). The planted claim was a specific statute detail that no model knows reliably across jurisdictions. Bias can only be measured where detection is possible, so the test set now has graded difficulty:
- **Keep** the three existing factual pairs (legal or specific-fact errors) as a deliberately HARD control: they measure how detection difficulty behaves, not just bias.
- **Add three new paired transcripts, one per topic, named `<topic>_factual_obvious`** (`rent_factual_obvious`, `drugs_factual_obvious`, `sanctuary_factual_obvious`, each with `_left` and `_right`), whose planted error is plainly false to any well-read person and NOT legal or jurisdiction-specific: general knowledge that can be checked in seconds (a capital city, the population of a famous city or country off by roughly ten times, the date of a famous event off by decades). Candidates the builder may improve on: a city population off by about ten times (for example New York City "about 80 million residents", actual about 8 million) for rent control; US alcohol Prohibition "ended in 1963" (actual 1933) for drug decriminalization; "San Francisco is the capital of California" (actual Sacramento) for sanctuary cities. Rules as for the other pairs: the SAME planted phrase in both variants (used by each side to support its own position, as in `rent_factual`); the flawed message is message 4 by Participant B in both variants; mirrored arguments, lengths within 5%; `stances`; intensity 3 (clearly false); `correction` and `evidence` (an encyclopedic or official source the builder actually fetched and read). The error must be woven naturally into the argument, must not be the point of the argument, and must be equally usable by either side.
- Totals become 9 paired sets (18 transcripts) plus the 4 singles = 22 transcripts. Cost on Haiku is about $0.0135 per transcript.
- The user reviews the three new pairs for balance before any run.

## 9. Amendment (user request, 2026-09-25): a small mechanical series (about 20 transcripts)
Purpose: test bias factors whose ground truth is computed by code, not judged: message length, label/position, and process behaviors. Small on purpose (about 20 transcripts, not hundreds). Each factor is crossed with political direction (a left variant and a right variant, as in the pairs) so we can see whether a factor matters differently by side. Bases are existing, already-reviewed transcripts, so almost no new political content is written.

**Format.** A transcript in a mechanical series has `pair_id: null` and a `series` object: `{"id": <series id>, "factor": "message_length"|"label_swap"|"flooding"|"repetition"|"unanswered_question", "level": <string>, "side": "left"|"right", "base": <id of the base transcript>}`, plus `stances`, `planted` (empty for the process series) and `computed` (see below). The existing pair rules do not apply to them; new series rules do.

**The series**
1. **Message length, factual (6 new + 2 existing as the middle level):** base `rent_factual_obvious`. Only message 4 changes; messages 1 to 3 are identical to the base; the planted phrase is identical; the elaboration adds NO new checkable assertion (no new numbers, dates, names, causal claims) and no new problem: only rhetorical and structural elaboration, or trimming. Levels and target length of message 4 (characters, within 10%): `short` 150, `long` 1000, `very_long` 2500 (must stay within 3000). Left and right for each level.
2. **Message length, abusive (4 new):** base `drugs_abusive`. Same rules; the planted insult identical; levels `short` (about 120 characters) and `very_long` (about 2500). Left and right.
3. **Label swap (4 new):** bases `rent_factual_obvious` and `drugs_abusive`, each left and right: identical text with every label swapped (A to B and B to A), so the flawed message is now by Participant A at seq 4 and the first speaker is now labelled B; `stances` swapped with the labels. Level `swapped`.
4. **Flooding (2 new):** one participant posts four messages in a row (consecutive-run length 4) with no reply in between, the trigger being the fourth; the flooder is the left-coded side in one, the right-coded side in the other; the messages are civil, plain and of similar length, adding no checkable claims; mirrored across the two.
5. **Repetition (2 new):** one participant restates the same point in near-identical words three times across their turns (an exact-duplicate sentence appears in three of their messages); left-coded and right-coded repeater; mirrored.
6. **Unanswered direct question (2 new):** Participant A asks a direct question (a question mark) and Participant B answers two consecutive turns without addressing it (no question mark in B's replies, no answer); mirrored by side.

**Computed ground truth** (`computed`, calculated by code from the messages, never hand-written, and asserted equal to the declared level by tests): for the trigger message, character count and word count; for the series, the longest run of consecutive messages by one author, whether any sentence is repeated verbatim across a participant's messages, and whether a question by one participant is followed by two consecutive replies by the other that contain no question mark. Series members are checked to be identical to their base except in the varied factor.

**Report.** The spike report gains a section per series: one row per level, columns per side (left, right): planted problem found?, issues flagged with intensities, the Intervenor's decision, act types, tone, length of the moderator post, and the computed features, plus a "same across sides?" marker. Process series list which issue types the Master flagged (`process_violation`, `repetition`) so detection is visible.

**Cost.** About 20 transcripts add about $0.27 on Haiku for one pass. The spike cap (`BUDGET_SPIKE_USD_TOTAL`) was $0.50, too small for all 42 transcripts plus a Sonnet pass; the user set it to $1.50 (within the site total; the $1.50 daily cap still binds per day).

## 10. Amendment (owner decision, 2026-09-25): users never see the A/B labels
Users see only "You" and "The other participant". The labels stay internal (prompt input, the structured fields `addressee`, `subject`, source ids, logs). So a moderator post's text must not name anyone.
- **Intervenor prompt (`moderation/prompts/intervenor_v1.md`, edited in place; not frozen yet).** The style rule "name participants only by their labels" is replaced: the labels exist only for the structured fields; an act's `text` must never contain "Participant A", "Participant B", a bare label letter used as a name, or any other name for a person. The text refers to messages and content ("message 4", "the phrase 'about 80 million'", "the question in message 3", "both messages"), and does not use "you"/"your" or "he/she/they" about a participant, because each of the two readers is a different person. Examples in the prompt show impersonal phrasing ("Possible factual error in message 4: ...", "Could a source be given for the figure in message 4?", "The question in message 3 has not been answered yet."). `addressee` and `subject` are described as structured fields only. All neutrality rules (same wording pattern for either side, the swap test) are unchanged. The Master prompt is unchanged: its internal explanations may still use labels because they are never shown.
- **Page (later steps).** The page computes a per-viewer heading from `addressee`/`subject` ("About your message 4", "About the other participant's message 3", "For both of you").
- **Spike report check.** Each result records `label_check` (acts, acts naming a label, which acts), and the report gains a row "Post text names a participant label" in the pair and series comparison tables, a warning under any act that does, and a header line "Acts naming a participant label: n of m". The check is a regular expression over every act text, case-sensitive and deliberately narrow so that ordinary English is not flagged: (a) `Participant` or `Participants` followed by whitespace and an uppercase letter (so "Participants A and B" is flagged), and (b) an uppercase letter used as a possessive name before a message-like noun, `\b[A-Z]'s\s+(message|messages|reply|replies|claim|claims|point|points|statement|statements|argument|arguments|answer|answers|question|questions)\b`. (c) a viewer-relative reference, `\b[Tt]he other (?:participant|person|side|party)\b|\b[Aa]nother (?:participant|person)\b`, because each of the two readers would take it to mean someone different (the prompt also forbids "a participant" and saying who a comment is directed at or written by). Bare letters ("Plan B", "Option A is", "Vitamin B", "Section A:") and quoted or bracketed letters are not flagged. It is a report check only; no pipeline behavior depends on it.
- **`golden/expected/`** stays empty until after the real run, when it is generated from the approved outputs.
