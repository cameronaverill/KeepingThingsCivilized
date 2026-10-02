# Think Together: design process and rationale (short version)

A condensed version of `docs/design_process_summary.md` (2026-10-02). Results cover 12 facts, 88 debates and three runs;
the 8 facts added on 2 October are not yet included.

## What I built

Think Together pairs two people who hold opposite positions and adds an AI moderator. Each message goes through two
separate Claude calls: one only detects problems, and the other only decides whether to step in, choosing from a fixed
menu. A third call runs a web search when a participant asks for factual background. Every decision is logged,
including decisions not to step in.

## What I focused on

I began with a broad list: mapping agreement, supplying facts, clarifying arguments, correcting logic and enforcing
procedure. Using the first working version narrowed it. I concluded that most unproductive discussion comes from
differing assumptions about the facts, so factual grounding became the focus, with web search because people argue about
current events. Agreement notes and clarity questions came second, and conduct last.

## How I approached neutrality

I defined neutrality before designing anything: given two equal contributions, the moderator intervenes at an equal
frequency, in an equal manner, and with equal intended effect. Political side must not change the treatment.

That definition is a comparison of rates, so the site had to produce countable records. This is why detection and
decision are separate calls, why acts come from a fixed menu, and why "no intervention" is recorded. The moderator also
sees only random "Participant A/B" labels, so it cannot favor a side it cannot identify.

## Tradeoffs

- **Web search versus testability.** Live search cannot be reproduced exactly. I accepted that because informed
  discussion is worth more than uninformed discussion, and the feature can be removed if testing shows a problem.
- **Preview versus clean measurement.** Showing the moderator's note before posting changes what people post. I kept
  it, logged the original draft, and added a switch for later A/B testing.
- **Neutrality versus usefulness.** This was the largest, and the evaluation exposed it (below).

## Supporting the participants' own reasoning

The moderator never takes a side. It mostly asks questions, offers research instead of imposing it, and links its
sources so participants can check them. The preview lets an author revise or post as written, so the choice stays theirs.

## Principles

Treat like cases alike, which is the neutrality definition itself. Keep the decision-maker blind to who the parties
are. Separate finding a problem from deciding the response. Record every decision so it can be reviewed. Give notice
before acting. Give deliberation a shared factual basis.

## The bias evaluation

**Bias** means that, for contributions equal in substance, treatment differs with the political side they help. I
measure how often the moderator steps in, what it does, and how accurate it is.

**Method.** I verified facts about sanctuary cities and made mirrored false versions of each: one helping the left and
one helping the right, at small, medium and large sizes. An AI wrote matched debates that differ only in that one claim.
Each debate ran through the real moderator three times, and an AI judge, not told the side, compared each reply with the
true fact. Differences between repeat runs of the same debate serve as the yardstick for chance.

**Results.** The moderator replied to every false claim on both sides, and I found no consistent left-right difference.
But 76% of those replies were the same request for a source, and only 9% said what was wrong. So it treats both sides
alike largely because it does little for either. Web-search notes did better: 97% flagged the false claim and 35%
corrected it.

**What I do with it.** A prompt change barely moved the numbers, so the fix is structural: let the moderator check
facts before it replies.

**Gaps.** I have not yet run a deliberately biased moderator, so I cannot yet show the test would catch bias. No person
has reviewed the generated debates, and there is one topic.

## Measuring success

Left-right gaps stay within chance in a test shown to catch planted bias, and usefulness is reported beside it: the
share of false claims correctly corrected and of true claims left alone.

## Scaling

The moderator does not depend on the topic, and the two-person limit is a setting. More conversations need a database
server and a job queue in place of the single file and single worker. The evaluation needs new verified facts for each
topic, but it is automated and has cost under $50 so far, so it can re-run on every prompt or model change.
