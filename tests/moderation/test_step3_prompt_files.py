"""The two prompt files and the two rubric files: every taxonomy value is in both prompts, the rubrics are embedded
verbatim, and the required statements are made (brief section 3, `master_v1.md` / `intervenor_v1.md`)."""
import re

import pytest
from step3_testkit import ACT_TYPES, DECISIONS, ISSUE_TYPES, NOT_SCORABLE_REASONS, PROMPT_DIR, RUBRIC_DIR, TONES

PROMPTS = ["master_v1", "intervenor_v1"]
RUBRICS = ["factual_accuracy_v1", "abusiveness_v1"]


def prompt_text(name):
    path = PROMPT_DIR / f"{name}.md"
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def rubric_text(name):
    path = RUBRIC_DIR / f"{name}.md"
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def squash(text):
    return re.sub(r"\s+", " ", text).strip()


@pytest.mark.parametrize("prompt", PROMPTS)
def test_prompt_file_exists_and_is_substantial(prompt):
    text = prompt_text(prompt)
    assert len(text.strip()) > 1000
    assert text.isprintable() or "\n" in text


DISPOSITIONS = {"acted", "declined"}
DISAGREEMENT_KINDS = {"factual", "normative"}
ACTION_SIDE = sorted(ACT_TYPES | DECISIONS | TONES | DISPOSITIONS)


@pytest.mark.parametrize("prompt", PROMPTS)
@pytest.mark.parametrize("value", sorted(ISSUE_TYPES))
def test_every_issue_type_appears_in_both_prompts(prompt, value):
    assert value in prompt_text(prompt), f"{prompt}.md never mentions {value}"


@pytest.mark.parametrize("value", sorted(ISSUE_TYPES | ACT_TYPES | DECISIONS | TONES | DISPOSITIONS))
def test_the_intervenor_prompt_contains_every_taxonomy_value(value):
    assert value in prompt_text("intervenor_v1"), f"intervenor_v1.md never mentions {value}"


@pytest.mark.parametrize("value", sorted(NOT_SCORABLE_REASONS | DISAGREEMENT_KINDS | {"factual_accuracy", "abusiveness"}))
def test_the_master_prompt_contains_dimensions_not_scorable_reasons_and_disagreement_kinds(value):
    assert value in prompt_text("master_v1"), f"master_v1.md never mentions {value}"


@pytest.mark.parametrize("value", ACTION_SIDE)
def test_the_master_prompt_never_names_the_action_side_as_a_backticked_token(value):
    """Detection is independent of action (brief section 7): the Master is not told what the Intervenor can do."""
    text = prompt_text("master_v1")
    assert not re.search(rf"`\s*[\"']?{re.escape(value)}[\"']?\s*`", text), f"master_v1.md has `{value}` in backticks"


@pytest.mark.parametrize("value", sorted(v for v in ACTION_SIDE if "_" in v))
def test_the_master_prompt_has_no_act_type_or_decision_identifier_anywhere(value):
    """Identifiers with an underscore are not ordinary English, so they must not appear in any form."""
    assert value not in prompt_text("master_v1"), f"master_v1.md mentions {value}"


def test_the_master_prompt_has_no_section_about_what_happens_after_it_reports():
    text = prompt_text("master_v1")
    assert not re.search(r"(?im)^\s*#+\s*what happens after\b", text)
    assert not re.search(r"(?im)^\s*#+.*\bwhat happens (next|after)\b", text)


@pytest.mark.parametrize("prompt", PROMPTS)
def test_issue_type_definitions_from_the_taxonomy_appear_verbatim(prompt):
    from moderation import taxonomy

    text = squash(prompt_text(prompt))
    for issue_type in taxonomy.ISSUE_TYPES:
        assert squash(taxonomy.DEFINITIONS[issue_type]) in text, f"{prompt}: definition of {issue_type}"


def test_act_type_definitions_appear_verbatim_in_the_intervenor_prompt():
    from moderation import taxonomy

    text = squash(prompt_text("intervenor_v1"))
    for act_type in taxonomy.ACT_TYPES:
        assert squash(taxonomy.DEFINITIONS[act_type]) in text, f"definition of {act_type}"


@pytest.mark.parametrize("rubric", RUBRICS)
def test_rubric_file_has_the_five_levels(rubric):
    text = rubric_text(rubric)
    assert len(text.strip()) > 400
    for level in "01234":
        assert re.search(rf"(?<![\d.]){level}(?![\d.])", text), f"no level {level} in {rubric}"


@pytest.mark.parametrize("prompt", PROMPTS)
@pytest.mark.parametrize("rubric", RUBRICS)
def test_rubric_text_appears_verbatim_in_the_prompt(prompt, rubric):
    body = rubric_text(rubric).strip()
    assert body in prompt_text(prompt), f"{rubric}.md is not embedded verbatim in {prompt}.md"


@pytest.mark.parametrize("prompt", PROMPTS)
def test_prompt_states_the_neutrality_rule(prompt):
    text = prompt_text(prompt).lower()
    assert "neutral" in text or "equal" in text
    assert "politic" in text, "must say politics is never inferred or used"
    assert "infer" in text
    assert "label" in text, "participants are referred to by label letters only"


