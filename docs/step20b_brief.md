# Step 20b brief — the Research component, the click, and the resulting message

Builds the real feature `docs/step20a_brief.md`/`step20a_revision_brief.md` and `docs/step20b_spike_brief.md` were
groundwork for: a participant clicks "Provide factual background" on an `offer_research` act, and gets back a real,
sourced, `web_search`-backed moderator message. `moderation/llm.py`'s `call_with_web_search` (now supports optional
`output_schema`, confirmed working end-to-end against the real API, 2026-09-27) is already built and committed —
**do not touch `moderation/llm.py`, `moderation/pricing.py`, or `moderation/budget.py` in this step.**

Read, in order: `docs/plan.md` section 2 ("Factual research via web search"), `docs/step20a_revision_brief.md`, and
`docs/step20b_spike_brief.md`'s findings section, before this brief.

## 1. Data model (`moderation/models.py`) — new `ModerationRun` kind

- `ModerationRun.KIND_CHOICES`: add `"research"` (`_choices(("live", "replay", "research"))`).
- New field `source_act = models.ForeignKey("moderation.InterventionAct", null=True, blank=True,
  on_delete=models.PROTECT, related_name="research_runs")` — the `offer_research` act this run is fulfilling.
  `PROTECT` for the same reason every other act/issue FK in this file is `PROTECT`: a research run's audit trail
  must survive even if something upstream is later deleted.
- New field `requested_by = models.ForeignKey("forum.Participant", null=True, blank=True,
  on_delete=models.PROTECT, related_name="research_requests")` — who clicked, for the "Requested by X" display and
  for future click-pattern monitoring (section 18's post-MVP list already anticipates this).
- **`trigger_message` for a research run is the message containing the claim being verified** — not a new message,
  reuse the existing required FK. Concretely: the first id in `source_act.source_message_ids.all()` ordered by
  `seq_no` (an act can cite more than one message; pick the earliest — this is a judgement call, flag it back to
  Claude if a scenario makes this ambiguous). This satisfies the existing `validate_rules()` constraints unchanged
  (`trigger.author_type == "user"`, `snapshot_seq == trigger.seq_no`) with zero changes to that validation logic.
  `posted_message` (existing field) is reused unchanged for the resulting note — its own validation
  (`posted.in_reply_to_id == trigger_message_id`) already makes the note "reply to" the claim's own message, which
  is exactly right.
- `validate_rules()`: add `if self.kind == "research" and self.source_act_id is None: raise _invalid("source_act",
  "A research run must have a source act.")` and the converse, `if self.kind != "research" and self.source_act_id
  is not None: raise _invalid("source_act", "Only a research run may have a source act.")`. Same pattern as the
  existing `kind == "replay"` checks right above it — follow that style.
- New constraint: `UniqueConstraint(fields=["source_act"], condition=Q(kind="research"),
  name="moderationrun_one_research_per_act")` — this is "the first click claims it": a second `INSERT` for the same
  `source_act` hits this constraint and fails with `IntegrityError`, which the view (item 3) catches and treats as
  "already requested," not an error.
- One new migration.

Test: the new choices/fields/constraints; a research run requires `source_act`, a non-research run rejects one; two
research runs for the same act collide on the unique constraint (`IntegrityError`); the existing `kind == "live"`
one-per-trigger constraint is unaffected (a live and a research run can share a `trigger_message`); `posted_message`
validation is unaffected by `kind`.

## 2. `config/tunables.py` — new tunables

- `RESEARCH_MODEL = "claude-sonnet-5"` (owner decision, already made earlier in this conversation: same tier as
  Master/Intervenor).
- `RESEARCH_MAX_TOKENS`: per the spike, 1024 was too low once several searches' results are in context (one real
  call hit `max_tokens` and got cut off). Set `2048` and say so in the comment (measured 2026-09-27 spike).
