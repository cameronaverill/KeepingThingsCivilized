"""The 30-message cap: the 30th is accepted and closes the conversation, the 31st is refused with the reason."""
import pytest

from fsvc_testkit import (
    assert_plain, counts, make_active_via_service, orm_moderator_message, rejection, svc,
)  # fmt: skip

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(clock):
    return make_active_via_service()


def fill(world, clock, n):
    """Post n messages alternating between the two people, 16 seconds apart (32 s per person). Returns the last."""
    last = None
    for i in range(n):
        clock.advance(16)
        who = world.u1 if i % 2 == 0 else world.u2
        last = svc().post_message(who, world.conv, f"message number {i + 1} in the exchange")
    return last


def reload(conv):
    from forum.models import Conversation

    return Conversation.objects.get(pk=conv.pk)


def test_the_limit_default_is_thirty(settings):
    assert settings.MAX_USER_MESSAGES_PER_CONVERSATION == 30


def test_the_thirtieth_message_is_accepted_and_the_conversation_is_closed_after_it(world, clock):
    last = fill(world, clock, 29)
    assert reload(world.conv).status == "active"
    clock.advance(31)
    thirtieth = svc().post_message(world.u2, world.conv, "the thirtieth and last")
    assert thirtieth.seq_no == 30 and last.seq_no == 29
    after = reload(world.conv)
    assert after.status == "closed"
    assert after.ended_by_id is None  # nobody ended it; the cap did
    assert after.ended_at is None


def test_the_thirty_first_message_is_refused_as_closed_with_the_limit_wording_and_saves_nothing(world, clock):
    fill(world, clock, 30)
    clock.advance(60)
    before = counts()
    for user in (world.u1, world.u2):
        exc = rejection(svc().post_message, user, world.conv, "one more please")
        # Architect ruling: the cap closes the conversation, so the 31st post is refused as `closed`; the message says
        # why (the limit) and what to do next. `conversation_full` is for an active conversation that already holds 30.
        assert exc.code == "closed"
        assert "This conversation is closed." in exc.message
        assert "reached its limit of 30 messages" in exc.message
        assert "start a new" in exc.message
        assert_plain(exc)
    assert counts() == before


def test_a_conversation_closed_by_the_cap_does_not_claim_someone_ended_it(world, clock):
    fill(world, clock, 30)
    for user in (world.u1, world.u2):
        exc = rejection(svc().post_message, user, world.conv, "one more please")
        assert "You ended this conversation." not in exc.message
        assert "The other participant ended this conversation." not in exc.message


def test_the_thirtieth_message_gets_its_moderation_run_too(world, clock):
    from moderation.models import ModerationRun

    fill(world, clock, 30)
    assert ModerationRun.objects.filter(conversation=world.conv, kind="live", status="pending").count() == 30


def test_the_limit_and_its_number_come_from_settings(world, clock, settings):
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 3
    fill(world, clock, 2)
    clock.advance(31)
    assert svc().post_message(world.u1, world.conv, "the third").seq_no == 3
    assert reload(world.conv).status == "closed"
    clock.advance(60)
    exc = rejection(svc().post_message, world.u2, world.conv, "the fourth")
    assert "limit of 3 messages" in exc.message
    assert "30" not in exc.message


def test_moderator_messages_do_not_count_toward_the_cap(world, clock, settings):
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 3
    m1 = svc().post_message(world.u1, world.conv, "first user message")
    orm_moderator_message(world.conv, in_reply_to=m1)
    clock.advance(16)
    m2 = svc().post_message(world.u2, world.conv, "second user message")
    orm_moderator_message(world.conv, in_reply_to=m2)
    orm_moderator_message(world.conv)
    clock.advance(16)
    third = svc().post_message(world.u1, world.conv, "third user message")
    assert third.seq_no == 6  # three user messages and three moderator posts came before or with it
    assert reload(world.conv).status == "closed"


def test_the_message_that_would_exceed_the_cap_is_refused_in_an_active_conversation(world, clock, settings):
    """State made by hand: 3 user messages already exist but the status is still active."""
    from forum.models import Conversation

    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 3
    fill(world, clock, 2)
    clock.advance(31)
    svc().post_message(world.u1, world.conv, "the third")
    Conversation.objects.filter(pk=world.conv.pk).update(status="active")  # undo the automatic close
    clock.advance(60)
    exc = rejection(svc().post_message, world.u2, world.conv, "the fourth")
    assert exc.code == "conversation_full"
    assert "limit of 3 messages" in exc.message and "closed" in exc.message and "start a new one" in exc.message


def test_reading_is_still_possible_after_the_cap(world, clock):
    from forum.viewmodels import conversation_view

    fill(world, clock, 30)
    view = conversation_view(world.u1, reload(world.conv))
    assert view["status"] == "closed"
    assert len(view["messages"]) == 30
    assert view["can_post"] is False
