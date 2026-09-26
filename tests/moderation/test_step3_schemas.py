"""moderation/schemas.py: the two agents' output models, and their fit with the structured-output API (brief section 3)."""
import pytest
from pydantic import ValidationError
from step3_testkit import (
    ACT_TYPES,
    DECISIONS,
    ISSUE_TYPES,
    TONES,
    act_dict,
    disposition_dict,
    discussion_map_dict,
    intervenor_output,
    issue_dict,
    make_id,
    master_output,
    style,
)

OUTPUT_MODELS = ["MasterOutput", "IntervenorOutput"]
ALL_MODELS = ["MasterIssue", "Disagreement", "DiscussionMap", "MasterOutput", "IssueDisposition", "Act", "IntervenorOutput"]


def model(name):
    from moderation import schemas

    return getattr(schemas, name)


# --- valid examples ------------------------------------------------------------------------------------------------

def test_master_issue_valid_for_every_issue_type():
    from moderation.schemas import MasterIssue

    for issue_type in ISSUE_TYPES:
        issue = MasterIssue.model_validate(issue_dict(1, 3, "a phrase", issue_type=issue_type))
        assert issue.issue_type == issue_type
        assert issue.quote == "a phrase"


@pytest.mark.parametrize("issue_type", sorted(ISSUE_TYPES - {"possible_factual_error", "abusive_language"}))
def test_intensity_none_is_allowed_for_issue_types_without_a_dimension(issue_type):
    from moderation.schemas import MasterIssue

    assert MasterIssue.model_validate(issue_dict(1, 1, "q", issue_type=issue_type, intensity=None)).intensity is None


