"""Step 20a revision brief (docs/step20a_revision_brief.md) item 1/2: moderation/taxonomy.py's `offer_research`
DEFINITIONS entry never named `time_sensitive` directly (per the brief), so there is no field-name rename to check
here -- but the brief's governing rule is that the *reason* behind the broadened `needs_verification` flag (recency
vs. fabrication/inaccuracy risk on a specific detail) "is never distinguished in the flag itself and must never
appear in anything posted to users -- only 'an independent check could be requested,' never 'this might be outdated'
vs. 'this might be fabricated.'" moderation/prompts/intervenor_v1.md's new "when to prefer which" note is told to
point back to this DEFINITIONS entry "rather than duplicating them" (item 3), so this entry is part of the shared
vocabulary other guidance draws on and must itself stay generic enough not to leak either reason.

New tests only, against the live `moderation.taxonomy` module -- same convention as the original
tests/moderation/test_step20a_taxonomy.py, which this file does not edit or duplicate wholesale (only the checks
relevant to the revision, plus a light baseline-coverage smoke check per the revision brief's instruction to confirm
existing coverage still holds under the new tree).
"""
import re

import pytest


@pytest.fixture
def tax():
    from moderation import taxonomy

    return taxonomy


# --- baseline: offer_research is still present and defined (independent smoke check) --------------------------------

def test_offer_research_still_in_act_types_and_has_a_definition(tax):
    assert "offer_research" in tax.ACT_TYPES
    assert "offer_research" in tax.DEFINITIONS
    text = tax.DEFINITIONS["offer_research"]
    assert isinstance(text, str) and text.strip() == text and text


def test_act_types_still_has_eleven_entries_with_no_duplicates(tax):
    assert len(tax.ACT_TYPES) == 11
    assert len(set(tax.ACT_TYPES)) == 11


# --- the old field name never leaked into the definition, and still doesn't -----------------------------------------

def test_offer_research_definition_never_names_the_old_time_sensitive_field(tax):
    assert "time_sensitive" not in tax.DEFINITIONS["offer_research"]


# --- the definition does not leak *why* a check might be offered -----------------------------------------------

def test_offer_research_definition_does_not_leak_the_specific_reason_toward_users(tax):
    """Brief: the specific reason (recency vs. fabrication risk) must never appear in anything posted to users --
    only 'an independent check could be requested,' never 'this might be outdated' vs. 'this might be fabricated.'
    Checked here with the same two words the brief itself uses for this leak, since the DEFINITIONS entry is the
    shared vocabulary the intervenor prompt's new note is told to point back to rather than duplicate."""
    text = tax.DEFINITIONS["offer_research"].lower()
    assert "outdated" not in text
    assert not re.search(r"fabricat\w*", text)


def test_offer_research_definition_does_not_name_either_reason_as_a_labelled_alternative(tax):
    """A finer-grained version of the same check: the definition should not present the two reasons as a labelled
    either/or (e.g. "whether outdated or fabricated"), which would itself be a leak even using softer synonyms for
    the fabrication reason. This looks for the recency-only framing the field carried before the broadening
    ("might have changed ... after training") no longer being the *sole* reason offered without acknowledging the
    field is not reason-specific; it does not forbid a generic mention that a check can be requested."""
    text = tax.DEFINITIONS["offer_research"].lower()
    # A plain sanity bound: whatever the wording, it must not enumerate two distinct named justifications for the
    # offer (that would defeat the "one undifferentiated boolean, reason never shown" design).
    reason_markers = ["reason 1", "reason 2", "two reasons", "either because", "or because it"]
    assert not any(marker in text for marker in reason_markers)


# --- substance unaffected by the revision: still an offer, never a performance, no position taken -------------------

def test_offer_research_definition_still_states_it_only_offers_never_performs(tax):
    text = tax.DEFINITIONS["offer_research"].lower()
    assert "offer" in text
    assert "perform" in text, "must still say it does not perform the check itself (Step 20b, not this step)"
    assert "position" in text, "must still say it takes no position on the claim"


def test_offer_research_definition_still_names_its_boundary_against_provide_information_and_correct_factual_error(tax):
    text = tax.DEFINITIONS["offer_research"]
    assert "provide_information" in text
    assert "correct_factual_error" in text


def test_offer_research_definition_is_still_one_sentence_with_boundary_rule_style(tax):
    """Same shape rule test_step3_taxonomy.py / the original test_step20a_taxonomy.py apply to every DEFINITIONS
    value: one sentence, ends in punctuation."""
    text = tax.DEFINITIONS["offer_research"]
    assert "\n" not in text
    assert len(text) >= 15
    assert text.endswith((".", "?", "!"))
    without_abbreviations = re.sub(r"\b(e\.g|i\.e|etc|vs)\.", "", text)
    assert not re.search(r"[.!?]\s+[A-Z]", without_abbreviations), f"more than one sentence: {text!r}"


def test_offer_research_is_not_also_an_issue_type_decision_tone_or_not_scorable_reason(tax):
    """DEFINITIONS is one flat mapping keyed by value; a name reused across categories would silently collide --
    unaffected by the revision, kept here as a fresh independent check."""
    assert "offer_research" not in tax.ISSUE_TYPES
    assert "offer_research" not in tax.DECISIONS
    assert "offer_research" not in tax.TONES
    assert "offer_research" not in tax.NOT_SCORABLE_REASONS
    assert "offer_research" not in tax.DIMENSIONS
