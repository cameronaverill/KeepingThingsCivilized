"""Step 7c, two-position model: enter_proposition(user, topic, side) in every branch, labels drawn at creation, legacy
waiting rows, posting while waiting with every limit unchanged."""
import pytest

from fsvc_testkit import (
    assert_plain, at, counts, make_active_via_service, make_prop, make_user, make_waiting, orm_moderator_message,
    rejection, svc,
)  # fmt: skip

pytestmark = pytest.mark.django_db


def enter_as(user, topic, side):
    return svc().enter_proposition(user, topic, side)


def people(conv):
    return list(conv.participants.order_by("join_order"))


def reload(conv):
    from forum.models import Conversation

    return Conversation.objects.get(pk=conv.pk)


# --- the side must be chosen ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, "", "PRO", "Con", "left", "both", "pro ", "yes", 1, True])
def test_a_missing_or_unknown_side_is_refused_with_invalid_side_and_nothing_is_created(bad):
    user, topic = make_user(), make_prop()
    before = counts()
    exc = rejection(svc().enter_proposition, user, topic, bad)
    assert exc.code == "invalid_side"
    assert exc.message == "Choose a position first."
    assert_plain(exc)
    assert counts() == before


def test_leaving_the_side_out_never_picks_one_silently():
    """Either a TypeError (argument required) or invalid_side; never a conversation created with a guessed side."""
    from forum.services import PostRejected

    before = counts()
    try:
        svc().enter_proposition(make_user(), make_prop())
    except TypeError:
        pass
    except PostRejected as exc:
        assert exc.code == "invalid_side"
    else:
        pytest.fail("entering without a side was accepted")
    assert counts()["conversations"] == before["conversations"] and counts()["participants"] == before["participants"]


@pytest.mark.parametrize("side", ["pro", "con"])
def test_either_valid_side_starts_a_conversation(side):
    user, topic = make_user(), make_prop()
    conv = enter_as(user, topic, side)
    (only,) = people(reload(conv))
    assert only.user_id == user.pk and only.side == side


# --- creating a waiting conversation --------------------------------------------------------------------------------------


def test_a_new_waiting_conversation_stores_the_creators_side_seed_and_label(settings):
    from forum.services import assign_labels

    user = make_user()
    conv = reload(enter_as(user, make_prop(), "con"))
    (only,) = people(conv)
    assert conv.status == "open" and only.join_order == 1
    assert isinstance(conv.label_seed, int) and conv.label_seed is not True
    assert only.label == assign_labels(conv.label_seed)[0]
    assert only.side == "con"


def test_the_seed_is_drawn_at_creation_and_kept_when_someone_joins():
    topic = make_prop()
    conv = reload(enter_as(make_user(), topic, "pro"))
    seed_before = conv.label_seed
    creator_before = people(conv)[0]
    label_before = creator_before.label
    assert seed_before is not None
    enter_as(make_user(), topic, "con")
    after = reload(conv)
    assert after.label_seed == seed_before  # no new seed is drawn
    creator_after = people(after)[0]
    assert creator_after.pk == creator_before.pk
    assert creator_after.label == label_before  # the first person's label never changes at join


def test_the_joiner_gets_the_other_label_and_the_chosen_side_and_join_order_two():
    from forum.services import assign_labels

    topic = make_prop()
    first, second = make_user(), make_user()
    conv = enter_as(first, topic, "pro")
    joined = enter_as(second, topic, "con")
    assert joined.pk == conv.pk
    conv = reload(conv)
    p1, p2 = people(conv)
    assert (p1.user_id, p2.user_id) == (first.pk, second.pk)
    assert (p1.join_order, p2.join_order) == (1, 2)
    assert (p1.side, p2.side) == ("pro", "con")
    assert (p1.label, p2.label) == assign_labels(conv.label_seed)
    assert {p1.label, p2.label} == {"A", "B"}
    assert conv.status == "active"


def test_the_creators_label_is_random_across_many_conversations(settings):
    settings.MAX_OPEN_CONVERSATIONS = 10_000
    user = make_user()
    labels = []
    seeds = []
    for _ in range(40):
        conv = reload(enter_as(user, make_prop(), "pro"))
        labels.append(people(conv)[0].label)
        seeds.append(conv.label_seed)
    assert set(labels) == {"A", "B"}
    assert 8 <= labels.count("A") <= 32
    assert len(set(seeds)) > 20


