"""post_message: what is created, the rejection codes and their wording, and the order in which the checks run."""
import pytest
from fsvc_testkit import enter  # noqa: E402

from fsvc_testkit import (
    ALL_CODES, assert_plain, counts, make_active, make_active_via_service, make_prop, make_user, make_waiting, orm_moderator_message,
    orm_user_message, rejection, svc,
)  # fmt: skip

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(clock):
    return make_active_via_service()


# --- a successful post ---------------------------------------------------------------------------------------------------


def test_a_post_creates_a_user_message_for_the_posters_participant(world):
    message = svc().post_message(world.u1, world.conv, "hello there friend")
    from forum.models import Message

    stored = Message.objects.get(pk=message.pk)
    assert stored.conversation_id == world.conv.pk
    assert stored.author_type == "user"
    assert stored.participant.user_id == world.u1.pk
    assert stored.seq_no == 1
    assert stored.content == "hello there friend"
    assert stored.char_count == len("hello there friend")


def test_the_message_of_the_second_participant_belongs_to_the_second_participant(world):
    svc().post_message(world.u1, world.conv, "first message here")
    message = svc().post_message(world.u2, world.conv, "reply from the other side")
    assert message.participant.user_id == world.u2.pk
    assert message.seq_no == 2


def test_sequence_numbers_run_one_two_three_across_both_people(world, clock):
    seqs = []
    for i in range(6):
        clock.advance(31)
        who = world.u1 if i % 2 == 0 else world.u2
        seqs.append(svc().post_message(who, world.conv, f"message number {i}").seq_no)
    assert seqs == [1, 2, 3, 4, 5, 6]


def test_the_stored_text_is_the_normalised_text_so_its_length_is_the_char_count(world):
    message = svc().post_message(world.u1, world.conv, "  spaced out \r\n text  \n")
    assert message.content == "spaced out \n text"  # CRLF became LF, outer whitespace gone (architect ruling)
    from forum.limits import count_message_chars

    assert message.char_count == count_message_chars("  spaced out \r\n text  \n") == len(message.content)


# --- not a participant ---------------------------------------------------------------------------------------------------


def test_a_stranger_cannot_post(world):
    before = counts()
    exc = rejection(svc().post_message, make_user(), world.conv, "hello there friend")
    assert exc.code == "not_participant"
    assert "You are not a participant in this conversation." in exc.message
    assert_plain(exc)
    assert counts() == before


def test_a_participant_of_another_conversation_cannot_post_here(world):
    other = make_active_via_service()
    assert rejection(svc().post_message, other.u1, world.conv, "hello there friend").code == "not_participant"


def test_the_creator_of_a_waiting_conversation_is_a_participant_but_a_stranger_is_not():
    conv = make_waiting()
    assert rejection(svc().post_message, make_user(), conv, "hello there friend").code == "not_participant"


# --- waiting -------------------------------------------------------------------------------------------------------------


def test_the_first_person_can_post_while_waiting_for_a_second():
    """Step 7c: the `waiting` refusal is gone; the creator posts in a waiting conversation like anyone else."""
    user = make_user()
    conv = enter(user, make_prop())
    message = svc().post_message(user, conv, "hello there friend")
    assert message.seq_no == 1 and message.participant.user_id == user.pk
    conv.refresh_from_db()
    assert conv.status == "open"  # still waiting; posting does not make it active


def test_a_second_person_joining_can_answer_at_once_and_the_first_persons_clock_continues(clock):
    world = make_active_via_service()
    third = make_user()
    waiting = enter(third, world.topic)
    svc().post_message(third, waiting, "hello there friend")
    clock.advance(10)
    fourth = make_user()
    joined = enter(fourth, world.topic)
    assert joined.pk == waiting.pk
    assert svc().post_message(fourth, joined, "an answer from the joiner").seq_no == 2
    assert rejection(svc().post_message, third, joined, "and again").code == "too_fast"


# --- closed --------------------------------------------------------------------------------------------------------------


def test_posting_in_a_closed_conversation_is_refused(world):
    svc().end_conversation(world.u1, world.conv)
    before = counts()
    exc = rejection(svc().post_message, world.u2, world.conv, "hello there friend")
    assert exc.code == "closed"
    assert "This conversation is closed." in exc.message
    assert_plain(exc)
    assert counts() == before


def test_the_closed_message_says_who_ended_it_for_each_viewer(world):
    svc().end_conversation(world.u1, world.conv)
    by_ender = rejection(svc().post_message, world.u1, world.conv, "hello there friend")
    by_other = rejection(svc().post_message, world.u2, world.conv, "hello there friend")
    assert "You ended this conversation." in by_ender.message
    assert "The other participant ended this conversation." not in by_ender.message
    assert "The other participant ended this conversation." in by_other.message
    assert "You ended this conversation." not in by_other.message


