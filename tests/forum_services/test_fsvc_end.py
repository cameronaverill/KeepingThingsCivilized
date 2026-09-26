"""end_conversation: who may end what, what it records, and what is still possible afterwards."""
import pytest

from fsvc_testkit import (
    assert_plain, counts, make_active_via_service, make_prop, make_user, make_waiting, participant_of, rejection, svc,
)  # fmt: skip

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(clock):
    return make_active_via_service()


def reload(conv):
    from forum.models import Conversation

    return Conversation.objects.get(pk=conv.pk)


def test_either_participant_can_end_an_active_conversation(world, clock):
    ended = svc().end_conversation(world.u1, world.conv)
    stored = reload(world.conv)
    assert stored.status == "closed"
    assert stored.ended_by_id == participant_of(world.conv, world.u1).pk
    assert stored.ended_at == clock()
    assert ended.pk == world.conv.pk and ended.status == "closed"


def test_the_second_participant_can_end_it_too(world):
    svc().end_conversation(world.u2, world.conv)
    assert reload(world.conv).ended_by_id == participant_of(world.conv, world.u2).pk


def test_the_creator_can_end_a_waiting_conversation():
    user = make_user()
    conv = make_waiting(user=user)
    svc().end_conversation(user, conv)
    stored = reload(conv)
    assert stored.status == "closed" and stored.ended_by.user_id == user.pk and stored.ended_at is not None


def test_a_waiting_conversation_created_through_the_pairing_rule_can_be_ended_by_its_creator():
    user, topic = make_user(), make_prop()
    conv = svc().enter_proposition(user, topic)
    svc().end_conversation(user, conv)
    assert reload(conv).status == "closed"


def test_ending_closes_it_for_both_people(world):
    svc().end_conversation(world.u1, world.conv)
    for user in (world.u1, world.u2):
        assert rejection(svc().post_message, user, world.conv, "hello there friend").code == "closed"


def test_a_stranger_cannot_end_a_conversation(world):
    before = counts()
    exc = rejection(svc().end_conversation, make_user(), world.conv)
    assert exc.code == "not_participant"
    assert "You are not a participant in this conversation." in exc.message
    assert_plain(exc)
    assert reload(world.conv).status == "active"
    assert reload(world.conv).ended_by_id is None
    assert counts() == before


def test_a_stranger_cannot_end_a_waiting_conversation():
    conv = make_waiting()
    assert rejection(svc().end_conversation, make_user(), conv).code == "not_participant"
    assert reload(conv).status == "open"


def test_ending_an_already_closed_conversation_is_refused_as_closed(world):
    svc().end_conversation(world.u1, world.conv)
    for user in (world.u1, world.u2):
        exc = rejection(svc().end_conversation, user, world.conv)
        assert exc.code == "closed"
        assert "This conversation is closed." in exc.message
        assert_plain(exc)


def test_ending_twice_does_not_change_who_ended_it_or_when(world, clock):
    svc().end_conversation(world.u1, world.conv)
    first = reload(world.conv)
    clock.advance(600)
    rejection(svc().end_conversation, world.u2, world.conv)
    again = reload(world.conv)
    assert (again.ended_by_id, again.ended_at) == (first.ended_by_id, first.ended_at)


def test_a_stranger_gets_not_participant_even_when_the_conversation_is_already_closed(world):
    svc().end_conversation(world.u1, world.conv)
    assert rejection(svc().end_conversation, make_user(), world.conv).code == "not_participant"


def test_a_conversation_closed_by_the_cap_cannot_be_ended_again(world, clock, settings):
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 2
    svc().post_message(world.u1, world.conv, "the first")
    clock.advance(5)
    svc().post_message(world.u2, world.conv, "the second")
    assert reload(world.conv).status == "closed"
    assert rejection(svc().end_conversation, world.u1, world.conv).code == "closed"


def test_ending_saves_no_message_and_creates_no_run(world, clock):
    svc().post_message(world.u1, world.conv, "hello there friend")
    before = counts()
    svc().end_conversation(world.u2, world.conv)
    assert counts() == before


def test_ending_uses_the_current_database_state_not_a_stale_object(world):
    stale = world.conv
    svc().end_conversation(world.u1, reload(world.conv))
    assert stale.status == "active"
    assert rejection(svc().end_conversation, world.u2, stale).code == "closed"


def test_ending_keeps_everything_readable_and_the_participants(world, clock):
    from forum.models import Message
    from forum.viewmodels import conversation_view

    svc().post_message(world.u1, world.conv, "hello there friend")
    svc().end_conversation(world.u2, world.conv)
    assert Message.objects.filter(conversation=world.conv).count() == 1
    assert reload(world.conv).participants.count() == 2
    for user in (world.u1, world.u2):
        assert len(conversation_view(user, reload(world.conv))["messages"]) == 1


def test_an_ended_conversation_no_longer_counts_as_open_for_either_person(world, settings):
    settings.MAX_OPEN_CONVERSATIONS = 1
    svc().end_conversation(world.u1, world.conv)
    for user in (world.u1, world.u2):
        assert svc().enter_proposition(user, make_prop()).pk