def test_the_label_does_not_depend_on_the_side_chosen(settings):
    settings.MAX_OPEN_CONVERSATIONS = 10_000
    user = make_user()
    for side in ("pro", "con"):
        labels = {people(reload(enter_as(user, make_prop(), side)))[0].label for _ in range(30)}
        assert labels == {"A", "B"}, side


def test_join_order_is_independent_of_label_and_of_side(settings):
    settings.MAX_OPEN_CONVERSATIONS = 10_000
    first, second = make_user(), make_user()
    seen = set()
    for i in range(30):
        topic = make_prop()
        creator_side = "pro" if i % 2 else "con"
        conv = enter_as(first, topic, creator_side)
        enter_as(second, topic, "con" if creator_side == "pro" else "pro")
        p1, p2 = people(reload(conv))
        assert (p1.join_order, p2.join_order) == (1, 2)
        assert p1.user_id == first.pk
        seen.add((p1.label, p1.side))
    assert len(seen) == 4  # every (label, side) combination for the person who entered first


# --- branch 1: the user's own conversation, whatever the side ------------------------------------------------------------


@pytest.mark.parametrize("first,again", [("pro", "pro"), ("pro", "con"), ("con", "pro"), ("con", "con")])
def test_the_users_own_conversation_is_returned_whatever_side_is_asked_for(first, again):
    user, topic = make_user(), make_prop()
    conv = enter_as(user, topic, first)
    assert enter_as(user, topic, again).pk == conv.pk
    conv = reload(conv)
    assert conv.participants.count() == 1
    assert people(conv)[0].side == first  # the stored side is not changed


def test_own_active_conversation_is_returned_whatever_the_side():
    world = make_active_via_service()  # u1 pro, u2 con
    assert enter_as(world.u1, world.topic, "con").pk == world.conv.pk
    assert enter_as(world.u2, world.topic, "pro").pk == world.conv.pk
    world.conv.refresh_from_db()
    assert [p.side for p in people(world.conv)] == ["pro", "con"]


def test_the_own_conversation_check_comes_before_the_open_limit(settings):
    settings.MAX_OPEN_CONVERSATIONS = 1
    user, topic = make_user(), make_prop()
    conv = enter_as(user, topic, "pro")
    assert enter_as(user, topic, "con").pk == conv.pk


def test_the_own_conversation_check_comes_before_the_side_is_used_to_pair():
    """A waiter on the topic on the opposite side does not steal a person who already has a conversation there."""
    topic = make_prop()
    user = make_user()
    mine = enter_as(user, topic, "pro")
    conv = enter_as(make_user(), topic, "con")  # joins the user's own waiting conversation
    assert conv.pk == mine.pk
    assert enter_as(user, topic, "con").pk == mine.pk


# --- branch 3: joining only the OPPOSITE side -----------------------------------------------------------------------------


@pytest.mark.parametrize("waiter,joiner", [("pro", "con"), ("con", "pro")])
def test_a_person_joins_a_waiting_conversation_only_on_the_opposite_side(waiter, joiner):
    topic = make_prop()
    conv = enter_as(make_user(), topic, waiter)
    second = make_user()
    assert enter_as(second, topic, joiner).pk == conv.pk
    conv = reload(conv)
    assert conv.status == "active"
    assert [p.side for p in people(conv)] == [waiter, joiner]


@pytest.mark.parametrize("side", ["pro", "con"])
def test_two_people_waiting_on_the_same_side_never_pair(side):
    topic = make_prop()
    first, second = make_user(), make_user()
    a = enter_as(first, topic, side)
    b = enter_as(second, topic, side)
    assert a.pk != b.pk
    for conv in (reload(a), reload(b)):
        assert conv.status == "open" and conv.participants.count() == 1


def test_a_third_person_on_the_same_side_still_does_not_join_either_waiter():
    topic = make_prop()
    a = enter_as(make_user(), topic, "pro")
    b = enter_as(make_user(), topic, "pro")
    c = enter_as(make_user(), topic, "pro")
    assert len({a.pk, b.pk, c.pk}) == 3
    assert all(reload(x).status == "open" for x in (a, b, c))


