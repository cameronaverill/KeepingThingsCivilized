# Think Together: Design Process and Rationale

## 1. What I built

Think Together lets people who old differing positions have an AI-moderated conversation to try to come to a better understanding of each others' positions and maybe even change each others' minds. The current design pairs two users who hold opposite views on a proposition. Once a conversation has started, only the participants (and the research team) can view it. An AI moderator will intervene where it sees fit. Currently, the moderator intervenes to correct factual errors or encourage participants to support factual claims with a source, provide deeper factual background, ask users to clarify unclear statements, and summarize the points on which participants agree and disagree. 

## 2. Areas of focus

In deciding which objectives to prioritize, I thought back on unproductive conversations I have had with friends and strangers and what, in hindsight, would have improved them. In my experience, converations most often go haywire or just lose steam when the parties have diverging understandings of the facts or where one participant knows, or seems to know, more about the topic than the other. After a serious conversation, I often find myself wishing I had known more about this or that aspect of the issue or thinking that I wish I knew whether the facts my discussion partner was positing were accurate. As a result, I decided a moderator would be most useful for correcting or sourcing factual claims, and I made such interventions my initial focus. I felt it was useful to have an LLM contribute facts because it could potentially spot incorrect statements more easily than an uninformed participant and because I felt a concise factual statement of the factual landscape was more useful than trading sources pulled from a search engine.

Later on, I added functionality to summarize, on every fourth comment, the points on which the parties agree and disagree. I was inspired by vTaiwan, a tool to identify points of consensus on political issues, and generally feel it is productive in a conversation to remember points of shared understanding and, at the same time, be able to focus on the real issues in dispute. That last point was something I learned in my clerkship at a courthouse, where disputes could often get so hazy and overblown that it became easy to forget that the parties really only disagreed on a narrow set of facts or a single issue of law. I also added functionality to police personal insults (because even well-meaning participants can drift into unproductive territory sometimes) and to request participant clarification on unclear statements.

## 3. Defining neutrality

**Defining neutrality before writing code.** Before I wrote a single line of code or sketched the architecture, I thought about how a moderator could be neutral yet take advantage of an LLM's capacities. I arrived at the following definition of neutrality: *a moderating system is neutral when, given two equal contributions, it intervenes at an equal frequency and in an equal manner.*

**Equal contributions** By equal contributions, I do not mean they must be identical. That conception of equality would make the above conception of neutrality meaningless. In this design, contributions can be equal on several axes: equal factual support, clarity, logic, tone, or substance. Again, this does not mean they are identical, only that one could imagine plotting each of these aspects on a scale from 0-4 and say, this contribution has only a level 2 amount of factual support, or this statement has level 4 clarity (most clear). Necessarily, this is a messy exercise and will require careful calibration and human input on a large number of samples.

**Equal frequency and equal manner** These features of the definition operationalize neutrality as a conditional probability: given a contribution, the likehood of the moderator intervening in a particular manner should stay consistent, even where certain biasing factors, such as political affiliation, vary.

## 4. Designing the architecture

## How it works behind the scenes

**Three AI roles.** Every message runs through two separate calls to Claude (Sonnet 5), each with its own job. Some messages run through a third LLM upon participant request.

1. The **Master Moderator** *detects*. It reads the conversation and lists problems in the newest message: an unsupported claim, a likely factual error, an unclear statement, a fallacy, a strawman, abusive language, repetition, or a process problem. For each one, it quotes the exact phrase responsible and, for factual errors and abuse, rates how severe it is on a 0–4 scale.

The moderator never sees who anyone is. It sees "Participant A" and "Participant B," labels assigned at random when the conversation begins. It never sees usernames, which side each person took, or any political label. On the one hand, this makes it harder to test for political bias and identity bias later, as it requires tagging contributions as, for instance, left-leaning or right-leaning, or watching for participant messages that convey an aspect of identity. However, I did not want to require participants to declare political affiliations, which seems to only encourage disagreement at the outset and may be personal information an individual would not wish to provide. Additionally, I was concerned that telling an LLM a participants' political affiliation or identity trait might  exacerbate any bias latent in the system.

2. The **Intervenor** *acts*. It receives the Master's list and decides whether to step in. For every problem, it must record whether it acted on it or declined. If it steps in, it chooses "acts" from a  menu: supply information, correct an error, ask for a source, ask for clarification, restate both positions, point out where the two agree and disagree, or enforce conduct or process. 

The reason for using two LLMs for every message was twofold: First, in my experience, I get the best performance from LLMs when I ask them to perform a narrow task (i.e., one task is better than two). Second, because I defined neutrality before designing the architecture, I realized it would be useful to be able to distinguish how the model perceived a message from how it chose to intervene. Where messages are perceived as presenting equal errors but interventions vary, that suggests the intervenor needs to be modified, and vice-versa.

3. The **Researcher** *digs deeper upon request*. This LLM is hit only when a participant clicks "Provide factual background." It makes one web-search call and writes a short note with sources. I separated this from the general intervenor because my initial design focused on factual interventions, I wanted to use as much compute on important factual research as possible, and because I did not want every message slowed by an expensive web search query.

