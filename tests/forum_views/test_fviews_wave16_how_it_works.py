"""forum/templates/forum/how_it_works.html, pinned verbatim.

Wave 16 item 7 (docs/wave16_brief.md) made five verbatim sentence deletions and one insertion on this page. The owner
then reworded the whole page (commit 5484f1c): it is now four short sections with no numbers, no Limits section and no
Pauses section. These tests pin that final wording, logged in and logged out, against the exact text so a leftover
double space, dangling conjunction or orphaned punctuation would be caught, and they keep the wave 16 absences."""
import re

from django.test import Client
from django.urls import reverse

import fviews_html as H
import fviews_kit as K

REMOVED = [
    "If a proposition is abusive, tell the site admin",
    "The AI moderator sees only the topic as a neutral statement and does not know who holds which side.",
    "You can block anyone: blocking ends any conversation you share",
    "people waiting to discuss are shown by username on the home page",
    "It can only reply to a person's message, never to its own, and it posts at most once per message.",
    "It is instructed to treat both sides of an argument the same way and never to take a side.",
]


def pages():
    return {"anonymous": Client().get(reverse("forum:how_it_works")),
            "logged in": K.client_for(K.make_user()).get(reverse("forum:how_it_works"))}


def para_with(root, needle):
    found = [p for p in root.find_all("p") if needle in p.text()]
    assert len(found) == 1, f"expected exactly one <p> containing {needle!r}, found {len(found)}"
    return found[0]


def quotes_straight(text):
    text = H.unescape(text)
    for curly, straight in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"')):
        text = text.replace(curly, straight)
    return text


def prose_sections(root):
    return [s for s in root.find("main").find_all("section")]


def assert_paragraph_reads_exactly(needle, expected):
    for label, response in pages().items():
        root = H.doc(response)
        para = para_with(root, needle)
        raw = quotes_straight(para.raw_text())
        assert "  " not in raw, f"{label}: leftover double space"
        assert raw.strip() == expected, label


# --- absences ---------------------------------------------------------------------------------------------------------

def test_none_of_the_removed_sentences_or_phrases_appear():
    for label, response in pages().items():
        text = H.doc(response).text()
        for phrase in REMOVED:
            assert phrase not in text, f"{label}: {phrase!r} still present"


def test_none_of_the_wording_the_owner_reworded_away_appears():
    for label, response in pages().items():
        text = H.page_norm(response)
        for phrase in K.HOW_REMOVED_WORDING:
            assert phrase not in text, f"{label}: {phrase!r} still present"


def test_the_page_has_no_bracket_characters_left():
    for label, response in pages().items():
        text = H.doc(response).text()
        assert "[" not in text and "]" not in text, label


def test_the_page_has_no_limits_section_and_no_pauses_section():
    for label, response in pages().items():
        root = H.doc(response)
        assert root.find_all(id="limits") == [], f"{label}: the #limits section is back"
        headings = [h.text().strip() for h in root.find("main").find_all("h2")]
        for removed in K.HOW_REMOVED_HEADINGS:
            assert removed not in headings, f"{label}: the {removed!r} section is back"
        assert root.find("main").find_all("ul") == [], f"{label}: the limits list is back"
        assert root.find("main").find_all("li") == [], f"{label}: the limits list is back"


def test_the_page_content_shows_no_digits_at_all():
    for label, response in pages().items():
        main = H.doc(response).find("main").text()
        assert re.findall(r"\d", main) == [], f"{label}: the page shows a number: {main!r}"


# --- structure --------------------------------------------------------------------------------------------------------

def test_the_page_is_exactly_the_four_sections_in_order():
    for label, response in pages().items():
        root = H.doc(response)
        main = root.find("main")
        assert [h.text().strip() for h in main.find_all("h1")] == ["How this works"], label
        assert [h.text().strip() for h in main.find_all("h2")] == K.HOW_SECTIONS, label
        sections = prose_sections(root)
        assert len(sections) == 4, label
        assert [[h.text().strip() for h in s.find_all("h2")] for s in sections] == [[name] for name in K.HOW_SECTIONS], label


def test_each_section_holds_exactly_its_paragraphs_verbatim():
    for label, response in pages().items():
        sections = prose_sections(H.doc(response))
        found = {s.find("h2").text().strip(): [quotes_straight(p.raw_text()).strip() for p in s.find_all("p")]
                 for s in sections}
        assert found == K.HOW_PARAGRAPHS, label


def test_the_only_paragraph_outside_the_sections_is_the_back_to_home_link():
    for label, response in pages().items():
        main = H.doc(response).find("main")
        outside = [p for p in main.find_all("p") if not [a for a in p.ancestors() if a.tag == "section"]]
        assert [p.text().strip() for p in outside] == ["Back to home"], label
        assert len(main.find_all("p")) == 7, label


# --- each paragraph, verbatim ------------------------------------------------------------------------------------------

