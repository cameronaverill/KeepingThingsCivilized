"""Step 7c revision 5: block_user, unblock_user, blocked_users, is_blocked_between; blocks applied in both directions in
waiting_groups and enter_proposition; usernames in my_conversations, conversation_view and the waiting groups."""
from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser

from fsvc_testkit import (
    assert_names_only_where_allowed, assert_plain, at, counts, make_active_via_service, make_prop, make_user, make_waiting,
    orm_user_message, participant_of, rejection, svc, views,
)  # fmt: skip

pytestmark = pytest.mark.django_db


def blocks():
    from forum.models import Block

    return Block.objects.all()


def reload(conv):
    from forum.models import Conversation

    return Conversation.objects.get(pk=conv.pk)


def enter_as(user, topic, side):
    return svc().enter_proposition(user, topic, side)


def pair_on(topic, first, second):
    conv = enter_as(first, topic, "pro")
    enter_as(second, topic, "con")
    return reload(conv)


# --- block_user ----------------------------------------------------------------------------------------------------------------


def test_blocking_creates_the_block_and_returns_the_ended_conversation_ids():
    world = make_active_via_service()
    ended = svc().block_user(world.u1, world.u2)
    assert ended == [world.conv.pk]
    (row,) = blocks()
    assert (row.blocker_id, row.blocked_id) == (world.u1.pk, world.u2.pk)


def test_the_shared_conversation_is_ended_by_the_blocker_and_closed_for_both(clock):
    world = make_active_via_service()
    svc().block_user(world.u1, world.u2)
    conv = reload(world.conv)
    assert conv.status == "closed"
    assert conv.ended_by_id == participant_of(world.conv, world.u1).pk
    assert conv.ended_at == clock()
    for user in (world.u1, world.u2):
        assert rejection(svc().post_message, user, conv, "hello there friend").code == "closed"


def test_the_closed_wording_names_the_blocker_as_the_one_who_ended_it():
    world = make_active_via_service()
    svc().block_user(world.u2, world.u1)
    by_blocker = rejection(svc().post_message, world.u2, reload(world.conv), "hello there friend")
    by_blocked = rejection(svc().post_message, world.u1, reload(world.conv), "hello there friend")
    assert "You ended this conversation." in by_blocker.message
    assert "The other participant ended this conversation." in by_blocked.message


def test_every_shared_open_or_active_conversation_is_ended():
    first = make_active_via_service()
    u1, u2 = first.u1, first.u2
    second_topic, third_topic = make_prop(), make_prop()
    second = pair_on(second_topic, u1, u2)
    third = pair_on(third_topic, u2, u1)  # the other way round
    ended = svc().block_user(u1, u2)
    assert sorted(ended) == sorted([first.conv.pk, second.pk, third.pk])
    for conv in (first.conv, second, third):
        assert reload(conv).status == "closed"
        assert reload(conv).ended_by_id == participant_of(conv, u1).pk  # always the blocker's own participant


def test_a_shared_conversation_that_is_already_closed_is_left_alone(clock):
    world = make_active_via_service()
    svc().end_conversation(world.u2, world.conv)
    before = reload(world.conv)
    clock.advance(600)
    assert svc().block_user(world.u1, world.u2) == []
    after = reload(world.conv)
    assert (after.ended_by_id, after.ended_at) == (before.ended_by_id, before.ended_at)
    assert blocks().count() == 1


def test_conversations_with_other_people_are_not_touched():
    world = make_active_via_service()
    other = make_active_via_service()  # two other people
    with_third = pair_on(make_prop(), world.u1, make_user())
    svc().block_user(world.u1, world.u2)
    assert reload(other.conv).status == "active"
    assert reload(with_third).status == "active"
    assert reload(world.conv).status == "closed"


def test_the_blockers_own_waiting_conversation_is_not_shared_and_stays_open():
    world = make_active_via_service()
    waiting = enter_as(world.u1, make_prop(), "pro")
    svc().block_user(world.u1, world.u2)
    assert reload(waiting).status == "open"