**The work happens in the background.** Posting a message saves it and queues a moderation job. A separate worker process picks up jobs one at a time, runs the Master Moderator and Intervenor, and posts the result. The page checks for new messages very few seconds. The design ensures LLM responses always post in order but that new users messages can be posted even while LLM responses are being generated. While this means an LLM might not always be responding to the message immediately preceding it, I found this preferable to stalling conversation for sometimes time-consuming computations.

**Everything is recorded in a ledger.** Each moderation job stores what the AI was shown, what it said, what was accepted or rejected and why, what it cost, and which prompt version and model produced it.

## 5. Specific design choices and tradeoffs. 

- **Preview versus immediate posting.** Initially, I expected to just post LLM responses onto the thread, but I decided to first preview interventions to the user. This was primarily because I hoped a preview feature might encourage users to reflect on potentially inaccurate, inflammatory, or unclear messages before they could potentially derail the discussion. However, I think it is an important signal if a participant ignores a mediator preview, so the mediator's message will post on the thread if the participant chooses to post their message anyways. I added a switch so the preview functionality can be A/B tested later as desired.
- **Web search versus testability.** Web search can be difficult to reproduce and introduces uncertainty, which makes neutrality harder to test later on. I accepted that tradeoff because informed discussion seemed like a higher priority, and I was worried the Intervenor alone would not always convey the most up-to-date or accurate facts. However, to narrow the possible outputs of the web search model, I limited it to only provide additional background on specific facts written by a participant, rather than to allow free-form user input into a web search.
- **A fixed menu of issues and acts** instead of encouraging more free-form judgment, because the same categories applied to both sides can be counted and compared. A moderator free to describe each message in its own words would be harder to audit and control.
- **No turn-taking necessary.** Nothing stops someone posting several messages in a row. That is realistic to how discussion forums work, and how the moderator handles flooding and unanswered questions is itself something to observe.
- **Private conversations.** All conversations are private to the participants. This was to encourage frank participation. For the same reason, I decided to display user names rather than personal names. Private threads mean other users of the site cannot learn from other people's conversations. This makes sense because I conceive of the website as primarily encouraging inter-personal discussion, but I may create public conversations later on, with private conversations remaining the default.

## 6. Bias evaluation

how you would generate or collect transcripts worth testing, including transcripts deliberately constructed to surface biased behavior; how you would score or flag bias (rubric-based review, statistical comparison across paired/counterfactual transcripts, LLM-as-judge, human review, or some combination); and what you would do with the result

**Operationalizing bias.**

I viewed bias as an absence of neutrality: intervening at unequal rates or in an unequal manner on  contributions that should be deemed equal. The focus of my design was factual interventions, though the setup can accommodate other interventions, such as interventions for clarity or addressing abusive language, later on.

In the case of factual errors, bias means the Master Moderator, when given two equally inaccurate factual claims, identifies one factual issue but not the other, or identifies both but assigns different severity scores to each. And for the Intervenor, bias in this context  means the Intervenor takes a different act on equally egregious factual errors, such as by asking for a source on one factual statement but declaring another incorrect or supplying what it views as the correct fact. Factual issues and severity scores identified by the Master Moderator are logged, as is the type of act the Intervenor takes in response to a specific identified error. This logging maeks quantifying bias relatively straightforward.

**Generating transcripts.**

I focused in this exercise on generating transcripts to test for factual interventions. However, the method would generally also work for other kinds of interventions, including clarifying interventions and interventions to police personal insults. 

Starting point: my fact bank.
- seeding/data/facts.json holds facts I verified. There are 20: 10 statistics (numbers), 9 law facts and 1 qualitative fact.
- Each fact has the true claim, a source note and a neutral one-line "subject". The subject tells the generator what the debate is about without giving it the claim.
- Nothing is generated for a fact until I have marked it verified and approved its false versions.

2. Planting the errors (the "seeds").
- Numbers: the true value is inflated or deflated by 10%, 50% or 200% (three severity levels). The left-helping and right-helping versions are exact mirrors. Percentages and other bounded values use values I set by hand.
- Laws, benefits and taxes: each has one false claim per side, written by me, with no severity levels.

3. Writing the debates.
- Topic: every debate has the same proposition: "Cities should limit their local police's cooperation with federal immigration enforcement."
- Two bases per fact: an LLM writes two separate 4-message debates for each fact. In the left base the left-leaning speaker states the claim, and in the right base (a mirrored rewrite) the right-leaning speaker states it.
- Structure: the last message ends with a claim marker. Participant B always states the claim, and the code assigns authors A, B, A, B.
- What the generator doesn't see: it never sees the claim or the figure, only the subject. The code inserts the claim and the fixed lead-in "This is the claim I am relying on."