def test_the_what_this_site_is_paragraph_reads_exactly_as_reworded_with_no_research_project_sentence():
    expected = (
        "Two people discuss a proposition in writing. An AI moderator reads each message and may add contributions "
        "of its own."
    )
    assert expected == K.HOW_SITE
    assert_paragraph_reads_exactly("Two people discuss a proposition in writing", expected)
    for label, response in pages().items():
        assert "research project" not in H.doc(response).text(), label


def test_the_get_started_paragraph_reads_exactly_as_written_and_replaces_the_propositions_paragraph():
    expected = "Get started by choosing a proposition to discuss or by creating one of your own."
    assert expected == K.HOW_GET_STARTED
    assert_paragraph_reads_exactly("Get started by choosing a proposition", expected)
    for label, response in pages().items():
        text = H.doc(response).text()
        assert "Anyone with an account can propose" not in text, label
        assert "tell the site admin" not in text, label
        assert "characters" not in text, label


def test_by_the_participants_appears_exactly_once_in_the_ending_a_conversation_paragraph():
    for label, response in pages().items():
        root = H.doc(response)
        text = root.text()
        assert text.count("by the participants") == 1, label
        para = para_with(root, "Either participant can end a conversation")
        assert "by the participants" in quotes_straight(para.raw_text())


def test_the_ending_a_conversation_paragraph_reads_exactly_as_reworded():
    expected = (
        "Either participant can end a conversation at any time. That closes it for both of you: it stays readable by "
        "the participants, but nobody can post in it. It is kept for the research record."
    )
    assert expected == K.HOW_ENDING
    assert_paragraph_reads_exactly("Either participant can end a conversation", expected)


def test_the_two_position_paragraph_is_gone_and_no_moderator_sees_only_the_topic_sentence_remains():
    for label, response in pages().items():
        root = H.doc(response)
        text = H.page_norm(response)
        assert [p for p in root.find_all("p") if "two opposing positions" in p.text()] == [], label
        assert K.HOW_PARAGRAPH_REMOVED not in text, label
        for sentence in ("Every conversation is between two opposing positions.",
                         "The home page lists positions that someone holds",
                         "Join one to take the other side",
                         "Conversations are private to their two participants.",
                         "The AI moderator sees only the topic"):
            assert sentence not in text, f"{label}: {sentence!r}"


def test_the_moderator_problems_paragraph_reads_exactly_as_reworded_and_ends_after_deciding_whether_to_jump_in():
    expected = (
        "After each message, the AI moderator looks for problems such as factual errors, unclear statements, and "
        "abusive language and decides whether to jump in."
    )
    assert expected == K.HOW_MODERATOR_PROBLEMS
    assert_paragraph_reads_exactly("the AI moderator looks for problems", expected)
    for label, response in pages().items():
        text = H.doc(response).text()
        assert "such as a factual" not in text, f"{label}: the 'a factual errors' typo is back"
        assert "It can only reply to a person's message" not in H.page_norm(response), label


def test_the_not_a_judge_paragraph_keeps_its_second_sentence_and_has_no_neutrality_one():
    expected = (
        "The moderator is not a judge, and it can be wrong. If you think a contribution by the moderator is mistaken, "
        "feel free to say so in the conversation."
    )
    assert expected == K.HOW_NOT_A_JUDGE
    assert_paragraph_reads_exactly("The moderator is not a judge", expected)
    for label, response in pages().items():
        text = H.page_norm(response)
        assert "instructed to treat both sides" not in text, label
        assert "never to take a side" not in text, label


def test_the_what_is_recorded_paragraph_reads_exactly_as_reworded_with_no_trailing_bracketed_placeholder():
    expected = "Your messages and the moderator's analysis and replies are stored for the research team's possible review."
    assert expected == K.HOW_RECORDED
    assert_paragraph_reads_exactly("are stored for the research team's possible review", expected)
    for label, response in pages().items():
        root = H.doc(response)
        para = para_with(root, "are stored for the research team")
        raw = quotes_straight(para.raw_text())
        assert "[" not in raw and "]" not in raw
        assert raw.rstrip().endswith("possible review."), label


def test_no_dangling_conjunction_or_orphaned_punctuation_in_any_paragraph():
    """A generic sweep of the six paragraphs for the classic signs of a sloppy sentence deletion."""
    needles = [
        "Two people discuss a proposition in writing", "Get started by choosing a proposition",
        "Either participant can end a conversation", "the AI moderator looks for problems",
        "The moderator is not a judge", "are stored for the research team",
    ]
    for label, response in pages().items():
        root = H.doc(response)
        for needle in needles:
            raw = quotes_straight(para_with(root, needle).raw_text()).strip()
            assert not re.search(r"\band\s*[.,]", raw), f"{label}: dangling 'and' in {raw!r}"
            assert not re.search(r"\s[.,;:]", raw), f"{label}: orphaned punctuation in {raw!r}"
            assert raw.endswith("."), f"{label}: paragraph does not end in a sentence: {raw!r}"
