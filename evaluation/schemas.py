"""Pydantic models for an LLM rater's output (docs/plan.md section 9, docs/step14_brief.md).

These are sent to the API as the structured-output schema (`Messages.parse(output_format=...)`), so they follow how
`moderation/schemas.py` builds its models: strict (no extra keys), every field required, no default values, plain
`Literal` enums and `X | None`. Unlike the Master's schema they carry NO range validators on purpose: an out-of-range
intensity, a missing reason or a quote that cannot be found spoils only that one finding, which the runner
(`evaluation.llm_rater`) drops and reports; a validator here would instead fail the whole output and cost a retry.

`detail` is a free-form dict for Pydantic (any keys validate), but structured-output decoding needs a fixed object shape,
so its JSON schema is declared here: the claim restated and the correct fact (empty strings when they do not apply).
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from moderation import taxonomy

NotScorableReason = Literal["unverifiable", "contested", "needs_context"]
assert NotScorableReason.__args__ == taxonomy.NOT_SCORABLE_REASONS

_DETAIL_JSON_SCHEMA = {
    "properties": {"claim": {"type": "string"}, "correct_fact": {"type": "string"}},
    "required": ["claim", "correct_fact"],
    "additionalProperties": False,
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RaterFinding(_Strict):
    local_id: str  # short id chosen by the rater, unique within the output, for example "f1"
    dimension: str  # one of the dimensions the rater was asked to rate
    quote: str  # the phrase, verbatim as written in the message; code finds it and computes the offsets
    intensity: int | None  # 0 to 4 on the dimension's rubric; null only together with a not_scorable_reason
    not_scorable_reason: NotScorableReason | None  # set exactly when intensity is null
    confidence: float | None  # 0.0 to 1.0
    detail: dict = Field(json_schema_extra=_DETAIL_JSON_SCHEMA)  # for example the claim restated and the correct fact


class RaterOutput(_Strict):
    findings: list[RaterFinding]
    no_issues_in: list[str]  # dimensions the rater looked at and found nothing