def test_the_targets_waiting_conversation_stays_open_too():
    world = make_active_via_service()
    waiting = enter_as(world.u2, make_prop(), "pro")
    svc().block_user(world.u1, world.u2)
    assert reload(waiting).status == "open"


def test_blocking_is_idempotent():
    world = make_active_via_service()
    assert svc().block_user(world.u1, world.u2) == [world.conv.pk]
    assert svc().block_user(world.u1, world.u2) == []
    assert svc().block_user(world.u1, world.u2) == []
    assert blocks().count() == 1


def test_blocking_someone_you_share_nothing_with_still_creates_the_block():
    a, b = make_user(), make_user()
    assert svc().block_user(a, b) == []
    assert blocks().count() == 1


def test_each_person_can_block_the_other_separately():
    a, b = make_user(), make_user()
    svc().block_user(a, b)
    svc().block_user(b, a)
    assert blocks().count() == 2


def test_the_block_is_committed_together_with_the_ended_conversations(settings):
    """One transaction: a failure while ending must not leave a half-done block. Simulated by an unknown target."""
    world = make_active_via_service()
    before = counts()
    from django.contrib.auth import get_user_model

    ghost = get_user_model()(username="ghostquill0001", email="ghostquill0001@mailbox.example")  # never saved: no pk
    assert rejection(svc().block_user, world.u1, ghost).code == "invalid_block"
    assert counts() == before and blocks().count() == 0
    assert reload(world.conv).status == "active"


def test_blocking_yourself_is_refused_with_a_friendly_message():
    a = make_user()
    exc = rejection(svc().block_user, a, a)
    assert exc.code == "invalid_block"
    assert exc.message == "You cannot block yourself."
    assert_plain(exc)
    assert blocks().count() == 0


def test_blocking_yourself_ends_nothing():
    world = make_active_via_service()
    rejection(svc().block_user, world.u1, world.u1)
    assert reload(world.conv).status == "active"


def test_an_anonymous_visitor_cannot_block():
    target = make_user()
    exc = rejection(svc().block_user, AnonymousUser(), target)
    assert exc.code == "login_required"
    assert blocks().count() == 0


@pytest.mark.parametrize("bad", [None, AnonymousUser()])
def test_an_unknown_or_anonymous_target_is_refused(bad):
    a = make_user()
    exc = rejection(svc().block_user, a, bad)
    assert exc.code == "invalid_block"
    assert_plain(exc)
    assert blocks().count() == 0


def test_a_target_deleted_from_the_database_is_an_unknown_user():
    a, b = make_user(), make_user()
    stale = type(b).objects.get(pk=b.pk)
    b.delete()
    assert rejection(svc().block_user, a, stale).code == "invalid_block"
    assert blocks().count() == 0


def test_blocking_saves_no_message_and_creates_no_run():
    world = make_active_via_service()
    svc().post_message(world.u1, world.conv, "hello there friend")
    before = counts()
    svc().block_user(world.u1, world.u2)
    after = counts()
    assert after["messages"] == before["messages"] and after["runs"] == before["runs"]


def test_a_synthetic_conversation_is_never_ended_by_a_block():
    from forum.models import Conversation, Participant

    a, b = make_user(), make_user()
    synthetic = Conversation.objects.create(topic=make_prop(), status="active", source="synthetic")
    Participant.objects.create(conversation=synthetic, user=None, label="A", join_order=1)
    svc().block_user(a, b)
    assert reload(synthetic).status == "active"


def test_ended_conversations_stay_readable_by_both_after_a_block():
    world = make_active_via_service()
    svc().post_message(world.u1, world.conv, "hello there friend")
    svc().block_user(world.u2, world.u1)
    for user in (world.u1, world.u2):
        result = views().conversation_view(user, reload(world.conv))
        assert result["status"] == "closed" and len(result["messages"]) == 1 and result["can_post"] is False


