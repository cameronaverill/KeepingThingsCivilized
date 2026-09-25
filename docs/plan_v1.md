# AI-Moderated Discussion Forum — MVP Plan

## Context
Build a minimal two-person discussion board where an AI moderator intervenes to make discussion more productive **while staying politically neutral**. Neutrality definition: *given two equal contributions (equal factual support, clarity, logic, procedural conduct), the system intervenes at equal frequency, in an equal manner, with equal intended effect* — regardless of political position (first priority), and later speaker identity, positional ordering, and content features (e.g. length).

The website is step one; a separate **bias-evaluation pipeline** (designed later, not part of the site) will consume the logs. So the core non-functional requirement is: **every moderation decision — including decisions *not* to intervene — is logged in a structured, reconstructable, easily batch-fetchable form.** Moderator *quality* (accurate facts, genuinely-unclear arguments flagged) will also be evaluated later, so logs must support that too.

Workspace `/workspace` is empty (only `.devcontainer`). Python 3.14, Node 22 available.

## Decisions (agreed)
- **Stack:** Python + Django, SQLite, server-rendered templates + small JS polling (every ~3s; no WebSockets).
- **LLM:** Claude only, direct Anthropic SDK. One small call function is the seam tests patch with a fake. Model per agent in settings (default `claude-sonnet-5`; confirm API details via `claude-api` skill when implementing).
- **Pipeline trigger:** Master Moderator → Intervenor runs **after every user message**.
- **Login:** When2Meet-style — enter a name, stored in session; error if name taken (case-insensitive). No passwords.
- **Participants:** 2 per thread for MVP (`MAX_PARTICIPANTS` setting); schema supports N.
- **Political labels:** NOT collected from users. Labels are per-*message*, added later via `Annotation` rows (judge/human/self), with source + confidence. Moderator LLMs never see names or stance — only `Participant A/B`.
- **Stance → political position:** messages are labelled `stance` = pro | con | neutral/mixed *relative to the topic's proposition* (easier and less ideologically loaded to judge than "is this left-wing?"). Political position is derived via per-topic `TopicLean` rows under multiple schemes: `compass` (two axes: `economic` left↔right, `social` libertarian↔authoritarian — the two-axis model, not the politicalcompass.org instrument) and `us_partisan` (`party` axis). Values are coordinates in −1..+1 (≈0 = topic doesn't test that axis), not quadrant labels. New schemes = new rows, no schema change. A direct `political_position` annotation remains possible as a cross-check. Headline scheme pre-registered before eval (likely `us_partisan` or `compass`/economic); others secondary.
- **Moderator post cadence:** at most one moderator post per user message (one run → one Intervention → ≤1 posted message); moderator messages never trigger runs. Acts per intervention capped by `MAX_ACTS_PER_INTERVENTION` (default 3); excess issues are declined with reasons. Late interventions (a newer user message arrived before the run finished) are still posted, and flagged `is_stale` on the run — never dropped, so every message is evaluated.
- **TDD:** pytest + pytest-django; tests written first for each increment; `FakeLLM` returns scripted JSON; optional `@pytest.mark.live` tests hit the real API.

## Taxonomy (single source of truth: `moderation/taxonomy.py`)
Used by DB choices, Pydantic schemas, and prompt text.

**Issue types (Master Moderator):** `unsupported_claim`, `possible_factual_error`, `unclear_statement`, `fallacy`, `strawman`, `incivility`, `repetition`, `process_violation`.

**Act types (Intervenor), each with a one-sentence definition in the prompt:**
`provide_information`, `correct_factual_error`, `improve_argumentation` (flag a reasoning flaw / suggest stronger form), `clarify_argument` (restate more clearly, content unchanged), `restate_positions`, `identify_agreement_disagreement` (common ground + whether cruxes are factual or normative), `request_information`, `request_clarification`, `enforce_conduct` (incl. softening tone), `enforce_process` (incl. flooding, turn-taking, prompting a party to respond).

**Decision:** `intervene` | `no_intervention` (a logged decision, never an absence of data).

