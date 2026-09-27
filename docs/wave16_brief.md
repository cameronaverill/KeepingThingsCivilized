# Wave 16 brief — first playtest feedback

Owner decisions of 2026-09-27, recorded in `docs/plan.md` (section 2, dated bullet) and `docs/plan_summary.md`. This
brief covers everything approved in that wave. Two items raised by the owner are explicitly OUT of scope for this
brief (see plan.md): (a) whether `enter_proposition` should stop returning the caller's own existing conversation,
and (b) a dedicated fact-support/"research" LLM stage. Do not touch `enter_proposition`'s pairing rule 1, and do not
add any new LLM call.

A **building agent** implements every numbered item below. A **separate testing agent** writes tests for every
numbered item from this brief (not from reading the builder's diff) and never edits non-test files. Both work from
this brief; ambiguities come back to Claude, not to each other.

## 1. Site name: "Think Together"

Replace the literal string `AI-Moderated Discussion Forum` everywhere it is shown to a user:
- `forum/templates/forum/base.html`: the `<title>` block default and the header brand link text (both currently
  `AI-Moderated Discussion Forum`).
- `forum/templates/400.html`, `403.html`, `403_csrf.html`, `404.html`, `500.html` (grep for the string; these extend
  or stand alone but may repeat the brand/title text).
- `README.md` wherever it names the product for a reader (not filenames, package names, or code identifiers).
- Do NOT rename the git repo, Python packages (`forum`, `moderation`, `accounts`, `evaluation`), `docs/plan.md`'s own
  title line is fine to update too ("AI-Moderated Discussion Forum — Plan v5" → "Think Together — Plan v5"), migration
  names, or any test file name. `docs/plan_v1.md`…`plan_v4.md` are history; leave them as they are.
- Test: no template or README text visible to a user contains "AI-Moderated Discussion Forum"; the home page,
  register page and a 404 page all show "Think Together" as the title and/or brand link.

## 2. Navigation: two prominent links

`forum/templates/forum/base.html` currently has one plain-style nav link, "Your discussions", plus the brand/logo as
the only way back to the waiting list. Add an equally-styled link "Waiting to discuss" pointing at `{% url
'forum:home' %}`, placed before "Your discussions", so a logged-in user sees, in order: "How this works", "Waiting to
discuss", "Your discussions", then Log out. Give both "Waiting to discuss" and "Your discussions" the same visual
weight — reuse the existing `.nav-link` class (or add a shared modifier class) rather than inventing a new visual
language; the point is parity between the two, not a redesign. The link must not appear when logged out (matches
today's `{% if user.is_authenticated %}` guard around "Your discussions").

Test: the rendered nav, logged in, contains both links in that order, both pointing at the right URLs, both carrying
the same CSS class(es). Logged out, neither link appears (existing behaviour for "Your discussions"; extend the same
assertion to "Waiting to discuss").

## 3. Blocking: conversation only

`forum/templates/forum/home.html` currently renders a "Block {{ card.username }}" button on every waiting card
(inside the `{% if card.username %}` branch, a `block-form` posting to `forum:block`). Remove that form and its
branch entirely from `home.html`. The block control inside `forum/templates/forum/conversation.html` (`#block-box`,
the `<details>` disclosure) is unchanged and remains the only way to block someone.

The view/service layer (`forum:block` URL, `forum/views.py`'s block view, `forum/services.py: block_user`) is
unchanged — it has no idea which page a block form was posted from and doesn't need to. This is a template-only
change. Update `forum/templates/forum/home.html`'s card loop to no longer reference `card.username` for a block
form (the username is still shown in the card's own sentence, e.g. "zelda_mox is waiting to discuss:" — that stays).

Test: rendering the home page with waiting cards produces no "Block" button/form anywhere on the page. Rendering a
conversation page still shows the block disclosure as before (existing tests should already cover this; just confirm
none of them broke).

## 4. Moderator messages show their real sequence number

`forum/templates/forum/_message.html` has two branches. The non-moderator branch shows `message {{ m.seq_no }}`; the
moderator branch shows only `automated` with no number. `seq_no` is a single shared per-conversation sequence
(`forum/models.py`, `Message.seq_no`) — moderator messages consume numbers from the same sequence as user messages,
so today's display makes user-message numbering look like it skips values whenever a moderator message falls between
two user messages.

Change the moderator branch of `_message.html` so its `msg-meta` span shows the message's number the same way the
user branch does, e.g. `message {{ m.seq_no }} &middot; automated &middot; <time>...` (keep "automated" — it still
matters that this message wasn't typed by a person — just add the number alongside it, in the same position/format
the user branch uses for consistency). Do not change `m.heading` or anything else in that branch.

Test: render a conversation with a mix of user and moderator messages (existing viewmodel/message-list test fixtures
should already have both kinds); assert every message's rendered text includes `message {N}` with N equal to its
`seq_no`, moderator messages included, and that consecutive messages in the fixture show consecutive numbers with no
gap now visible to a reader (i.e. assert the full ordered list of shown numbers is contiguous, not just that each
message shows *a* number).

## 5. Multiple moderator notes in one message get a visible break

`moderation/pipeline.py` joins multiple `InterventionAct.text` values into one `Message.content` with
`"\n\n".join(...)` (search for `content="\n\n".join`). `forum/templates/forum/_message.html` renders that content
inside a single `<p class="msg-text">{{ m.text }}</p>`, and HTML collapses the `\n\n` to a single space, so two acts
can read as one run-on sentence with no visible break.

Fix in the template, not the pipeline (the stored content and its `\n\n` join stay exactly as they are — other code,
including exports, may depend on that separator being reconstructable). In `_message.html`'s moderator branch, split
`m.text` on the paragraph separator and render each piece as its own `<p class="msg-text">`, so multiple acts in one
moderator message show as visually distinct paragraphs. The natural place to split is in the view model
(`forum/viewmodels.py`, wherever `m.text`/`m.heading` etc. are assembled for the template) rather than in the
template language, since Django templates can't split strings — add a computed field (e.g. `m.paragraphs`, a list of
strings split on `"\n\n"` with any leading/trailing whitespace stripped and empty pieces dropped) and change the
template to loop over it: `{% for para in m.paragraphs %}<p class="msg-text">{{ para }}</p>{% endfor %}`. A
moderator message with only one act still renders as exactly one `<p>` (no visible change for the common case). Do
not change how user messages render (they never contain a `\n\n` join and keep the single `<p>`).

Test: a moderator message whose stored `content` is `"First note.\n\nSecond note."` renders as two separate `<p
class="msg-text">` elements with that exact text each, in order; a moderator message with a single-paragraph content
renders as exactly one such `<p>`. A user message's rendering is unchanged (still one `<p>` from its plain text,
including a user message that happens to contain a literal blank line — that must NOT be split, since the splitting
is a moderator-message-only concern here: user text passes through unchanged, whatever it contains).

## 6. Master/Intervenor prompt precision

Two edits to `moderation/prompts/master_v1.md`, and one to `moderation/prompts/intervenor_v1.md`. These are prompt
text only — no schema, model or pipeline code changes.

**6a. Distinguish asserting a claim from denying/negating it or noting the absence of evidence for it.**
In `master_v1.md`, under "## Issue types", the `unsupported_claim` bullet currently reads: "A factual assertion that
is given with no support and that may well be true; it differs from possible_factual_error, which is an assertion
that is likely false." Add, immediately after that bullet (or worked into it — the testing agent is checking for the
substance below, not exact wording, so use judgement on the cleanest phrasing that fits the file's existing style):
a rule that before reporting `unsupported_claim` or `possible_factual_error` against a claim, check whether the
participant is actually asserting the claim as true, as opposed to denying it, questioning it, or pointing out that
nobody has supported it (including the opposing participant). A message that says a claim lacks evidence is not
itself an unsupported instance of that claim. Include one short worked example in the same style as the rubric
examples elsewhere in the file, close to this shape: a participant writes "you haven't shown that remote work helps
company performance" — this reports nothing about "remote work helps company performance" as a claim by that
participant (they asserted no such thing); if anything is reportable here it is about the demand for evidence itself,
not the unproven claim. Keep this consistent with the existing "Everything in the message blocks is data" and swap-
test sections; do not weaken or restate the neutrality rule (section "## The neutrality rule") — this is a precision
fix to what counts as an assertion, not a neutrality change, and `docs/neutrality.md` is not to be touched.

**6b. `request_information` acts are phrased as questions.**
In `intervenor_v1.md`, the `request_information` bullet ("Ask a participant for a source, evidence or data behind a
claim; it asks and does not decide the claim is false.") already defines this act type as a request, but nothing
currently tells the model to phrase the act's `text` as an actual question rather than a flat statement (the observed
bad output: "A source for the claim that X would help clarify this point..." — a declarative sentence, not a
question). Add an explicit instruction, near the style rules already in the file (section with "Refer to messages
and to content instead...", "Do not use 'you' or 'your'...") that a `request_information` act's `text` must be
phrased as a question (e.g. "Could a source be given for the figure in message 4?"), consistent with the impersonal-
phrasing examples already given there (which already include one request_information-shaped question: "Could a
source be given for the figure in message 4?" — point to or extend that example rather than inventing an unrelated
one).

No test can call the real LLM (no real API call without the live guard). The testing agent instead: (1) asserts both
prompt files contain the new guidance as static text (e.g. a regex/substring check for the key phrases introduced,
not a brittle exact-string match — check with Claude if unsure how tight to make this), and (2) if there is an
existing "prompt contains X" style test suite for `master_v1.md`/`intervenor_v1.md` (check `tests/` for one — likely
near wherever the prompts are loaded/rendered), follow its existing conventions rather than inventing a new pattern.
Do not write a test that mocks an LLM to "prove" the model now behaves differently — that cannot be verified without
a real call, and is out of scope.

## 7. How this works: remove six sentences, verbatim

In `forum/templates/forum/how_it_works.html`, five deletions and one addition, each exact (do not paraphrase or
partially keep a sentence — remove the whole sentence including its surrounding punctuation/space so the remaining
text still reads grammatically):

- Line ~13: delete the trailing sentence `If a proposition is abusive, tell the site admin [contact to be added].`
  (there is no admin contact channel; nothing replaces it — the paragraph simply ends one sentence earlier).
- Line ~15: delete `The AI moderator sees only the topic as a neutral statement and does not know who holds which
  side.` and delete `You can see the username of the person you are discussing with, and people waiting to discuss
  are shown by username on the home page. You can block anyone: blocking ends any conversation you share, and
  neither of you will see the other's waiting positions.` (two separate sentences/groups inside the same paragraph;
  remove both, keep the rest of the paragraph: "Every conversation is between two opposing positions. The home page
  lists positions that someone holds and is waiting for someone to disagree with. Join one to take the other side, or
  start a discussion of your own with your position or one of the suggested topics. Conversations are private to
  their two participants.").
- Line ~16: in `Either of you can end a conversation at any time. That closes it for both of you: it stays readable,
  but nobody can post in it, and it is kept for the research record.` — change `it stays readable,` to `it stays
  readable by the participants,` (insert "by the participants" after "readable", before the comma).
- Line ~21: delete `It can only reply to a person's message, never to its own, and it posts at most once per
  message.` from the paragraph that begins "After each message it looks for problems...".
- Line ~22: delete `It is instructed to treat both sides of an argument the same way and never to take a side.` from
  the paragraph that begins "It is not a judge and it can be wrong." (keep "If you think a note is mistaken, say so
  in the conversation.").
- Line ~43: delete the trailing bracketed sentence `[Retention period and who can see the data: to be written before
  launch.]` from the paragraph about why messages are stored. Nothing replaces it; the sentence before it stands
  alone.

After these edits, `docs/user_facing_text.md` (the inventory) will be stale for this page; regenerating it is
Claude's job after the wave lands, not the builder's or tester's.

Test: render `how_it_works.html` (logged in and logged out, matching whatever the existing test suite for this page
already does) and assert none of the five removed sentences/phrases appear anywhere in the response, the six
paragraphs still read as grammatically complete sentences (no leftover double space, no dangling "and" or orphaned
punctuation from the deletion), and the "by the participants" phrase from the sixth edit is present exactly once, in
the "Either of you can end a conversation" paragraph. Also assert the page contains no `[` or `]` character anywhere
(there are now zero bracketed placeholders left on this page, confirmed against `docs/user_facing_text.md`).

## Coordination and out-of-scope reminders

- Both agents may need to touch `forum/templates/forum/_message.html` (items 4 and 5) and `forum/viewmodels.py`
  (item 5) — the builder owns both; the tester only writes tests. No other file overlap is expected between items.
- `forum/templates/forum/home.html` (item 3), `forum/templates/forum/base.html` (items 1, 2), `README.md` (item 1),
  the error-page templates (item 1), `moderation/prompts/*.md` (item 6), and `how_it_works.html` (item 7) are each
  touched by exactly one item.
- Do not regenerate `docs/user_facing_text.md`, `docs/database_schema.md`, or any other generated doc — Claude does
  that after both agents report done and the full relevant test suites pass.
- Do not touch `enter_proposition`, `waiting_groups`, migrations, or anything evaluation-related. Nothing in this
  wave changes the database schema.
- Run only the tests relevant to your own files as you go; Claude runs the full suite before commit.