- `RESEARCH_MAX_USES = 3` (the `max_uses` passed to `web_search`; the spike observed the model visibly bump against
  a ceiling of 4, so 3-4 is a reasonable starting point, not a precisely justified number — say so in the comment).
- `RESEARCH_MAX_SOURCES_SHOWN = 3` (item 4's source-list cap).
- No new dollar cap tunable (owner decision, earlier in this conversation): research calls draw from the existing
  per-conversation/day/site caps, same as any other `purpose="moderation"` call — do not add a
  `BUDGET_RESEARCH_...` constant.

Test: existing `tests/test_tunables.py` conventions (every tunable has a comment, is exposed on `settings`, etc.)
extend automatically; no new test file needed for this item alone.

## 3. `moderation/research.py` — the component itself (new file)

Mirrors `moderation/pipeline.py`'s shape (a `run_moderation(run)`-style entry point), but much smaller — one call,
not two, and no item-level issue/act validation (there's nothing to validate against: the output is one note, not
a list of claims).

- `run_research(run)`:
  1. Re-check `run.kind == "research"` and `run.status in ("pending", "running")` — same defensive re-check style
     as `pipeline.run_moderation`'s trigger re-check, for the same reason (belt-and-suspenders against a bug
     upstream ever handing this function the wrong row).
  2. Build the prompt from `run.source_act`: the act's own `text` (what was offered), the issue(s) it cites via
     `source_issue_ids` (their `quote`, `explanation`, `issue_type`), and the claim's message text
     (`run.trigger_message.content`) for context. Do **not** include participant labels, usernames, or which side
     made the claim — same blinding as Master/Intervenor input; this call has no more reason to know who said
     something than they do.
  3. Call `llm.call_with_web_search(purpose="moderation", agent="research", model=settings.RESEARCH_MODEL,
     max_tokens=settings.RESEARCH_MAX_TOKENS, max_uses=settings.RESEARCH_MAX_USES, output_schema=ResearchNote,
     conversation_id=run.conversation_id, run_id=run.pk, ...)` — note `purpose="moderation"`, not a new purpose:
     this is a live, user-facing moderation cost, drawn from the site budget like Master/Intervenor calls, per the
     owner's decision that this doesn't get its own budget line.
  4. On `LLMRefused`/`BudgetUnavailable`/`LLMAPIError`/`LLMOutputError`/`BreakerOpen`: same failure-reason-code
     pattern as `pipeline.run_moderation` (`skipped_budget`, `skipped_disabled`, `failed` with a reason) — reuse
     those exact reason codes and the retry-once-on-`LLMOutputError` behavior from `agents._call_with_retry` if it
     fits cleanly; do not invent new failure vocabulary if the existing one already covers the case.
  5. On success: build the posted message's text from `ResearchNote.text` plus a rendered source list (item 4),
     create the `Message` (author_type="moderator", in_reply_to=run.trigger_message, conversation=run.conversation),
     set `run.posted_message`, finish the run `status="done"`.
  6. `ResearchNote` (new Pydantic schema in `moderation/schemas.py`, `_Strict`, same rules as `MasterOutput`/
     `IntervenorOutput` — no defaults, every field required): `text: str` (the neutral note, same style rules as
     Intervenor acts — no participant labels, no "you"/"your", brief, no overstating certainty) and
     `confidence: float` (0.0-1.0, the model's own confidence in the note, validated the same way
     `MasterIssue.confidence` is). Do **not** ask the model to self-report which sources it used — item 4 computes
     that from the real API response, not from the model's own claim.

## 4. Source list — computed in code, not asked of the model

