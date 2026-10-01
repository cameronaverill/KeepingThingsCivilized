# Think Together: a retrospective

Written 2026-09-28, for you, to retrace the thinking behind the project. It draws on `docs/plan.md` and its four
earlier versions, `docs/neutrality.md`, the step briefs, and the decisions recorded along the way. Where a reason
was never written down, this document says so rather than guessing.

---

# Part 1: The app

## What it does today

Think Together is a website where two people who disagree about something talk it through, with an AI moderator
helping the conversation stay productive.

A conversation always starts from a **position**, such as "My position is that rent control lowers rents." The home
page, "Waiting to discuss," lists positions that someone holds and is waiting for someone to disagree with. You
click one to take the other side, and the two of you are paired. You can also start your own: write a position
yourself, or pick one of the seeded topics and choose a side. If nobody is waiting on the opposite side, your
conversation waits for them, and you can post in the meantime. Your conversations, open or finished, are on a
separate page, "Your discussions."

Inside a conversation, the two participants see each other's usernames and take turns however they like; nothing
forces alternation. There are three limits: 3,000 characters per message, 30 seconds between one person's messages,
and 30 messages per conversation. Either person can end the conversation, and either can block the other from
inside it, which ends every conversation they share and keeps them from being paired again.

After every message, the AI moderator reads the conversation and decides whether to step in. When it does, its
note appears as a numbered message in the thread, with a heading telling each reader who it's about ("About your
message 4," "About the other participant's message 3," or "For both of you"). Two extra features sit on top:

- **Preview.** When you press Post, the moderator checks your draft first. If it would intervene, you see its note
  before anyone else does and choose to edit your message or post it as written.
- **Factual background.** When the moderator's note concerns a factual claim, it can carry a "Provide factual
  background" button. Either participant can click it, and the moderator then searches the web and posts a short,
  sourced note.

The site is live on Fly.io, with the database backed up continuously to Backblaze B2. **The moderator is switched
off on the live site** (`LLM_ENABLED = False`), which was the intended default: turning it on is a deliberate step
you take once you're satisfied with the spending limits described below. Until then, every message is recorded as
"moderation switched off" and no AI runs.

## How it works behind the scenes

**Two AI roles, not one.** Every message is handled by two separate calls to Claude (Sonnet 5), each with its own
job:

1. The **Master Moderator** only *detects*. It reads the conversation and lists problems in the newest message: an
   unsupported claim, a likely factual error, an unclear statement, a fallacy, a strawman, abusive language,
   repetition, or a process problem such as flooding. For each one, it quotes the exact phrase responsible and, for
   factual errors and abuse, rates how severe it is on a 0–4 scale.
2. The **Intervenor** only *acts*. It receives the Master's list and decides whether to step in. For every problem
   it was handed, it must record whether it acted on it or declined, so declining is a recorded decision rather
   than silence. If it steps in, it writes up to three short "acts" from a fixed menu: supply information, correct
   an error, ask for a source, ask for clarification, restate both positions, point out where the two agree and
   disagree, or enforce conduct or process.

A third role, the **Research** step, runs only when someone clicks "Provide factual background." It makes one
web-search call and writes a short note with sources.

**The moderator never sees who anyone is.** It sees "Participant A" and "Participant B," labels assigned at random
when the conversation begins. It never sees usernames, which side each person took, or any political label. Users,
in turn, never see those A/B labels: the page works out each reader's heading from who the note is addressed to.

**The work happens in the background.** Posting a message saves it and queues a moderation job. A separate worker
process picks up jobs one at a time, runs the two AI roles, and posts the result. The page checks for new messages
every few seconds, so the moderator's note appears shortly after yours.

**Money is guarded in layers.** Only one file in the codebase is allowed to talk to Anthropic, and every call goes
through the same checks first. Before each call, the worst-case cost is added to spending so far; if that would
pass any cap, the call is refused. The caps are $5 in total, $1.50 per day, and $1.25 per conversation, with a
separate $10 for evaluation work, and a $10 monthly limit set in the Anthropic Console as a backstop. A circuit
breaker stops all calls after a spending-limit error or a run of API errors, and emails you when it trips. A kill
switch turns everything off. If the spending ledger can't be read, no call is made.

**Everything is recorded.** Each moderation job stores what the AI was shown, what it said, what was accepted or
rejected and why, what it cost, and which prompt version and model produced it. This record exists for the
evaluation described in Part 2.

## Why it's built this way

