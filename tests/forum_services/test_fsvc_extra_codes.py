"""Codes beyond the eleven in the brief that the architect allowed: login_required, invalid_reply, not_saved."""
import pytest
from django.contrib.auth.models import AnonymousUser

from fsvc_testkit import (
    ALL_CODES, EXTRA_CODES, assert_plain, counts, make_active_via_service, make_prop, make_waiting, rejection, svc,
)  # fmt: skip

pytestmark = pytest.mark.django_db


def test_a_visitor_who_is_not_logged_in_cannot_create_propose_enter_post_or_end():
    world = make_active_via_service()
    waiting = make_waiting()
    visitor = AnonymousUser()
    before = counts()
    for call in (
        lambda: svc().create_proposition(visitor, "Trains should be free"),
        lambda: svc().enter_proposition(visitor, make_prop()),
        lambda: svc().post_message(visitor, world.conv, "hello there friend"),
        lambda: svc().end_conversation(visitor, waiting),
    ):
        exc = rejection(call)
        assert exc.code in ("login_required", "not_participant")
        assert exc.code in ALL_CODES | EXTRA_CODES
        assert_plain(exc)
    after = counts()
    assert after["topics"] == before["topics"] + 1  # only the make_prop() made for the enter call
    assert after["messages"] == before["messages"] and after["runs"] == before["runs"]
    assert after["conversations"] == before["conversations"] and after["participants"] == before["participants"]


def test_a_visitor_cannot_read_a_conversation_either():
    from forum.viewmodels import conversation_view

    world = make_active_via_service()
    exc = rejection(conversation_view, AnonymousUser(), world.conv)
    assert exc.code in ("login_required", "not_participant")


def test_replying_to_a_message_of_this_conversation_is_stored(clock):
    world = make_active_via_service()
    first = svc().post_message(world.u1, world.conv, "the first message here")
    clock.advance(5)
    reply = svc().post_message(world.u2, world.conv, "a reply to the first", in_reply_to=first)
    assert reply.in_reply_to_id == first.pk


def test_replying_to_a_message_of_another_conversation_is_refused_and_saves_nothing(clock):
    world = make_active_via_service()
    other = make_active_via_service()
    foreign = svc().post_message(other.u1, other.conv, "a message somewhere else")
    before = counts()
    exc = rejection(svc().post_message, world.u1, world.conv, "a reply that points elsewhere", in_reply_to=foreign)
    assert exc.code == "invalid_reply"
    assert_plain(exc)
    assert counts() == before


def test_a_post_without_a_reply_target_has_none(clock):
    world = make_active_via_service()
    assert svc().post_message(world.u1, world.conv, "no reply target here").in_reply_to_id is None