def test_the_ended_conversation_no_longer_counts_as_open(settings):
    settings.MAX_OPEN_CONVERSATIONS = 1
    world = make_active_via_service()
    svc().block_user(world.u1, world.u2)
    assert enter_as(world.u1, make_prop(), "pro").pk  # a slot is free again


# --- unblock_user ------------------------------------------------------------------------------------------------------------------


def test_unblocking_removes_the_block_and_reopens_nothing():
    world = make_active_via_service()
    svc().block_user(world.u1, world.u2)
    svc().unblock_user(world.u1, world.u2)
    assert blocks().count() == 0
    assert reload(world.conv).status == "closed"


def test_unblocking_is_idempotent_and_never_raises_for_a_stranger():
    a, b = make_user(), make_user()
    svc().unblock_user(a, b)
    svc().block_user(a, b)
    svc().unblock_user(a, b)
    svc().unblock_user(a, b)
    assert blocks().count() == 0


def test_unblocking_removes_only_the_blockers_own_block():
    a, b, c = make_user(), make_user(), make_user()
    svc().block_user(a, b)
    svc().block_user(b, a)
    svc().block_user(a, c)
    svc().unblock_user(a, b)
    pairs = {(x.blocker_id, x.blocked_id) for x in blocks()}
    assert pairs == {(b.pk, a.pk), (a.pk, c.pk)}


def test_the_blocked_person_cannot_lift_the_block_by_unblocking():
    a, b = make_user(), make_user()
    svc().block_user(a, b)
    svc().unblock_user(b, a)  # b never blocked a: nothing to remove
    assert blocks().count() == 1


def test_an_anonymous_visitor_cannot_unblock():
    a, b = make_user(), make_user()
    svc().block_user(a, b)
    try:
        svc().unblock_user(AnonymousUser(), b)
    except Exception as exc:  # a PostRejected is the friendly way
        assert getattr(exc, "code", None) == "login_required"
    assert blocks().count() == 1


# --- blocked_users -------------------------------------------------------------------------------------------------------------------


def test_blocked_users_lists_username_and_since_newest_first(clock):
    viewer = make_user()
    first, second, third = make_user(), make_user(), make_user()
    for target in (first, second, third):
        svc().block_user(viewer, target)
        clock.advance(60)
    result = svc().blocked_users(viewer)
    assert [r["username"] for r in result] == [third.username, second.username, first.username]
    assert all(set(r) == {"username", "since"} for r in result)
    assert result[0]["since"] > result[1]["since"] > result[2]["since"]


def test_since_is_the_time_of_the_block(clock):
    viewer, target = make_user(), make_user()
    svc().block_user(viewer, target)
    assert svc().blocked_users(viewer)[0]["since"] == clock()


def test_blocked_users_holds_only_the_viewers_own_blocks():
    a, b, c = make_user(), make_user(), make_user()
    svc().block_user(a, b)
    svc().block_user(c, a)  # someone blocked a: a's list does not show it
    assert [r["username"] for r in svc().blocked_users(a)] == [b.username]
    assert [r["username"] for r in svc().blocked_users(c)] == [a.username]
    assert svc().blocked_users(b) == []


def test_blocked_users_carries_no_email_or_id():
    a, b = make_user(), make_user()
    svc().block_user(a, b)
    (row,) = svc().blocked_users(a)
    assert b.email not in repr(row) and str(b.pk) not in {str(v) for v in row.values() if isinstance(v, (str, int))}


def test_blocked_users_after_unblocking_and_for_an_anonymous_visitor():
    a, b = make_user(), make_user()
    svc().block_user(a, b)
    svc().unblock_user(a, b)
    assert svc().blocked_users(a) == []
    assert svc().blocked_users(AnonymousUser()) == []


# --- is_blocked_between --------------------------------------------------------------------------------------------------------------


def test_is_blocked_between_is_true_in_either_direction_and_symmetric():
    a, b, c = make_user(), make_user(), make_user()
    svc().block_user(a, b)
    assert svc().is_blocked_between(a, b) is True
    assert svc().is_blocked_between(b, a) is True
    assert svc().is_blocked_between(a, c) is False
    assert svc().is_blocked_between(c, b) is False


