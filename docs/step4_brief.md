# Step 4 brief: database models (split in two, 4a forum and 4b moderation)

Status: contract for the agents, written 2026-09-25. Plan reference: section 8 (data model) and section 14 step 4; the no-self-reply rule is plan section 2. Four agents work on this in parallel, each with its own files and its own tests folder:

| Agent | Owns | Tests folder |
|---|---|---|
| 4a builder | `forum/` (models, limits, apps, migrations), the `forum` line in `INSTALLED_APPS` | (writes no tests) |
| 4a tester | tests only | `tests/models_forum/` |
| 4b builder | additions to `moderation/models.py`, `moderation/migrations/0004_*` | (writes no tests) |
| 4b tester | tests only | `tests/models_moderation/` |

Rules for everyone: use `/workspace/.venv/bin/python`; no real API calls, no network, do not read or create `.env`; do not commit or change git state; shared files (`config/settings.py`) are edited only with small targeted edits after re-reading; builders never edit tests, testers never edit non-test code; test files get unique names (`test_forum_*.py`, `test_modmodels_*.py`) and helper modules unique names (never import from `conftest`). Do not touch anything outside your ownership, in particular the step 3 files (`moderation/{taxonomy,schemas,quotes,prompting,series}.py`, `moderation/prompts/`, `moderation/management/commands/spike.py`, `golden/`, `rubrics/`, `tests/moderation/`), the accounts app (step 6 is running in parallel), and `config/urls.py`.

## Decisions
1. **Plain-integer references from the ledger stay.** `LLMCall.conversation_id` and `LLMCall.run_id` remain plain integers (no foreign keys) so spend history can never be erased with its context. Step 4 adds no FK from `LLMCall`; `ModerationRun` gets a helper `llm_calls()` returning `LLMCall.objects.filter(run_id=self.pk)`.
2. **Deletion is deliberate.** Relations use `on_delete=PROTECT` (nothing cascades silently); research logs are never deleted as a side effect.
3. **Two layers of enforcement for the rules that matter:** model-level validation in `save()` (friendly `ValidationError`) and, where a rule crosses tables, a SQLite trigger created in a migration (`RunSQL`, with reverse SQL) so raw SQL, `bulk_create` and the admin cannot bypass it. A violated trigger surfaces as `django.db.utils.IntegrityError`.
4. **The no-self-reply rule (plan section 2, layer 2):** a `ModerationRun` cannot be created or updated so that its `trigger_message` is anything but a user-authored message, at model level AND by database trigger.
5. Taxonomy values (issue types, act types, tones, decisions, dimension mapping) come from `moderation/taxonomy.py` (step 3; already present): import them, never retype them.

## 4a. `forum` app (new)
`forum/__init__.py`, `apps.py`, `models.py`, `limits.py`, `migrations/0001_initial.py`. Add `"forum"` to `INSTALLED_APPS` FIRST (the 4b agent needs it), then build. No views, urls, templates or admin (later steps).

**`forum/limits.py`:** `count_message_chars(text: str) -> int`: normalize line endings to `\n` (CRLF and lone CR), Unicode-normalize to NFC, strip leading and trailing whitespace, count code points (an emoji is 1). This is the single counting rule used everywhere (plan section 4).

**Models** (`forum/models.py`):
- `Topic`: `title` (unique), `description`, `proposition`, `leans` (JSON, default `dict`), `created_at`.
- `Experiment`: `name` (unique), `kind` (`paired` | `series` | `replay` | `warmup` | `observational`), `description`, `config` (JSON, default `dict`), `created_at`.
- `Conversation`: `topic` FK (PROTECT), `status` (`open` | `active` | `closed`, default `open`), `source` (`human` | `synthetic`, default `human`), `experiment` FK (null, PROTECT), `pair_id` (blank string), `variant` (blank string), `transcript_id` (blank string; the golden transcript this synthetic conversation came from), `label_seed` (nullable big integer), `created_at`. Constraint: `transcript_id` is unique per experiment when not blank.
- `Participant`: `conversation` FK (PROTECT), `user` FK to `settings.AUTH_USER_MODEL` (null, PROTECT), `label` (one uppercase letter A to Z), `join_order` (positive int), `joined_at`. Constraints: unique (`conversation`, `label`); unique (`conversation`, `join_order`); unique (`conversation`, `user`) when user is not null. Validation in `save()`: a participant of a `human` conversation must have a user; of a `synthetic` conversation must not; the number of participants of a conversation may not exceed `settings.MAX_PARTICIPANTS`.
- `Message`: `conversation` FK (PROTECT), `seq_no` (positive int), `author_type` (`user` | `moderator`), `participant` FK (null, PROTECT), `in_reply_to` FK to self (null, PROTECT), `content` (text), `char_count` (positive int, computed), `planted` (JSON, default `list`), `created_at`. Constraint: unique (`conversation`, `seq_no`).
  - `save()` on create: if `seq_no` is None, assign `max(seq_no) + 1` (1 for the first) atomically; if given, it must be greater than the current maximum in that conversation (seq numbers only increase). `char_count` is always set from `count_message_chars(content)`; content must be non-empty after that count.
  - A `user` message must have a `participant` belonging to the same conversation; a `moderator` message must have NO participant.
  - `in_reply_to`, when given, must be in the same conversation and have a smaller `seq_no`.
  - A `user` message whose `char_count` exceeds `settings.MAX_MESSAGE_CHARS` is rejected (`ValidationError`) at save, so the limit cannot be bypassed even by code that skips the forum's posting function (plan section 4). Moderator messages are not limited.
  - Database CheckConstraints where expressible: `seq_no >= 1`, `char_count >= 1`, moderator messages have no participant, user messages have one.