@pytest.mark.parametrize("intensity", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("issue_type", ["possible_factual_error", "abusive_language"])
def test_intensity_zero_to_four_is_valid_for_the_dimensioned_types(issue_type, intensity):
    from moderation.schemas import MasterIssue

    assert MasterIssue.model_validate(issue_dict(1, 1, "q", issue_type=issue_type, intensity=intensity)).intensity == intensity


def test_master_output_valid_with_and_without_issues():
    assert master_output([]).issues == []
    out = master_output([issue_dict(1, 2, "one"), issue_dict(2, 2, "two", issue_type="fallacy")])
    assert [i.quote for i in out.issues] == ["one", "two"]
    assert out.discussion_map.disagreements[0].kind == "factual"


def test_discussion_map_valid():
    from moderation.schemas import DiscussionMap

    dm = DiscussionMap.model_validate(discussion_map_dict())
    assert dm.agreements and dm.disagreements[0].summary
    assert DiscussionMap.model_validate({"agreements": [], "disagreements": []}).disagreements == []


@pytest.mark.parametrize("kind", ["factual", "normative"])
def test_disagreement_kinds(kind):
    from moderation.schemas import Disagreement

    assert Disagreement.model_validate({"summary": "s", "kind": kind}).kind == kind


@pytest.mark.parametrize("disposition", ["acted", "declined"])
def test_issue_disposition_valid(disposition):
    from moderation.schemas import IssueDisposition

    assert IssueDisposition.model_validate(disposition_dict(1, disposition, "because")).disposition == disposition


@pytest.mark.parametrize("act_type", sorted(ACT_TYPES))
def test_act_valid_for_every_act_type(act_type):
    from moderation.schemas import Act

    assert Act.model_validate(act_dict(type=act_type)).type == act_type


@pytest.mark.parametrize("tone", sorted(TONES))
def test_act_valid_for_every_tone(tone):
    from moderation.schemas import Act

    assert Act.model_validate(act_dict(tone=tone)).tone == tone


@pytest.mark.parametrize("label", ["A", "B", "all", "both", "none", "Participant A", "Participant Z", "anything at all"])
def test_act_labels_are_plain_strings_not_checked_against_a_conversation(label):
    """Step 5 (the pipeline) validates addressee and subject against the conversation's labels; the schema does not."""
    from moderation.schemas import Act

    act = Act.model_validate(act_dict(addressee=label, subject=label))
    assert act.addressee == label and act.subject == label


def test_act_accepts_several_source_ids_and_none():
    from moderation.schemas import Act

    act = Act.model_validate(act_dict(issue_ns=(1, 2, 3), seqs=(4, 5)))
    assert len(act.source_issue_ids) == 3 and len(act.source_message_ids) == 2
    empty = Act.model_validate(act_dict(issue_ns=(), seqs=()))
    assert empty.source_issue_ids == [] and empty.source_message_ids == []


@pytest.mark.parametrize("decision", sorted(DECISIONS))
def test_intervenor_output_valid_for_each_decision(decision):
    out = intervenor_output(decision, "why")
    assert out.decision == decision and out.rationale == "why"
    assert out.issue_dispositions == [] and out.acts == []


def test_intervenor_output_valid_full():
    out = intervenor_output(
        "intervene", "r",
        dispositions=[disposition_dict(1, "acted", "x"), disposition_dict(2, "declined", "y")],
        acts=[act_dict(text="Please provide a source."), act_dict(type="enforce_conduct", tone="firm", text="Keep it civil.")],
    )
    assert [a.text for a in out.acts] == ["Please provide a source.", "Keep it civil."]
    assert out.issue_dispositions[1].disposition == "declined"


@pytest.mark.parametrize("name", ALL_MODELS)
def test_every_model_exists_and_is_a_pydantic_model(name):
    from pydantic import BaseModel

    assert issubclass(model(name), BaseModel)


def test_models_round_trip_through_json():
    from moderation.schemas import IntervenorOutput, MasterOutput

    m = master_output([issue_dict(1, 2, "quote with \"quotes\" and <tags> & unicode — \U0001F600", intensity=None)])
    assert MasterOutput.model_validate_json(m.model_dump_json()) == m
    i = intervenor_output("intervene", "r", [disposition_dict(1, "acted")], [act_dict()])
    assert IntervenorOutput.model_validate_json(i.model_dump_json()) == i


# --- invalid examples ----------------------------------------------------------------------------------------------

def _drop(data, key):
    copy = dict(data)
    del copy[key]
    return copy


@pytest.mark.parametrize("field", ["id", "message_id", "issue_type", "quote", "explanation", "confidence"])
def test_master_issue_requires_its_fields(field):
    with pytest.raises(ValidationError):
        model("MasterIssue").model_validate(_drop(issue_dict(1, 1, "q"), field))


@pytest.mark.parametrize("bad", ["", "bogus", "Unsupported_Claim", "unsupported claim", " fallacy", "factual_accuracy", None, 3])
def test_master_issue_rejects_an_unknown_issue_type(bad):
    with pytest.raises(ValidationError):
        model("MasterIssue").model_validate({**issue_dict(1, 1, "q"), "issue_type": bad})


@pytest.mark.parametrize("bad", [-1, 5, 100])
def test_master_issue_rejects_intensity_outside_zero_to_four(bad):
    """The range is checked locally by the model (the SDK strips numeric constraints from the wire schema)."""
    with pytest.raises(ValidationError):
        model("MasterIssue").model_validate(issue_dict(1, 1, "q", issue_type="abusive_language", intensity=bad))


def test_master_issue_rejects_a_non_string_quote_and_non_numeric_intensity():
    with pytest.raises(ValidationError):
        model("MasterIssue").model_validate({**issue_dict(1, 1, "q"), "quote": ["a", "b"]})
    with pytest.raises(ValidationError):
        model("MasterIssue").model_validate({**issue_dict(1, 1, "q"), "intensity": "high"})


def test_master_issue_rejects_an_unrelated_message_id():
    with pytest.raises(ValidationError):
        model("MasterIssue").model_validate({**issue_dict(1, 1, "q"), "message_id": "not a message id"})


@pytest.mark.parametrize("bad", ["", "moral", "Factual", "both", None])
def test_disagreement_rejects_an_unknown_kind(bad):
    with pytest.raises(ValidationError):
        model("Disagreement").model_validate({"summary": "s", "kind": bad})


@pytest.mark.parametrize("field", ["summary", "kind"])
def test_disagreement_requires_its_fields(field):
    with pytest.raises(ValidationError):
        model("Disagreement").model_validate(_drop({"summary": "s", "kind": "factual"}, field))


@pytest.mark.parametrize("field", ["agreements", "disagreements"])
def test_discussion_map_requires_its_fields(field):
    with pytest.raises(ValidationError):
        model("DiscussionMap").model_validate(_drop(discussion_map_dict(), field))


@pytest.mark.parametrize("field", ["issues", "discussion_map"])
def test_master_output_requires_its_fields(field):
    good = {"issues": [], "discussion_map": discussion_map_dict()}
    with pytest.raises(ValidationError):
        model("MasterOutput").model_validate(_drop(good, field))


def test_master_output_rejects_issues_that_are_not_a_list_or_are_invalid():
    with pytest.raises(ValidationError):
        model("MasterOutput").model_validate({"issues": "none", "discussion_map": discussion_map_dict()})
    with pytest.raises(ValidationError):
        model("MasterOutput").model_validate({"issues": [{"id": make_id(1)}], "discussion_map": discussion_map_dict()})


@pytest.mark.parametrize("bad", ["", "ignored", "Acted", "accepted", None])
def test_disposition_rejects_unknown_values(bad):
    with pytest.raises(ValidationError):
        model("IssueDisposition").model_validate({**disposition_dict(1, "acted"), "disposition": bad})


@pytest.mark.parametrize("field", ["issue_id", "disposition", "reason"])
def test_disposition_requires_its_fields(field):
    with pytest.raises(ValidationError):
        model("IssueDisposition").model_validate(_drop(disposition_dict(1, "acted"), field))


@pytest.mark.parametrize("field", ["type", "addressee", "subject", "source_issue_ids", "source_message_ids", "tone", "text"])
def test_act_requires_its_fields(field):
    with pytest.raises(ValidationError):
        model("Act").model_validate(_drop(act_dict(), field))


@pytest.mark.parametrize("bad", ["", "unsupported_claim", "correct", "ENFORCE_CONDUCT", None])
def test_act_rejects_an_unknown_type(bad):
    with pytest.raises(ValidationError):
        model("Act").model_validate({**act_dict(), "type": bad})


@pytest.mark.parametrize("bad", ["", "angry", "Firm", "harsh", None])
def test_act_rejects_an_unknown_tone(bad):
    with pytest.raises(ValidationError):
        model("Act").model_validate({**act_dict(), "tone": bad})


def test_act_rejects_a_non_list_of_source_ids():
    with pytest.raises(ValidationError):
        model("Act").model_validate({**act_dict(), "source_issue_ids": make_id(1)})


@pytest.mark.parametrize("field", ["decision", "rationale", "issue_dispositions", "acts"])
def test_intervenor_output_requires_its_fields(field):
    good = {"decision": "no_intervention", "rationale": "r", "issue_dispositions": [], "acts": []}
    with pytest.raises(ValidationError):
        model("IntervenorOutput").model_validate(_drop(good, field))


@pytest.mark.parametrize("bad", ["", "maybe", "Intervene", "none", None])
def test_intervenor_output_rejects_an_unknown_decision(bad):
    with pytest.raises(ValidationError):
        model("IntervenorOutput").model_validate(
            {"decision": bad, "rationale": "r", "issue_dispositions": [], "acts": []}
        )


def test_intervenor_output_rejects_an_invalid_act_inside_the_list():
    with pytest.raises(ValidationError):
        model("IntervenorOutput").model_validate(
            {"decision": "intervene", "rationale": "r", "issue_dispositions": [], "acts": [{**act_dict(), "tone": "angry"}]}
        )


# --- fit with the structured-output API ----------------------------------------------------------------------------

UNSUPPORTED_KEYWORDS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "multipleOf",
    "maxItems", "uniqueItems", "oneOf", "not", "patternProperties", "if", "then", "else", "$dynamicRef",
}