def test_is_blocked_between_after_unblocking_and_with_both_blocks():
    a, b = make_user(), make_user()
    svc().block_user(a, b)
    svc().block_user(b, a)
    svc().unblock_user(a, b)
    assert svc().is_blocked_between(a, b) is True  # b still blocks a
    svc().unblock_user(b, a)
    assert svc().is_blocked_between(a, b) is False


def test_is_blocked_between_with_yourself_or_a_visitor_is_false():
    a = make_user()
    assert svc().is_blocked_between(a, a) is False
    assert svc().is_blocked_between(a, AnonymousUser()) is False


# --- waiting_groups: blocks in both directions ----------------------------------------------------------------------------------------


def groups(viewer, query=None):
    return svc().waiting_groups(viewer, query)


def wait(topic, user, when, side="pro"):
    return make_waiting(topic, user, created_at=when, side=side, seed=3)


def test_a_group_whose_only_waiter_the_viewer_blocked_is_not_listed():
    topic, viewer, waiter = make_prop(), make_user(), make_user()
    wait(topic, waiter, at(1))
    assert [g["topic"].pk for g in groups(viewer)] == [topic.pk]
    svc().block_user(viewer, waiter)
    assert groups(viewer) == []


def test_a_group_whose_only_waiter_blocked_the_viewer_is_not_listed():
    topic, viewer, waiter = make_prop(), make_user(), make_user()
    wait(topic, waiter, at(1))
    svc().block_user(waiter, viewer)
    assert groups(viewer) == []


def test_blocks_hide_the_viewers_waiting_position_from_the_blocked_person_as_well():
    topic, a, b = make_prop(), make_user(), make_user()
    wait(topic, a, at(1))
    svc().block_user(b, a)
    assert groups(b) == []
    assert groups(a) == []  # a has a conversation of their own on the topic, so it is never listed to them


def test_the_next_allowed_waiter_of_a_group_is_shown_and_supplies_since_and_username():
    topic, viewer = make_prop(), make_user()
    blocked_old, allowed_new = make_user(), make_user()
    wait(topic, blocked_old, at(1))
    wait(topic, allowed_new, at(5))
    svc().block_user(viewer, blocked_old)
    (entry,) = groups(viewer)
    assert entry["waiting_username"] == allowed_new.username
    assert entry["since"] == at(5)


def test_the_next_allowed_waiter_is_shown_when_the_older_one_blocked_the_viewer():
    topic, viewer = make_prop(), make_user()
    old, new = make_user(), make_user()
    wait(topic, old, at(1))
    wait(topic, new, at(5))
    svc().block_user(old, viewer)
    (entry,) = groups(viewer)
    assert entry["waiting_username"] == new.username and entry["since"] == at(5)


def test_the_username_shown_is_the_oldest_allowed_waiter_of_the_group():
    topic, viewer = make_prop(), make_user()
    oldest, middle, newest = make_user(), make_user(), make_user()
    wait(topic, oldest, at(1))
    wait(topic, middle, at(3))
    wait(topic, newest, at(7))
    (entry,) = groups(viewer)
    assert (entry["waiting_username"], entry["since"]) == (oldest.username, at(1))
    svc().block_user(viewer, oldest)
    (entry,) = groups(viewer)
    assert (entry["waiting_username"], entry["since"]) == (middle.username, at(3))
    svc().block_user(viewer, middle)
    (entry,) = groups(viewer)
    assert (entry["waiting_username"], entry["since"]) == (newest.username, at(7))
    svc().block_user(viewer, newest)
    assert groups(viewer) == []


def test_a_block_only_affects_the_groups_of_the_blocked_person():
    a_topic, b_topic, viewer = make_prop(), make_prop(), make_user()
    blocked, fine = make_user(), make_user()
    wait(a_topic, blocked, at(1))
    wait(b_topic, fine, at(2))
    svc().block_user(viewer, blocked)
    assert [g["topic"].pk for g in groups(viewer)] == [b_topic.pk]