From `WebSearchResult.tool_blocks` (each a `web_search_tool_result` block, whose `.content` is a list of
`WebSearchResultBlock`-shaped objects with real `.title`/`.url` fields — confirmed directly against the real API in
the spike): flatten every result across every tool block, in the order the API returned them (a relevance proxy,
not asserted as ground truth), deduplicate by domain (`urllib.parse.urlparse(url).netloc`, one result per domain,
first occurrence wins), cap at `settings.RESEARCH_MAX_SOURCES_SHOWN`. Render as a short list appended to the
posted message in a format `forum/templates/forum/_message.html` (item 6) can turn into the mockup's "source pill"
style — a plain data structure is enough here (e.g. a JSON list of `{title, url}` stored somewhere reachable from
the template; look at how `InterventionAct`'s other computed-in-code fields are stored and reuse that pattern rather
than inventing a new one, e.g. a JSON field on the posted message or the run, whichever fits the existing schema
better — flag back to Claude if neither fits cleanly).

Test: multiple domains dedupe to one each; more results than the cap are truncated, not just the first N before
dedup; a call with zero tool_blocks (a search that came back empty) produces zero sources, not an error; malformed
or missing `.title`/`.url` on a result block is skipped, not fatal.

## 5. `moderation/worker.py` — claiming and processing research runs

`claim_next_run` currently claims only `kind="live"` runs. Extend its candidate query to `kind__in=("live",
"research")` — both are real, user-facing, async work the worker should pick up; keep the existing per-conversation
serialization (`_running_sibling`) applying to both kinds together (a research run and a live run in the same
conversation still shouldn't run concurrently, for the same reasons live runs already serialize). `process_one`
dispatches to `pipeline.run_moderation` for `kind in ("live", "replay")`-shaped work today; add a dispatch to
`research.run_research` for `kind == "research"`. Do not change `reap_stuck_runs`'s logic, only confirm it applies
uniformly (it should, since it works off `status`/`claimed_at`, not `kind`) — but note in your report if
`RUN_TIMEOUT_SECONDS` (300s) looks tight against the spike's observed latency (up to ~160s for 4 searches; a
`max_uses` of 3-4 could plausibly approach or exceed 300s in a slow case) — flag it, don't silently change the
tunable yourself.

Test: a research run is claimable by the same `claim_next_run` machinery; a live and a research run in the same
conversation still serialize (one blocks the other); `process_one` routes a research run to `research.run_research`
and a live run to `pipeline.run_moderation`, unchanged; a stuck research run reaps the same way a stuck live run does.

## 6. Forum layer — the click endpoint, the button, and the pending state

- **New URL and view**, following `forum/urls.py`'s existing `c/<int:conversation_id>/check/` pattern exactly:
  `path("c/<int:conversation_id>/research/<int:act_id>/", views.request_research, name="request_research")`.
- **Button eligibility, widened 2026-09-27 (owner decision): not `offer_research`-only.** The button is available on
  any valid act whose `act_type` is `offer_research`, `correct_factual_error`, or `provide_information` — i.e. any
  act that addresses a checkable factual claim, whether or not the Master flagged `needs_verification`. Rationale:
  confident, directly-asserted corrections stay exactly as they are (unchanged, per the earlier design decision),
  but a participant can still ask for an independently-researched second opinion even when the moderator was
  confident — this doesn't reopen the free-text or always-on-floating-button ideas already rejected (section 18):
  it's still attached to a specific moderator act, still moderator-mediated, still a fixed "request background"
  action with no user-authored input. `research.py` (item 3) needs no change for this — it already builds its
  query from the act's cited issue(s) (quote/explanation), not from which act type triggered it.
- **`forum/views.py: request_research`** — `@login_required @require_POST @never_cache`, same shape as `check`/
  `check_edit`: load the conversation (`_load_conversation`, 404 if missing), resolve `_my_participant` (404 if not
  a participant — same "don't reveal whether a conversation exists" posture as every other participant-gated view
  here), look up the `InterventionAct` by `act_id` (404 if it doesn't belong to a run in this conversation, its
  `act_type` isn't one of the three above, or it isn't `validity="valid"`). Create the `ModerationRun(kind="research",
  source_act=act, requested_by=participant, trigger_message=<item 1's rule>, conversation=found)` inside a
  `transaction.atomic()` block (per `moderation/llm.py`'s own hard rule, this creation must NOT be inside the same
  transaction as any LLM call — it isn't; only the run row is created here, the worker calls `research.run_research`
  later, out of process). Catch `IntegrityError` from the unique constraint (item 1) and treat it as success — the
  run already exists, return its state, do not create a duplicate or show an error. Return the same
  `_check_json`-style JSON shape used elsewhere (`{"status": ..., ...}`) — the exact fields are your call, but the
  front end (below) needs enough to show "pending" immediately after a successful click.
