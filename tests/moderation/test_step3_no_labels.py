"""Users never see the A/B labels (owner decision, 2026-09-25): the Intervenor's act text must not name participants. Prompt-file tests assert on distinctive rules; the naming check is
moderation/label_check.py.

Definition used here (the builder's regex is r"\\bParticipants?\\s+[A-Z]\\b" plus a standalone bare or quoted label used as a
name, case-sensitive): "Participant A", "Participant B" and "Participants A and B" are flagged; lower-case "participant a",
ordinary English such as a sentence starting with "A source ..." and "the claim in message 4" are not. Bare or quoted single
letters as names ("B should ...") are NOT tested either way (ambiguous, reported).
"""
import re

import pytest
from step3_testkit import PROMPT_DIR


def prompt(name):
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


# --- (1) prompt files ---------------------------------------------------------------------------------------------------

def test_the_intervenor_prompt_no_longer_tells_the_model_to_name_participants_by_label():
    text = prompt("intervenor_v1")
    assert not re.search(r"Name participants only by their labels", text, re.I)
    assert not re.search(r"name\s+participants[^.\n]*by\s+their\s+labels?", text, re.I)


def lines_with(text, *needles):
    return [l for l in text.splitlines() if all(re.search(n, l, re.I) for n in needles)]


def test_the_intervenor_prompt_forbids_labels_and_names_in_the_act_text():
    text = prompt("intervenor_v1")
    rule = lines_with(text, r"\btext\b", r"Participant\s+[AB]", r"\b(never|must not|do not|may not)\b")
    assert rule, "a rule saying the act `text` never contains 'Participant A/B'"
    assert re.search(r"\bname\b", "\n".join(rule), re.I) or re.search(r"(any|a)\s+name", text, re.I)


def test_the_intervenor_prompt_says_to_refer_to_messages_and_content_with_examples():
    text = prompt("intervenor_v1")
    assert re.search(r"message\s+4", text), "an example such as 'message 4'"
    assert re.search(r"the phrase", text, re.I)
    assert re.search(r"question in message\s+3", text, re.I)


def test_the_intervenor_prompt_forbids_you_your_and_pronouns_about_a_participant():
    text = prompt("intervenor_v1")
    rule = lines_with(text, r"\byou\b", r"\byour\b", r"\b(never|no|not|avoid)\b")
    assert rule, "a rule against 'you' / 'your' about a participant"
    pron = lines_with(text, r"\b(he|she|they)\b", r"\b(never|no|not|avoid)\b")
    assert pron, "a rule against he / she / they about a participant"
    assert re.search(r"impersonal", text, re.I)


def test_the_intervenor_prompt_keeps_the_swap_test_and_the_neutrality_rules():
    text = prompt("intervenor_v1")
    assert re.search(r"swap test", text, re.I)
    assert re.search(r"same (wording|words)", text, re.I) or re.search(r"either side", text, re.I)
    assert "politic" in text.lower() and "infer" in text.lower()


def test_the_intervenor_prompt_still_documents_the_label_values_of_the_structured_fields():
    text = prompt("intervenor_v1")
    addressee = lines_with(text, r"`addressee`")
    subject = lines_with(text, r"`subject`")
    assert addressee and subject
    for value in ("Participant A", "Participant B", "all"):
        assert any(value in l for l in addressee), f"addressee should still list {value!r}"
    for value in ("Participant A", "Participant B", "both", "none"):
        assert any(value in l for l in subject), f"subject should still list {value!r}"


def test_the_intervenor_prompt_says_labels_are_for_the_structured_fields_only():
    text = prompt("intervenor_v1")
    assert re.search(r"labels?[^.\n]*(structured|fields?)[^.\n]*(only)|only[^.\n]*(structured|fields?)", text, re.I) or lines_with(
        text, r"\blabels?\b", r"\bonly\b", r"\b(fields?|addressee|subject)\b"
    )


def test_the_master_prompt_is_unchanged_in_behavior_it_still_knows_participants_by_label():
    text = prompt("master_v1")
    assert "Participant A" in text and "Participant B" in text
    assert not re.search(r"act\s+`?text`?[^.\n]*never", text, re.I), "the no-label rule belongs to the Intervenor's output only"


# --- pinned rulings on what counts as naming a label (architect, 2026-09-25) --------------------------------------------------
# Flag ONLY (a) "Participant"/"Participants" + whitespace + an uppercase letter A-Z and (b) an uppercase letter + 's + one of
# message(s), reply/replies, claim(s), point(s), statement(s), argument(s), answer(s), question(s).

