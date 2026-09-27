# Foreign-key cleanup brief (moderation and evaluation)

Status: contract for the agents, written 2026-09-26, owner-approved. Some id columns should be real foreign keys. Two independent halves, four agents.

| Agent | Owns | Tests |
|---|---|---|
| Moderation builder (the step 19 back-end builder, resumed) | `moderation/models.py`, the uncommitted migration `moderation/migrations/0005_preview_mode_and_check.py` (regenerate it; there must stay exactly ONE 0005), `moderation/preview.py`, `moderation/admin.py`, `moderation/queries.py` (only if needed) | none |
| Moderation tester (the step 19 tester, resumed) | tests only | `tests/preview_backend/`, `tests/models_moderation/`, `tests/exports/` and `tests/admin_site/` where a pin needs it |
| Evaluation builder (the step 12 builder, resumed) | `evaluation/models.py`, a NEW migration `evaluation/migrations/0002_*` (generate in a scratch copy of the repo, must reverse cleanly), and the minimal changes to `evaluation/llm_rater.py`, `evaluation/consensus.py`, `evaluation/blinding.py`, `evaluation/calibration.py`, `evaluation/matching.py` that the model changes force | none |
| Evaluation tester (the step 12 tester, resumed) | tests only | `tests/evaluation_models/`, `tests/llm_raters/`, `tests/rater_matching/` where a pin needs it |

Rules for everyone: `/workspace/.venv/bin/python`; no real API calls, no network; never read, print or create `.env` or any key; no git commands; scratch folders under `/tmp/claude-1000/-workspace/26b48491-740d-41f7-af98-d9c9c69e12c2/scratchpad/`; a dev server uses `/workspace/db.sqlite3`: NEVER run migrate or anything against it (generate migrations in a scratch copy of the repo and move them in; verify forward, backward, forward and `makemigrations --check` on scratch databases); mutation copies are `rsync -a` copies outside the repo, deleted at the end. Other agents are editing `forum/` and its tests: do not touch those.

## Moderation half
Replace the plain integer id columns with real foreign keys, all `on_delete=PROTECT`, keeping the DATABASE COLUMN NAMES the same where they exist (Django's `<field>_id`):
- `PreviewMode.conversation` becomes a OneToOne-style unique foreign key to `forum.Conversation` (keep the uniqueness).
- `PreviewCheck.conversation` (FK to `forum.Conversation`), `PreviewCheck.participant` (FK to `forum.Participant`), `PreviewCheck.resulting_message` (nullable FK to `forum.Message`), `PreviewCheck.reused_by_run` (nullable FK to `moderation.ModerationRun`).
- STAY plain: `PreviewCheck.llm_call_ids` (a JSON list of ledger ids), `LLMCall.run_id`, `LLMCall.conversation_id` (the ledger is append-only and must never be blocked or changed by other tables).
- Keep every existing constraint, index and CHECK. The migration depends on `forum` `0001_initial` only (the models it points at exist there), so it does not clash with forum migration 0004.
- Keep the public interface of `moderation/preview.py` unchanged (`preview_mode(conversation_id)`, `check_draft(conversation, participant, draft_text)`, `resolve_check(check_id, action, *, message_id=None)`, `claim_reusable_check`): callers still pass ids or objects as today; inside, use the foreign key fields. All existing tests of these functions must keep passing except where a test builds a `PreviewCheck` with a plain id for a row that does not exist (that now correctly needs a real row; the tester adapts the fixtures).
- Privacy note for the tests: PROTECT means a conversation with previews cannot be deleted until its checks are removed. That is intended.

## Evaluation half
- `Rating.llm_call`: a nullable foreign key to `moderation.LLMCall`, `on_delete=PROTECT`, replacing the plain `BigIntegerField llm_call_id` (same column name `llm_call_id`; existing triggers that mention the column keep working; check the trigger "a human rating has no llm_call_id" and the LLM-rater rule still hold). The migration must convert cleanly on a database that already has ratings (existing values stay), and reverse.
- `Annotation.source` (text "self" or "rater:<name>") is replaced by `Annotation.rater`, a nullable foreign key to `evaluation.Rater` (`on_delete=PROTECT`; null means the researcher's own label, formerly "self"). The migration converts existing rows: "self" becomes null, "rater:<name>" becomes the rater with that name (if no such rater exists the migration raises a clear error instead of losing data), and it reverses to the old text form. Update the model validation and any code that read `source`.
- The polymorphic targets (`target_type`/`target_id` on `Rating`, `ConsensusFinding`, `CalibrationItem`, `Annotation`) are NOT changed in this task.
- Minimal changes to the evaluation code and `evaluation/llm_rater.py` so they use `llm_call` (they should mostly keep working through `llm_call_id`); do not otherwise change behaviour. Update `tunables`/docs only if a name changes.

## Tests
Both testers: run the whole area, update every pin that relied on the plain columns (fixtures that made rows with made-up ids), add tests for the new foreign keys (the database refuses a made-up id, PROTECT blocks deleting the referenced row, migration forward/backward/forward with existing rows, `makemigrations --check` clean), keep every behaviour test green, and do a short independent mutation pass on a `rsync -a` copy outside the repo. Also run the `tests/test_repo_hygiene.py` and scanner tests.
