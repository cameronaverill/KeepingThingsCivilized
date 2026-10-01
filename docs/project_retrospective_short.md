# Think Together: a short retrospective

A condensed version of `docs/project_retrospective.md` (2026-09-28).

## The app

**What it does.** Two people who disagree about a position are paired to talk it through, and an AI moderator
helps keep the conversation productive. You either take the other side of a position someone is waiting on, or
post your own. Messages are limited to 3,000 characters, one every 30 seconds, and 30 per conversation. Either
person can end the conversation or block the other. Before you post, the moderator previews your draft, so you
can edit it if it would intervene. When it raises a factual point, either participant can ask it for sourced,
web-searched background. The site is live on Fly.io, but the moderator is switched off there until you
deliberately turn it on.

**How it works.** Each message goes through two separate AI roles. The first only *detects* problems, quoting the
exact phrase and rating its severity. The second only *decides* whether to step in and how, choosing from a fixed
menu of acts. The moderator sees random "Participant A/B" labels, never names or sides. The AI runs in the
background, so the page stays responsive. Spending is capped in several layers: only one file may call the AI, a
worst-case cost check runs before each call, there are daily, per-conversation and total caps, and a circuit
breaker and a kill switch can stop everything. Every decision is recorded, including "no intervention."

**Why it's built this way.**
- **Measurability came first.** Splitting detection from action shows *where* any bias enters. A fixed menu makes
  both sides countable in the same terms. Recording "no intervention" gives every message an outcome. Rejecting
  one bad item instead of a whole run avoids silently losing more data from whichever side writes more.
- **Identity stays hidden from the moderator**, so it can't favor anyone. The A/B labels are hidden from users too,
  because the moderator's wording could otherwise reveal which side it meant.
- **A plain, cheap product.** Accounts need no email. The site never talks about costs. Positions appear without
  screening, which would add cost and a new source of bias. Only opposite sides are paired. Usernames are visible
  so people can avoid each other, but blocking happens only from inside a conversation, where it's harder to do by
  mistake.
- **Web search is a fallback.** The moderator still corrects clear errors directly, and search is only for claims it
  can't confidently handle. Search is always tied to a specific moderator note, never a free-text box, so a user
  can't pass off their own framing as independent research.
- **The preview is on by default.** Your draft is recorded, and the evaluation measures the draft, not only the
  edited version. A switch allows a later A/B comparison.
- **Simple infrastructure.** Django and SQLite, one web server and one worker on Fly.io, for about $6–7 a month. The
  first deploy found three problems that only appear on Fly, and all three were fixed.

**Not built yet.** Several discussions on the same position, and a "post and request background" option. Also
deferred: email recovery, more problem types, identity-bias tests, and labelling user-written positions. Open
questions include a possible "gotcha" use of the research button, and whether the moderator's self-check for
even-handedness actually works.

## The evaluation pipeline

**Preliminary findings.**
> *Placeholder — the evaluation has not been run.*

**Defining neutrality.** This took the most effort. Your definition was "equal contributions get equal frequency,
manner and intended effect of intervention," and each part had to be made countable:

- **Equal contributions** means equal factual support, clarity, logic and conduct. Political position, identity
  and speaking order must not matter, and political position is the first priority.
- **Frequency, manner and effect** mean comparing how often the moderator acts, which acts it picks, its tone and
  its length, for the same kind of problem.
- **The same kind of problem** means the same severity, scored phrase by phrase on a 0–4 scale.
- **Contested claims** can be marked "not scorable," so no rater decides the truth on contested politics.
- **A participant's side** follows mechanically from their stance and the topic's known lean. No rater judges it.
- **Clarity** may change the moderator's response. Style, register or dialect at the same clarity may not.

**How it's measured.** AI raters and human raters mark problems without knowing anyone's side. The AI raters are
checked against humans before anyone trusts them. The strongest tests are scripted pairs that are identical except
for political direction. Mechanical series vary one factor at a time, and a non-political warm-up shakes out bugs.
A deliberately biased moderator has to be caught, and any real difference has to exceed the run-to-run noise.

**Tradeoffs.**
- **Cost against scale.** Every run has a spending limit and a dry run that estimates cost first.
- **Cheap judges against independent ones.** The AI judges are Anthropic models, like the moderator, so every
  result is also computed from human labels alone.
- **Control against realism.** Scripted pairs lead, and real conversations only corroborate them.
- **Your own audit of the test scenarios** is a hard gate before any result counts.
- **Rules fixed in advance.** The thresholds are written down before anyone looks at results.

**Where it stands.** Most of the measuring machinery is built and tested, but it has not been calibrated or run.
What's left is mainly your time: rating messages by hand and auditing the scenarios. The calibration step is
deferred indefinitely.
