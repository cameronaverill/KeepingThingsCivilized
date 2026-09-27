"""Step 20a revision brief (docs/step20a_revision_brief.md) item 1: `MasterIssue.time_sensitive` is renamed to
`needs_verification` and broadened to cover two reasons (recency, and fabrication/inaccuracy risk on a specific
checkable detail) under one plain boolean -- the reason is never surfaced, so the field stays undifferentiated.

Written from the revision brief directly (not from any coding agent's diff). This is a NEW file, not an edit to
tests/moderation/test_step20a_schemas.py -- that file is being renamed/rewritten in place by a separate coding agent
(same file-ownership group as moderation/schemas.py and moderation/taxonomy.py, per the brief's "Coordination and
out-of-scope reminders"), so this file must not depend on its content surviving in any particular shape, only on the
live `moderation.schemas` module and `step3_testkit`'s builders (also owned by that group, but usable read-only here
exactly as the original file used them).

Expected to fail until the rename lands in this working tree: `needs_verification` doesn't exist yet, so payloads
built with it are rejected as unknown/extra depending on model config, and `.needs_verification` attribute access
raises AttributeError. That is the coding agent's change landing separately in the shared tree -- do not chase it,
just note it.
"""
import inspect

import pytest
from pydantic import ValidationError

from step3_testkit import act_dict, issue_dict, master_output


def model(name):
    from moderation import schemas

    return getattr(schemas, name)


# --- time_sensitive no longer exists anywhere in schemas.py, needs_verification does -------------------------------

def test_schemas_module_source_has_no_time_sensitive_reference_anywhere():
    """The brief is explicit: time_sensitive should no longer exist as a field name anywhere in schemas.py."""
    from moderation import schemas

    source = inspect.getsource(schemas)
    assert "time_sensitive" not in source


def test_master_issue_has_needs_verification_field_and_not_time_sensitive():
    MasterIssue = model("MasterIssue")
    assert "needs_verification" in MasterIssue.model_fields
    assert "time_sensitive" not in MasterIssue.model_fields


# --- needs_verification is required on MasterIssue ------------------------------------------------------------------

def test_master_issue_without_needs_verification_is_rejected():
    """issue_dict() (step3_testkit) is expected to set needs_verification once that group's rename pass lands;
    popping it here keeps this test meaningful regardless of the builder's current state."""
    payload = issue_dict(1, 1, "q", issue_type="possible_factual_error")
    payload.pop("needs_verification", None)
    with pytest.raises(ValidationError):
        model("MasterIssue").model_validate(payload)


def test_master_issue_with_only_the_old_field_name_present_is_still_rejected():
    """A payload carrying the old `time_sensitive` key but missing `needs_verification` must still fail -- the old
    name is not tolerated as a silent alias for the new one."""
    payload = issue_dict(1, 1, "q", issue_type="possible_factual_error")
    payload.pop("needs_verification", None)
    payload["time_sensitive"] = True
    with pytest.raises(ValidationError):
        model("MasterIssue").model_validate(payload)


def test_master_issue_needs_verification_true_and_false_both_validate():
    MasterIssue = model("MasterIssue")
    true_issue = MasterIssue.model_validate(
        {**issue_dict(1, 1, "q", issue_type="possible_factual_error"), "needs_verification": True}
    )
    false_issue = MasterIssue.model_validate(
        {**issue_dict(2, 2, "q", issue_type="unsupported_claim"), "needs_verification": False}
    )
    assert true_issue.needs_verification is True
    assert false_issue.needs_verification is False


def test_master_issue_needs_verification_is_not_coupled_to_issue_type_by_a_validator():
    """Same as the original field's contract: meaningless (always false) for issue types other than
    possible_factual_error/unsupported_claim, but still required on every issue, with no validator coupling it to
    issue_type -- true must still validate for an unrelated issue type."""
    MasterIssue = model("MasterIssue")
    issue = MasterIssue.model_validate({**issue_dict(1, 1, "q", issue_type="fallacy"), "needs_verification": True})
    assert issue.needs_verification is True


@pytest.mark.parametrize("bad", [[], {}, None, "banana"])
def test_master_issue_rejects_a_non_boolean_needs_verification(bad):
    with pytest.raises(ValidationError):
        model("MasterIssue").model_validate(
            {**issue_dict(1, 1, "q", issue_type="possible_factual_error"), "needs_verification": bad}
        )


# --- round-trip through MasterOutput, both values ---------------------------------------------------------------

def test_master_issue_needs_verification_round_trips_through_master_output_json():
    MasterOutput = model("MasterOutput")
    true_payload = {**issue_dict(1, 1, "current stat", issue_type="possible_factual_error"), "needs_verification": True}
    false_payload = {**issue_dict(2, 2, "1648 date", issue_type="unsupported_claim"), "needs_verification": False}
    out = master_output([true_payload, false_payload])
    assert out.issues[0].needs_verification is True
    assert out.issues[1].needs_verification is False
    assert MasterOutput.model_validate_json(out.model_dump_json()) == out


def test_needs_verification_true_validates_identically_for_a_recency_style_and_a_fabrication_style_claim():
    """The revision's whole point: the schema has no concept of *why* the flag is true, only that it is. A
    fabrication-style claim ("a 2024 Harvard study proved X reduces Y by 40%") sets needs_verification exactly like
    a recency-style one ("the unemployment rate fell last month") -- same field, no sub-reason, no branching, and
    both issues end up with the identical set of model fields."""
    MasterIssue = model("MasterIssue")
    recency = MasterIssue.model_validate(
        {
            **issue_dict(1, 1, "the national unemployment rate fell last month", issue_type="possible_factual_error"),
            "needs_verification": True,
        }
    )
    fabrication = MasterIssue.model_validate(
        {
            **issue_dict(
                2,
                2,
                "a 2024 Harvard study proved that remote work reduces productivity by 40%",
                issue_type="possible_factual_error",
            ),
            "needs_verification": True,
        }
    )
    assert recency.needs_verification is True
    assert fabrication.needs_verification is True
    assert set(type(recency).model_fields) == set(type(fabrication).model_fields)


# --- ActType baseline coverage still holds after the rename (independent smoke check) -------------------------------

def test_act_type_literal_still_includes_offer_research_in_taxonomy_order():
    from moderation import taxonomy

    ActType = model("ActType")
    assert ActType.__args__ == taxonomy.ACT_TYPES
    assert "offer_research" in ActType.__args__


def test_act_still_valid_for_offer_research():
    Act = model("Act")
    act = Act.model_validate(act_dict(type="offer_research"))
    assert act.type == "offer_research"


@pytest.mark.parametrize("bad", ["", "bogus", "OFFER_RESEARCH", "Offer_Research", "offer-research", " offer_research"])
def test_act_still_rejects_an_unknown_act_type_string_including_near_misses(bad):
    with pytest.raises(ValidationError):
        model("Act").model_validate({**act_dict(), "type": bad})