### Measurability shaped the product from the start

The plan's first principle was "log first": the site is step one, and a separate evaluation uses its records.
Most of the unusual choices follow from that.

- **Two roles instead of one** because bias can enter at either stage. The moderator might notice one side's errors
  less often, or notice them equally but act on them less often. Those are different problems with different fixes,
  so they have to be measured separately.
- **A fixed menu of problems and acts** instead of free-form judgment, because the same categories applied to both
  sides can be counted and compared. A moderator free to describe each message in its own words would be much
  harder to audit.
- **"No intervention" is recorded like any other outcome**, so every message has a result. Without that, there is
  no way to say how *often* the moderator intervenes for each side.
- **One bad item doesn't sink the whole job.** If the Master misquotes a phrase, only that item is rejected. Failing
  the whole job would throw away the valid findings with it, and because longer messages have more chances to
  contain a misquote, it would silently remove more data from whichever side writes longer messages.
- **The AI returns quotes, not character positions**, because models are unreliable at counting characters. Code
  finds the quote in the message instead.
- **No turn-taking rule.** Nothing stops someone posting several messages in a row. That is realistic, and how the
  moderator handles flooding and unanswered questions is itself something to observe.

### Keeping identity and position out of the moderator's view

The random A/B labels, the stored random seed, and the rule that the moderator never sees usernames or sides all
serve one goal: the moderator can't treat people differently based on who they are or which side they took,
because it doesn't know. Speaking order is recorded separately so that order effects stay testable, even though
the moderator can't use them.

Hiding the labels from users came later (2026-09-25). If the moderator's text said "Participant A," readers could
work out which side it was talking about from what it said, which reintroduces the problem. So the moderator's
notes may not name anyone, and the page computes each reader's heading instead.

### A plain, calm, low-cost product

- **Username and password only, no email.** Email confirmation and password reset were built and then removed
  (2026-09-26). A forgotten password is reset by you by hand. No reason was recorded beyond keeping the first
  version simple; the code can be restored from git history.
- **No talk of costs to users.** Pages explain limits in terms of keeping the discussion readable. When moderation
  is paused, the page says so, not why it costs money.
- **Anyone can post a position, and it appears immediately**, with no review and no AI screening. Screening would add
  cost and a new place for bias to enter.
- **Every conversation is between two opposite positions**, and only opposite sides are paired. Two people waiting
  on the same side never meet.
- **Usernames are shown, and people can block each other** (2026-09-26). This reversed an earlier rule that
  participants never saw each other's names, so that people can avoid particular participants. The moderator still
  never sees names. After the first playtest, blocking was moved from the home page into the conversation itself,
  because blocking someone from a card showing only a username was too easy to do by mistake or on a whim.
- **The moderator never replies to itself.** This is enforced in four independent places (the code path that posts
  user messages, a database rule, a check before any AI call, and the spending caps as a last resort), so a bug in
  one can't start an endless, costly loop.

### Factual research as a fallback, not a replacement

You judged the moderator's most valuable job to be supplying accurate background, not policing tone, and expected
people to debate current events the model's training can't know about. The design question was how to add live
web search without undermining what already worked.

The answer: the existing moderator keeps correcting clear-cut errors directly, exactly as before, and search is a
fallback for claims it can't confidently handle, either because they may be recent or because the model might
simply be wrong or making up a detail. You explicitly rejected sending *all* factual content through search: it
would weaken the moderator exactly where a direct correction is cheapest and safest, and would tangle the existing,
measurable evaluation with a harder-to-measure question about who clicks.

The button is always attached to a specific moderator note, never a standing "research anything" control and never
a free-text box. A free-text box would let a participant slip their own leading framing into what then reads as
independent research, and it would turn a measurable moderator decision into an unmeasurable user choice. The
button was later extended to "ask for a source" notes, after you saw a real one that had no button, since that note
is the moderator saying it has no confident answer of its own.

### Preview, adopted with its costs in view

The preview came from your suggestion (2026-09-26) and was approved as the default the same day, with the
instruction "do not go crazy." Before approving it, you weighed that it is a second moderator decision about the
same message, so it must meet the same neutrality standard; that people who learn to edit before posting change what
the records measure; and that it costs an extra moderation run per attempt. The result: the check is recorded, the
evaluation measures the message as first drafted, and a switch (`PREVIEW_SHARE`) can later give previews to only
some conversations for an A/B comparison. It is set to "everyone" for now.

### Engineering choices

