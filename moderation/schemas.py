"""Pydantic models for the two agents' outputs (docs/plan.md section 6).

These are sent to the API as the structured-output schema (`Messages.parse(output_format=...)`), so they use only what
that feature supports: plain objects, lists, strings, integers, floats, booleans, `Literal` enums and `X | None`. No
recursion, no default values, and NO numeric or length constraints in the JSON schema (the SDK strips them anyway);
ranges are checked by validators here, which run locally when the SDK parses the reply. Every field is required.

Labels (`addressee`, `subject`) are plain strings here; the pipeline (step 5) checks them against the conversation's
labels, and checks that ids refer to real messages and issues.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from moderation import taxonomy

# These Literal types are written out so that the JSON schema has plain enums. tests/moderation checks that they match
# the taxonomy tuples exactly, so the two cannot drift apart unnoticed.
IssueType = Literal[
    "unsupported_claim",
    "possible_factual_error",
    "unclear_statement",
    "fallacy",
    "strawman",
    "abusive_language",
    "repetition",
    "process_violation",
]
ActType = Literal[
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
]
Decision = Literal["intervene", "no_intervention"]
Tone = Literal["gentle", "neutral", "firm"]
Disposition = Literal["acted", "declined"]
DisagreementKind = Literal["factual", "normative"]

assert IssueType.__args__ == taxonomy.ISSUE_TYPES
assert ActType.__args__ == taxonomy.ACT_TYPES
assert Decision.__args__ == taxonomy.DECISIONS
assert Tone.__args__ == taxonomy.TONES
assert Disposition.__args__ == taxonomy.DISPOSITIONS
assert DisagreementKind.__args__ == taxonomy.DISAGREEMENT_KINDS


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Master Moderator ----------------------------------------------------------------------------------------------

class MasterIssue(_Strict):
    id: str  # short id chosen by the model, unique within the output, for example "i1"
    message_id: int  # the id of the message the issue is in, as shown in the transcript
    issue_type: IssueType
    quote: str  # the exact phrase (or the whole message) that has the problem; code finds it in the message
    explanation: str  # one or two sentences saying what the problem is, in neutral wording
    confidence: float  # 0.0 to 1.0: how sure the Master is that this is a real issue
    intensity: int | None  # 0 to 4 on the rubric for the issue type's dimension; null for issue types without one

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, value):
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @field_validator("intensity")
    @classmethod
    def _intensity_in_range(cls, value):
        low, high = taxonomy.INTENSITY_RANGE
        if value is not None and not low <= value <= high:
            raise ValueError(f"intensity must be between {low} and {high}")
        return value


class Disagreement(_Strict):
    summary: str
    kind: DisagreementKind


class DiscussionMap(_Strict):
    agreements: list[str]
    disagreements: list[Disagreement]


class MasterOutput(_Strict):
    issues: list[MasterIssue]
    discussion_map: DiscussionMap


# --- Intervenor ----------------------------------------------------------------------------------------------------

class IssueDisposition(_Strict):
    issue_id: str
    disposition: Disposition
    reason: str


class Act(_Strict):
    type: ActType
    addressee: str  # "Participant A", "Participant B" or "all"
    subject: str  # the participant the act is about, or "both" or "none"
    source_issue_ids: list[str]
    source_message_ids: list[int]
    tone: Tone
    text: str  # the words that will be posted for this act


class IntervenorOutput(_Strict):
    decision: Decision
    rationale: str
    issue_dispositions: list[IssueDisposition]
    acts: list[Act]
