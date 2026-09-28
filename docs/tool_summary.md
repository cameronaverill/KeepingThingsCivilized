# Think Together: What the Tool Does and How It's Built to Be Evaluated

A working summary written for the owner, covering how the tool uses LLMs to make
disagreement more productive, how it is designed to be politically neutral, what
in that design specifically exists to support a later bias evaluation, what the
evaluation pipeline itself looks like, and what's left to do. Sources: `docs/plan.md`
(v5, the source of truth), `docs/neutrality.md` (the owner's own criteria), and
`docs/plan_summary.md`. Where this document paraphrases, the two source documents
still govern in case of any conflict.

## 1. How the tool uses LLMs to facilitate productive disagreement

Think Together pairs two people who hold opposite sides of a single proposition
("My position is that X" vs. its opposite or "I disagree with this position") in
a plain, turn-free two-person discussion. After **every** user message, a fixed
two-stage LLM pipeline runs once:

- **The Master Moderator (detection).** Reads the transcript so far and flags
  specific problems in the *newest* message (plus a few inherently cross-message
  issue types — repetition, strawman, process violations — which can look back
  further). It does not comment on old messages a second time under a new issue
  type ("only-new-issues rule"). Each issue names a **type** from a fixed taxonomy
  (`unsupported_claim`, `possible_factual_error`, `unclear_statement`, `fallacy`,
  `strawman`, `abusive_language`, `repetition`, `process_violation`), quotes the
  exact **phrase** responsible (or the whole message when that's the unit of the
  problem), gives a confidence, and — for the two dimensions with a scoring
  rubric so far (`factual_accuracy`, `abusiveness`) — an **intensity from 0–4**.
  It also produces a `discussion_map` of agreements and disagreements (the latter
  tagged factual vs. normative). Since 2026-09-27 it also flags a claim
  `needs_verification` when it can't confidently let the pipeline assert on it
  directly (see §1's web-search addition below).

- **The Intervenor (action).** Given the transcript and the Master's *valid*
  issues, decides `intervene` or `no_intervention`, gives a disposition
  (`acted`/`declined`) for **every** valid issue it was handed (so declining is
  itself a recorded, auditable decision, not silence), and — if it intervenes —
  writes up to `MAX_ACTS_PER_INTERVENTION` (3) discrete **acts** from a second
  fixed taxonomy: `provide_information`, `correct_factual_error`,
  `improve_argumentation`, `clarify_argument`, `restate_positions`,
  `identify_agreement_disagreement`, `request_information`,
  `request_clarification`, `enforce_conduct`, `enforce_process`. Each act
  records who it's addressed to, who it's about, which issues/messages it comes
  from, a self-reported tone (`gentle`/`neutral`/`firm`), and the text itself.
  The acts are rendered, in order, as a single moderator message.

So the moderator's "productive disagreement" toolkit is deliberately not just
"correct the facts" — it also asks for clarification, restates positions,
names points of agreement/disagreement, asks a party to respond, and can
enforce conduct or process (flooding, an unanswered direct question). This
breadth is itself a testable design choice: §2/§3 of `neutrality.md` explicitly
lists these act *types* as the level at which equal treatment must hold, not
just "did it step in."

**Factual grounding (the newest piece, 2026-09-27, "Step 20b").** The owner's
view, recorded in `plan.md` §2, is that the moderator's single most valuable
function is supplying accurate factual background, and that participants will
routinely debate current events the model's training data can't know about or
gets wrong. Rather than route all factual content through live search (which
would weaken the moderator exactly where a direct, confident answer is cheap
and safe, and would tangle the measurable existing pipeline with a much harder
click/uptake dynamic), the design keeps the existing direct-assertion behavior
as the default and adds live web search as a narrow **fallback**:
- The Master's `needs_verification` flag (renamed from an initially narrower
  `time_sensitive`, once it became clear the real risk is broader than
  recency — the model can also simply fabricate a specific detail like a
  citation or statistic) marks a checkable claim the existing pipeline can't
  confidently assert on outright, for either reason.
- The Intervenor can then choose the `offer_research` act, or attach a
  "Provide factual background" button to `correct_factual_error`,
  `provide_information`, or `request_information` acts — never a free
  standing, always-on, or free-text tool, so a research request is always tied
  to a specific act the moderator already made.
- Either participant may click the button; the first click claims a new
  `ModerationRun` of kind `research`, which reuses the existing async
  worker/pipeline machinery to make one `web_search`-enabled, structured-output
  call and post a short, sourced note as an ordinary moderator message.
- This is evaluated **separately** from the core detection/action metrics
  (§9 item 6, below), specifically because live retrieval breaks the
  reproducibility the rest of the evaluation depends on.

## 2. How the tool is designed for neutral moderation, and how that's operationalized

The owner's neutrality standard (`docs/neutrality.md`, in their own words):

> A moderating system is unbiased when, given two equal contributions, it
> intervenes at an equal frequency, in an equal manner, and with equal
> intended effect.

Two things are named as fixed ("equal factual support, clarity, logic,
procedural conduct" — call this "equal substance"), and three things are named
as things that must **not** move treatment when substance is held equal:
political position (first priority), speaker identity, and positional
ordering. Style/register/dialect at *equal clarity* also must not move
treatment, though clarity itself legitimately can.

The plan operationalizes this in several concrete, code-level ways:

- **Blinding.** The moderator LLMs never see usernames, emails, political
  stance, or which side of the proposition a participant holds. They see only
  randomly assigned `Participant A/B` labels, generated at conversation
  creation with a stored seed. Prompt builders (`agents.py`) receive only
  labels and message text, never `User` objects — enforced by a test, not just
  convention. The Research component (`moderation/research.py`) follows the
  identical rule: its prompt carries no participant label, username, or side.
  Order of speaking/joining is logged separately (not hidden from the system,
  just not given to the moderator), specifically so **positional** bias stays
  testable even though the moderator itself can't use position as a signal.
- **Users never see the labels either** (2026-09-25 decision): act text must
  never name a participant by label, and the UI computes a per-viewer heading
  ("About your message 4" / "About the other participant's message 3" / "For
  both of you") from each act's `addressee`/`subject` fields rather than from
  raw label text — closing a channel by which the moderator's own output could
  leak whom it's talking about in a way a reader could map back to a side.
- **A fixed, shared taxonomy instead of free-form judgment.** Every issue and
  act comes from the single source of truth in `moderation/taxonomy.py`, which
  also drives the Pydantic schemas and the prompt text — one place, not three
  independently-drifting descriptions. This matters for neutrality because it
  is the same categorical scheme applied to both sides; a moderator free to
  invent ad hoc descriptions per message would be much harder to audit for
  equal treatment.
- **Everything is logged, including "nothing happened."** A `ModerationRun` is
  created for every user message, and a `no_intervention` decision is a
  logged, structured outcome, not silence. This is what makes "equal
  frequency" measurable at all — there is a denominator (every triggering
  event) and a numerator (every intervention), for both sides.
- **Two independently measured stages.** Detection (did the Master notice?)
  and action (given a notice, did the Intervenor act?) are logged and will be
  measured as two separate conditional probabilities, because a bias that
  looks like "doesn't intervene as much for side X" could originate at either
  stage, and the fix would differ.
- **Item-level validation instead of whole-run failure.** A bad quote, an
  unknown message id, an act that breaches the only-new-issues rule, or acts
  over the cap are rejected individually (with a reason, kept in the database)
  rather than failing the entire run. This matters for neutrality
  measurement specifically because whole-run failures would silently and
  disproportionately remove data from whichever side tends to write longer or
  more complex messages — rejection rates are explicitly reported **by side**
  so that failure mode itself is auditable.
- **No turn-taking rule, on purpose.** Nothing requires alternation or stops
  one participant posting repeatedly; this is treated as a realistic
  condition to observe the moderator's process-related behavior under
  (flooding, unanswered questions), not something to be engineered away
  before it can be measured.
- **Injection safety as a neutrality-adjacent guarantee.** User text is always
  inserted as escaped, delimited data, with an explicit instruction that
  embedded instructions must be ignored; an injection attempt is one of the
  golden-set test cases. This isn't about political bias per se, but it
  closes off a channel by which one participant could otherwise manipulate the
  moderator's behavior toward themselves or the other side.
- **A prompt-precision fix from real transcripts (2026-09-27)** required the
  Master to distinguish a participant *asserting* a claim from one *denying or
  noting the absence of evidence for* the same claim — otherwise "you haven't
  shown X" could be misfiled as an unsupported claim by the person who is in
  fact challenging it, which would be a real (if subtle) equal-treatment bug,
  not just a UX nit.

## 3. Design choices made with a later bias evaluation specifically in mind

The plan's own framing (`plan.md` §1) is "log first": the site is phase A, and
a wholly separate evaluation pipeline (phase B) consumes its logs — so nearly
every data-model decision below exists because the evaluation was designed
*before* being built, not bolted on after:

- **Phrase-level, not message-level, ground truth.** Every issue and every
  human/LLM rating attaches to a specific quoted phrase (with `quote_start`/
  `quote_end` offsets and a match-type flag: exact vs. normalized vs.
  not-found), never just "this message was bad." This is what makes
  "equal contributions" comparable at all — two messages of very different
  length and content can each contain a directly comparable phrase-level
  finding at a given intensity.
- **A 0–4 intensity scale plus a first-class "not scorable" outcome.** Forcing
  every contested political claim onto a numeric scale would itself inject
  the rater's judgment of "truth" into the ground truth. Instead, a rater
  (human or LLM) can decline to score a phrase as `unverifiable`,
  `contested`, or `needs_context`, and how often each *side's* claims get
  called contested/unverifiable is itself a reported statistic.
- **Random A/B labels with a stored, per-conversation seed**, and every
  paired test transcript is run under **both** label assignments —
  specifically to distinguish a real political-direction effect from an
  artifact of which literal label ("A" or "B") happened to be attached to
  which side.
- **Everything an LLM call did is priced, versioned, and logged
  (`LLMCall`)**: prompt version and its SHA-256 hash, exact request/response,
  token counts by type, cost, latency, status. This lets a later analysis
  reconstruct exactly which prompt version and model produced any given
  decision, and separates evaluation spend (`purpose=golden|replay|judge`)
  from production spend so an audit never distorts the live budget.
- **Config snapshots per run** (`ModerationRun.config_snapshot`): models,
  temperatures, prompt versions/hashes, and the tunables in effect at run
  time, stored on the row itself — so a run from months ago remains
  interpretable even after prompts or models change.
- **Item-level rejection reasons, reported by side** (see §2 above) exist
  specifically so a "your intervention rate differs by side" finding can be
  distinguished from "your *data quality* differs by side," which would
  otherwise masquerade as a neutrality finding.
- **A `replay` run kind that never posts.** The exact same pipeline function
  (`run_moderation`) can be re-run against a frozen transcript snapshot
  without ever creating a real user-visible message — this is the mechanism
  the entire paired-transcript evaluation and the intervention-preview
  feature both reuse, rather than building separate infrastructure.
- **`Experiment`/`pair_id`/`variant`/`source` fields on `Conversation`** exist
  from the schema's inception to distinguish real (`human`) from synthetic
  (`synthetic`) conversations and to group matched pairs and replicates,
  even though nothing used them until phase B.
- **Position/order logged even though the moderator can't see it**, so
  positional bias (who spoke first) stays testable as a separate axis from
  political-side bias, exactly matching `neutrality.md` §5's explicit "keep
  testable for later" list (speaker identity, ordering, message length).
- **`Topic.leans`**, a JSON field giving each side's position under multiple
  political-direction schemes (a left/right compass with economic and social
  axes, and a US-partisan scheme), is attached to seeded topics at creation —
  this is what lets "side" be derived mechanically from `(topic.leans,
  participant.stance)` rather than requiring a rater to label political
  direction after the fact, which would introduce exactly the kind of
  rater-supplied "truth" the not-scorable categories are designed to avoid
  elsewhere. User-created propositions have no `leans` and are explicitly
  parked as a separate, unlabeled stratum until a rater panel can blindly
  assign a direction later.
- **The Intervenor's per-act tone self-tag and code-computed act features**
  (character length, word count, is-question, whether it quotes the
  participant) exist so "manner" (harsh vs. polite, long vs. short) is
  measurable without requiring a rater to score every single act by hand —
  directly answering `neutrality.md` §4's requirement that "whether one
  person is corrected very harshly and another very gently should be knowable
  without the evaluation pipeline having to score everything."
- **The web-search Research feature was scoped from day one around
  measurability**, not just usefulness: the standing always-on button and the
  free-text research box were both explicitly rejected because they would
  turn a *measurable moderator decision* into an *unmeasurable user-behavior
  question*; the chosen design keeps every research trigger tied to a
  specific, already-logged moderator act.

## 4. The evaluation pipeline: how bias is defined, tested, and prioritized

**Definition of bias (operational).** Neutrality is treated as **equalized
odds**: for a given trigger condition C (e.g., "a phrase with an unsupported
claim at intensity 3") and two sides X and Y, P(moderator response | C, side
X) ≈ P(moderator response | C, side Y), evaluated separately for:
1. **Detection** — does the Master raise a matching valid issue, given a
   ground-truth finding at a given intensity, by the speaker's side?
2. **Action** — given a raised issue, does the Intervenor act, at that
   intensity and side?
3. **False positives** — of phrases actually rated 0 (or never flagged), what
   share does the Master flag anyway, by side?
4. **Data quality by side** — rejected-item rates, failed/skipped runs,
   "not scorable" rates.

Everything is compared **at equal intensity and equal difficulty** — the plan
is explicit that a gap where the moderator barely detects anything on either
side (a "difficulty" mismatch) says nothing about bias.

**Ground truth comes from a calibrated rater panel**, not from the moderator
itself: two Anthropic models at temperature 0 (versioned/hashed prompts,
logged like any other call) plus human raters, all using the *same* rubrics
and blinded to usernames, `Topic.leans`, experiment/variant labels, and the
moderator's own output. Findings from different raters are merged in code by
phrase-span overlap (IoU ≥ 0.5 by default); disagreements past a threshold, or
disagreement on whether something is even scorable, route to human
adjudication. Before the LLM panel is trusted on anything unlabeled, it's
calibrated against ~100 human-labeled messages per dimension: human–human
agreement, LLM-vs-human agreement, **symmetry of the LLM judges' own errors by
political side** (since the judges are Anthropic models too and may share the
Master's blind spots), and a **human-only benchmark** — the headline metrics
computed on human labels alone, precisely so a shared-blind-spot problem
between the Master and its LLM judges can't hide in the results.

**Test families**, roughly in order of how much confidence they can support:
1. **Political-direction pairs (the headline test).** Matched scripted
   transcripts, identical in structure/length/hedging/number of checkable
   assertions, differing only in political direction, at two difficulty
   tiers (obvious vs. hard), each run under both label assignments — sides
   compared only within a difficulty tier.
2. **Mechanical series (cheap, exact ground truth).** Crossed series where
   one code-computable factor varies (message length, label swap, speaking
   order, flooding, repetition, an unanswered question) with everything else
   fixed, run for both political directions, ground truth computed
   mechanically rather than by any rater.
3. **Non-political warm-up.** The identical paired/mechanical machinery on
   low-stakes topics (school start times, bike lanes, remote work) —
   validates the *pipeline*, explicitly **not** evidence of political
   neutrality (models are often specifically tuned to behave differently on
   political topics), but a cheap way to shake out bugs before spending on
   sensitive content.
4. **Controls**, required before any "no bias found" claim is trusted:
   - **Positive control** — run the whole pipeline against a deliberately
     biased moderator and confirm the analysis actually detects it.
   - **Negative control / noise floor** — repeated runs of identical
     transcripts, since Sonnet 5 takes no temperature setting, to establish
     how much variation is just noise before any "real" gap is claimed.
   - **Human-only benchmark** (above) — checks the raters' own bias.
5. **Observational (later, corroboration only).** Real user conversations,
   confounded by the fact that one side may simply argue worse or better, so
   used only to support the paired results, never to establish them alone.
6. **Web-search research feature — its own, separate lens (2026-09-27).**
   Live retrieval breaks the golden-transcript methodology's core guarantee
   (reproducible, author-controlled input), so it gets two different
   mechanisms instead of one: a **frozen-evidence replay** (capture real
   search results once, freeze them, replay identical evidence through
   matched political pairs — tests only whether *equal evidence* gets equal
   treatment) and **ongoing production monitoring** (not a one-time audit —
   tracks retrieval asymmetry and click-through-by-side on real usage,
   reported rather than pass/failed).

**What's prioritized now vs. explicitly deferred:**
- *Now, first priority:* whether political position/direction changes
  treatment, holding substance constant — this is called out in
  `neutrality.md` as "the most concerning form of bias," and it's the only
  axis the paired-transcript track is built to test today.
- *Kept testable, not yet analyzed:* positional/order effects (label swap and
  order are already in the mechanical series and logged, just not yet the
  headline analysis), content-feature bias like raw message length below the
  cap.
- *Explicitly out of scope for v1, named and reasoned about rather than
  ignored:* speaker-identity characteristics (race, gender, etc. — the design
  never collects them and the moderator never sees them, so this axis can't
  even be tested yet, only "kept testable" structurally); source-quality/
  political-lean parity of *cited sources* in the web-search feature
  (judged too value-laden a project to take on now, deliberately narrowed to
  "does this app's own pattern deviate from a direct same-query baseline");
  differential uptake and requester-favoring bias in who clicks "Provide
  factual background" (named as a real risk, not mitigated in the MVP);
  quality evaluation beyond bias (is a correction actually *right*, did the
  discussion get more productive) — noted as a real future direction but not
  designed yet.
- *A hard, unresolved constraint on all of the above:* no bias result may be
  reported or trusted, and the pre-registration finalized, until the owner
  personally audits the transcripts (Step 17, `plan.md` §19) — the current
  ~42-transcript golden set is explicitly "mechanics only, unaudited," and
  every result computed from it so far carries that label.

## 5. Next steps

Per `plan.md` §14/§16 and `plan_summary.md`, work proceeds one step of section
14 at a time, tests first, with the owner approving each step before the next
begins. As of this writing:

- **Immediately in progress:** Step 20b, the factual web-search research
  feature (§2/§9 item 6 above) — the Master's `needs_verification` flag, the
  `offer_research`/button-eligibility Intervenor behavior, the `research`
  `ModerationRun` kind and its worker/pipeline integration, and the forum-side
  click and resulting-message rendering. Recent commits show this landing in
  pieces (structured `web_search` support in `llm.py`, the Research
  component itself, the forum click handling, a bug fix for a missing
  "Provide factual background" button on `request_information` acts).
- **Already scheduled, not yet started:**
  - Preview-integration for the research feature (a third preview choice,
    "Post and request background") — deferred until Step 20b's core is
    verified.
  - The remaining Phase A steps if not already complete (queries/exports/
    analysis functions, seed topics, README, the end-to-end verification
    checklist) and then Phase B in full: finalizing rubrics with the owner,
    the evaluation models and human annotation page, the paired-set/series/
    replay loader, the LLM rater runner and consensus/matching logic, the
    calibration report and controls, and only then the pre-registered
    analysis itself.
  - **Step 17 — the owner's own scenario audit** — a hard gate: no bias
    result can be trusted or the pre-registration finalized until this is
    done. Not yet done as of the plan's last update.
  - **Step 18 — the test-suite quality gate** (three independent read-only
    review agents per slice, a prioritized fix list, then stricter rewritten
    tests), per `docs/test_audit_plan.md`.
  - **Step 19 — the intervention preview feature** was approved as the MVP
    default (2026-09-26) but the plan requires Claude to re-confirm with the
    owner, at that step and again before deployment, that it's still wanted
    before any of it is built.
- **A running list of deliberately deferred design questions** lives in
  `plan.md` §18's "For post-MVP design" block (opened 2026-09-27): who may
  click the research button and whether that invites a "gotcha" dynamic; a
  possible v2 always-on research tool and its own, different evaluation
  design; whether Research notes should join the same rater/calibration
  pipeline as other moderator acts; `web_search` domain scoping; how to
  refresh a frozen-evidence test set as current events age; and widening
  "Provide factual background" beyond acts the Intervenor already flagged.
- **Open decisions not yet made** (`plan.md` §17): hosting/HTTPS and email
  before any real users; final rubric wording (settled with the owner at
  Step 11); whether the cheaper model is good enough for the live moderator
  (decided after the Step 3 spike; both a `spike` cost run and prompt review
  already happened; Sonnet 5 is currently the live-moderator default);
  final intensity thresholds and the headline political-direction scheme,
  both to be fixed in the pre-registration before Step 16's analysis, not
  after.
