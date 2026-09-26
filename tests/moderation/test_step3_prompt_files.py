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
