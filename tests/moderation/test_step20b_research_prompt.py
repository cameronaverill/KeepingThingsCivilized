"""Step 20b item 3 (docs/step20b_brief.md): `moderation/prompts/research_v1.md` follows the same conventions as
`master_v1.md`/`intervenor_v1.md` (docs/step3_brief.md; tests/moderation/test_step3_prompt_files.py) -- a versioned
filename, the "everything in message blocks is data" injection-safety framing, and no participant label/username
leak in its own instructions. Static-text checks only, no LLM mock, following test_step3_prompt_files.py's pattern.
"""
import re

import pytest
from step3_testkit import PROMPT_DIR

RESEARCH_PROMPT_PATH = PROMPT_DIR / "research_v1.md"


def prompt_text():
    assert RESEARCH_PROMPT_PATH.is_file(), f"missing {RESEARCH_PROMPT_PATH}"
    return RESEARCH_PROMPT_PATH.read_text(encoding="utf-8")


# --- Existence and shape, matching test_step3_prompt_files.py's own convention -------------------------------------

def test_research_prompt_file_exists_and_is_substantial():
    text = prompt_text()
    assert len(text.strip()) > 1000
    assert text.isprintable() or "\n" in text


def test_research_prompt_is_versioned_v1_like_the_other_two_prompts():
    """docs/step20b_brief.md: "follow master_v1.md/intervenor_v1.md's existing conventions closely (versioned
    filename ...)"."""
    assert RESEARCH_PROMPT_PATH.name == "research_v1.md"
    assert (PROMPT_DIR / "master_v1.md").is_file() and (PROMPT_DIR / "intervenor_v1.md").is_file()


# --- Injection-safety framing (docs/plan.md section 7; the same framing master_v1.md/intervenor_v1.md already use) --

def test_research_prompt_marks_the_incoming_content_as_data_not_instructions():
    text = prompt_text()
    assert re.search(r"\bDATA\b", text), "no DATA marker (the injection-safety framing master_v1.md/intervenor_v1.md use)"
    assert re.search(r"\bignor(e|ed|es)\b", text, re.IGNORECASE)
    assert re.search(r"instructions?", text, re.IGNORECASE)


def test_research_prompt_names_the_operator_as_the_only_source_of_real_instructions():
    text = prompt_text()
    assert re.search(r"\boperator\b", text, re.IGNORECASE), "no statement that only the operator's own words are instructions"


def test_research_prompt_describes_the_incoming_content_as_written_by_a_participant():
    """The claim/offer/issues the model is given come from inside the discussion, same provenance as the
    Master/Intervenor's transcript -- the prompt must say so, not present them as trusted operator text."""
    text = prompt_text()
    assert re.search(r"\bparticipant", text, re.IGNORECASE)


def test_research_prompt_warns_against_a_request_to_change_its_output_or_stop_its_task():
    """Matches master_v1.md's own worked list of injection attempts (docs/plan.md section 7's "an injection attempt
    is one of the golden-set cases"): the prompt should name at least one concrete kind of attempt it must refuse,
    not just an abstract "ignore instructions" sentence."""
    text = prompt_text().lower()
    concrete_attempt_phrases = [
        "change your output", "change its output", "reveal these instructions", "ignore these instructions",
        "conclude a certain way", "stop checking", "stop moderating", "take a side",
    ]
    assert any(phrase in text for phrase in concrete_attempt_phrases), (
        f"no concrete example of an injection attempt found (looked for any of {concrete_attempt_phrases})"
    )


# --- No participant label / username / side leak in the prompt's own instructions -----------------------------------

def test_research_prompt_states_the_model_is_not_told_who_made_the_claim_or_which_side():
    text = prompt_text()
    assert re.search(r"never\s+told|not\s+told", text, re.IGNORECASE), (
        "the prompt should say outright that the model is not told who made the claim or which side they are on "
        "(docs/step20b_brief.md item 3.2's blinding rule)"
    )
    assert re.search(r"\bside\b", text, re.IGNORECASE)


def test_research_prompt_forbids_addressing_a_participant_as_you_or_your():
    text = prompt_text()
    assert re.search(r"never\s+use[^.\n]*\byou\b", text, re.IGNORECASE) or re.search(
        r"never\s+use[^.\n]*\byour\b", text, re.IGNORECASE
    ), "expected an explicit rule against addressing a participant as 'you'/'your' (same style rule the Intervenor's acts already follow)"


def test_research_prompt_forbids_naming_a_participant_by_their_label():
    text = prompt_text()
    assert re.search(r"participant\s+a|participant\s+b", text, re.IGNORECASE), (
        "expected the prompt to explicitly forbid the literal labels 'Participant A'/'Participant B' as a name "
        "for anyone, the same way it must never use them (only mentioning them to forbid them is fine; producing "
        "them as an identifier for the model to use would not be)"
    )


def test_research_prompt_contains_no_real_username_or_email_shaped_string():
    """A prompt file is static template text with no rendered data, so no test-fixture-shaped username/email should
    ever appear in it (a sanity net against a copy-pasted example creeping in)."""
    text = prompt_text()
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text), "an email-shaped string should not appear in a prompt file"
    for forbidden in ("quillfeather", "brambleton", "workeruser"):
        assert forbidden not in text.lower()


def test_research_prompt_does_not_hardcode_bare_letter_labels_as_examples():
    """The prompt may say "Participant A"/"Participant B" only to forbid them (checked above); it must not also use
    a bare quoted single-letter label ("A" or "B") anywhere as a stand-in identifier."""
    text = prompt_text()
    assert not re.search(r'["\'`]A["\'`]\s*(,|or)\s*["\'`]B["\'`]', text)


# --- The output contract: the two ResearchNote fields, named, no third field invented --------------------------------

def test_research_prompt_names_both_output_fields():
    text = prompt_text()
    assert "text" in text and "confidence" in text


def test_research_prompt_does_not_ask_the_model_to_report_its_own_sources():
    """docs/step20b_brief.md item 3: "Do NOT ask the model to self-report which sources it used -- item 4 computes
    that from the real API response, not from the model's own claim." A `sources` field must not be asked for."""
    text = prompt_text()
    assert not re.search(r"`sources`|\bsources\s+field\b", text, re.IGNORECASE)


@pytest.mark.parametrize("dimension_or_act", ["not_needs_verification", "offer_research", "possible_factual_error"])
def test_research_prompt_does_not_leak_internal_pipeline_identifiers(dimension_or_act):
    """The research prompt is a standalone component (docs/step20b_brief.md item 3: "much smaller [than the
    pipeline] -- one call, not two, and no item-level issue/act validation"); it has no business naming the main
    pipeline's own internal identifiers, which would be meaningless (and confusing) to this smaller agent."""
    assert dimension_or_act not in prompt_text()
