"""Wave 16 item 7 (docs/wave16_brief.md): five verbatim sentence deletions and one insertion on
forum/templates/forum/how_it_works.html. Checked logged in and logged out, against the exact remaining wording so a
leftover double space, dangling conjunction or orphaned punctuation from a sloppy deletion would be caught."""
import re

from django.conf import settings
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


def test_none_of_the_removed_sentences_or_phrases_appear():
    for label, response in pages().items():
        text = H.doc(response).text()
        for phrase in REMOVED:
            assert phrase not in text, f"{label}: {phrase!r} still present"


def test_the_page_has_no_bracket_characters_left():
    for label, response in pages().items():
        text = H.doc(response).text()
        assert "[" not in text and "]" not in text, label


def test_by_the_participants_appears_exactly_once_in_the_ending_a_conversation_paragraph():
    for label, response in pages().items():
        root = H.doc(response)
        text = root.text()
        assert text.count("by the participants") == 1, label
        para = para_with(root, "Either of you can end a conversation")
        assert "by the participants" in quotes_straight(para.raw_text())


def test_the_ending_a_conversation_paragraph_reads_exactly_as_edited():
    expected = (
        "Either of you can end a conversation at any time. That closes it for both of you: it stays readable by "
        "the participants, but nobody can post in it, and it is kept for the research record."
    )
    for label, response in pages().items():
        root = H.doc(response)
        para = para_with(root, "Either of you can end a conversation")
        raw = quotes_straight(para.raw_text())
        assert "  " not in raw, f"{label}: leftover double space"
        assert raw.strip() == expected, label


def test_the_two_position_paragraph_reads_exactly_as_edited_with_no_moderator_sentence():
    expected = (
        "Every conversation is between two opposing positions. The home page lists positions that someone holds "
        "and is waiting for someone to disagree with. Join one to take the other side, or start a discussion of "
        "your own with your position or one of the suggested topics. Conversations are private to their two "
        "participants."
    )
    for label, response in pages().items():
        root = H.doc(response)
        para = para_with(root, "Every conversation is between two opposing positions")
        raw = quotes_straight(para.raw_text())
        assert "  " not in raw, f"{label}: leftover double space"
        assert raw.strip() == expected, label


def test_the_propositions_paragraph_ends_cleanly_after_the_character_limit_with_no_admin_sentence():
    expected = (
        f"Anyone with an account can propose a statement to discuss. It appears on the home page straight away; "
        f"nobody at the site reviews it first, and listing one is not an endorsement. You can create up to "
        f"{settings.MAX_PROPOSITIONS_PER_USER_PER_DAY} a day, and each can be up to "
        f"{settings.MAX_PROPOSITION_CHARS} characters."
    )
    for label, response in pages().items():
        root = H.doc(response)
        para = para_with(root, "Anyone with an account can propose")
        raw = quotes_straight(para.raw_text())
        assert "  " not in raw, f"{label}: leftover double space"
        assert raw.strip() == expected, label


def test_the_moderator_problems_paragraph_ends_after_deciding_whether_to_say_anything():
    expected = (
        "After each message it looks for problems such as a factual error, abusive language, flooding the "
        "conversation, or an unanswered question, and decides whether to say anything."
    )
    for label, response in pages().items():
        root = H.doc(response)
        para = para_with(root, "After each message it looks for problems")
        raw = quotes_straight(para.raw_text())
        assert "  " not in raw, f"{label}: leftover double space"
        assert raw.strip() == expected, label


def test_the_not_a_judge_paragraph_keeps_its_second_sentence_and_drops_the_neutrality_one():
    expected = "It is not a judge and it can be wrong. If you think a note is mistaken, say so in the conversation."
    for label, response in pages().items():
        root = H.doc(response)
        para = para_with(root, "It is not a judge and it can be wrong")
        raw = quotes_straight(para.raw_text())
        assert "  " not in raw, f"{label}: leftover double space"
        assert raw.strip() == expected, label


def test_the_what_is_recorded_paragraph_has_no_trailing_bracketed_placeholder():
    for label, response in pages().items():
        root = H.doc(response)
        para = para_with(root, "so the research team can study how moderation treats different viewpoints")
        raw = quotes_straight(para.raw_text())
        assert "[" not in raw and "]" not in raw
        assert raw.rstrip().endswith("different viewpoints."), label


def test_no_dangling_conjunction_or_orphaned_punctuation_in_the_edited_paragraphs():
    """A generic sweep of the six touched paragraphs for the classic signs of a sloppy sentence deletion."""
    needles = [
        "Either of you can end a conversation", "Every conversation is between two opposing positions",
        "Anyone with an account can propose", "After each message it looks for problems",
        "It is not a judge and it can be wrong", "so the research team can study how moderation treats different viewpoints",
    ]
    for label, response in pages().items():
        root = H.doc(response)
        for needle in needles:
            raw = quotes_straight(para_with(root, needle).raw_text()).strip()
            assert not re.search(r"\band\s*[.,]", raw), f"{label}: dangling 'and' in {raw!r}"
            assert not re.search(r"\s[.,;:]", raw), f"{label}: orphaned punctuation in {raw!r}"
            assert raw.endswith(('.', ':')), f"{label}: paragraph does not end in a sentence: {raw!r}"