- Helper on `Conversation`: `messages_up_to(seq_no)` returning the queryset ordered by `seq_no`, and `next_seq()`.

## 4b. `moderation` app additions
Append to `moderation/models.py` and add `moderation/migrations/0004_*` (depends on `forum` 0001 and `moderation` 0003; wait until `forum/migrations/0001_initial.py` exists before running `makemigrations moderation`; run it with `DJANGO_CSRF_TRUSTED_ORIGINS` unset if `.env` interferes).

**Models**
- `ModerationRun`: `conversation` FK (PROTECT), `trigger_message` FK to `forum.Message` (PROTECT), `snapshot_seq` (positive int), `kind` (`live` | `replay`), `replay_of` FK to self (null, PROTECT), `replicate` (int, default 1), `status` (`pending` | `running` | `done` | `failed` | `skipped_budget` | `skipped_disabled`, default `pending`), `attempts` (int, default 0), `is_stale` (bool), `decision` (blank | `intervene` | `no_intervention`), `rationale` (text), `posted_message` OneToOne to `forum.Message` (null, PROTECT), `failure_reason` (short string, blank; for example `invalid_trigger`), `error` (text), `config_snapshot` (JSON, default `dict`), `discussion_map` (JSON, default `dict`), `claimed_at`, `started_at`, `finished_at` (nullable datetimes), `created_at`. Method `llm_calls()`.
  - Model validation in `save()`: `trigger_message` must be `author_type == "user"` and belong to `conversation`; `snapshot_seq == trigger_message.seq_no`; a `live` run has no `replay_of`; a `replay` run has no `posted_message` (also a CheckConstraint); `posted_message`, when set, must be a moderator message of the same conversation with `in_reply_to == trigger_message`.
  - Constraints: unique live run per trigger message (`UniqueConstraint(fields=["trigger_message"], condition=Q(kind="live"))`); `replay` implies `posted_message` is null (CheckConstraint); `attempts >= 0`; `replicate >= 1`.
  - **Database triggers (migration `RunSQL`, with reverse SQL dropping them):** (1) `BEFORE INSERT` and `BEFORE UPDATE OF trigger_message_id` on `moderation_moderationrun`: abort with a clear message unless the referenced `forum_message.author_type` is `user`; (2) `BEFORE INSERT` and `BEFORE UPDATE OF posted_message_id`: when `posted_message_id` is not null, abort unless that message is a `moderator` message.
- `Issue`: `run` FK (PROTECT), `local_id` (string), `message` FK to `forum.Message` (PROTECT), `issue_type` (choices from taxonomy), `dimension` (blank or a taxonomy dimension; derived in `save()` from `taxonomy.dimension_for(issue_type)`), `quote`, `quote_start` and `quote_end` (nullable ints), `quote_match` (`exact` | `normalized` | `not_found`), `explanation`, `confidence` (float), `intensity` (nullable small int), `validity` (`valid` | `rejected`, default `valid`), `rejection_reason` (blank). Constraints: unique (`run`, `local_id`); `intensity` between 0 and 4 when not null; `quote_match = not_found` if and only if both offsets are null. Validation in `save()`: `message` must belong to `run.conversation` and not be later than the run's `snapshot_seq`; offsets, when present, satisfy `0 <= start < end <= len(message.content)`; for an `exact` match `message.content[start:end] == quote`; `intensity` must be null when `dimension` is blank; **an issue whose message is moderator-authored must have `validity = "rejected"` with a non-empty reason** (plan section 2, layer 3); a rejected issue needs a non-empty `rejection_reason`.
- `IssueDisposition`: `issue` OneToOne (PROTECT), `disposition` (`acted` | `declined`), `reason`. Validation: only a `valid` issue can have a disposition.
- `InterventionAct`: `run` FK (PROTECT), `order` (positive int), `act_type` (choices from taxonomy), `tone` (choices from taxonomy), `text`, `addressee` (a label letter or `all`), `subject` (a label letter, `both` or `none`), `validity`, `rejection_reason`, computed features `char_len`, `word_count`, `is_question`, `quotes_participant` (set in `save()` from `text`: characters, whitespace-separated words, whether any `?` occurs, whether a double-quoted span occurs), M2M `source_issues` (to `Issue`) and `source_messages` (to `forum.Message`). Constraints: unique (`run`, `order`); `order >= 1`. Validation: the number of `valid` acts of a run may not exceed `settings.MAX_ACTS_PER_INTERVENTION`; source issues must belong to the same run; source messages to the same conversation.