- **Django and SQLite** because they are simple and run on every cheap host checked, including PythonAnywhere.
  Python 3.13 rather than 3.14 for the same reason.
- **Tests first, with separate agents.** For each step, one agent wrote the code and a different agent wrote the
  tests from the written contract, so the tests weren't written by whoever wrote the code. No test touches the
  real API; a fake stands in.
- **Every tunable number in one file**, `config/tunables.py`, enforced by a test.
- **A final test audit** (2026-09-28) had 39 read-only agents review the whole suite for gaps, weak tests and poor
  style. Every issue fixed so far turned out to be a missing test, not a code bug.
- **Fly.io for hosting**, about $6–7 a month. It keeps a persistent disk, which SQLite needs, and runs the web
  server and the worker side by side. Because SQLite allows only one writer at a time, there is exactly one of each,
  never more. The first real deploy found three problems that local checks couldn't: database setup had to move to
  web-server startup, where the disk is attached; Django had to trust Fly's secure-connection header, or every login
  failed; and the web machine needed more memory for password checking.

## What wasn't built, and what's next

**Decided but not built yet**
- Letting a person hold several discussions on the same position (today, choosing a side returns you to your
  existing one).
- A third preview choice, "Post and request background." Researching a draft *before* posting was deliberately
  ruled out: a draft has nothing to attach research to, and it would let someone run paid searches on drafts they
  never post.

**Deliberately deferred**
- Email for account recovery.
- More problem types with severity scales (fallacies, unclear statements, strawmen).
- Tests for bias by identity (race, gender), which can't be run yet because the site never collects identity.
- A survey after each discussion, specialized fact-checking agents, and process rules enforced in code.
- Labelling the political direction of user-written positions, so their conversations can join the analysis.

**Open questions you named and chose not to solve yet**
- Whether the button invites a "gotcha" dynamic, with one person checking every claim the other makes.
- Whether the people who click differ by political side, and whether the research note leans toward whoever
  clicked.
- Whether to restrict which websites the search may use.
- Whether "extended thinking" could improve factual accuracy alongside search.
- Whether the Master's built-in "swap test" (checking itself for symmetry within a single answer) actually works,
  or needs to be checked from outside.

---

# Part 2: The evaluation pipeline

## Preliminary findings

> **Placeholder.** The evaluation has not been run. No result exists yet, and any output produced from the current
> test conversations is labelled "mechanics only, unaudited" until you complete the scenario audit (Step 17).
>
> *To be filled in: headline detection, action and false-positive rates by side; the size of the noise floor; whether
> the deliberately biased moderator was caught; judge symmetry; data-quality differences by side.*

## Turning "neutral" into something measurable

More time went into defining neutrality than into any other part of the project, because nothing downstream can be
trusted without a precise definition. It started from your own words:

> A moderating system is unbiased when, given two equal contributions, it intervenes at an equal frequency, in an
> equal manner, and with equal intended effect.

Each part of that sentence had to be pinned down.

**"Equal contributions."** You defined equal as equal factual support, clarity, logic and conduct, and named what
must *not* change the treatment when those are held equal: political position, the speaker's identity, and the
order people spoke in. Political position came first, as the most concerning.

**"Equal frequency, manner and effect."** You framed these as conditional rates: given the same kind of problem, does
the moderator respond equally often, in the same way? That turned an abstract principle into a comparison that can
be counted. Frequency is how often it steps in. Manner is which acts it picks, its tone, and how long its note is.
Intended effect comes from the moderator's own labels for now and from human judgment later. You added that harsh
versus gentle treatment should be visible without scoring every message, which is why each act records its own
tone and length.

**"The same kind of problem."** Comparing sides only makes sense for problems of the same size. One side's claims in
a given conversation might simply be more wrong. So every problem is scored for severity, 0–4, and sides are
compared only at equal severity. Early versions rated whole messages; this was refined to single phrases, because a
long message can contain one bad sentence and a whole-message score would hide it.

**Who decides what's true.** Scoring factual accuracy requires someone to decide the truth, and on contested political
claims whoever decides is exactly where bias would creep in. So a rater may decline to score a claim and say why:
not checkable, genuinely contested, or needing more context. How often each side's claims are declined is reported
as its own result.

**Which side is which.** Asking a rater "is this left-wing?" would inject their judgment. Instead each message is
marked as for or against the stated position, and each seeded topic records which political direction each side
leans. Side then follows mechanically, with no rater involved.

