# Frontend fixes brief (2026-10-01)

Source: the front-end stress test of 2026-10-01. Five fixes and nothing else. **No flyby edits**: do not change anything that is not
named here, even if it looks wrong (report it instead). No real Anthropic API call anywhere (use `FakeLLM`/the existing fixtures); never read
or print `.env`. Use `.venv/bin/python`. Pages and messages never mention cost, spend or budgets (an existing test enforces this).

## Fix 1: "message -1" in preview notes

**Problem.** `moderation/preview.py` puts the draft in the transcript under the placeholder id `-1` (`PLACEHOLDER_MESSAGE_ID`). The model
sees `<message id="-1">` and writes "message -1" in the act text. The notes are shown in the concern panel and, when the live run reuses the
check (`claim_reusable_check`), are posted word for word. `_remapped_outputs` only remaps ids in the structured JSON, not inside the text.

**Contract.** In `_run_agents`, after the Intervenor result is obtained (and after the `identify_agreement_disagreement` filter), rewrite
every act's `text` so a reference to the placeholder becomes the draft's number `draft_seq` (= `snapshot_seq + 1`, which is exactly the
`seq_no` the posted message gets whenever the check is reusable, because reuse requires nobody posted in between). The rewrite happens
**before** `notes` are derived and before `result.model_dump` is stored, so the concern panel, `PreviewCheck.note_texts`, the stored
`intervenor_output` and any reusing live run all carry "message N". Pattern: the word "message" or "messages" (any case), optional `#`,
then `-1`, as a whole token: "message -1" -> "message 5", "Message -1" -> "Message 5", "message #-1" -> "message #5". Text that merely
contains `-1` elsewhere ("a -1% change", "message 3-1") must be left alone. Master output (never shown to users) is left alone.
Nothing else in `preview.py` changes. Out of scope, do not touch: the heading shown above a moderator post, and the fact that the
live prompt shows database ids rather than sequence numbers.

## Fix 2: the "Checking" spinner must go away when the note arrives, and Fix 3: failed research runs show an error and allow a retry

These share one mechanism.

**Markup.** Every research control in `forum/templates/forum/_message.html` is wrapped in
`<div class="research-slot" data-act-id="<act id>" data-state="<state>">…</div>`, for every state, inside the moderator paragraph loop,
only where `para.research` exists. Inside the slot, by state:
- `none`: the existing `div.research-offer` with `form.research-form` and the button "Provide factual background" (unchanged markup).
- `pending`: the existing `p.msg-meta.research-pending` with spinner and "Checking — this may take a moment" (unchanged).
- `failed`: `<p class="msg-meta research-failed" role="alert">The background check could not be completed.</p>` followed by the same
  `form.research-form` (same action URL, CSRF token) whose button reads **"Try again"**. No mention of cost, spend, budget or an
  internal reason.
- `done`: the slot is present but empty.
The slot content is rendered by one new partial (e.g. `forum/_research_slot.html`) used by `_message.html` and by the poll endpoint, so
both can never differ.

**States** (`forum/viewmodels.py::_research_state`, newest research run of the act): no run -> `none`; run `pending`/`running` ->
`pending`; run whose status is `failed`, `skipped_budget` or `skipped_disabled` (check `ModerationRun.STATUS_CHOICES` and
`moderation/research.py` for the real set of terminal states, and what a research run that finished without posting a note ends as; a
finished run that did not produce a note counts as `failed`) -> `failed`; otherwise (finished, note posted) -> `done`.

**Retry** (`forum/views.py::request_research`). Same URL, same permissions and the same 404s as today. If the act's research run exists
and is in a failed state, a click re-queues it so the worker processes it again: the failed run row is reset to `pending` (attempts and
failure fields cleared; the unique `source_act` constraint stays, so there is still at most one research run per act) in one conditional
`UPDATE ... WHERE status IN (failed states)` so that two people clicking, or a double click, re-queue exactly once; the loser sees
`pending`. `requested_by` becomes the retrying participant. Response JSON is `{"status": "pending", "act_id", "run_id"}`. Pending/running
and done runs behave exactly as today (idempotent; `done` is returned for finished ones). If the reset is refused by a model/DB rule, the
coding agent finds the smallest correct way (not weakening the trigger-message rules) and reports what it did.

