"""Front-end fixes 2, fix B helper (docs/frontend_fixes_brief2.md): `moderation.references.renumber_message_references`.
Pure function, no database. Written from the contract."""
import pytest

from moderation.references import renumber_message_references as renum

M = {38: 4, 41: 5, 7: 3}


@pytest.mark.parametrize(
    "text,mapping,expected",
    [
        # the word, any case, single number
        ("message 38", M, "message 4"),
        ("Message 38 is wrong.", M, "Message 4 is wrong."),
        ("MESSAGE 38", M, "MESSAGE 4"),
        ("mEsSaGe 38", M, "mEsSaGe 4"),
        ("In message 38, the claim.", M, "In message 4, the claim."),
        ("messages 38", M, "messages 4"),
        ("Messages 38", M, "Messages 4"),
        ("MESSAGES 38", M, "MESSAGES 4"),
        # spacing and hash
        ("message38", M, "message4"),
        ("message  38", M, "message  4"),
        ("message #38", M, "message #4"),
        ("message#38", M, "message#4"),
        ("Message #38", M, "Message #4"),
        ("messages #38", M, "messages #4"),
        # lists
        ("messages 38 and 41", M, "messages 4 and 5"),
        ("messages 38 or 41", M, "messages 4 or 5"),
        ("messages 38 & 41", M, "messages 4 & 5"),
        ("messages 38, 41", M, "messages 4, 5"),
        ("messages 38,41", M, "messages 4,5"),
        ("messages 38, 41 and 7", M, "messages 4, 5 and 3"),
        ("messages 38, 41 or 7", M, "messages 4, 5 or 3"),
        ("messages 38, 7 & 41", M, "messages 4, 3 & 5"),
        ("messages 38 and 41 and 7", M, "messages 4 and 5 and 3"),
        ("Messages 38 and 41 disagree.", M, "Messages 4 and 5 disagree."),
        ("messages 38 and 41.", M, "messages 4 and 5."),
        # a list where only some numbers are keys
        ("messages 38 and 1", M, "messages 4 and 1"),
        ("messages 1 and 38", M, "messages 1 and 4"),
        ("messages 1, 38 and 2", M, "messages 1, 4 and 2"),
        # trailing punctuation still a reference
        ("see message 38.", M, "see message 4."),
        ("see message 38, which", M, "see message 4, which"),
        ("(message 38)", M, "(message 4)"),
        ("message 38; message 41!", M, "message 4; message 5!"),
        ("message 38's claim", M, "message 4's claim"),
        ('"message 38"', M, '"message 4"'),
        ("message 38\nmessage 41", M, "message 4\nmessage 5"),
        ("message 38", {38: 38}, "message 38"),
        ("message 38", {38: 1000}, "message 1000"),
        ("message 38", {38: 7, 7: 38}, "message 7"),
        # several references
        ("message 38 says X; message 41 replies to message 38.", M, "message 4 says X; message 5 replies to message 4."),
        ("message 38 or message 41", M, "message 4 or message 5"),
        ("message 38 and then 41", M, "message 4 and then 41"),
        # negative placeholder
        ("message -1", {-1: 5}, "message 5"),
        ("Message #-1", {-1: 5}, "Message #5"),
        ("messages -1 and 38", {-1: 5, 38: 4}, "messages 5 and 4"),
        ("message -1.", {-1: 12}, "message 12."),
        ("message -1?", {-1: 12}, "message 12?"),
    ],
)
def test_references_are_replaced(text, mapping, expected):
    assert renum(text, mapping) == expected


@pytest.mark.parametrize(
    "text",
    [
        "message 1",  # positional, not a key
        "Message #1 and message 2",
        "messages 1 and 2",
        "message 138",  # contains 38 but is not 38
        "message 380",
        "message 3",  # prefix of nothing
        "message 3-1",
        "message 38-1",
        "message 12abc",
        "message 38abc",
        "message 38x",
        "message -10x",
        "message -38",  # negative of a key is not the key
        "message 38-",
        "message 38-ish",
        "message 38_a",
        "message 3.5",  # fine: "3" is not a key
        "messaged 38",
        "premessage 38",
        "unmessage 38",
        "message_38",
        "messagess 38",
        "the messages about 38",
        "message alone and 38 alone",
        "message # 38x",
        "message",
        "message #",
        "messages",
        "38",
        "On 38 occasions the message was sent",
        "The 38% figure, message-based, 41 times",
        "a-message 38",
        "xmessages 41",
        "message 38abc and 41x",
    ],
)
def test_everything_else_is_untouched(text):
    assert renum(text, M) == text