**Detection versus action.** Later refinement split the question in two: does the moderator *notice* problems equally
by side, and, once it has noticed, does it *act* equally?

**Style.** Clarity may legitimately change how the moderator responds, since an unclear argument warrants a request
for clarification. Register, dialect or formality at equal clarity must not.

## How the pipeline measures it

**Ground truth from a rater panel.** Two Claude models (Sonnet 5 and Haiku 4.5) and human raters mark problem
phrases and their severity, using the same written scales. Raters are blinded: they never see usernames, which
side a topic leans, which version of a test they're looking at, or what the moderator said. Where raters disagree
too much, a human settles it.

**Checking the raters before trusting them.** Before the AI raters are used on anything, they are compared with human
raters on about 100 messages per problem type: how well humans agree with each other, how well the AI agrees with
humans, and whether the AI's mistakes lean toward one side.

**Test families,** from strongest evidence to weakest:
1. **Matched pairs (the headline test).** Two scripted conversations that are identical in structure, length and
   number of checkable claims, and differ only in political direction. Each contains a planted problem of known
   severity, and each is run with the A/B labels both ways round. Pairs come in an obvious tier and a hard tier,
   and sides are compared only within a tier.
2. **Mechanical series.** One factor changes while everything else stays fixed: message length, swapped labels,
   speaking order, flooding, repetition, an unanswered question. The truth is computed by code, so no rater is
   needed.
3. **Non-political warm-up.** The same machinery on low-stakes topics (school start times, bike lanes). It shakes out
   bugs cheaply but is explicitly *not* evidence of political neutrality, because models are often tuned to behave
   differently on political topics.
4. **Controls.** A deliberately biased moderator must be caught before any "no bias found" result is believed.
   Repeated runs of the same conversation measure how much results vary by chance, and any real difference has to
   exceed that.
5. **Real conversations**, later, only to support the paired results, since real conversations can't hold substance
   equal.

**The research feature gets its own evaluation**, because live search breaks the core guarantee of the others:
input you control and can reproduce. It uses search results captured once and replayed through matched claim pairs,
plus ongoing monitoring of real use.

## Tradeoffs and other factors

- **Cost against scale.** Pairs, times two label orders, times repeated runs, adds up quickly. Every evaluation
  command requires a spending limit and offers a dry run that prints the estimated cost. A spike in September measured
  about 1.5 cents per moderated message with caching, so hundreds of pairs are affordable.
- **Cheap judges against independent judges.** Both AI raters are Anthropic models, like the moderator, and may share
  its blind spots. A non-Anthropic judge would be more independent but needs another account and budget. You chose
  cost first, with a safeguard: every headline number is also computed from human labels alone, so a shared blind
  spot can't hide in the results.
- **Control against realism.** Scripted pairs can hold substance equal but aren't real conversations; real
  conversations are realistic but confounded, since one side might genuinely argue worse. The design leads with
  control and uses real data only to corroborate.
- **Your own audit as a hard gate.** You accepted the 42 test conversations as scaffolding so the machinery could be
  built, on the condition that you would audit them yourself before any result counts. No result may be reported
  until that audit is done, because the scenarios themselves could carry bias.
- **Pre-registration.** The thresholds, the number of pairs, and what counts as passing are to be fixed in writing
  before looking at results, so the analysis can't be tuned to the answer.
- **What was scoped out.** Judging whether the web sources cited for each side are equally reliable is itself a
  value-laden project you chose not to take on. Instead, a narrower question: does wrapping search inside this
  discussion format change what Claude's search would say on its own?

## Where it stands

**Built:** the rater tables, blinding, the calibration-set builder, the replay command (both label orders, repeats,
spending limits), the AI rater runner, the merging of rater findings, the metric calculations, and the test sets
(9 matched pairs, 4 single cases, about 20 mechanical-series conversations, plus the warm-up set).

**Not built or not run:** final wording of the severity scales (Step 11); the human rating page; the calibration report
(Step 15, which you deferred indefinitely on 2026-09-28); the pre-registered analysis (Step 16, which depends on
it); your scenario audit (Step 17); and the three research-feature evaluations. One robustness check, re-running the
analysis with phrases snapped to whole sentences, exists but isn't switched on, pending Steps 15–16.

The honest summary: the measuring instrument is largely built and tested, but it hasn't been calibrated or used.
The remaining work is less about code than about your time, as a human rater and as the auditor of the test
scenarios. `docs/evaluation_pipeline_todo.md` lists it step by step.
