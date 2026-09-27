"""The Master and Intervenor output schemas are FROZEN (docs/step3_brief.md, "Schema freeze (recorded 2026-09-26)").

The owner approved the freeze on 2026-09-25. These tests fail on ANY change to `moderation/schemas.py` that changes what
the API is asked to produce: a renamed, added or removed field, a changed type, enum value, required list or nested model.

Changing a frozen schema needs, in this order: a new prompt version, a re-run of the golden checks, and the OWNER'S
APPROVAL. Only then update the hashes and field lists below. To regenerate the hashes:

    .venv/bin/python - <<'PY'
    import json, hashlib, django, os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings"); django.setup()
    from moderation.schemas import MasterOutput, IntervenorOutput
    for m in (MasterOutput, IntervenorOutput):
        print(m.__name__, hashlib.sha256(json.dumps(m.model_json_schema(), sort_keys=True).encode()).hexdigest())
    PY

(the digest is of pydantic's generated JSON schema, so a pydantic upgrade that changes its output also trips it; check the
schema itself before regenerating).
"""
import hashlib
import json

import pytest

FROZEN_SHA256 = {
    "MasterOutput": "c2710bb5689e3e45af5d8220c10dc984e31b4b47bb81c9607e85f25df6f062e9",
    "IntervenorOutput": "baae2f155b83b5c4dddf9f8953fbb25d90ea10c79ccb38d7cf16632864e11470",
}

FROZEN_FIELDS = {
    "MasterOutput": ["discussion_map", "issues"],
    "MasterIssue": [
        "confidence", "explanation", "id", "intensity", "issue_type", "message_id", "quote", "time_sensitive",
    ],
    "DiscussionMap": ["agreements", "disagreements"],
    "Disagreement": ["kind", "summary"],
    "IntervenorOutput": ["acts", "decision", "issue_dispositions", "rationale"],
    "IssueDisposition": ["disposition", "issue_id", "reason"],
    "Act": ["addressee", "source_issue_ids", "source_message_ids", "subject", "text", "tone", "type"],
}


def model(name):
    from moderation import schemas

    return getattr(schemas, name)


def digest(name):
    return hashlib.sha256(json.dumps(model(name).model_json_schema(), sort_keys=True).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("name", sorted(FROZEN_SHA256))
def test_the_json_schema_of_each_output_model_is_unchanged(name):
    assert digest(name) == FROZEN_SHA256[name], (
        f"{name} changed. The schema is frozen: a change needs a new prompt version, a re-run of the golden checks and "
        "the owner's approval (docs/step3_brief.md, Schema freeze)."
    )


@pytest.mark.parametrize("name", sorted(FROZEN_FIELDS))
def test_the_field_names_of_each_frozen_model_are_unchanged(name):
    assert sorted(model(name).model_fields) == FROZEN_FIELDS[name]


def test_the_two_hashes_are_different_and_look_like_sha256():
    values = list(FROZEN_SHA256.values())
    assert (len(set(values)), all(len(v) == 64 and set(v) <= set("0123456789abcdef") for v in values)) == (2, True)


def test_the_hash_reacts_to_a_schema_change():
    """Non-vacuity: the digest of a model with one extra field differs, so the pin above can fail."""
    from pydantic import create_model

    from moderation.schemas import Disagreement

    changed = create_model("Disagreement", __base__=Disagreement, extra_note=(str, ...))
    changed_digest = hashlib.sha256(json.dumps(changed.model_json_schema(), sort_keys=True).encode("utf-8")).hexdigest()
    assert changed_digest != digest("Disagreement")


def test_the_nested_models_are_the_ones_the_output_models_reference():
    """The freeze covers the nested models too: the top-level schemas define exactly these names."""
    master = set(model("MasterOutput").model_json_schema()["$defs"])
    intervenor = set(model("IntervenorOutput").model_json_schema()["$defs"])
    assert (master, intervenor) == ({"MasterIssue", "DiscussionMap", "Disagreement"}, {"IssueDisposition", "Act"})