FLAGGED = [
    "Participant A", "Participant B, please cite a source.", "Participants A and B should keep to the topic.", "Both Participant B and Participant A agree.",
    "Participant Z", "Participant Alpha", "Participant Ann", "Participants\nB", "Participant  B", "Note for Participant A: keep it short.",
    "B's message needs a source.", "A's claim is unsupported.", "The B's replies were short.", "B's questions are fair.", "A's answer is missing.",
    "See B's point.", "A's statement and B's arguments differ.", "B's messages", "B's message.",
    # (c) viewer-relative references (the insulted reader would read "the other participant" as themselves)
    "This comment is directed at the other participant.", "Please respond to the other person.", "The other side raised a fair point.",
    "Another participant already answered.", "the other party", "Another person asked this.", "Directed at The other participant.",
]
NOT_FLAGGED = [
    "participant b", "participant B", "Participant", "Participants and moderators", "Participant 4",
    "Plan B.", "Plan B is fine.", "Option A is fine.", "Vitamin B, and C.", "Section A: scope.", "A source would help.", "A cap on annual increases",
    "Please give a source for the claim in message 4.", "The question in message 3 has not been answered.",
    'The letter "B" is unclear.', "'A' is a variable here.", "(A) is the first option.", "[B] marks the second.", "\u201cB\u201d",
    "participants in general are welcome", "the other message", "another point", "the other day", "Another message", "the other posts",
    "another participation", "the other personality", "the other sidewalk", "another personal insult",
    "b's message", "B's own view", "A's and B's", "the value of A's", "Bs message", "AB's message", "Type B's", "B'sX message",
]


def names_label(text):
    from moderation.label_check import names_a_label

    return names_a_label(text)


@pytest.mark.parametrize("text", FLAGGED)
def test_these_texts_are_flagged_as_naming_a_label(text):
    assert names_label(text) is True, text


@pytest.mark.parametrize("text", NOT_FLAGGED)
def test_these_texts_are_not_flagged(text):
    assert names_label(text) is False, text


def test_the_check_is_case_sensitive_on_the_letter_and_the_word_participant_must_be_capitalised_form_or_plural():
    assert names_label("Participant B") and not names_label("Participant b") and not names_label("participant B")
    assert names_label("B's reply") and not names_label("b's reply")


def test_a_label_inside_a_longer_text_is_found_anywhere_in_it():
    text = "Possible factual error in message 4: the figure is far above published estimates. Participant B may wish to cite a source."
    assert names_label(text)
    assert not names_label(text.replace("Participant B may wish", "The author may wish"))


# --- (c) "the other participant" style references and the new prompt rule ---------------------------------------------------------

@pytest.mark.parametrize("text", ["the other participant", "The other person", "The other side", "the other party", "Another participant", "another person"])
def test_viewer_relative_references_flag_in_either_capitalisation_of_the_first_letter(text):
    assert names_label(text) is True, text


@pytest.mark.parametrize("text", ["THE OTHER PARTICIPANT", "The Other Participant", "ANOTHER PARTICIPANT", "another party", "another side", "a participant"])
def test_only_the_ruled_forms_of_the_viewer_relative_pattern_flag(text):
    """Case-sensitive except for the first letter of "The"/"Another"; "another party/side" and a bare "a participant" are not in
    the report-only regex (the prompt still forbids them)."""
    assert names_label(text) is False, text


def test_the_impersonal_example_is_clean():
    assert names_label("Message 4 contains a personal insult.") is False


def test_the_intervenor_prompt_forbids_the_other_participant_style_references():
    text = prompt("intervenor_v1")
    for phrase in ("the other participant", "the other person", "the other side", "the other party", "another participant", "a participant"):
        assert phrase in text, f"the prompt must name {phrase!r} as forbidden"
    forbid = lines_with(text, r"the other participant", r"\b(never|must not|do not|not|no)\b")
    assert forbid, "the forbidding rule"


def test_the_intervenor_prompt_forbids_saying_who_a_comment_is_directed_at_or_written_by():
    text = prompt("intervenor_v1")
    assert re.search(r"directed at", text, re.I)
    assert re.search(r"written by", text, re.I)
    rule = lines_with(text, r"directed at", r"written by")
    assert rule and re.search(r"\b(never|must not|do not|not|no)\b", rule[0], re.I), rule


def test_the_intervenor_prompt_gives_the_impersonal_insult_example():
    assert "Message 4 contains a personal insult." in prompt("intervenor_v1")


def test_the_master_prompt_does_not_carry_the_post_wording_rules():
    text = prompt("master_v1")
    assert "Message 4 contains a personal insult." not in text
    assert not re.search(r"directed at", text, re.I)