def test_a_block_between_other_people_changes_nothing_for_the_viewer():
    topic, viewer = make_prop(), make_user()
    x, y = make_user(), make_user()
    wait(topic, x, at(1))
    svc().block_user(x, y)
    assert len(groups(viewer)) == 1


def test_unblocking_brings_the_group_back():
    topic, viewer, waiter = make_prop(), make_user(), make_user()
    wait(topic, waiter, at(1))
    svc().block_user(viewer, waiter)
    svc().unblock_user(viewer, waiter)
    assert [g["waiting_username"] for g in groups(viewer)] == [waiter.username]


def test_a_person_who_blocked_the_viewer_hides_only_their_own_side_of_a_topic():
    topic, viewer = make_prop(), make_user()
    pro_waiter, con_waiter = make_user(), make_user()
    wait(topic, pro_waiter, at(1), "pro")
    wait(topic, con_waiter, at(2), "con")
    svc().block_user(pro_waiter, viewer)
    assert [(g["waiting_side"], g["waiting_username"]) for g in groups(viewer)] == [("con", con_waiter.username)]


def test_the_search_still_works_with_blocks():
    viewer, blocked, fine = make_user(), make_user(), make_user()
    a = make_prop("Cats are wonderful company")
    b = make_prop("Cats need daily walks")
    wait(a, blocked, at(1))
    wait(b, fine, at(2))
    svc().block_user(viewer, blocked)
    assert [g["topic"].pk for g in groups(viewer, "cats")] == [b.pk]


def test_the_group_ordering_uses_only_joinable_waiters():
    """A newer waiter that is blocked does not lift its group up the list."""
    viewer = make_user()
    blocked = make_user()
    a, b = make_prop(), make_prop()
    wait(a, make_user(), at(2))
    wait(b, make_user(), at(3))
    wait(a, blocked, at(9))  # would make group a the newest if it counted
    svc().block_user(viewer, blocked)
    assert [g["topic"].pk for g in groups(viewer)] == [b.pk, a.pk]


def test_the_number_of_queries_with_blocks_stays_constant():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    viewer = make_user()
    for i in range(30):
        waiter = make_user()
        wait(make_prop(), waiter, at(1) + timedelta(minutes=i))
        if i % 3 == 0:
            svc().block_user(viewer, waiter)
    with CaptureQueriesContext(connection) as queries:
        result = groups(viewer)
        for g in result:
            g["topic"].proposition, g["waiting_username"], g["since"]
    assert len(result) == 20
    assert len(queries) <= 5, len(queries)


# --- enter_proposition: blocks in both directions ----------------------------------------------------------------------------------------


def test_a_joiner_never_joins_a_waiter_they_blocked():
    topic, joiner, waiter = make_prop(), make_user(), make_user()
    conv = enter_as(waiter, topic, "pro")
    svc().block_user(joiner, waiter)
    result = enter_as(joiner, topic, "con")
    assert result.pk != conv.pk
    assert reload(conv).status == "open" and reload(conv).participants.count() == 1
    assert reload(result).status == "open"  # a new waiting conversation of their own


def test_a_joiner_never_joins_a_waiter_who_blocked_them():
    topic, joiner, waiter = make_prop(), make_user(), make_user()
    conv = enter_as(waiter, topic, "pro")
    svc().block_user(waiter, joiner)
    result = enter_as(joiner, topic, "con")
    assert result.pk != conv.pk
    assert reload(conv).participants.count() == 1


def test_it_falls_through_to_the_next_oldest_allowed_waiter():
    topic = make_prop()
    joiner, blocked, allowed = make_user(), make_user(), make_user()
    old = make_waiting(topic, blocked, created_at=at(1), side="pro", seed=1)
    new = make_waiting(topic, allowed, created_at=at(2), side="pro", seed=2)
    svc().block_user(joiner, blocked)
    assert enter_as(joiner, topic, "con").pk == new.pk
    assert reload(old).status == "open"
    assert reload(new).status == "active"