**Tone (self-reported per act):** `gentle` | `neutral` | `firm`.

## Agent I/O (Pydantic models in `moderation/schemas.py`, validated structured output)
**Master Moderator** input: full transcript with `Participant A/B` labels + message ids, prior moderator posts, previously raised issues with their dispositions (marked already-raised), and code-computed process facts (e.g. consecutive-message counts, timestamps).
**Only-new-issues rule:** the Master flags issues in the *newest* message only, plus inherently cross-message issues (`repetition`, `strawman`, `process_violation` such as unanswered questions or flooding). It must not re-raise earlier issues — prevents double counting in frequency metrics. `Issue.message` = the message containing the problem (may differ from the trigger message); a strawman's explanation cites the misrepresented message.
Output:
- `issues[]`: `{id, message_id, participant, issue_type, quote, explanation, confidence}`
- `discussion_map`: `{agreements[], disagreements[{summary, kind: factual|normative}]}`

**Intervenor** input: transcript + Master output.
Output:
- `decision`, `rationale`
- `issue_dispositions[]`: for **every** issue: `{issue_id, disposition: acted|declined, reason}`
- `acts[]`: `{type, addressee (A|B|all), subject (A|B|both|none), source_issue_ids, source_message_ids, tone, text}`

Each act carries its own text → length/tone attributable per party. The posted moderator message = acts rendered in order; `Participant A/B` in text is replaced with display names at render time only.
Code validates references (issue ids, labels, message ids); on failure: one retry, then run marked `failed`, nothing posted, everything logged.
No third tagging LLM in MVP — self-tags + deterministic features now; an offline judge later calibrates self-tags on a sample.

## Database schema (Django models)
`forum` app:
- `Handle` (name, name_lower unique, created_at)
- `Topic` (title, proposition — a single arguable statement, description)
- `TopicLean` (topic FK, side pro|con, scheme e.g. `compass`/`us_partisan`, axis e.g. `economic`/`social`/`party`, value float −1..+1, rationale text; unique (topic, side, scheme, axis))
- `Conversation` (topic FK, status, created_at)
- `Participant` (conversation FK, handle FK, label A/B/…, join_order, joined_at; unique per conv)
- `Message` (conversation FK, seq_no, author_type user|moderator, participant FK?, intervention FK?, content, created_at; unique (conversation, seq_no))

`moderation` app:
- `ModerationRun` (conversation FK, trigger_message FK, status pending|running|done|failed, is_stale bool, config_snapshot json: models, temps, prompt versions+hashes, discussion_map json, error, timestamps)
- `LLMCall` (run FK, agent master|intervenor, attempt, model, prompt_version, prompt_sha256, temperature, request json (exact payload), raw_response, parsed json, tokens_in/out, latency_ms, error, created_at)
- `Issue` (run FK, local_id, message FK, participant FK, issue_type, quote, explanation, confidence)
- `Intervention` (run FK 1:1, decision, rationale, posted_message FK?)
- `InterventionAct` (intervention FK, order, act_type, addressee_participant FK? (null = all), subject_participant FK? , subject_scope, tone, text, + deterministic: char_len, word_count, is_question, quotes_participant)
- `ActSource` links: act↔issue, act↔message (M2M)
- `IssueDisposition` (issue FK, intervention FK, disposition, reason)
- `Annotation` (content_type/object_id generic target, dimension e.g. `stance` (pro/con/neutral)/`political_position`/`tone`/`directional_effect`, value, source e.g. `self`/`judge:<model>`/`human:<name>`, confidence, created_at) — empty in MVP, ready for eval.

Prompts: versioned files `moderation/prompts/master_v1.md`, `intervenor_v1.md`; version name + sha256 recorded per call.