def test_the_oldest_waiter_on_the_opposite_side_is_joined_first():
    topic = make_prop()
    old = make_waiting(topic, created_at=at(1), side="pro", seed=11)
    new = make_waiting(topic, created_at=at(2), side="pro", seed=12)
    assert enter_as(make_user(), topic, "con").pk == old.pk
    assert enter_as(make_user(), topic, "con").pk == new.pk
    assert reload(old).status == reload(new).status == "active"


def test_only_opposite_side_waiters_are_candidates_even_when_a_same_side_waiter_is_older():
    topic = make_prop()
    same_side_older = make_waiting(topic, created_at=at(1), side="con", seed=21)
    opposite_newer = make_waiting(topic, created_at=at(2), side="pro", seed=22)
    joined = enter_as(make_user(), topic, "con")
    assert joined.pk == opposite_newer.pk
    assert reload(same_side_older).status == "open"


def test_the_oldest_of_several_opposite_waiters_is_chosen_when_both_sides_are_present():
    """Waiters on both sides can only come from stored data (people on opposite sides pair at once). The oldest
    waiter holding the OPPOSITE side of the joiner's choice is the one joined."""
    topic = make_prop()
    w1 = make_waiting(topic, created_at=at(1), side="con", seed=31)
    w2 = make_waiting(topic, created_at=at(2), side="pro", seed=32)
    w3 = make_waiting(topic, created_at=at(3), side="con", seed=33)
    assert enter_as(make_user(), topic, "pro").pk == w1.pk
    assert enter_as(make_user(), topic, "con").pk == w2.pk
    assert enter_as(make_user(), topic, "pro").pk == w3.pk


def test_the_joiner_side_is_the_side_they_chose_and_the_two_sides_always_differ():
    topic = make_prop()
    conv = enter_as(make_user(), topic, "con")
    enter_as(make_user(), topic, "pro")
    assert sorted(p.side for p in people(reload(conv))) == ["con", "pro"]


def test_the_other_topic_and_closed_and_synthetic_waiters_are_still_not_joined():
    from forum.models import Conversation, Participant

    topic, other_topic = make_prop(), make_prop()
    make_waiting(other_topic, side="pro", seed=41)
    closed = make_waiting(topic, side="pro", seed=42)
    Conversation.objects.filter(pk=closed.pk).update(status="closed")
    synthetic = Conversation.objects.create(topic=topic, status="open", source="synthetic")
    Participant.objects.create(conversation=synthetic, user=None, label="A", join_order=1)
    joiner = make_user()
    conv = enter_as(joiner, topic, "con")
    assert conv.pk not in (closed.pk, synthetic.pk)
    conv = reload(conv)
    assert conv.status == "open" and [p.side for p in people(conv)] == ["con"]


def test_a_person_never_joins_their_own_waiting_conversation_through_the_side_rule():
    topic = make_prop()
    user = make_user()
    mine = enter_as(user, topic, "pro")
    assert enter_as(user, topic, "con").pk == mine.pk
    assert reload(mine).participants.count() == 1


# --- legacy waiting rows -----------------------------------------------------------------------------------------------------


def test_a_legacy_waiter_with_a_blank_side_counts_as_pro_a_con_joiner_joins_it():
    topic = make_prop()
    legacy = make_waiting(topic)  # label "A", no seed, blank side
    assert people(legacy)[0].side == "" and legacy.label_seed is None
    joined = enter_as(make_user(), topic, "con")
    assert joined.pk == legacy.pk
    assert reload(legacy).status == "active"


def test_a_pro_joiner_does_not_join_a_legacy_blank_waiter():
    topic = make_prop()
    legacy = make_waiting(topic)
    conv = enter_as(make_user(), topic, "pro")
    assert conv.pk != legacy.pk
    assert reload(legacy).status == "open" and reload(legacy).participants.count() == 1


def test_joining_an_old_null_seed_row_draws_a_seed_then_and_assigns_both_labels():
    from forum.services import assign_labels

    topic = make_prop()
    legacy = make_waiting(topic)
    first_user = people(legacy)[0].user
    joiner = make_user()
    enter_as(joiner, topic, "con")
    conv = reload(legacy)
    assert isinstance(conv.label_seed, int)
    p1, p2 = people(conv)
    assert (p1.user_id, p2.user_id) == (first_user.pk, joiner.pk)
    assert (p1.label, p2.label) == assign_labels(conv.label_seed)
    assert {p1.label, p2.label} == {"A", "B"}
    assert p2.side == "con"