def test_it_falls_through_several_blocked_waiters():
    topic, joiner = make_prop(), make_user()
    waiters = [make_user() for _ in range(3)]
    convs = [make_waiting(topic, w, created_at=at(1 + i), side="pro", seed=i) for i, w in enumerate(waiters)]
    svc().block_user(joiner, waiters[0])
    svc().block_user(waiters[1], joiner)
    assert enter_as(joiner, topic, "con").pk == convs[2].pk


def test_when_every_waiter_is_blocked_a_new_waiting_conversation_is_created_on_the_chosen_side():
    topic, joiner = make_prop(), make_user()
    waiter = make_user()
    make_waiting(topic, waiter, created_at=at(1), side="pro", seed=1)
    svc().block_user(joiner, waiter)
    fresh = reload(enter_as(joiner, topic, "con"))
    assert fresh.status == "open"
    (only,) = fresh.participants.all()
    assert only.user_id == joiner.pk and only.side == "con"
    assert fresh.label_seed is not None


def test_blocked_people_can_each_wait_on_opposite_sides_without_ever_pairing():
    topic, a, b = make_prop(), make_user(), make_user()
    svc().block_user(a, b)
    conv_a = enter_as(a, topic, "pro")
    conv_b = enter_as(b, topic, "con")
    assert conv_a.pk != conv_b.pk
    assert reload(conv_a).status == reload(conv_b).status == "open"


def test_blocks_only_stop_the_blocked_pair_a_third_person_still_joins():
    topic, a, b, c = make_prop(), make_user(), make_user(), make_user()
    svc().block_user(a, b)
    conv = enter_as(b, topic, "pro")
    enter_as(a, topic, "con")  # cannot join b's, starts own
    assert enter_as(c, topic, "con").pk == conv.pk  # c is free to join b


def test_unblocking_lets_the_pair_meet_again():
    topic, a, b = make_prop(), make_user(), make_user()
    svc().block_user(a, b)
    svc().unblock_user(a, b)
    conv = enter_as(a, topic, "pro")
    assert enter_as(b, topic, "con").pk == conv.pk
    assert reload(conv).status == "active"


def test_a_persons_own_conversation_is_still_returned_whatever_blocks_exist():
    topic, a, b = make_prop(), make_user(), make_user()
    mine = enter_as(a, topic, "pro")
    svc().block_user(b, a)
    assert enter_as(a, topic, "pro").pk == mine.pk


def test_blocks_and_sides_together_only_the_opposite_side_and_not_blocked_waiter_is_joined():
    topic, joiner = make_prop(), make_user()
    same_side = make_user()
    blocked_opposite, allowed_opposite = make_user(), make_user()
    make_waiting(topic, same_side, created_at=at(1), side="con", seed=1)
    make_waiting(topic, blocked_opposite, created_at=at(2), side="pro", seed=2)
    good = make_waiting(topic, allowed_opposite, created_at=at(3), side="pro", seed=3)
    svc().block_user(joiner, blocked_opposite)
    assert enter_as(joiner, topic, "con").pk == good.pk


def test_the_legacy_blank_side_waiter_obeys_blocks_too():
    topic, joiner, waiter = make_prop(), make_user(), make_user()
    legacy = make_waiting(topic, waiter)
    svc().block_user(waiter, joiner)
    assert enter_as(joiner, topic, "con").pk != legacy.pk
    assert reload(legacy).participants.count() == 1


# --- my_conversations and conversation_view: usernames ----------------------------------------------------------------------------------------


def test_my_conversations_names_the_other_person_and_none_while_waiting():
    world = make_active_via_service()
    waiting = enter_as(world.u1, make_prop(), "pro")
    rows = {e["conversation"].pk: e for e in svc().my_conversations(world.u1)}
    assert rows[world.conv.pk]["other_username"] == world.u2.username
    assert rows[waiting.pk]["other_username"] is None
    other_rows = svc().my_conversations(world.u2)
    assert other_rows[0]["other_username"] == world.u1.username


