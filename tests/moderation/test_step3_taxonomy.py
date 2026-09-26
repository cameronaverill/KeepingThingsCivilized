"""moderation/taxonomy.py: the single source of every issue type, act type, dimension, decision and tone (brief section 3)."""
import re
from collections.abc import Mapping

import pytest
from step3_testkit import (
    ACT_TYPES,
    DECISIONS,
    DIMENSION_TO_ISSUE_TYPE,
    ISSUE_TYPES,
    NOT_SCORABLE_REASONS,
    RUBRIC_DIR,
    TONES,
)


@pytest.fixture
def tax():
    from moderation import taxonomy

    return taxonomy


def as_list(value):
    return list(value)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("ISSUE_TYPES", ISSUE_TYPES),
        ("ACT_TYPES", ACT_TYPES),
        ("DECISIONS", DECISIONS),
        ("TONES", TONES),
        ("NOT_SCORABLE_REASONS", NOT_SCORABLE_REASONS),
    ],
)
def test_value_sets_are_exactly_the_documented_ones(tax, name, expected):
    values = as_list(getattr(tax, name))
    assert set(values) == expected
    assert len(values) == len(set(values)), "no duplicates"
    assert all(isinstance(v, str) for v in values)


def test_there_are_eight_issue_types_and_ten_act_types(tax):
    assert len(set(tax.ISSUE_TYPES)) == 8
    assert len(set(tax.ACT_TYPES)) == 10


def test_intensity_range_is_zero_to_four(tax):
    assert tuple(tax.INTENSITY_RANGE) == (0, 4)


def test_cross_message_issue_types(tax):
    assert set(tax.CROSS_MESSAGE_ISSUE_TYPES) == {"repetition", "strawman", "process_violation"}
    assert set(tax.CROSS_MESSAGE_ISSUE_TYPES) <= set(tax.ISSUE_TYPES)


def _field(entry, name):
    return entry[name] if isinstance(entry, Mapping) else getattr(entry, name)


def test_dimensions_registry(tax):
    assert set(tax.DIMENSIONS) == {"factual_accuracy", "abusiveness"}
    assert _field(tax.DIMENSIONS["factual_accuracy"], "issue_type") == "possible_factual_error"
    assert _field(tax.DIMENSIONS["factual_accuracy"], "coverage") == "all_claims"
    assert _field(tax.DIMENSIONS["abusiveness"], "issue_type") == "abusive_language"
    assert _field(tax.DIMENSIONS["abusiveness"], "coverage") == "flagged_only"


def test_every_dimension_points_at_a_real_issue_type_and_has_a_rubric_file(tax):
    for name, entry in tax.DIMENSIONS.items():
        assert _field(entry, "issue_type") in set(tax.ISSUE_TYPES)
        assert (RUBRIC_DIR / f"{name}_v1.md").is_file(), f"adding a dimension means adding rubrics/{name}_v1.md"


@pytest.mark.parametrize("dimension, issue_type", sorted(DIMENSION_TO_ISSUE_TYPE.items()))
def test_dimension_for_maps_the_two_dimensioned_issue_types(tax, dimension, issue_type):
    assert tax.dimension_for(issue_type) == dimension


@pytest.mark.parametrize("issue_type", sorted(ISSUE_TYPES - set(DIMENSION_TO_ISSUE_TYPE.values())))
def test_dimension_for_is_none_for_every_other_issue_type(tax, issue_type):
    assert tax.dimension_for(issue_type) is None


def test_exactly_two_issue_types_have_a_dimension(tax):
    assert {t for t in tax.ISSUE_TYPES if tax.dimension_for(t) is not None} == set(DIMENSION_TO_ISSUE_TYPE.values())


def all_values():
    return sorted(ISSUE_TYPES | ACT_TYPES | DECISIONS | TONES | NOT_SCORABLE_REASONS)


def test_no_value_name_is_shared_between_categories(tax):
    """DEFINITIONS is keyed by value, so a value in two categories would silently share (and overwrite) an entry."""
    lists = [tax.ISSUE_TYPES, tax.ACT_TYPES, tax.DECISIONS, tax.TONES, tax.NOT_SCORABLE_REASONS]
    everything = [v for values in lists for v in values]
    assert len(set(everything)) == len(everything)


@pytest.mark.parametrize("value", all_values())
def test_every_value_has_a_one_sentence_definition(tax, value):
    assert value in tax.DEFINITIONS, f"DEFINITIONS has no entry for {value}"
    text = tax.DEFINITIONS[value]
    assert isinstance(text, str) and text.strip()
    assert text == text.strip()
    assert "\n" not in text, "one sentence, on one line"
    assert len(text) >= 15
    assert text.endswith((".", "?", "!")), "a sentence ends with punctuation"
    without_abbreviations = re.sub(r"\b(e\.g|i\.e|etc|vs)\.", "", text)
    assert not re.search(r"[.!?]\s+[A-Z]", without_abbreviations), f"more than one sentence: {text!r}"


def test_definitions_state_the_boundary_between_unsupported_claim_and_possible_factual_error(tax):
    """Plan section 5: unsupported = no support, may be true; possible error = likely false."""
    assert "support" in tax.DEFINITIONS["unsupported_claim"].lower()
    assert "false" in tax.DEFINITIONS["possible_factual_error"].lower()


def test_definitions_of_dimensions_if_present_are_non_empty_strings(tax):
    for name in tax.DIMENSIONS:
        if name in tax.DEFINITIONS:
            assert isinstance(tax.DEFINITIONS[name], str) and tax.DEFINITIONS[name].strip()