@pytest.mark.parametrize("text", ["message 38", "message 4", "messages 41", "message 3-1", "nothing here", ""])
def test_empty_map_returns_the_text_unchanged(text):
    assert renum(text, {}) == text


@pytest.mark.parametrize("text", ["", " ", "\n"])
def test_empty_text(text):
    assert renum(text, M) == text


@pytest.mark.parametrize(
    "text",
    ["No numbers at all.", "Both messages disagree about the claim.", "In 2023 there were 38 cases and 41 deaths."],
)
def test_no_references_means_no_change_even_when_numbers_are_keys(text):
    assert renum(text, M) == text


def test_only_the_reference_changes_among_other_digits():
    text = "In 2023, 38 people (and 41%) said so; message 38 and 7 cases."
    # "7 cases" continues the list (a number after "and"), so it is renumbered too; 2023, 38 and 41% are untouched.
    got = renum(text, M)
    assert got.startswith("In 2023, 38 people (and 41%) said so; message 4 and ")
    text2 = "In 2023, 38 people (and 41%) said so; message 38."
    assert renum(text2, M) == "In 2023, 38 people (and 41%) said so; message 4."


def test_the_replacement_is_one_pass_with_no_chaining():
    swap = {4: 2, 2: 1}
    assert renum("messages 4 and 2", swap) == "messages 2 and 1"
    assert renum("message 4", swap) == "message 2"
    assert renum("message 2", swap) == "message 1"
    assert renum("messages 2, 4", swap) == "messages 1, 2"
    assert renum("messages 4, 2 and 4", swap) == "messages 2, 1 and 2"
    assert renum("message 4 or message 2", swap) == "message 2 or message 1"
    assert renum("messages 4 & 2", swap) == "messages 2 & 1"


def test_a_swap_map_is_a_swap_not_a_collapse():
    swap = {1: 2, 2: 1}
    assert renum("messages 1 and 2", swap) == "messages 2 and 1"
    assert renum("messages 1, 2, 1", swap) == "messages 2, 1, 2"


def test_every_number_of_a_list_is_replaced_not_just_the_first_or_last():
    mapping = {10: 1, 20: 2, 30: 3, 40: 4}
    assert renum("messages 10, 20, 30 and 40", mapping) == "messages 1, 2, 3 and 4"
    assert renum("messages 10 & 20 & 30", mapping) == "messages 1 & 2 & 3"
    assert renum("messages 10 or 20 or 30", mapping) == "messages 1 or 2 or 3"


def test_hash_is_kept_on_every_form():
    assert renum("message #38", M) == "message #4"
    assert renum("messages #38 and 41", M) == "messages #4 and 5"


def test_a_positional_citation_survives_when_the_pk_is_big():
    assert renum("message 1 and message 38", {38: 4}) == "message 1 and message 4"
    assert renum("message 1", {38: 4, 39: 5}) == "message 1"


def test_a_number_is_matched_whole_not_as_a_prefix_or_suffix():
    assert renum("message 3", {38: 4, 13: 9}) == "message 3"
    assert renum("message 138", {38: 4, 13: 9}) == "message 138"
    assert renum("message 13", {1: 9, 3: 8}) == "message 13"


def test_only_reference_numbers_change_not_words_around_them():
    assert renum("The message 38 and the Message 41 about message", M) == "The message 4 and the Message 5 about message"


def test_returns_a_str_and_does_not_mutate_the_map():
    m = dict(M)
    out = renum("message 38", m)
    assert isinstance(out, str) and m == M


def test_idempotent_on_identity_mapping():
    ident = {1: 1, 2: 2, 3: 3}
    t = "messages 1, 2 and 3 and message #2"
    assert renum(t, ident) == t


def test_quoted_and_multiline_text_is_handled_per_reference():
    t = 'The user wrote "see message 38".\n\nAnd message 41 said:\nmessages 7 & 38.'
    assert renum(t, M) == 'The user wrote "see message 4".\n\nAnd message 5 said:\nmessages 3 & 4.'


def test_large_text_without_references_is_fast_and_unchanged():
    t = ("lorem 38 ipsum messages " * 2000) + "end"
    assert renum(t, M) == t