@pytest.mark.parametrize("who", ["u1", "u2"])
def test_the_closed_message_says_what_to_do_next(world, who):
    svc().end_conversation(world.u1, world.conv)
    exc = rejection(svc().post_message, getattr(world, who), world.conv, "hello there friend")
    leftover = exc.message.replace("This conversation is closed.", "").replace("You ended this conversation.", "")
    leftover = leftover.replace("The other participant ended this conversation.", "").strip()
    assert len(leftover) > 10, exc.message


def test_a_waiting_conversation_ended_by_its_creator_reports_closed_not_waiting():
    user = make_user()
    conv = make_waiting(user=user)
    svc().end_conversation(user, conv)
    exc = rejection(svc().post_message, user, conv, "hello there friend")
    assert exc.code == "closed" and "You ended this conversation." in exc.message


def test_a_stale_conversation_object_does_not_fool_the_closed_check(world):
    from forum.models import Conversation

    stale = world.conv  # still says active in memory
    svc().end_conversation(world.u2, Conversation.objects.get(pk=world.conv.pk))
    assert stale.status == "active"
    assert rejection(svc().post_message, world.u1, stale, "hello there friend").code == "closed"


# --- empty ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", " ", "\n\n", "\t \r\n \t", " "])
def test_an_empty_message_is_refused_and_nothing_is_saved(world, text):
    before = counts()
    exc = rejection(svc().post_message, world.u1, world.conv, text)
    assert exc.code == "empty"
    assert "Your message is empty." in exc.message
    assert_plain(exc)
    assert counts() == before


# --- too long: 3,000 and 3,001 -----------------------------------------------------------------------------------------


def test_exactly_3000_characters_is_accepted(world):
    message = svc().post_message(world.u1, world.conv, "a" * 3000)
    assert message.char_count == 3000


def test_3001_characters_is_refused_with_count_limit_and_excess(world):
    before = counts()
    exc = rejection(svc().post_message, world.u1, world.conv, "a" * 3001)
    assert exc.code == "too_long"
    assert "Your message is 3,001 characters" in exc.message
    assert "the limit is 3,000" in exc.message
    assert "shorten it by 1 character" in exc.message
    assert "keep the discussion readable" in exc.message
    assert "cost" not in exc.message.lower()
    assert_plain(exc)
    assert counts() == before


def test_the_plan_example_3412_characters(world):
    exc = rejection(svc().post_message, world.u1, world.conv, "a" * 3412)
    assert "Your message is 3,412 characters; the limit is 3,000." in exc.message
    assert "Please shorten it by 412 characters." in exc.message
    assert "Messages are capped to keep the discussion readable" in exc.message


def test_length_is_counted_by_the_one_counting_rule_crlf_is_one_character(world):
    text = "a\r\n" * 1499 + "aa"  # 2,998 + 2 = 3,000 characters once CRLF counts as one; 4,499 as typed
    assert len(text) > 3000
    assert svc().post_message(world.u1, world.conv, text).char_count == 3000


def test_one_character_more_after_crlf_normalisation_is_refused(world):
    exc = rejection(svc().post_message, world.u1, world.conv, "a\r\n" * 1499 + "aaa")
    assert exc.code == "too_long" and "3,001" in exc.message


def test_surrounding_whitespace_is_not_counted(world):
    assert svc().post_message(world.u1, world.conv, "   \n" + "a" * 3000 + "\n   ").char_count == 3000


def test_combining_marks_are_composed_before_counting(world):
    assert svc().post_message(world.u1, world.conv, "é" * 3000).char_count == 3000


def test_an_emoji_counts_as_one_character(world):
    assert svc().post_message(world.u1, world.conv, "\U0001F600" * 3000).char_count == 3000


def test_an_emoji_beyond_the_limit_is_refused(world, clock):
    exc = rejection(svc().post_message, world.u1, world.conv, "\U0001F600" * 3001)
    assert exc.code == "too_long"


def test_the_length_limit_and_its_numbers_come_from_settings(world, settings):
    settings.MAX_MESSAGE_CHARS = 10
    exc = rejection(svc().post_message, world.u1, world.conv, "x" * 25)
    assert "25 characters" in exc.message
    assert "limit is 10" in exc.message
    assert "shorten it by 15 characters" in exc.message
    assert "3,000" not in exc.message
    assert svc().post_message(world.u1, world.conv, "y" * 10)


# --- order of the checks -------------------------------------------------------------------------------------------------


def test_not_a_participant_is_reported_before_closed(world):
    svc().end_conversation(world.u1, world.conv)
    assert rejection(svc().post_message, make_user(), world.conv, "hello there friend").code == "not_participant"


def test_not_a_participant_is_reported_before_empty_in_a_waiting_conversation():
    conv = make_waiting()
    assert rejection(svc().post_message, make_user(), conv, "").code == "not_participant"


def test_closed_is_reported_before_empty(world):
    svc().end_conversation(world.u1, world.conv)
    assert rejection(svc().post_message, world.u2, world.conv, "").code == "closed"


def test_closed_is_reported_before_too_long(world):
    svc().end_conversation(world.u1, world.conv)
    assert rejection(svc().post_message, world.u2, world.conv, "a" * 5000).code == "closed"