def test_a_blocked_conversation_stays_listed_as_ended_with_the_other_persons_name():
    world = make_active_via_service()
    svc().block_user(world.u1, world.u2)
    (mine,) = svc().my_conversations(world.u1)
    (theirs,) = svc().my_conversations(world.u2)
    assert mine["status"] == theirs["status"] == "ended"
    assert mine["other_username"] == world.u2.username and theirs["other_username"] == world.u1.username


def test_the_view_gives_the_other_persons_username_and_none_while_waiting():
    world = make_active_via_service()
    assert views().conversation_view(world.u1, reload(world.conv))["other_username"] == world.u2.username
    assert views().conversation_view(world.u2, reload(world.conv))["other_username"] == world.u1.username
    lone = make_user()
    conv = enter_as(lone, make_prop(), "pro")
    assert views().conversation_view(lone, conv)["other_username"] is None


def test_each_message_carries_author_name_none_for_the_viewers_own_and_for_the_moderator():
    from fsvc_testkit import orm_moderator_message

    world = make_active_via_service()
    m1 = orm_user_message(world.conv, participant_of(world.conv, world.u1), "from the first person")
    orm_user_message(world.conv, participant_of(world.conv, world.u2), "from the second person")
    orm_moderator_message(world.conv, in_reply_to=m1)
    as_first = views().conversation_view(world.u1, reload(world.conv))["messages"]
    assert [(m["kind"], m["author_name"]) for m in as_first] == [("you", None), ("other", world.u2.username), ("moderator", None)]
    as_second = views().conversation_view(world.u2, reload(world.conv))["messages"]
    assert [(m["kind"], m["author_name"]) for m in as_second] == [("other", world.u1.username), ("you", None), ("moderator", None)]


def test_author_name_is_present_on_polled_messages_too():
    world = make_active_via_service()
    orm_user_message(world.conv, participant_of(world.conv, world.u1), "one")
    orm_user_message(world.conv, participant_of(world.conv, world.u2), "two")
    (only,) = views().conversation_view(world.u1, reload(world.conv), after_seq=1)["messages"]
    assert only["author_name"] == world.u2.username


def test_the_viewers_own_username_and_every_email_never_appear_in_the_view():
    world = make_active_via_service()
    orm_user_message(world.conv, participant_of(world.conv, world.u1), "from the first person")
    orm_user_message(world.conv, participant_of(world.conv, world.u2), "from the second person")
    for viewer, other in ((world.u1, world.u2), (world.u2, world.u1)):
        assert_names_only_where_allowed(views().conversation_view(viewer, reload(world.conv)), viewer, other)


def test_the_other_persons_name_is_in_no_other_field_of_the_view():
    world = make_active_via_service()
    orm_user_message(world.conv, participant_of(world.conv, world.u2), "from the second person")
    result = views().conversation_view(world.u1, reload(world.conv))
    from fsvc_testkit import name_paths

    paths = list(name_paths(result, world.u2.username))
    assert paths and all(p[-1] in {"other_username", "author_name"} for p in paths), paths


def test_a_waiting_person_sees_nobody_named():
    lone = make_user()
    conv = enter_as(lone, make_prop(), "pro")
    svc().post_message(lone, conv, "my opening message")
    result = views().conversation_view(lone, conv)
    assert result["other_username"] is None
    assert [m["author_name"] for m in result["messages"]] == [None]
    assert_names_only_where_allowed(result, lone)


# --- accepted as built ------------------------------------------------------------------------------------------------------------------


def test_block_user_returns_the_sorted_conversation_ids():
    u1, u2 = make_user(), make_user()
    convs = [pair_on(make_prop(), u1 if i % 2 else u2, u2 if i % 2 else u1) for i in range(4)]
    ended = svc().block_user(u1, u2)
    assert ended == sorted(c.pk for c in convs) and isinstance(ended, list)