def wire_schema(name):
    """What the SDK actually sends: model_json_schema() through the SDK's own transform_schema."""
    from anthropic import transform_schema

    return transform_schema(model(name).model_json_schema())


def resolve(root, node):
    seen = set()
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        assert ref.startswith("#/"), f"only local references are expected: {ref}"
        assert ref not in seen, "a $ref that points at itself"
        seen.add(ref)
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        node = target
    return node


def all_nodes(node, path=()):
    """Every schema-like dict in a JSON schema, skipping the NAMES inside `properties` and `$defs`."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
                for name, sub in value.items():
                    yield from all_nodes(sub, path + (key, name))
            elif key == "enum" or key == "required":
                continue
            else:
                yield from all_nodes(value, path + (key,))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from all_nodes(item, path + (i,))


@pytest.mark.parametrize("name", OUTPUT_MODELS)
def test_the_sdk_can_transform_each_output_schema(name):
    assert wire_schema(name)["type"] == "object"


@pytest.mark.parametrize("name", OUTPUT_MODELS)
def test_wire_schema_uses_no_unsupported_keyword(name):
    schema = wire_schema(name)
    for path, node in all_nodes(schema):
        bad = UNSUPPORTED_KEYWORDS & set(node)
        assert not bad, f"{name}: unsupported keyword(s) {sorted(bad)} at {path}"


@pytest.mark.parametrize("name", OUTPUT_MODELS)
def test_raw_schema_uses_only_constraints_the_sdk_strips_and_no_unions_besides_optional(name):
    """Numeric constraints may exist in the raw schema only because the SDK strips them (previous test); unions must be X | None."""
    raw = model(name).model_json_schema()
    for path, node in all_nodes(raw):
        assert not ({"oneOf", "not", "patternProperties", "$dynamicRef"} & set(node)), (name, path)
        if "anyOf" in node:
            members = node["anyOf"]
            assert len(members) == 2 and {"type": "null"} in members, f"{name}: a union that is not Optional at {path}: {members}"


@pytest.mark.parametrize("name", OUTPUT_MODELS)
def test_schema_has_no_recursion(name):
    raw = model(name).model_json_schema()
    defs = raw.get("$defs", {})

    def refs(node):
        found = []
        for _p, sub in all_nodes(node):
            if "$ref" in sub:
                found.append(sub["$ref"].rsplit("/", 1)[-1])
        return found

    graph = {key: set(refs(value)) for key, value in defs.items()}
    for ref in refs({k: v for k, v in raw.items() if k != "$defs"}):
        assert ref in defs, f"dangling $ref {ref}"
    for key, targets in graph.items():
        assert targets <= set(defs), f"dangling $ref inside {key}"

    visiting, done = set(), set()

    def dfs(key):
        assert key not in visiting, f"$ref cycle through {key}"
        if key in done:
            return
        visiting.add(key)
        for target in graph[key]:
            dfs(target)
        visiting.discard(key)
        done.add(key)

    for key in graph:
        dfs(key)


def enum_of(model_name, field):
    raw = model(model_name).model_json_schema()
    node = resolve(raw, raw["properties"][field])
    if "anyOf" in node:
        options = [resolve(raw, m) for m in node["anyOf"] if m != {"type": "null"}]
        assert len(options) == 1
        node = options[0]
    assert "enum" in node or "const" in node, f"{model_name}.{field} is not a Literal enum: {node}"
    return set(node["enum"]) if "enum" in node else {node["const"]}


@pytest.mark.parametrize(
    "model_name, field, expected",
    [
        ("MasterIssue", "issue_type", ISSUE_TYPES),
        ("Act", "type", ACT_TYPES),
        ("Act", "tone", TONES),
        ("IntervenorOutput", "decision", DECISIONS),
        ("Disagreement", "kind", {"factual", "normative"}),
        ("IssueDisposition", "disposition", {"acted", "declined"}),
    ],
)
def test_literal_enums_equal_the_taxonomy(model_name, field, expected):
    assert enum_of(model_name, field) == set(expected)


def test_intensity_is_an_optional_integer_in_the_schema():
    raw = model("MasterIssue").model_json_schema()
    node = raw["properties"]["intensity"]
    assert "anyOf" in node
    types = sorted(m.get("type") for m in node["anyOf"])
    assert types == ["integer", "null"]


def test_the_style_of_ids_is_stable():
    """Guards the tolerant builders themselves: whatever style validates must keep validating."""
    assert style()["id"] in ("str", "int")
