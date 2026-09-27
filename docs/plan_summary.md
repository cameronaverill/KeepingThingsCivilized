# Plan Summary (for quick reference)

Short version of `docs/plan.md` (v5). If the two disagree, `plan.md` wins.

## What we're building
A two-person discussion forum with an AI moderator that makes discussions more productive while staying **politically neutral**: equal contributions get equal treatment, whichever side they come from. The site logs every moderation decision, including decisions not to step in, so a separate evaluation can test it for bias.

## How the moderator works
- After every user message, two Claude calls run:
  - The **Master Moderator** flags problems in specific phrases (e.g. factual errors, abusive language), each with an intensity from 0 to 4.
  - The **Intervenor** decides whether to step in, and posts up to 3 actions.
- The AI sees only "Participant A/B", assigned at random. It never sees names or political sides.
- A bad item in the model's output is rejected on its own. The rest of the run is kept.

## Cost controls (you pay for the API)
- **Console:** a dedicated workspace with a **$10/month** spend limit (set this before step 3).
- **In the app:** all API calls go through one function. It checks the budget before each call and refuses anything that would go over:
  - $5 total for the site
  - $1.50 a day
  - $1.25 per conversation
  - $10 for evaluation, kept separate
- **Also:** a circuit breaker, a kill switch, and prompt caching.
- **Where to change the numbers:** all in `config/tunables.py`.

## Users
- Username + password (securely hashed) and required email confirmation.
- Messages are capped at **3,000 characters**. The server enforces this, and users see a counter plus a clear error explaining why.
- Posting is limited to one message every 30 seconds per person. Whenever someone can't post (too long, too fast, conversation full or closed, something broke), the page says exactly why and what to do next. There is no turn-taking rule: one person can post several messages in a row (a realistic case the moderator should handle, and one we test).

## Measuring bias (later, phase B)
- **Two stages, measured separately:**
  - **Detection:** does the moderator notice a problem?
  - **Action:** given that it noticed, does it act?
- Both are compared **by political side, at equal intensity**.
- **Ground truth:** two Claude judges and human raters, all using the same rubrics. Each phrase gets an issue type and a 0–4 intensity, or is marked "not scorable" (e.g. contested) with a reason.
- Humans calibrate the AI judges. The metrics are also computed on the human labels alone.
- **Test families:** (1) matched political-direction pairs, at obvious and hard difficulty and compared only at equal difficulty; (2) a small mechanical series (message length, label swap, flooding, repetition, unanswered question) whose ground truth is computed by code; (3) a non-political warm-up to shake out the pipeline cheaply (validates the machinery, not political neutrality); (4) real conversations later, as corroboration only.
- **Controls:** a deliberately biased moderator must be detected (positive control); repeated runs and identical pairs measure the noise floor. The number of pairs is set by a power calculation, and the current ~42 transcripts are a smoke test, not evidence.

## How we build
One step at a time, tests first. A **coding agent** writes each component and a **separate testing agent** writes its tests. Claude owns the architecture and reviews both. You approve each step before the next begins. Python 3.13, Django 6.1.

## Build steps
**Phase A: the site**
1. Project skeleton and settings file (done)
2. API gateway with the budget guard (no real calls yet)
3. Prompt trial with real calls (under $0.50); you review the output
4. Database models
5. Moderation pipeline
6. Accounts (passwords, email confirmation)
7. Forum pages and the message limit
8. Background worker
9. Exports and analysis functions
10. Seed topics, README, final end-to-end check

**Phase B: the evaluation**

11. Finalize rubrics
12. Human rating page
13. Paired test runs
14. AI judges
15. Calibration report
16. Pre-registered analysis

## Still to decide
- Hosting with HTTPS, and an email-sending account, before real users
- Rubric wording
- Whether the cheaper model is good enough for the live moderator (decided after step 3)
- The analysis thresholds, fixed in the pre-registration

## Owner to-do
- **Audit the transcripts yourself** before trusting any result from them (plan section 19; now scheduled as Step 17, the owner gate at the end of the bias evaluation): balance, planted problems and sources, mechanical series, difficulty tiers, side effects. Review pack 3 was approved on 2026-09-25 as mechanics only. Record the date and version; any edit means re-audit. Status: content audit not yet done; results so far are "mechanics only, unaudited".
- **Final quality gate (Step 18):** an audit of the whole test suite by three separate analysis agents per slice (coverage gaps, mutation and false positives, style), a prioritized checklist you approve, then stricter tests. Procedure in `docs/test_audit_plan.md`.
- **Idea to reconsider (Step 19, not approved):** preview the moderator's intervention to a user before they post, so they can edit or accept it. It affects neutrality measurement, cost and the research design, so Claude will ask you at that step (and before deployment) whether you still want it. Details in plan section 14.
- **Propositions (2026-09-25):** any logged-in user can create one (max 200 characters, 20 per day); it appears immediately, newest first with search; the home page shows the proposition text only. Users never see A/B labels (they see "You" and "The other participant"). Conversations are read only by the two participants (pending your confirmation).