def test_the_unknown_target_message_and_the_self_message_are_the_agreed_wording():
    a = make_user()
    assert rejection(svc().block_user, a, None).message == "That person was not found."
    assert rejection(svc().block_user, a, a).message == "You cannot block yourself."


def test_a_conversation_with_one_participant_is_never_ended_by_a_block():
    a, b = make_user(), make_user()
    waiting = enter_as(a, make_prop(), "pro")
    svc().block_user(a, b)
    svc().block_user(b, a)
    assert reload(waiting).status == "open"


def test_an_inactive_account_can_be_blocked():
    a, b = make_user(), make_user()
    type(b).objects.filter(pk=b.pk).update(is_active=False)
    assert svc().block_user(a, type(b).objects.get(pk=b.pk)) == []
    assert blocks().count() == 1


def test_unblock_user_returns_true_when_it_removed_a_block_and_false_otherwise():
    a, b = make_user(), make_user()
    assert svc().unblock_user(a, b) is False
    svc().block_user(a, b)
    assert svc().unblock_user(a, b) is True
    assert svc().unblock_user(a, b) is False
    assert svc().unblock_user(a, None) is False


def test_unblock_user_refuses_an_anonymous_caller():
    a, b = make_user(), make_user()
    svc().block_user(a, b)
    assert rejection(svc().unblock_user, AnonymousUser(), b).code == "login_required"
    assert blocks().count() == 1


def test_waiting_groups_query_count_is_the_same_for_one_group_and_for_thirty():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    viewer = make_user()
    wait(make_prop(), make_user(), at(1))

    def count():
        with CaptureQueriesContext(connection) as queries:
            for g in groups(viewer):
                g["topic"].proposition, g["waiting_username"], g["since"]
        return len(queries)

    one = count()
    for i in range(29):
        waiter = make_user()
        wait(make_prop(), waiter, at(2) + timedelta(minutes=i))
        if i % 4 == 0:
            svc().block_user(viewer, waiter)
    assert count() == one


def test_a_heading_falls_back_to_the_other_participants_for_a_person_without_a_username():
    from forum.models import Conversation, Participant
    from fsvc_testkit import orm_act, orm_moderator_message, orm_run

    viewer = make_user()
    conv = Conversation.objects.create(topic=make_prop(), status="active", source="human", label_seed=1)
    Participant.objects.create(conversation=conv, user=viewer, label="A", join_order=1, side="pro")
    other = Participant.objects.create(conversation=conv, user=make_user(), label="B", join_order=2, side="con")
    theirs = orm_user_message(conv, other, "their words")
    Participant.objects.filter(pk=other.pk).update(user=None)  # a participant with no account behind it
    mod = orm_moderator_message(conv, in_reply_to=theirs)
    run = orm_run(conv, theirs, status="done", posted_message=mod)
    orm_act(run, "B", "B", [theirs])
    result = views().conversation_view(viewer, reload(conv))
    assert [m for m in result["messages"] if m["kind"] == "moderator"][0]["heading"] == "About the other participant's message 1"
    assert result["other_username"] is None


def test_a_shared_conversation_still_marked_open_is_ended_too():
    """Every OPEN or active conversation the two share (stored data can hold two people in an open one)."""
    from forum.models import Conversation

    world = make_active_via_service()
    Conversation.objects.filter(pk=world.conv.pk).update(status="open")
    assert svc().block_user(world.u1, world.u2) == [world.conv.pk]
    assert reload(world.conv).status == "closed"


def test_a_persons_own_conversation_is_still_returned_by_entering_even_if_a_block_row_exists_with_their_partner():
    """Rule 1 of the pairing rule (own conversation first) is unchanged by blocks: a Block row made outside
    block_user (for example in the admin's database) does not make the person lose their own conversation."""
    from forum.models import Block

    world = make_active_via_service()
    Block.objects.create(blocker=world.u2, blocked=world.u1)
    assert enter_as(world.u1, world.topic, "pro").pk == world.conv.pk
    assert enter_as(world.u2, world.topic, "con").pk == world.conv.pk
