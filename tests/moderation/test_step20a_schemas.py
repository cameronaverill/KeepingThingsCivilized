"""Step 20a item 2 (docs/step20a_brief.md): `moderation/schemas.py` gains a required `MasterIssue.time_sensitive`
bool, and `"offer_research"` is added to the `ActType` Literal.

New tests only; existing fixtures (test_step3_schemas.py, step3_testkit.py) are the building agent's mechanical
fixture pass (brief item 2, steps 1-3) and are not touched here. This file builds its own payloads, starting from
step3_testkit's tolerant builders (issue_dict/act_dict/master_output) and adding time_sensitive explicitly, so it
does not depend on that pass having landed anywhere except inside this file.

Expected to fail (ValidationError not raised where expected, or KeyError-shaped AssertionErrors) until
moderation/schemas.py actually declares the field and the Literal member; that is the coding agent's change landing
in the same working tree.
"""
import pytest
from pydantic import ValidationError
from step3_testkit import act_dict, issue_dict, master_output


def model(name):
    from moderation import schemas

    return getattr(schemas, name)


# --- time_sensitive is required on MasterIssue ------------------------------------------------------------------

def test_master_issue_without_time_sensitive_is_rejected():
    """issue_dict() (step3_testkit) does not set time_sensitive; until the builder's fixture pass adds it there this
    is also an accurate smoke test of the mechanical pass itself, but the point of this test is the schema: an
    otherwise-complete MasterIssue payload lacking time_sensitive must fail validation, because the API's
    structured-output schema forbids field defaults."""
    payload = issue_dict(1, 1, "q", issue_type="possible_factual_error")
    payload.pop("time_sensitive", None)  # tolerate the fixture pass having already added it upstream
    with pytest.raises(ValidationError):
        model("MasterIssue").model_validate(payload)


def test_master_issue_time_sensitive_true_and_false_both_validate():
    from moderation.schemas import MasterIssue

    true_issue = MasterIssue.model_validate({**issue_dict(1, 1, "q", issue_type="possible_factual_error"), "time_sensitive": True})
    false_issue = MasterIssue.model_validate({**issue_dict(2, 2, "q", issue_type="unsupported_claim"), "time_sensitive": False})
    assert true_issue.time_sensitive is True
    assert false_issue.time_sensitive is False


def test_master_issue_time_sensitive_is_not_coupled_to_issue_type_by_a_validator():
    """The brief is explicit: 'meaningless (always false) for issue types other than possible_factual_error /
    unsupported_claim, but still required on every issue' and 'no validator needed (it's a plain bool)'. So
    time_sensitive=True must still validate for an unrelated issue type; nothing may silently reject or coerce it."""
    from moderation.schemas import MasterIssue

    issue = MasterIssue.model_validate({**issue_dict(1, 1, "q", issue_type="fallacy"), "time_sensitive": True})
    assert issue.time_sensitive is True


@pytest.mark.parametrize("bad", [[], {}, None, "banana"])
def test_master_issue_rejects_a_non_boolean_time_sensitive(bad):
    """Pydantic v2's default lax mode coerces some strings ("yes"/"true"/"no"/"false"/"1"/"0"/etc., case-insensitive)
    to bool for a plain `bool` field; the brief explicitly said no validator is needed (no StrictBool), so those
    coercible strings are not tested here as rejections. [] , {}, None and a non-coercible string are not coercible
    under lax mode and must still be rejected."""
    with pytest.raises(ValidationError):
        model("MasterIssue").model_validate({**issue_dict(1, 1, "q", issue_type="possible_factual_error"), "time_sensitive": bad})


# --- round-trip through MasterOutput, both values -------------------------------------------------------------------

def test_master_issue_time_sensitive_round_trips_through_master_output_json():
    from moderation.schemas import MasterOutput

    true_payload = {**issue_dict(1, 1, "current stat", issue_type="possible_factual_error"), "time_sensitive": True}
    false_payload = {**issue_dict(2, 2, "1648 date", issue_type="unsupported_claim"), "time_sensitive": False}
    out = master_output([true_payload, false_payload])
    assert out.issues[0].time_sensitive is True
    assert out.issues[1].time_sensitive is False
    assert MasterOutput.model_validate_json(out.model_dump_json()) == out


# --- ActType gains offer_research, in order, without widening what else is accepted ----------------------------------

def test_act_type_literal_includes_offer_research_in_taxonomy_order():
    from moderation import taxonomy
    from moderation.schemas import ActType

    assert ActType.__args__ == taxonomy.ACT_TYPES
    assert "offer_research" in ActType.__args__


def test_act_valid_for_offer_research():
    from moderation.schemas import Act

    act = Act.model_validate(act_dict(type="offer_research"))
    assert act.type == "offer_research"


@pytest.mark.parametrize("bad", ["", "bogus", "OFFER_RESEARCH", "Offer_Research", "offer-research", " offer_research", "offer_research "])
def test_act_still_rejects_an_unknown_act_type_string_including_near_misses_of_the_new_one(bad):
    with pytest.raises(ValidationError):
        model("Act").model_validate({**act_dict(), "type": bad})


@pytest.mark.parametrize(
    "act_type",
    [
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
    ],
)
def test_the_original_ten_act_types_still_validate_unaffected_by_the_new_eleventh(act_type):
    from moderation.schemas import Act

    assert Act.model_validate(act_dict(type=act_type)).type == act_type
