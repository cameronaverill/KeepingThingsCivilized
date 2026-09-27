"""The single source of truth for every category the moderator agents use (docs/plan.md section 5).

Schemas (`moderation/schemas.py`), prompts (`moderation/prompts/*.md`) and tests all read from this file, and a test
fails if a prompt leaves out any value. To add a value: add it here (with a definition), then update the prompt files.

`DEFINITIONS` is one flat mapping from every value (issue type, act type, decision, tone, disposition, disagreement kind,
"not scorable" reason, dimension) to a one-sentence definition that includes the boundary rule against its nearest
neighbour. The names do not collide across categories.
"""

ISSUE_TYPES = (
    "unsupported_claim",
    "possible_factual_error",
    "unclear_statement",
    "fallacy",
    "strawman",
    "abusive_language",
    "repetition",
    "process_violation",
)

ACT_TYPES = (
    "provide_information",
    "correct_factual_error",
    "improve_argumentation",
    "clarify_argument",
    "restate_positions",
    "identify_agreement_disagreement",
    "request_information",
    "request_clarification",
    "enforce_conduct",
    "enforce_process",
    "offer_research",
)

DECISIONS = ("intervene", "no_intervention")

TONES = ("gentle", "neutral", "firm")

# Why a rater (human or LLM) may decline to give an intensity. An outcome, not a score.
NOT_SCORABLE_REASONS = ("unverifiable", "contested", "needs_context")

# What the Intervenor did with each valid issue.
DISPOSITIONS = ("acted", "declined")

# The kind of a disagreement in the discussion map.
DISAGREEMENT_KINDS = ("factual", "normative")

# Intensity scale for the issue types that have a dimension: 0 (none) to 4 (most severe), inclusive.
INTENSITY_RANGE = (0, 4)

# Issue types about the conversation as a whole, so they may be raised on an older message (the only-new-issues rule).
CROSS_MESSAGE_ISSUE_TYPES = {"repetition", "strawman", "process_violation"}

# A dimension has an intensity rubric (rubrics/<dimension>_v1.md). `coverage` says what raters record:
# all_claims = every checkable claim including accurate ones (intensity 0); flagged_only = only phrases with intensity >= 1.
DIMENSIONS = {
    "factual_accuracy": {"issue_type": "possible_factual_error", "coverage": "all_claims"},
    "abusiveness": {"issue_type": "abusive_language", "coverage": "flagged_only"},
}

DEFINITIONS = {
    # --- Issue types (Master Moderator) ---
    "unsupported_claim": (
        "A factual assertion that is given with no support and that may well be true; it differs from "
        "possible_factual_error, which is an assertion that is likely false."
    ),
    "possible_factual_error": (
        "A checkable factual assertion that is likely false; if credible sources genuinely disagree, or it rests on a "
        "value judgment, it is not an error (use no issue, or unsupported_claim if it is simply unsupported)."
    ),
    "unclear_statement": (
        "A statement whose meaning is too vague or ambiguous for the other participant to respond to; a clear "
        "statement you disagree with is not unclear."
    ),
    "fallacy": (
        "Reasoning in which the conclusion does not follow from what is offered (for example a non sequitur or a "
        "false dilemma); a conclusion you disagree with is not a fallacy."
    ),
    "strawman": (
        "Attacking a position that the other participant did not actually state, or a distorted version of it; it "
        "can only be judged by comparing messages."
    ),
    "abusive_language": (
        "Language that attacks or demeans a person rather than addressing their argument, from mild rudeness to "
        "insults; strong disagreement with an argument, phrased civilly, is not abusive."
    ),
    "repetition": (
        "Restating a point already made earlier in the conversation, without adding anything new; it can only be "
        "judged across messages."
    ),
    "process_violation": (
        "Breaking the conversation's procedures, such as flooding it with messages, dominating the turn-taking, or "
        "ignoring a direct question; it concerns how the participant takes part, not what they say."
    ),
    # --- Act types (Intervenor) ---
    "provide_information": (
        "Supply a relevant, checkable fact or context to the discussion without asserting that anyone made an error; "
        "use correct_factual_error when a specific claim is wrong."
    ),
    "correct_factual_error": (
        "Identify a specific factual claim that is wrong and state what is correct, briefly and without blaming the "
        "speaker."
    ),
    "improve_argumentation": (
        "Point out a reasoning problem (a fallacy, a gap or a strawman) in a participant's argument so they can "
        "repair it; it does not restate the argument."
    ),
    "clarify_argument": (
        "Restate a participant's argument more clearly without changing its content, so the other participant can "
        "respond to it."
    ),
    "restate_positions": (
        "Summarize where each participant currently stands, with equal care for each side."
    ),
    "identify_agreement_disagreement": (
        "Point out what the participants agree on and where they disagree, and whether the disagreement is about "
        "facts or about values."
    ),
    "request_information": (
        "Ask a participant for a source, evidence or data behind a claim; it asks and does not decide the claim is false."
    ),
    "request_clarification": (
        "Ask a participant to say what they mean when a statement is too vague or ambiguous to respond to."
    ),
    "enforce_conduct": (
        "Ask a participant to stop abusive or demeaning language and return to the argument."
    ),
    "enforce_process": (
        "Address how the conversation runs, such as flooding, turn-taking, or prompting a participant to respond to a "
        "question that was put to them."
    ),
    "offer_research": (
        "Offer the participants an independent factual check on a claim the Master could not confidently vouch for "
        "itself; it takes no position on the claim itself and does not perform any check — it only offers one, unlike "
        "provide_information or correct_factual_error, which state something as fact."
    ),
    # --- Decisions ---
    "intervene": "Post a moderator message consisting of one to three acts.",
    "no_intervention": "Post nothing, because the discussion is proceeding well enough without the moderator.",
    # --- Tones (self-reported for each act) ---
    "gentle": "Warm and tentative in wording, framed as a suggestion or a question.",
    "neutral": "Plain and matter-of-fact, neither warm nor stern.",
    "firm": "Direct and unambiguous about what is expected, while staying courteous.",
    # --- Dispositions ---
    "acted": "The Intervenor addressed the issue in one of its acts.",
    "declined": "The Intervenor chose not to act on the issue, and says why.",
    # --- Disagreement kinds ---
    "factual": "A disagreement that could in principle be settled by evidence.",
    "normative": "A disagreement about values or priorities that evidence alone cannot settle.",
    # --- Reasons a claim cannot be scored (an outcome, not a score) ---
    "unverifiable": "There is no reliable evidence either way, so the claim can be neither confirmed nor refuted.",
    "contested": (
        "Credible sources genuinely disagree, or the claim rests on a value judgment; it must not be forced onto the "
        "0 to 4 scale."
    ),
    "needs_context": "Whether the claim is right depends on context that the conversation does not give.",
    # --- Dimensions ---
    "factual_accuracy": "How wrong a checkable factual claim is, on the 0 to 4 rubric in rubrics/factual_accuracy_v1.md.",
    "abusiveness": "How abusive a phrase is, on the 0 to 4 rubric in rubrics/abusiveness_v1.md.",
}


def dimension_for(issue_type):
    """The dimension whose rubric scores this issue type, or None if the issue type has no intensity."""
    for dimension, info in DIMENSIONS.items():
        if info["issue_type"] == issue_type:
            return dimension
    return None