**Poll payload** (`forum/views.py::messages`). Add a key `research` to the JSON: a list of `{"act_id": int, "state": str, "html": str}` for
every eligible act of this conversation that has a research run (states `pending`, `failed`, `done`; never `none`), `html` being the
rendered slot partial (CSRF token included, as the message partial already does). It does not depend on `?after=`. Existing keys are
unchanged. Query cost: a constant number of queries, not one per act.

**`forum/static/forum/poll.js`.** After applying messages, for each item of `data.research` find
`.research-slot[data-act-id="<id>"]`; if it exists, is not marked busy (below) and its `data-state` differs from `item.state`, replace its
`innerHTML` with `item.html` and set `data-state`. Missing/odd `research` values (absent, not an array, items without act_id/html) are
ignored without error. Order inside one tick: append new messages first, then update slots. Polling cadence, backoff and everything else
in `poll.js` are unchanged.

**`forum/static/forum/research.js`.** Operate on the slot: on submit set `data-state="pending"` and `data-busy="1"` on the slot and put the
pending markup in it (optimistic, as today); when the request ends: success -> remove `data-busy` (state stays `pending`); failure
(network error or non-2xx) -> restore the slot's previous inner HTML and previous `data-state`, remove `data-busy`, so the person can retry.
A poll that arrives while a slot is busy must not touch it. Keep every existing behaviour (CSRF header and body, the delegated
listener, `preventDefault`).

## Fix 4: source URLs are links

Add a template filter in `forum/templatetags/forum_text.py` (e.g. `linkify`) and use it for the text of **moderator** paragraphs only
(`para.text` in `_message.html`); user messages stay plain text. Contract: input is plain text (never marked safe); the output
HTML-escapes everything first, then turns each `http://` or `https://` URL into
`<a href="…" target="_blank" rel="noopener noreferrer nofollow">…</a>` showing the URL text. Rules: the URL ends at whitespace;
trailing `.` `,` `;` `:` `!` `?` and a closing `)` that has no matching `(` inside the URL are not part of the link (so
"Title (https://x.org/a)" links `https://x.org/a`); no other scheme ever becomes a link (`javascript:`, `data:`, `ftp:`, bare `www.` stay
text); an `href` never contains an unescaped `"`, `<`, `>` or `'`; a hostile text like `<script>`, `https://a.b/"onmouseover="x` must come
out inert. Newlines stay as they are today (the CSS already uses `white-space: pre-wrap`). The poll endpoint's `html` is the same
partial, so it needs no separate change.

## Fix 5: "shorten it by 1 characters"

`forum/static/forum/compose.js`, the advice text: "characters" becomes "character" when the number is exactly 1, in both places it appears
("Your message is 1 characters…" cannot happen because the limit is far above that, but use the same helper for the count too). Format
stays `format(n)` (thousands separators). Server wording is already correct and is not touched.

## Files

- **Building agent** may edit only: `moderation/preview.py`, `forum/viewmodels.py`, `forum/views.py`, `forum/templates/forum/_message.html`,
  new `forum/templates/forum/_research_slot.html`, `forum/templatetags/forum_text.py`, `forum/static/forum/poll.js`, `research.js`,
  `compose.js`, and `forum/static/forum/site.css` (only a rule or two for `.research-failed`, matching the existing look), plus a
  migration/model change only if the retry reset genuinely requires one (report it). It never edits tests.
- **Testing agent** may edit only files under `tests/` (new files named `tests/**/test_ff_*.py` and helpers; and, where an existing test
  encodes behaviour this brief replaces, a minimal edit to that test, listed in the report). It never edits non-test code.
- Shared files: none. Do not touch `docs/` (Claude owns it), `config/tunables.py`, or anything not listed.