- **Template (`forum/templates/forum/_message.html`)**: for an eligible act (see above — `offer_research`,
  `correct_factual_error`, or `provide_information`) with no existing research run for it yet, render the "Provide
  factual background" button (matching the mockup's visual style — plain, calm,
  matching `.btn`'s existing classes in `site.css`, not a new visual language); once a research run exists for that
  act (any status), render "Checking — this may take a moment" if not yet `done`/`failed`, or nothing extra once
  `done` (the resulting note is just the next moderator message in the normal list — no special rendering needed
  for the "after" state). This needs the act's research-run state available in the view model
  (`forum/viewmodels.py`) — compute it the same way other per-message/per-act computed fields already reach the
  template (follow the existing pattern there, e.g. how `m.paragraphs` was added in wave16, rather than inventing a
  new plumbing style).
- **JS (`forum/static/forum/compose.js` or a new small script, your call, follow whichever existing convention
  fits)**: on a successful click, disable the button and show the pending text immediately (optimistic UI) rather
  than waiting for the next poll cycle; the existing `poll.js` polling (~3s) naturally picks up the eventual new
  moderator message once posted, no changes needed there.
- **No cost language anywhere** (owner decision, section 2): the pending state says only that it may take a moment,
  never anything about spend, exactly like every other "moderation paused"-style message in this app already does.

Test: the button renders for a valid act of any of the three eligible types with no existing research run, and not
for any other act type (`request_information`, `enforce_conduct`, etc.), nor for a rejected act of an eligible type;
clicking creates
exactly one `ModerationRun(kind="research")`; a second click (or two concurrent requests) never creates a second
one (the `IntegrityError` path); a non-participant gets 404, not the button's behavior; the pending state shows
correctly once a run exists and clears once the resulting message is visible; no page or response text anywhere in
this feature mentions cost, spend, or budget.

## Coordination and out-of-scope reminders

- Suggested split, matching the file-ownership pattern used for every prior step in this project: **Group A**
  (`moderation/models.py` + migration + `config/tunables.py`, item 1-2), **Group B** (`moderation/research.py` +
  `moderation/schemas.py`'s new `ResearchNote` + `moderation/worker.py`, items 3-5), **Group C** (`forum/urls.py`,
  `forum/views.py`, `forum/viewmodels.py`, `forum/templates/forum/_message.html`, the JS, item 6). Group C depends
  on Group A's `source_act`/`requested_by` fields and Group B's run-processing existing, but — per this project's
  established working method — write against the names this brief already specifies; don't wait to see the other
  groups' actual diffs land before writing your own code.
- Do not touch `moderation/llm.py`, `moderation/pricing.py`, `moderation/budget.py` — already done, committed,
  and confirmed against the real API. A change to any of those is a new, separate, high-scrutiny step, not this one.
- Do not add a new `LLMCall` purpose (`purpose="moderation"` is correct and deliberate, per the owner's budget
  decision) or a new dollar-cap tunable.
- Do not build the general/always-on self-serve research button, or anything free-text — both are explicitly out
  of scope (section 18's post-MVP list) and were rejected earlier in this conversation.
- `moderation/prompts/research_v1.md` (new prompt file, Group B): follow `master_v1.md`/`intervenor_v1.md`'s
  existing conventions closely (versioned filename, the "everything in message blocks is data" injection-safety
  framing, the neutrality/swap-test language, impersonal style rules) rather than writing a differently-styled
  prompt for this one component.
