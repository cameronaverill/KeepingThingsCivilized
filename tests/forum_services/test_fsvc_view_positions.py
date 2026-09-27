"""Step 7c: conversation_view's position keys (my_side, other_side, position_texts, waiting_for_second)."""
import pytest

from fsvc_testkit import (
    assert_names_only_where_allowed, enter, make_active, make_active_via_service, make_prop, make_user, make_waiting, svc, views,
)  # fmt: skip

pytestmark = pytest.mark.django_db


def view(user, conv):
    from forum.models import Conversation

    return views().conversation_view(user, Conversation.objects.get(pk=conv.pk))


def seeded(text="Rents should be capped", opposing="Rents should not be capped"):
    from forum.models import Topic

    return Topic.objects.create(title="Rent control", proposition=text, opposing_position=opposing)


def test_the_new_keys_are_present():
    w = make_active_via_service()
    result = view(w.u1, w.conv)
    assert {"my_side", "other_side", "position_texts", "waiting_for_second"} <= set(result)


def test_an_active_conversation_gives_each_viewer_their_own_side_and_the_opposite_one():
    w = make_active_via_service()  # u1 pro, u2 con
    mine, theirs = view(w.u1, w.conv), view(w.u2, w.conv)
    assert (mine["my_side"], mine["other_side"]) == ("pro", "con")
    assert (theirs["my_side"], theirs["other_side"]) == ("con", "pro")


@pytest.mark.parametrize("sides", [("pro", "con"), ("con", "pro")])
def test_the_sides_follow_the_stored_sides_not_the_join_order_or_the_label(sides):
    w = make_active(sides=sides, labels=("B", "A"))
    assert (view(w.ua, w.conv)["my_side"], view(w.ua, w.conv)["other_side"]) == sides
    assert (view(w.ub, w.conv)["my_side"], view(w.ub, w.conv)["other_side"]) == (sides[1], sides[0])


@pytest.mark.parametrize("side", ["pro", "con"])
def test_a_waiting_conversation_gives_my_side_and_no_other_side(side):
    user, topic = make_user(), make_prop()
    conv = enter(user, topic, side)
    result = view(user, conv)
    assert result["waiting"] is True and result["waiting_for_second"] is True
    assert result["my_side"] == side
    assert result["other_side"] is None
    assert result["can_post"] is True and result["cannot_post_reason"] is None


def test_the_flag_turns_off_when_the_second_person_joins():
    topic = make_prop()
    first = make_user()
    conv = enter(first, topic, "pro")
    assert view(first, conv)["waiting_for_second"] is True
    enter(make_user(), topic, "con")
    result = view(first, conv)
    assert result["waiting_for_second"] is False and result["other_side"] == "con"


def test_position_texts_of_a_seeded_topic_carry_both_wordings():
    topic = seeded()
    w = make_active(topic)
    for user in (w.ua, w.ub):
        assert view(user, w.conv)["position_texts"] == {"pro": "Rents should be capped", "con": "Rents should not be capped"}


def test_position_texts_of_a_user_created_topic_have_no_con_wording():
    topic = svc().create_proposition(make_user(), "cats make better pets than dogs")
    w = make_active(topic)
    assert view(w.ua, w.conv)["position_texts"] == {"pro": "Cats make better pets than dogs", "con": None}


def test_the_texts_are_the_same_for_both_people_and_do_not_reveal_who_holds_what():
    topic = seeded()
    w = make_active(topic)
    assert view(w.ua, w.conv)["position_texts"] == view(w.ub, w.conv)["position_texts"]


def test_a_closed_conversation_keeps_the_position_keys():
    w = make_active(seeded())
    svc().end_conversation(w.ua, w.conv)
    result = view(w.ub, w.conv)
    assert result["status"] == "closed"
    assert (result["my_side"], result["other_side"]) == ("con", "pro")
    assert result["position_texts"]["pro"] == "Rents should be capped"


def test_a_legacy_waiting_row_with_a_blank_side_reads_as_pro():
    user = make_user()
    conv = make_waiting(user=user)
    result = view(user, conv)
    assert result["my_side"] == "pro" and result["other_side"] is None


def test_the_position_keys_add_no_label_name_or_email():
    w = make_active(seeded())
    for user in (w.ua, w.ub):
        assert_names_only_where_allowed(view(user, w.conv), user, w.ub if user is w.ua else w.ua)


def test_the_old_keys_are_all_still_there():
    w = make_active_via_service()
    assert {
        "status", "proposition", "you_ended", "ended_by_other", "messages", "moderation_notice", "message_count",
        "message_limit", "waiting", "can_post", "cannot_post_reason",
    } <= set(view(w.u1, w.conv))  # fmt: skip