- **No email for the MVP (decided 2026-09-26):** registration is a unique username plus a password and logs you in straight away. The confirmation, resend and email-reset flows are removed (they stay in git history, commit 758e957). A forgotten password is reset by you with `manage.py changepassword <username>`. Email and real SMTP are a deployment decision (plan section 17).
- **Users are never told about API costs (2026-09-26):** limits are explained as keeping the discussion readable, and a pause says only that moderation is paused. The composer's helper text under the button is removed.
- **Waiting conversations are usable (2026-09-26):** the first person can post while waiting (no extra limit), the moderator reviews those messages as usual, and the opening message is previewed on the home page so a possible second person can see whether they agree before joining. Labels are now assigned when the conversation is created. Brief: `docs/step7c_brief.md`.
- **Two positions per proposition (2026-09-26):** you choose a side. Seeded propositions carry both wordings ("My position is that X" / "My position is that not X"); a user-created one carries the proposer's position and the other side is "I disagree with this position". Each participant records a side (new column `Participant.side`, plus optional `Topic.opposing_position`; forum migration 0003). Choosing a side joins a waiting person on the opposite side, otherwise you start and wait. The moderator sees only the neutral claim.
- **Home page shows positions only (2026-09-26):** no preview of a waiting person's opening message. A conversation page shows only your own position. After "My position is that" the first letter is lower-cased unless the first word looks like a name or an acronym.
- **Home lists only waiting positions (2026-09-26):** each card shows a position someone holds and one button to take the other side; a "Your conversations" section keeps your own conversations reachable. To start something new you use the propose page: write your own position or pick a seeded topic and a side.
- **Your discussions is its own page (2026-09-26):** the home page shows the waiting positions first and a Start a new discussion button; your own conversations live on a separate "Your discussions" page (search box "Search your discussions") linked from the header.
- **Usernames and blocking (2026-09-26):** participants see each other's usernames, and waiting cards show the waiting person's username. You can block someone from the conversation page or a waiting card: blocking ends any conversation you share, and neither of you sees the other's waiting positions or gets paired with the other. Blocked people are listed (with unblock) on a page linked from Your discussions. The moderator still never sees names. New table forum.Block (migration forum 0004).

- **First playtest feedback (2026-09-27), brief `docs/wave16_brief.md`:** site renamed **Think Together**; header shows "Waiting to discuss" and "Your discussions" as two equally prominent links; blocking is offered only from a conversation, not from a waiting card; moderator messages now show their real number in the sequence; multiple moderator notes in one message get a visible break instead of running together; several explanatory sentences removed from How this works as redundant (who the moderator can see, blocking mechanics, "replies once, never to itself", "instructed to be neutral"); the "tell the site admin" line and the bracketed retention-period placeholder are removed (no other bracketed placeholders exist). A prompt-precision fix: the Master must not mistake a participant denying or noting the absence of evidence for a claim for the participant asserting that claim, and `request_information` acts are phrased as questions.

- **Unlimited discussions per proposition (2026-09-27):** choosing a side always tries to pair or create a new conversation, even if you already have one on that proposition; no shortcut is added, you go through the propose page again each time. "Open your conversation" is unaffected.

- **Web-search research feature (2026-09-27, design only, not yet built):** the moderator's biggest value is judged to be factual grounding, and participants are expected to debate current events, so the Master gets a new `time_sensitive` tag for claims it can't be confident are still current, and the Intervenor can offer (never force) a "Provide factual background" check that a new, single research step runs via `web_search`, reusing the existing worker/async machinery. Evaluated by (a) frozen, replayable matched-evidence pairs, testing whether equal evidence gets equal treatment, and (b) ongoing production monitoring of live retrieval and click-through by side — not a one-time audit. Source-quality/political-lean parity of cited sources is explicitly out of scope for v1 (relying on Anthropic's own broader work on search neutrality); instead a bounded set of paired samples checks whether *this app's pattern* deviates from a direct same-query baseline. Full rationale: `docs/plan.md` sections 2, 9 and 18.
