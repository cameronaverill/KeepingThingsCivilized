"""Step 20b item 3 (docs/step20b_brief.md): `moderation/schemas.py` gains `ResearchNote`, the structured-output
schema for `moderation/research.py::run_research`'s `call_with_web_search(..., output_schema=ResearchNote, ...)`
call. Same rules as `MasterIssue`/`MasterOutput`: `_Strict` (extra="forbid"), no default values (the API's
structured-output schema forbids them), every field required, `confidence` a float checked into [0.0, 1.0] the same
way `MasterIssue.confidence` is (docs/plan.md section 6). The brief is explicit that the model is NOT asked which
sources it used (that is item 4, computed in code from the real API response) -- so `ResearchNote` carries only
`text` and `confidence`, nothing about sources.

New tests only; `moderation/schemas.py` already declares `ResearchNote` as this file is written (concurrent Group B
work), so these are expected to pass, not merely to fail cleanly -- but if a mismatch appears (field renamed, a
default added, a sources field invented), that is exactly what this file exists to catch, so treat a failure here as
a real signal, not noise.
"""
import pytest
from pydantic import ValidationError


def model(name):
    from moderation import schemas

    return getattr(schemas, name)


def payload(**overrides):
    values = {"text": "Independent sources broadly agree the figure is lower than stated.", "confidence": 0.7}
    values.update(overrides)
    return values


# --- required fields, no defaults -------------------------------------------------------------------------------

def test_research_note_validates_with_both_required_fields():
    note = model("ResearchNote").model_validate(payload())
    assert note.text == payload()["text"]
    assert note.confidence == 0.7


def test_research_note_without_text_is_rejected():
    data = payload()
    data.pop("text")
    with pytest.raises(ValidationError):
        model("ResearchNote").model_validate(data)


def test_research_note_without_confidence_is_rejected():
    data = payload()
    data.pop("confidence")
    with pytest.raises(ValidationError):
        model("ResearchNote").model_validate(data)


def test_research_note_has_no_default_values_for_either_field():
    """The API's structured-output schema forbids defaults (moderation/schemas.py's module docstring); an empty
    payload must be rejected for BOTH fields at once, not silently filled in."""
    with pytest.raises(ValidationError) as exc_info:
        model("ResearchNote").model_validate({})
    missing = {error["loc"][0] for error in exc_info.value.errors() if error["type"] == "missing"}
    assert missing == {"text", "confidence"}


def test_research_note_rejects_an_unknown_extra_field():
    """_Strict (extra='forbid'): the model must not be able to smuggle a sources/citations field of its own past the
    schema (item 3's rule that the model is never asked which sources it used)."""
    with pytest.raises(ValidationError):
        model("ResearchNote").model_validate({**payload(), "sources": ["https://example.com"]})


# --- confidence range, same rule as MasterIssue.confidence ------------------------------------------------------

@pytest.mark.parametrize("value", [0.0, 1.0, 0.5, 0.0001, 0.9999])
def test_research_note_accepts_confidence_at_and_inside_the_boundaries(value):
    note = model("ResearchNote").model_validate(payload(confidence=value))
    assert note.confidence == value


@pytest.mark.parametrize("value", [-0.0001, -1.0, 1.0001, 2.0, -5, 100])
def test_research_note_rejects_confidence_outside_0_to_1(value):
    with pytest.raises(ValidationError):
        model("ResearchNote").model_validate(payload(confidence=value))


@pytest.mark.parametrize("bad", [[], {}, None, "high"])
def test_research_note_rejects_a_non_numeric_confidence(bad):
    with pytest.raises(ValidationError):
        model("ResearchNote").model_validate(payload(confidence=bad))


def test_research_note_text_must_be_a_string():
    with pytest.raises(ValidationError):
        model("ResearchNote").model_validate(payload(text=12345))


# --- shape: _Strict, and independent of the other two agent schemas ----------------------------------------------

def test_research_note_is_a_strict_model_like_the_other_agent_schemas():
    from moderation.schemas import MasterIssue, ResearchNote, _Strict

    assert issubclass(ResearchNote, _Strict)
    assert ResearchNote.model_config.get("extra") == "forbid"
    assert issubclass(MasterIssue, _Strict)  # same family, for contrast


def test_research_note_round_trips_through_json():
    from moderation.schemas import ResearchNote

    note = ResearchNote.model_validate(payload())
    assert ResearchNote.model_validate_json(note.model_dump_json()) == note


def test_research_note_json_schema_has_no_default_for_either_field():
    """A belt-and-suspenders check on the actual JSON schema sent to the API (not just pydantic's validation
    behaviour): neither field may carry a "default" key, since a structured-output default the model never
    fills in would silently defeat 'no defaults'."""
    from moderation.schemas import ResearchNote

    schema = ResearchNote.model_json_schema()
    properties = schema.get("properties", {})
    assert set(properties) == {"text", "confidence"}
    for name, prop in properties.items():
        assert "default" not in prop, f"{name} must not have a JSON-schema default"
    assert schema.get("required") is None or set(schema["required"]) == {"text", "confidence"}
