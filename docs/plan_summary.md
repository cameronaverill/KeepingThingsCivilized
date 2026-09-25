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
  - $1 a day
  - $0.50 per conversation
  - $10 for evaluation, kept separate
- **Also:** a circuit breaker, a kill switch, and prompt caching.
- **Where to change the numbers:** all in `config/tunables.py`.

## Users
- Username + password (securely hashed) and required email confirmation.
- Messages are capped at **3,000 characters**. The server enforces this, and users see a counter plus a clear error explaining why.
- Posting is limited to one message every 30 seconds per person. Whenever someone can't post (too long, too fast, conversation full or closed, something broke), the page says exactly why and what to do next.

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
- **Audit the transcripts yourself** before trusting any result from them (plan section 19): balance, planted problems and sources, mechanical series, difficulty tiers, side effects. Record the date and version; any edit means re-audit. Status: not yet done.