def test_a_waiting_conversation_reports_empty_and_too_long_like_any_other():
    user = make_user()
    conv = enter(user, make_prop())
    assert rejection(svc().post_message, user, conv, "").code == "empty"
    assert rejection(svc().post_message, user, conv, "a" * 5000).code == "too_long"


def test_closed_is_reported_for_a_waiting_conversation_that_was_ended():
    user = make_user()
    conv = make_waiting(user=user)
    svc().end_conversation(user, conv)
    assert rejection(svc().post_message, user, conv, "").code == "closed"


def test_empty_is_reported_before_too_fast(world, clock):
    svc().post_message(world.u1, world.conv, "hello there friend")
    clock.advance(1)
    assert rejection(svc().post_message, world.u1, world.conv, "  ").code == "empty"


def test_too_long_is_reported_before_too_fast(world, clock):
    svc().post_message(world.u1, world.conv, "hello there friend")
    clock.advance(1)
    assert rejection(svc().post_message, world.u1, world.conv, "a" * 3001).code == "too_long"


def test_too_fast_is_reported_before_conversation_full(clock):
    """A conversation that is somehow still active with 30 user messages: the person who just posted is told to wait
    (the contract's order puts the gap before the cap); the other person is told the conversation is full."""
    from datetime import timedelta

    w = make_active()
    for i in range(29):
        orm_user_message(w.conv, w.a if i % 2 == 0 else w.b, f"message number {i}", when=clock() - timedelta(hours=1))
    orm_user_message(w.conv, w.a, "just now", when=clock())
    exc_a = rejection(svc().post_message, w.ua, w.conv, "one more for the road")
    exc_b = rejection(svc().post_message, w.ub, w.conv, "one more for the road")
    assert exc_a.code == "too_fast"
    assert exc_b.code == "conversation_full"


def test_conversation_full_is_the_code_when_an_active_conversation_already_holds_the_limit(clock):
    w = make_active()
    from datetime import timedelta

    for i in range(30):
        orm_user_message(w.conv, w.a if i % 2 == 0 else w.b, f"message number {i}", when=clock() - timedelta(hours=1))
    before = counts()
    exc = rejection(svc().post_message, w.ua, w.conv, "one more for the road")
    assert exc.code == "conversation_full"
    assert "This conversation has reached its limit of 30 messages and is closed." in exc.message
    assert "You can start a new one." in exc.message
    assert_plain(exc)
    assert counts() == before


# --- every code is one of the documented ones ---------------------------------------------------------------------------


def test_every_rejection_code_seen_here_is_one_of_the_documented_codes(world, clock):
    seen = set()
    seen.add(rejection(svc().post_message, make_user(), world.conv, "hello there friend").code)
    seen.add(rejection(svc().post_message, world.u1, world.conv, "").code)
    seen.add(rejection(svc().post_message, world.u1, world.conv, "a" * 3001).code)
    svc().post_message(world.u1, world.conv, "hello there friend")
    seen.add(rejection(svc().post_message, world.u1, world.conv, "hello again friend").code)
    svc().end_conversation(world.u2, world.conv)
    seen.add(rejection(svc().post_message, world.u1, world.conv, "hello again friend").code)
    assert seen == {"not_participant", "empty", "too_long", "too_fast", "closed"}
    assert seen <= ALL_CODES


def test_there_is_no_turn_taking_code_or_wording_anywhere_in_the_service():
    import inspect

    import forum.services as services

    source = inspect.getsource(services).lower()
    assert "your turn" not in source
    assert "not_your_turn" not in source
    assert "turn_taking" not in source and "alternat" not in source


def test_moderator_messages_in_the_conversation_do_not_change_who_may_post(world, clock):
    first = svc().post_message(world.u1, world.conv, "hello there friend")
    orm_moderator_message(world.conv, in_reply_to=first)
    clock.advance(31)
    assert svc().post_message(world.u1, world.conv, "and another thought").seq_no == 3


def test_the_stored_text_is_nfc_composed(world):
    message = svc().post_message(world.u1, world.conv, "cafe\u0301 au lait")
    assert message.content == "caf\u00e9 au lait"
    assert message.char_count == len(message.content) == 12


def test_a_visitor_without_an_account_is_not_a_participant_even_of_a_conversation_with_no_users(world):
    """A synthetic conversation has participants whose user is null; nobody anonymous may act as one of them."""
    from django.contrib.auth.models import AnonymousUser

    from forum.models import Conversation, Participant

    conv = Conversation.objects.create(topic=world.topic, status="active", source="synthetic")
    Participant.objects.create(conversation=conv, user=None, label="A", join_order=1)
    Participant.objects.create(conversation=conv, user=None, label="B", join_order=2)
    before = counts()
    for call in (
        lambda: svc().post_message(AnonymousUser(), conv, "hello there friend"),
        lambda: svc().end_conversation(AnonymousUser(), conv),
    ):
        assert rejection(call).code in ("login_required", "not_participant")
    assert counts() == before
