# Frontend fixes brief 2 (2026-10-01)

Two fixes and nothing else; no flyby edits (report anything else you notice). No real Anthropic API call anywhere (FakeLLM and the existing
fixtures); never read or print `.env`; use `.venv/bin/python`. Pages and messages never mention cost, spend or budgets.

## Fix A: poll-appended moderator messages have an empty CSRF token

`forum/views.py::messages` renders each message with `render_to_string("forum/_message.html", {"m": message})` and no request, so
`{% csrf_token %}` in the research form renders empty and a click on a poll-appended "Provide factual background" button is rejected
(403). Contract: pass `request=request` to that `render_to_string` call so the token is present; nothing else about the payload changes
(key names, `html` shape, escaping). The token must be the real current token for the viewer (same value the page's own forms carry in the
same session). Moderator and user messages alike go through the same call; user messages have no form, so their HTML is unchanged.

## Fix B: "message N" in moderator text must be the number shown on the page

**Problem.** The agents' transcript shows each message as `<message id="<database pk>">` (`moderation/pipeline.py::transcript_from_messages`,
`moderation/prompting.py::render_message`), and the Intervenor's act text says things like "message 38", while the page labels messages with
their per-conversation `seq_no` ("message 4"). Nothing reconciles them (the stress test hid it because pk == seq in a fresh database).
The model sometimes cites the id and sometimes counts positionally, and **the model's input must not change** (the prompts and the
transcript ids stay exactly as they are; this is a post-processing fix only, applied identically whatever the speaker's side).

**Contract.** One pure helper, in a new module `moderation/references.py`:
`renumber_message_references(text, id_to_seq)` where `id_to_seq` maps a transcript message id to its `seq_no`. It returns `text` with every
reference to a message replaced by the displayed number:
- A reference is the whole word "message" or "messages" (any case), optional spaces, optional `#`, then a number list: one integer
  (optionally negative), or several joined by `,`, `and`, `or`, `&` (e.g. "messages 12 and 14", "messages 12, 14 and 15"). Each number
  token must end at a non-word, non-hyphen character ("message 3-1", "message 12abc", "message -10x" are not references and stay
  untouched; "message 3." and "message 3," are references).
- Each number in a reference is replaced **independently and in one pass** (no chaining: with {4: 2, 2: 1}, "messages 4 and 2" becomes
  "messages 2 and 1") by `id_to_seq[number]` if the number is a key; a number that is not a key is left exactly as written (this is how a
  positional "message 1" survives). The `#` is kept. Text without references, or an empty map, is returned unchanged.
- Known and accepted limit (document it in the module docstring): if the model cites a position that happens to equal another window
  message's id, it is renumbered; this needs small ids and a non-identity mapping, so it is rare.

**Where it is applied** (both user-visible paths, before the text is stored or posted, so stored `InterventionAct.text`, the posted
message content and the paragraph matching in `forum/viewmodels.py::_act_paragraphs` all agree):
1. The live pipeline (`moderation/pipeline.py`): the text of each valid act, with `id_to_seq` built from the run's transcript
   (`{m["id"]: m["seq_no"] for m in transcript}`). Find the one place where act text becomes `InterventionAct.text`/the posted content.
2. The draft preview (`moderation/preview.py::_run_agents`): replace the current `_name_the_draft` step with this helper, the map being
   the transcript's ids plus the placeholder: `{**{m["id"]: m["seq_no"] for m in transcript}}` already contains `-1 -> snapshot_seq + 1`
   (the draft is in `transcript`), so "message -1" still becomes the draft's number exactly as before (keep every behaviour fix 1 of
   `docs/frontend_fixes_brief.md` established, including `message #-1` -> `message #5`, case preserved, `-1%` and `message 3-1` untouched).
   `_name_the_draft` may be removed or kept as a thin wrapper, whichever keeps existing tests meaningful; say which.
Replays (`kind = replay`) go through the same pipeline code; do not special-case them. The Master's output and `explanation`/`rationale`
text are not rewritten (never shown to users).

## Files

- **Building agent** may edit only: `forum/views.py`, `moderation/pipeline.py`, `moderation/preview.py`, new `moderation/references.py`.
  It never edits tests, prompts, or `config/`.
- **Testing agent** may edit only files under `tests/` (new files `tests/**/test_ff2_*.py`; a minimal edit to an existing test only where it
  encodes behaviour this brief replaces, listed in the report; if `tests/preview_backend/test_ff_placeholder_text.py` imports
  `_name_the_draft` and the builder removes it, adapt that import). It never edits non-test code.
- Shared files: none. Do not touch `docs/`.
