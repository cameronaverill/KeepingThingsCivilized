"""Step 20a item 1 (docs/step20a_brief.md): `moderation/taxonomy.py` gains an `offer_research` act type.

New tests only, written against the live `moderation.taxonomy` module (not the frozen ACT_TYPES/ISSUE_TYPES etc.
snapshots in step3_testkit.py, which belong to a different agent to update). These tests fail with an AssertionError
(not an ImportError) until `taxonomy.py` is edited, since the module itself imports fine either way.

The "every taxonomy value has a definition" / "every prompt mentions every taxonomy value" tests already exist in
test_step3_taxonomy.py and test_step3_prompt_files.py; per the brief they are expected to extend automatically to
cover offer_research once ACT_TYPES and DEFINITIONS carry it (and, for the prompt test, once the prompt files are
updated by Group 3). This file does not duplicate those generic tests; it adds targeted coverage for the new value
and for the "nothing else changed" boundary the brief insists on.
"""
import re

import pytest

ORIGINAL_TEN_ACT_TYPES = (
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
)

ORIGINAL_ISSUE_TYPES = {
    "unsupported_claim",
    "possible_factual_error",
    "unclear_statement",
    "fallacy",
    "strawman",
    "abusive_language",
    "repetition",
    "process_violation",
}
ORIGINAL_DECISIONS = {"intervene", "no_intervention"}
ORIGINAL_TONES = {"gentle", "neutral", "firm"}
ORIGINAL_NOT_SCORABLE_REASONS = {"unverifiable", "contested", "needs_context"}
ORIGINAL_DIMENSIONS = {"factual_accuracy", "abusiveness"}


@pytest.fixture
def tax():
    from moderation import taxonomy

    return taxonomy


# --- item 1: offer_research is added, appended, and defined ---------------------------------------------------------

def test_offer_research_is_in_act_types(tax):
    assert "offer_research" in tax.ACT_TYPES


def test_act_types_now_has_eleven_entries_with_no_duplicates(tax):
    assert len(tax.ACT_TYPES) == 11
    assert len(set(tax.ACT_TYPES)) == 11


def test_offer_research_is_appended_not_inserted(tax):
    """The brief requires the existing ten to keep their order and offer_research to be appended, not inserted."""
    assert tuple(tax.ACT_TYPES[:10]) == ORIGINAL_TEN_ACT_TYPES
    assert tax.ACT_TYPES[10] == "offer_research"
    assert tax.ACT_TYPES[-1] == "offer_research"


def test_offer_research_has_a_definitions_entry(tax):
    assert "offer_research" in tax.DEFINITIONS
    text = tax.DEFINITIONS["offer_research"]
    assert isinstance(text, str) and text.strip() == text and text


def test_offer_research_definition_is_one_sentence_with_boundary_rule_style(tax):
    """Same shape check test_step3_taxonomy.py applies to every other value: one sentence, ends in punctuation, and
    (since this is the "boundary rule" style used throughout DEFINITIONS) states the boundary against its nearest
    neighbours by name."""
    text = tax.DEFINITIONS["offer_research"]
    assert "\n" not in text
    assert len(text) >= 15
    assert text.endswith((".", "?", "!"))
    without_abbreviations = re.sub(r"\b(e\.g|i\.e|etc|vs)\.", "", text)
    assert not re.search(r"[.!?]\s+[A-Z]", without_abbreviations), f"more than one sentence: {text!r}"


def test_offer_research_definition_states_it_only_offers_never_performs(tax):
    text = tax.DEFINITIONS["offer_research"].lower()
    assert "offer" in text
    assert "perform" in text, "must say it does not perform the check itself (that is Step 20b, not this step)"
    assert "position" in text, "must say it takes no position on the claim"


def test_offer_research_definition_names_its_boundary_against_provide_information_and_correct_factual_error(tax):
    text = tax.DEFINITIONS["offer_research"]
    assert "provide_information" in text
    assert "correct_factual_error" in text


def test_offer_research_is_not_also_an_issue_type_decision_tone_or_not_scorable_reason(tax):
    """DEFINITIONS is one flat mapping keyed by value; a name reused across categories would silently collide."""
    assert "offer_research" not in tax.ISSUE_TYPES
    assert "offer_research" not in tax.DECISIONS
    assert "offer_research" not in tax.TONES
    assert "offer_research" not in tax.NOT_SCORABLE_REASONS
    assert "offer_research" not in tax.DIMENSIONS


# --- item 1's negative scope: nothing else in the taxonomy changed --------------------------------------------------

def test_issue_types_decisions_tones_and_not_scorable_reasons_are_unchanged(tax):
    assert set(tax.ISSUE_TYPES) == ORIGINAL_ISSUE_TYPES
    assert set(tax.DECISIONS) == ORIGINAL_DECISIONS
    assert set(tax.TONES) == ORIGINAL_TONES
    assert set(tax.NOT_SCORABLE_REASONS) == ORIGINAL_NOT_SCORABLE_REASONS


def test_dimensions_are_unchanged(tax):
    assert set(tax.DIMENSIONS) == ORIGINAL_DIMENSIONS


# --- confirm the generic existing tests that should "extend automatically" actually do, live -------------------------

def test_every_value_currently_in_act_types_has_a_definition_including_offer_research(tax):
    """A live-taxonomy version of test_step3_taxonomy.py::test_every_value_has_a_one_sentence_definition, which is
    parametrized off a frozen snapshot in step3_testkit.py (not this brief's concern to update). This confirms the
    real contract directly: every current ACT_TYPES value, including the new one, has a definition."""
    for act_type in tax.ACT_TYPES:
        assert act_type in tax.DEFINITIONS, f"DEFINITIONS has no entry for {act_type}"