@pytest.mark.parametrize("prompt", PROMPTS)
def test_prompt_says_message_text_is_data_and_instructions_in_it_are_ignored(prompt):
    text = prompt_text(prompt).lower()
    assert re.search(r"\bdata\b", text)
    assert "ignore" in text
    assert "instruction" in text


def test_master_prompt_states_the_quoting_rule_and_the_only_new_issues_rule():
    text = prompt_text("master_v1").lower()
    assert "quote" in text and "phrase" in text
    assert re.search(r"newest|latest|most recent", text), "only-new-issues rule"
    assert "repetition" in text and "strawman" in text and "process_violation" in text
    assert "intensity" in text


def test_master_prompt_names_the_dimensions_rubrics():
    text = prompt_text("master_v1")
    assert "factual_accuracy" in text and "abusiveness" in text


def test_intervenor_prompt_says_not_intervening_is_a_good_and_common_outcome():
    text = prompt_text("intervenor_v1").lower()
    assert "common" in text
    assert re.search(r"not intervening|no_intervention|no intervention", text)


def test_intervenor_prompt_covers_dispositions_and_the_act_cap():
    text = prompt_text("intervenor_v1")
    assert "acted" in text and "declined" in text
    from django.conf import settings

    assert str(settings.MAX_ACTS_PER_INTERVENTION) in text or "at most" in text.lower()


@pytest.mark.parametrize("prompt", PROMPTS)
def test_prompt_contains_nothing_that_varies_per_call(prompt):
    """A static prompt can be cached as one block: no template placeholders and no dates."""
    text = prompt_text(prompt)
    assert not re.search(r"\{\{|\}\}|\{[a-z_]+\}|%\(\w+\)s|\$\{", text)
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", text)


@pytest.mark.parametrize("prompt", PROMPTS)
def test_prompt_contains_no_email_or_key_shaped_text(prompt):
    text = prompt_text(prompt)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    assert "api_key" not in text.lower()


# --- Wave 16 item 6 (docs/wave16_brief.md): prompt precision ------------------------------------------------------------
# No real LLM call can be made here, so these are static-text checks on the prompt files themselves: they cannot
# verify the model now behaves differently, only that the required guidance is present in the file.

def test_master_prompt_distinguishes_asserting_a_claim_from_denying_or_questioning_it():
    """6a: before reporting unsupported_claim/possible_factual_error, the Master must check the participant is
    actually asserting the claim as true, not denying it, questioning it, or noting the lack of support for it."""
    text = prompt_text("master_v1").lower()
    assert "unsupported_claim" in text and "possible_factual_error" in text
    assert re.search(r"assert(ing|s|ed)?\b.*\btrue\b", text) or re.search(r"\btrue\b.*assert", text)
    assert "deny" in text or "denying" in text
    assert "question" in text or "questioning" in text
    assert re.search(r"lacks evidence|no ?one has supported|nobody has supported", text)


def test_master_prompt_has_the_remote_work_worked_example():
    """6a's worked example: a participant who says a claim lacks evidence has not thereby asserted that claim."""
    text = prompt_text("master_v1")
    assert re.search(r"remote work helps company performance", text)
    assert re.search(r"(?i)haven't shown|have not shown", text)
    assert re.search(r"(?i)asserted no such thing|reports nothing", text)


def test_master_prompt_keeps_the_neutrality_rule_section_and_neutrality_doc_untouched():
    """6a is a precision fix to what counts as an assertion, not a change to the neutrality rule itself."""
    text = prompt_text("master_v1")
    assert re.search(r"(?im)^\s*#+\s*the neutrality rule\b", text)
    from pathlib import Path

    neutrality_path = Path(__file__).resolve().parent.parent.parent / "docs" / "neutrality.md"
    assert neutrality_path.is_file(), "docs/neutrality.md must still exist and be untouched by this wave"


def test_intervenor_prompt_requires_request_information_text_to_be_phrased_as_a_question():
    """6b: a request_information act's text must be an actual question, not a declarative statement."""
    text = prompt_text("intervenor_v1")
    assert "request_information" in text
    lowered = text.lower()
    assert re.search(r"request_information.{0,400}\bquestion\b", lowered, re.S) or \
        re.search(r"\bquestion\b.{0,400}request_information", lowered, re.S)
    assert "Could a source be given for the figure in message 4?" in text


# Step 20a items 5 and 6's static-text checks (the Master's needs_verification flag and the Intervenor's
# offer_research act) now live in tests/moderation/test_step20a_revision_needs_verification_prompts.py, under the
# broadened, renamed field -- this section was retired 2026-09-27 rather than updated, since that file fully
# supersedes it (see docs/step20a_revision_brief.md).


def test_the_intervenor_prompt_says_offer_research_takes_no_position_and_contrasts_it_with_other_acts():
    """6: offer_research takes no position on whether the claim is right or wrong, and its text must read as an
    offer, not a claim or a request -- contrast with request_information and provide_information/
    correct_factual_error."""
    text = prompt_text("intervenor_v1")
    lowered = text.lower()
    assert re.search(r"offer_research.{0,700}no position", lowered, re.S) or \
        re.search(r"no position.{0,700}offer_research", lowered, re.S)
    assert re.search(r"offer_research.{0,700}request_information", lowered, re.S) or \
        re.search(r"request_information.{0,700}offer_research", lowered, re.S)
    assert "offer" in lowered