4. Checks on every base.
- Format: exactly 4 messages, the marker ends the last message, and no digits or size or trend words (such as "widespread" or "surge") appear in the text.
- Pair check: the left and right bases must have the same message count, lengths within 25%, and wording different enough (similarity below 0.6).
- Stance audit: a second LLM call checks that each speaker keeps one position throughout.
- Retries: a failed base is retried up to 3 times with the reason as a hint. Rejected drafts are saved separately.

5. Building the arms. From each base, only the claim text is swapped. Each statistic gets a true arm and 3 error sizes, per side (8 debates). Each non-numeric fact gets a true arm and 1 error, per side (4 debates). The planted claim, its correction and the side are recorded in the transcript.

6. Output and use.
- Files: bases go to generated/bases/ and transcripts to generated/transcripts/, checked by the replay validator.
- Replay: I replay them as synthetic conversations through the real moderator without posting. The moderator sees only "Participant A/B", the text and the topic, never a stance.

Scale and cost. The first 12 facts produced 88 debates for about $1.80, including failed attempts. My 8 new facts produced 32 debates for about $0.55, with no regenerations needed, for 120 debates overall. I later corrected the ban and detainer facts, and those debates were regenerated through the same pipeline.


**Current design: planted errors in matched pairs.**

1. *Facts.* I verified 12 facts on one topic, sanctuary cities (20 as of 2 October).
2. *Mirrored errors.* Each statistic gets a left-helping and a right-helping false version at three sizes: about 10%, 50%
   and 200% off. Inflating multiplies and deflating divides, so the two are exact mirrors. Each law fact gets one false
   version per side, which I approve by hand.
3. *Debates.* An AI writes two matched four-message debates per fact, one per side. The final message holds the claim.
   Variants differ only in that claim, which a mechanical check enforces. That gave 88 debates.
4. *Replay.* Each debate runs through the real moderator, three times, since the model's output varies.
5. *Judge.* One AI judge compares each reply with the true fact: missed, doubted without correcting, wrong correction,
   correct correction. It is not told the side or the error size.
6. *Comparison.* Rates by side at equal error size, and matched pairs compared directly. The difference between repeat
   runs of the same debate is the yardstick for chance.

The same steps run on the web-search notes.

**Results so far.**

- The moderator replied to every false claim (192 of 192) on both sides, and to 90% of true claims.
- 76% of its replies to false claims asked for a source. Only 9% said what was wrong.
- Web-search notes flagged 97% of false claims and gave the correct correction in 35%. They corrected all 9 false law
  claims.
- Left and right versions of the same claim got different moderator outcomes in 10 of 96 pairs, split 4 to 6. For web
  search it was 17 of 44, close to the 34% that differ when the same debate is simply re-run.

There is no consistent left-right difference. But the moderator treats both sides alike largely because it gives nearly
the same reply every time. A one-sentence prompt change to make it state facts more often changed almost nothing.

**What I do with the result.** The finding points to the design, not the wording: the moderator needs a way to check
facts before replying, so the next step is to move web search earlier. The evaluation also corrected my own inputs: the
search notes disputed two of my "verified" facts, I checked the sources, and I fixed both.

**What is missing.** A deliberately biased moderator has not been run, so "no difference found" cannot yet be told apart
from "cannot detect a difference." Swapping the A/B labels has not been run. No person has reviewed the generated
debates, and the judge has not been checked against human ratings. There is one topic, and the samples are small.

## 7. How I would measure success

- **Neutrality:** left-right gaps in rate, act type and accuracy stay within the re-run yardstick, in a setup shown to
  catch a planted bias.
- **Usefulness, reported beside it:** the share of false claims correctly corrected, and the share of true claims left
  alone or confirmed. Today these are low, and that is the main thing to improve.
- **Effect on people, later:** how often authors revise after a preview, how often research is requested and by which
  side, and whether participants end with a clearer statement of where they disagree. A post-discussion survey is
  planned but not built.

## 8. Scaling

- **Many conversations.** Moderation runs in a background worker and costs about 1.5 cents per message. The current
  single database file and single worker would need to become a database server and a job queue.
- **Many topics.** The moderator and its menu do not depend on the topic. The evaluation does: each topic needs its own
  verified facts and mirrored errors. User-written positions have no left-right label, so they cannot yet join the
  analysis.
- **Larger groups.** The two-person limit is a setting, not a table constraint, and each participant has a join order.
  A group needs pairing logic and a rule for whom a note addresses. The fairness test extends from two sides to rates
  per participant.
- **Evaluation at scale.** The pipeline is automated end to end and has cost under $50 so far, so it can re-run on every
  prompt or model change.

## 9. Post-MVP list

- Positive control and label-swap runs.
- Neutrality tests for the clarity acts and the agreement note.
- More natural placement of non-numeric claims: hand-written examples, then real conversations from the site.
- A human panel to calibrate error severity, rate how appropriate interventions are, and spot-check the judge.
- Research feature: who clicks, whether notes favor the requester, a "gotcha" use of the button, source restrictions,
  background on any message.
- Whether the moderator's prompt-level self-check for even-handedness works at all.
- Bias by identity, speaking order, message length and writing style.
