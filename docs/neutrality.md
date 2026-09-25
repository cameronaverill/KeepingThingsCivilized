# Neutrality Criteria

The user's definition of neutrality for the AI moderator, as they gave it during the design sessions (Sep 2026). Sections 1–5 are in the user's own words, lightly formatted. Section 6 notes how `docs/plan.md` puts them into practice.

## 1. Core definition

> A MODERATING SYSTEM IS UNBIASED WHEN, GIVEN TWO EQUAL CONTRIBUTIONS, IT INTERVENES AT AN EQUAL FREQUENCY, IN AN EQUAL MANNER, AND WITH EQUAL INTENDED EFFECT.

Also stated as: "We intervene alike on statements in an alike manner and frequency."

The AI must be neutral and must not "put the thumb on the scale" for one side or the other.

## 2. Equal substance

**Factors that make contributions equal.** Equal:
1. factual support
2. clarity/prose/style
3. logic/absence of fallacies
4. procedural conduct

**Factors that should NOT affect equal treatment** (holding the other features of contributions constant):
1. political position
2. identity of speaker
3. positional ordering

## 3. Equal frequency, manner and intended effect

These are essentially conditional probabilities: given equivalent user contributions, does the system intervene similarly, and is the intervention intended to have a similar effect on users?

They should all be tested at the level of the **type and content** of intervention. The model might intervene to:
- supply facts
- request evidence
- identify errors
- ask for clarification
- formulate a clearer argument
- soften tone
- identify agreement/disagreement
- ask a participant to respond
- regulate procedures, like the number of contributions

## 4. Bias categories and how to test them

**Most important bias categories:**
- positional
- identity
- prose/style
- political affiliation

**For each category:**
1. Observe transcripts for the rates at which intervention triggers occur (scoring identifies these). For example:
   - a user makes an unsupported factual claim
   - a user makes an unclear statement
   - a user does any of the other things that might prompt intervention
2. Then compare the conditional rates. For example: where a user makes an unsupported factual claim, how often did the moderator intervene for liberal vs. conservative viewpoints?

**Stats to count within these conditional probabilities:**
- number of interventions
- nature of intervention: harsh or polite
- length of intervention
- substance of intervention

Also: whether one person is corrected very harshly and another very gently should be knowable without the evaluation pipeline having to score everything.

## 5. Priorities and scope

- **First focus:** whether changing the political affiliation or position associated with the speaker's statement changes how the moderator treats the contribution. This is the most concerning form of bias.
- **Keep testable for later:** speaker identity characteristics (race, gender, etc.), positional ordering, and content features such as message length.
- **Questions to answer eventually:**
  - Did the moderator intervene more for one political position?
  - Did it use different types of intervention for otherwise equivalent contributions?
- **Quality matters too:** the moderator shouldn't be equally bad for everyone. Eventually we want to know:
  - whether its factual interventions are accurate
  - whether it identified arguments that really were unclear
  - long-term, whether it made the overall discussion more productive

**Later refinements** (agreed with the user in later sessions):
- **Two stages:** measure separately whether the moderator *notices* a problem at different rates by side, and whether, *given a noticed problem*, it *intervenes* at different rates.
- **Ground truth:** it is phrase-level. Each phrase gets an issue dimension (e.g. factual accuracy, abusiveness) and an intensity from 0 to 4, scored by an LLM judge panel that human raters calibrate.

## 6. How the plan puts this into practice

A summary; details are in `docs/plan.md`, sections 1, 5, 6 and 9.

- **Equal contributions → equal treatment** is tested as equalized odds: P(act type T | trigger C, side X) ≈ P(act type T | trigger C, side Y). Triggers are phrase-level findings, compared at equal intensity.
- **Frequency:** detection, action, false-positive and unwarranted-intervention rates by side.
- **Manner:** act type, self-reported tone, length and other features computed in code, per act, attributed to the participant each act is about.
- **Intended effect:** the Intervenor's self-tags now, and a rater's directional score on a sample later.
- **Holding substance constant:** the paired test track flips only the political direction of otherwise identical transcripts, with planted problems of known intensity.
- **Keeping protected factors out of the moderator's view:** the moderator never sees usernames, identity or political lean. It sees only random "Participant A/B" labels, and ordering is logged so positional bias can be tested.
- **Style:** clarity may legitimately change how the moderator responds. Register, dialect or formality at equal clarity must not.
