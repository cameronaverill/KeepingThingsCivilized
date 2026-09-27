"""evaluation/schemas.py: `RaterFinding` and `RaterOutput`, and their fit with the structured-output API (docs/step14_brief.md)."""
import llmr_kit as kit
import pytest
from pydantic import ValidationError

FINDING_FIELDS = ["local_id", "dimension", "quote", "intensity", "not_scorable_reason", "confidence", "detail"]
OUTPUT_FIELDS = ["findings", "no_issues_in"]
UNSUPPORTED_KEYWORDS = {"oneOf", "not", "patternProperties", "$dynamicRef", "if", "then", "else", "minimum", "maximum", "minLength", "maxLength"}


def schemas():
    from evaluation import schemas as module

    return module


def valid_finding(**changes):
    return {**kit.finding("f1", "factual_accuracy", "a phrase", 2), **changes}


def output_dict(*findings, no_issues=()):
    return kit.answer(*findings, no_issues=no_issues)


def all_nodes(node):
    """Every dict inside a JSON schema, at any depth."""
    found = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(all_nodes(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(all_nodes(item))
    return found


class TestFields:
    def test_a_finding_has_exactly_the_contract_fields(self):
        assert list(schemas().RaterFinding.model_fields) == FINDING_FIELDS

    def test_an_output_has_exactly_the_contract_fields(self):
        assert list(schemas().RaterOutput.model_fields) == OUTPUT_FIELDS

    def test_a_valid_output_parses_and_keeps_every_value(self):
        parsed = schemas().RaterOutput.model_validate(output_dict(valid_finding(confidence=0.25), no_issues=["abusiveness"]))
        (only,) = parsed.findings
        assert (only.local_id, only.dimension, only.quote, only.intensity, only.not_scorable_reason, only.confidence) == (
            "f1", "factual_accuracy", "a phrase", 2, None, 0.25,
        )
        assert (only.detail, parsed.no_issues_in) == ({"claim": "a claim", "correct_fact": "a fact"}, ["abusiveness"])

    def test_an_empty_output_is_valid(self):
        parsed = schemas().RaterOutput.model_validate(output_dict())
        assert (parsed.findings, parsed.no_issues_in) == ([], [])


class TestStrictness:
    @pytest.mark.parametrize("field", FINDING_FIELDS)
    def test_every_finding_field_is_required(self, field):
        data = valid_finding()
        del data[field]
        with pytest.raises(ValidationError):
            schemas().RaterFinding.model_validate(data)

    @pytest.mark.parametrize("field", OUTPUT_FIELDS)
    def test_every_output_field_is_required(self, field):
        data = output_dict()
        del data[field]
        with pytest.raises(ValidationError):
            schemas().RaterOutput.model_validate(data)

    def test_an_extra_key_on_a_finding_is_refused(self):
        with pytest.raises(ValidationError):
            schemas().RaterFinding.model_validate(valid_finding(rationale="because"))

    def test_an_extra_key_on_the_output_is_refused(self):
        with pytest.raises(ValidationError):
            schemas().RaterOutput.model_validate({**output_dict(), "summary": "fine"})

    @pytest.mark.parametrize(
        "changes",
        [
            {"local_id": 5}, {"dimension": None}, {"quote": 7}, {"intensity": "high"}, {"intensity": 2.5},
            {"confidence": "sure"}, {"detail": "a claim"}, {"not_scorable_reason": "because"},
        ],
    )
    def test_a_value_of_the_wrong_type_is_refused(self, changes):
        with pytest.raises(ValidationError):
            schemas().RaterFinding.model_validate(valid_finding(**changes))

    @pytest.mark.parametrize("findings", ["not a list", {"f1": {}}, [1], [None]])
    def test_findings_must_be_a_list_of_findings(self, findings):
        with pytest.raises(ValidationError):
            schemas().RaterOutput.model_validate({"findings": findings, "no_issues_in": []})

    @pytest.mark.parametrize("no_issues", ["abusiveness", [1], None])
    def test_no_issues_in_must_be_a_list_of_strings(self, no_issues):
        with pytest.raises(ValidationError):
            schemas().RaterOutput.model_validate({"findings": [], "no_issues_in": no_issues})


class TestValuesTheRunnerJudgesItself:
    """An out-of-rule finding must not fail the whole output (a retry would be paid for); the runner drops it and reports it."""

    @pytest.mark.parametrize("intensity", [0, 1, 2, 3, 4])
    def test_each_intensity_of_the_scale_is_accepted(self, intensity):
        assert schemas().RaterFinding.model_validate(valid_finding(intensity=intensity)).intensity == intensity

    @pytest.mark.parametrize("reason", ["unverifiable", "contested", "needs_context"])
    def test_each_not_scorable_reason_is_accepted_with_a_null_intensity(self, reason):
        parsed = schemas().RaterFinding.model_validate(valid_finding(intensity=None, not_scorable_reason=reason))
        assert (parsed.intensity, parsed.not_scorable_reason) == (None, reason)

    def test_a_null_intensity_without_a_reason_still_parses(self):
        parsed = schemas().RaterFinding.model_validate(valid_finding(intensity=None, not_scorable_reason=None))
        assert (parsed.intensity, parsed.not_scorable_reason) == (None, None)

    def test_an_intensity_together_with_a_reason_still_parses(self):
        parsed = schemas().RaterFinding.model_validate(valid_finding(intensity=2, not_scorable_reason="contested"))
        assert (parsed.intensity, parsed.not_scorable_reason) == (2, "contested")

    def test_a_null_confidence_parses(self):
        assert schemas().RaterFinding.model_validate(valid_finding(confidence=None)).confidence is None

    @pytest.mark.parametrize("detail", [{}, {"claim": "x"}, {"anything": 1, "at": ["all"]}])
    def test_detail_is_a_free_form_dict(self, detail):
        assert schemas().RaterFinding.model_validate(valid_finding(detail=detail)).detail == detail

    def test_the_dimension_is_any_string_so_an_unknown_one_is_the_runners_to_drop(self):
        assert schemas().RaterFinding.model_validate(valid_finding(dimension="fallacy")).dimension == "fallacy"


class TestTheApiSchema:
    def raw(self):
        return schemas().RaterOutput.model_json_schema()

    def test_the_sdk_can_transform_the_output_schema(self):
        from anthropic import transform_schema

        assert transform_schema(self.raw())["type"] == "object"

    def test_the_sent_schema_uses_no_unsupported_keyword(self):
        from anthropic import transform_schema

        used = set().union(*(set(node) for node in all_nodes(transform_schema(self.raw()))))
        assert used & UNSUPPORTED_KEYWORDS == set()

    def test_the_output_object_forbids_extra_keys(self):
        assert self.raw()["additionalProperties"] is False

    def test_the_finding_object_forbids_extra_keys(self):
        assert self.raw()["$defs"]["RaterFinding"]["additionalProperties"] is False

    def test_every_field_of_both_objects_is_listed_as_required(self):
        raw = self.raw()
        assert (sorted(raw["required"]), sorted(raw["$defs"]["RaterFinding"]["required"])) == (
            sorted(OUTPUT_FIELDS), sorted(FINDING_FIELDS),
        )

    def test_a_union_in_the_schema_is_only_ever_an_optional(self):
        unions = [node["anyOf"] for node in all_nodes(self.raw()) if "anyOf" in node]
        assert len(unions) == 3
        assert all(len(members) == 2 and {"type": "null"} in members for members in unions)

    def test_the_intensity_is_an_optional_integer(self):
        node = self.raw()["$defs"]["RaterFinding"]["properties"]["intensity"]
        assert sorted(member["type"] for member in node["anyOf"]) == ["integer", "null"]

    def test_the_not_scorable_reason_enum_is_the_taxonomys(self):
        from moderation import taxonomy

        node = self.raw()["$defs"]["RaterFinding"]["properties"]["not_scorable_reason"]
        (option,) = [member for member in node["anyOf"] if member != {"type": "null"}]
        assert option["enum"] == list(taxonomy.NOT_SCORABLE_REASONS)

    def test_the_detail_object_is_the_claim_and_the_correct_fact_both_required(self):
        detail = self.raw()["$defs"]["RaterFinding"]["properties"]["detail"]
        assert (sorted(detail["properties"]), sorted(detail["required"]), detail["additionalProperties"]) == (
            ["claim", "correct_fact"], ["claim", "correct_fact"], False,
        )

    def test_the_schema_has_no_recursion(self):
        defs = self.raw()["$defs"]
        references = {name: {node["$ref"].rsplit("/", 1)[-1] for node in all_nodes(body) if "$ref" in node} for name, body in defs.items()}
        assert all(name not in targets for name, targets in references.items())