def test_old_null_seed_rows_get_random_labels_across_many_joins(settings):
    settings.MAX_OPEN_CONVERSATIONS = 10_000
    firsts = set()
    for _ in range(30):
        topic = make_prop()
        legacy = make_waiting(topic)
        enter_as(make_user(), topic, "con")
        firsts.add(people(reload(legacy))[0].label)
    assert firsts == {"A", "B"}


def test_a_legacy_blank_side_row_can_still_be_ended_and_posted_to():
    user = make_user()
    legacy = make_waiting(user=user)
    assert svc().post_message(user, legacy, "hello there friend").seq_no == 1
    svc().end_conversation(user, legacy)
    assert reload(legacy).status == "closed"


# --- limits on entering, with sides --------------------------------------------------------------------------------------


def test_too_many_open_applies_to_starting_and_to_joining_with_a_side(settings):
    settings.MAX_OPEN_CONVERSATIONS = 2
    busy = make_user()
    for _ in range(2):
        enter_as(busy, make_prop(), "pro")
    topic = make_prop()
    waiting = enter_as(make_user(), topic, "pro")
    assert rejection(svc().enter_proposition, busy, topic, "con").code == "too_many_open"  # a join
    assert rejection(svc().enter_proposition, busy, make_prop(), "pro").code == "too_many_open"  # a new one
    assert reload(waiting).status == "open" and reload(waiting).participants.count() == 1


def test_a_hidden_proposition_cannot_be_entered_on_either_side():
    topic = make_prop(hidden=True)
    for side in ("pro", "con"):
        assert rejection(svc().enter_proposition, make_user(), topic, side).code == "hidden"


def test_a_bad_side_is_refused_before_anything_is_created_even_on_a_topic_with_a_waiter():
    topic = make_prop()
    waiting = enter_as(make_user(), topic, "pro")
    before = counts()
    assert rejection(svc().enter_proposition, make_user(), topic, "middle").code == "invalid_side"
    assert counts() == before
    assert reload(waiting).participants.count() == 1


# --- posting while waiting: every limit unchanged ------------------------------------------------------------------------


@pytest.fixture
def waiter(clock):
    user, topic = make_user(), make_prop()
    conv = enter_as(user, topic, "pro")
    return type("W", (), {"user": user, "topic": topic, "conv": conv})


def runs(conv):
    from moderation.models import ModerationRun

    return ModerationRun.objects.filter(conversation=conv)


def test_the_first_person_can_post_while_waiting_and_the_conversation_stays_waiting(waiter):
    message = svc().post_message(waiter.user, waiter.conv, "my opening message here")
    assert message.seq_no == 1 and message.author_type == "user"
    assert reload(waiter.conv).status == "open"
    assert reload(waiter.conv).participants.count() == 1


def test_a_post_while_waiting_creates_one_pending_live_run(waiter):
    message = svc().post_message(waiter.user, waiter.conv, "my opening message here")
    (run,) = runs(waiter.conv)
    assert (run.kind, run.status, run.trigger_message_id, run.snapshot_seq) == ("live", "pending", message.pk, 1)


def test_the_gap_applies_while_waiting(waiter, clock):
    svc().post_message(waiter.user, waiter.conv, "my opening message here")
    clock.advance(12)
    exc = rejection(svc().post_message, waiter.user, waiter.conv, "and another")
    assert exc.code == "too_fast" and exc.retry_after == 18
    assert "Please wait 18 more seconds" in exc.message
    clock.advance(18)
    assert svc().post_message(waiter.user, waiter.conv, "and another").seq_no == 2


def test_the_length_and_empty_limits_apply_while_waiting(waiter):
    before = counts()
    assert rejection(svc().post_message, waiter.user, waiter.conv, "  ").code == "empty"
    assert rejection(svc().post_message, waiter.user, waiter.conv, "a" * 3001).code == "too_long"
    assert counts() == before
    assert svc().post_message(waiter.user, waiter.conv, "a" * 3000).char_count == 3000