## Fetching / reconstruction
- `moderation/queries.py`: `get_conversation_bundle(conversation_id)` → messages with nested runs → llm_calls, issues (+dispositions), intervention → acts; `list_conversations(**filters)`; `export_conversation(id) -> dict`.
- Management commands: `export_conversation <id> [--out file.json]`, `export_all --out dir/`, `seed_topics`.
- SQL views (migration `RunSQL`): `v_acts` (act + subject/addressee labels + topic + triggering message + linked issue types), `v_issue_outcomes` (each issue + disposition + acting act type/tone) — load straight into pandas later.
- Django admin with inlines for all models (browse logs while developing).

## Module layout
```
/workspace
  manage.py, requirements.txt, pytest.ini, README.md, .env.example (ANTHROPIC_API_KEY)
  config/            settings.py, urls.py
  forum/             models, views, urls, templates/, static/poll.js, sessions helper, admin
  moderation/        taxonomy.py, schemas.py, prompts/, llm.py (single call fn), agents.py (master, intervenor),
                     pipeline.py (run_moderation), runner.py (background thread + per-conversation lock),
                     features.py (deterministic act features), queries.py, admin, management/commands/
  tests/             test_handles, test_threads, test_messages, test_schemas, test_llm_logging,
                     test_pipeline, test_runner, test_queries, test_views, test_live (marked)
```

## Web flow
Home: enter name → topic/thread list (start thread on a topic, or join an open one) → thread page (join assigns next label by join order; 3rd person gets "thread full") → post message → `transaction.on_commit` enqueues moderation run → page polls `/c/<id>/messages?after=<seq>` (HTML fragment) and shows moderator posts when ready. Runs execute in a background thread, serialized per conversation (`MODERATION_RUN_MODE = "thread" | "sync"`; tests use `sync`).

## Build order (each step: failing tests → implement → green)
1. Skeleton: Django project, settings, pytest-django config, requirements.
2. Models + migrations for both apps; model-level tests (uniqueness, seq_no ordering, label assignment).
3. Name entry + session + duplicate-name error.
4. Threads: list, create, join (capacity), post, poll endpoint.
5. Taxonomy + Pydantic schemas + prompt loader/versioning (validation tests incl. bad references).
6. `llm.py` call + `LLMCall` logging (fake client; success, error, retry).
7. `pipeline.run_moderation`: master → issues → intervenor → validate → intervention/acts/dispositions/features → moderator message; `no_intervention` path; failure path.
8. Runner: on_commit trigger, background thread, per-conversation serialization.
9. Queries, export commands, SQL views, admin.
10. `seed_topics` (topics with propositions + `TopicLean` rows and rationales), README (trace one message end-to-end), live smoke test.

## Discussion notes (rationale, for later sessions)
- Neutrality = equalized odds: P(act T | trigger C, side=X) ≈ P(act T | C, side=Y), where side comes from message stance × `TopicLean` under the chosen scheme; also check unwarranted interventions (no trigger) per side.
- Prose/style: *clarity* legitimately differentiates; *register/dialect/formality* at equal clarity must not.
- Eval (later, separate from site): counterfactual paired track first (planted triggers, flip only political direction; McNemar / Wilcoxon), observational track on real logs second. Judge from a different model family; check judge symmetry; validate on ~100 hand labels; pre-register headline metrics.
- "Intended effect" source = Intervenor self-tags; later judge scores directional effect (−2..+2) on a sample.
- Quality eval later: accuracy of factual acts, whether flagged unclear statements really were unclear, discussion productivity.
- Future: specialized agents (fact-check detector/corrector), identity/positional/length bias tests, optional post-discussion private self-report survey, code-enforced process rules, task queue instead of thread.

## Verification
- `pytest` fully green (live tests skipped without API key); `pytest -m live` once with key.
- `python manage.py migrate && python manage.py seed_topics && python manage.py runserver`; open two browser sessions (normal + incognito), enter two names (confirm duplicate-name error), join same thread, exchange messages incl. an unsupported claim and an insult; confirm moderator posts appear and a no-intervention run is logged for benign messages.
- Inspect in `/admin`: runs, LLM calls (exact request/response), issues, dispositions, acts with addressee/subject/tone.
- `python manage.py export_conversation <id>` produces a complete nested JSON; query `v_acts` via sqlite3.