## Tests each tester writes (from this contract; tests first, they may pass at once because the builders work in parallel, so prove they are not vacuous by running them against a scratch copy without the feature)
- **4a tester (`tests/models_forum/`)**: every field, default and choice; each constraint at model level AND at database level (bypass `save()` with `bulk_create` or a queryset `update` where possible); `seq_no` auto-assignment and strictly increasing; labels and join orders unique; a user cannot join a conversation twice; human/synthetic participant rules; `MAX_PARTICIPANTS`; message author rules (user needs a participant of the same conversation, moderator has none); `in_reply_to` rules; `char_count` computed; over-limit user message rejected at exactly `MAX_MESSAGE_CHARS + 1` and accepted at `MAX_MESSAGE_CHARS`, moderator message not limited; `count_message_chars` (CRLF, NFC composition, trimming, emoji, combining marks, empty, whitespace-only, very long); `PROTECT` behaviour; `makemigrations --check` clean; helpers; no view/URL/admin added.
- **4b tester (`tests/models_moderation/`)**: everything for `ModerationRun`, `Issue`, `IssueDisposition`, `InterventionAct` above; **the no-self-reply rule at both layers** (a run with a moderator-authored trigger is refused by model validation, and a raw `INSERT` and a raw `UPDATE` through `connection.cursor()` are refused by the database trigger; a run whose trigger is a user message works); the posted-message rules and its trigger; replays never post; unique live run per trigger while replays may repeat; issues on moderator messages must be rejected; quote offsets and exact-match equality; dimension derivation and intensity rules; one disposition per issue; act order uniqueness and the act cap; features; a property-style test: create many user messages with runs and moderator posts and assert that no run anywhere has a moderator trigger and at most one live run per user message; `LLMCall` remains free of foreign keys (its `run_id` and `conversation_id` are plain integers) and `ModerationRun.llm_calls()` works; the reverse migration drops the triggers; `makemigrations --check` clean.

## Done when
`manage.py migrate` works on a fresh database; the whole suite passes; Claude has reviewed both apps; the testing agents' break-it passes (mutation testing on a REAL isolated copy: `rsync -a`, never hard links; verify link counts; never write to `/workspace`) are clean; the user approves; step 4 is committed after step 3.

## Amendments (architect, after the 4b builder's report)
These decisions are binding for builder and tester.
- `quotes_participant`: true when the act text has a double-quoted span (straight `"x"` or curly) with at least one character inside. `char_len = len(text)`, `word_count = len(text.split())`, `is_question` = a `?` occurs.
- Act cap counts the run's OTHER valid acts (excluding the act itself), so updating an act does not trip it.
- `addressee` must be `A`-`Z` or `all`; `subject` must be `A`-`Z`, `both` or `none` (checked in `save()` only).
- `Issue`: `quote_match = not_found` iff both offsets are null (half-null pair refused at model and database level); database also checks `intensity` 0 to 4; the issue's message must belong to the run's conversation with `seq_no <= snapshot_seq`; defaults `quote_match="not_found"`, `confidence=0.0`.
- A rejected `InterventionAct` needs a non-empty `rejection_reason` (model level plus CheckConstraint).
- `save()` does not call `full_clean()`; invalid choice values are not rejected there. `replay_of` is not required for replays.
- Two extra triggers on `forum_message` (kept): a message referenced as a run's trigger cannot change `author_type` away from `user`; a run's posted message cannot change away from `moderator`.
- M2M consistency (source issues same run, source messages same conversation) enforced through `m2m_changed`, both directions.
- Related names: `moderation_runs`, `posted_by_run`, `issues`, `disposition`, `acts`, `acts_sourced`.

## Amendments for 4a (architect, after the 4a builder's report)
- `seq_no` is NOT NULL in the database but `blank=True`; `save()` assigns it when None. `char_count` is `editable=False` and recomputed on every `save()`.
- `Participant.label`: DB CheckConstraint (one letter A to Z), regex validator and `save()` check; `join_order >= 1` has a DB check and a validator.
- `MAX_PARTICIPANTS` is checked only when creating a participant. The `seq_no` rules apply on create only.
- Uniqueness violations surface as `IntegrityError` (no friendly layer at model level; step 7's posting function pre-checks and speaks to users).
- DB CHECK constraints on the choice fields (`Experiment.kind`, `Conversation.status`, `Conversation.source`, `Message.author_type`), built from the choice lists.
- Migration rule: never leave `forum/migrations/0001_initial.py` missing; replace atomically.