def test_the_thirty_message_limit_counts_all_user_messages_while_waiting(waiter, clock):
    for i in range(29):
        clock.advance(31)
        svc().post_message(waiter.user, waiter.conv, f"waiting message number {i + 1}")
    assert reload(waiter.conv).status == "open"
    clock.advance(31)
    assert svc().post_message(waiter.user, waiter.conv, "the thirtieth").seq_no == 30
    assert reload(waiter.conv).status == "closed"
    clock.advance(31)
    exc = rejection(svc().post_message, waiter.user, waiter.conv, "the thirty first")
    assert exc.code == "closed" and "limit of 30 messages" in exc.message


def test_thirty_messages_are_in_total_across_the_waiting_and_the_active_phase(waiter, clock, settings):
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 4
    svc().post_message(waiter.user, waiter.conv, "one while waiting")
    clock.advance(31)
    svc().post_message(waiter.user, waiter.conv, "two while waiting")
    second = make_user()
    enter_as(second, waiter.topic, "con")
    clock.advance(1)
    svc().post_message(second, waiter.conv, "three after joining")
    clock.advance(31)
    svc().post_message(waiter.user, waiter.conv, "four")
    assert reload(waiter.conv).status == "closed"


def test_a_closed_waiting_conversation_refuses_posts(waiter):
    svc().end_conversation(waiter.user, waiter.conv)
    exc = rejection(svc().post_message, waiter.user, waiter.conv, "hello there friend")
    assert exc.code == "closed" and "You ended this conversation." in exc.message


def test_a_stranger_cannot_post_into_a_waiting_conversation(waiter):
    assert rejection(svc().post_message, make_user(), waiter.conv, "hello there friend").code == "not_participant"


def test_nothing_says_the_person_must_wait_for_someone_to_join(waiter):
    import inspect

    import forum.services as services

    assert "once someone else joins" not in inspect.getsource(services)
    for text in ("", "a" * 5000, "fine text here"):
        try:
            svc().post_message(waiter.user, waiter.conv, text)
        except services.PostRejected as exc:
            assert exc.code != "waiting"
            assert "someone else joins" not in exc.message


def test_the_joiner_reads_everything_posted_before_joining(waiter, clock):
    from forum.viewmodels import conversation_view

    svc().post_message(waiter.user, waiter.conv, "first while waiting")
    clock.advance(31)
    svc().post_message(waiter.user, waiter.conv, "second while waiting")
    second = make_user()
    enter_as(second, waiter.topic, "con")
    result = conversation_view(second, reload(waiter.conv))
    assert [m["text"] for m in result["messages"]] == ["first while waiting", "second while waiting"]
    assert [m["kind"] for m in result["messages"]] == ["other", "other"]


@pytest.mark.django_db(transaction=True)
def test_sync_mode_calls_the_pipeline_for_a_post_while_waiting(waiter, settings, fake_pipeline):
    settings.MODERATION_RUN_MODE = "sync"
    svc().post_message(waiter.user, waiter.conv, "my opening message here")
    assert len(fake_pipeline.calls) == 1
    assert fake_pipeline.calls[0]["in_atomic_block"] is False


def test_moderator_posts_in_a_waiting_conversation_do_not_change_the_rules(waiter, clock):
    first = svc().post_message(waiter.user, waiter.conv, "my opening message here")
    orm_moderator_message(waiter.conv, in_reply_to=first)
    clock.advance(31)
    assert svc().post_message(waiter.user, waiter.conv, "and a second message").seq_no == 3


# --- architect rulings ---------------------------------------------------------------------------------------------------


def test_invalid_side_is_checked_before_hidden_and_before_too_many_open(settings):
    hidden = make_prop(hidden=True)
    assert rejection(svc().enter_proposition, make_user(), hidden, "sideways").code == "invalid_side"
    settings.MAX_OPEN_CONVERSATIONS = 1
    busy = make_user()
    enter_as(busy, make_prop(), "pro")
    assert rejection(svc().enter_proposition, busy, make_prop(), None).code == "invalid_side"
    assert rejection(svc().enter_proposition, busy, make_prop(), "pro").code == "too_many_open"


def test_joining_a_legacy_blank_side_waiter_does_not_backfill_its_side():
    topic = make_prop()
    legacy = make_waiting(topic)
    enter_as(make_user(), topic, "con")
    first, second = people(reload(legacy))
    assert first.side == ""  # left as stored; only the joiner's side is written
    assert second.side == "con"
    assert reload(legacy).status == "active"
