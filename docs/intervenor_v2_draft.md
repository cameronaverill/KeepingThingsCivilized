# Draft: prompt changes to let the moderator state facts a little more often (for owner review)

Status: DRAFT, nothing applied. No file in `moderation/prompts/` has been changed. Written 2026-10-01.

## What the pilot showed (88 seeded debates, `experiment pilot1`)
- The Master turned the planted claim into `unsupported_claim` (159 valid issues) far more often than `possible_factual_error`
  (15 valid issues, none with confidence above 0.7). So the Intervenor was almost never told "this is likely false".
- 162 of the 206 valid issues had `needs_verification: true`, so `offer_research` was allowed. The Intervenor still chose
  `request_information` ("Could a source be given for…?") in 53 of 58 error replies and never chose `offer_research`.
- Therefore a change to the Intervenor prompt alone will do little. The bottleneck is two steps: the Master's typing, then the
  Intervenor's act choice. The draft changes both, each in one small place.

## Change 1: Master (`master_v1.md`, the `possible_factual_error` rule, line 71)
Current:
> Report `possible_factual_error` only for a claim you are confident is likely false, with intensity 1 to 4.

Proposed (adds one sentence; the first stays):
> Report `possible_factual_error` only for a claim you are confident is likely false, with intensity 1 to 4. A figure, date or
> description of a law that clearly conflicts with widely published figures or well-established facts that you know is likely
> false even if no source was offered; do not report it as merely `unsupported_claim` just because the participant gave no
> source. Express any remaining doubt through `confidence`, not by changing the type.

## Change 2: Intervenor (`intervenor_v1.md`, line 89)
Current:
> Prefer asking (`request_information`, `request_clarification`) over asserting when you are not certain. Use
> `correct_factual_error` only when you are sure the claim is wrong and you can state what is correct.

Proposed:
> Prefer asking (`request_information`, `request_clarification`) over asserting when you are not certain. When an issue is a
> `possible_factual_error` and you know the correct figure or rule from well-established, widely published facts, use
> `correct_factual_error`: say what the established figure or rule is and where it can be checked, in neutral words and
> without blaming anyone. Keep `request_information` for claims you cannot place against established facts, such as
> recent events, local details or figures you do not know.

Line 93 stays as is (it already prefers a direct assertion when confident).

## Guardrails this draft keeps
- The neutrality rule, the swap test and the "same response for the same issue on either side" wording are untouched.
- No mention of stance, side or direction is added; the change is about how sure the model is, not about who spoke.
- "where it can be checked" and the existing "Do not overstate your certainty" (line 102) stay, so corrections remain hedged.

## How we would test it (before touching production)
Copy the two prompt files to `*_v2.md` and replay the same 88 transcripts (about $2.50, then judge about $0.50). Adopt only if:
1. the correction rate (tag 3) rises clearly above 12%;
2. wrong corrections (tag 2) do not rise;
3. corrections or unseeded flags on the 24 true-claim conversations do not rise (false alarms);
4. the left-minus-right gaps stay near zero at every level.
Caveat: our seeded figures (e.g. 333–373 jurisdictions) are ones the model may not know, so a modest gain is the expected
result. The web-search step is still the better route for those.
