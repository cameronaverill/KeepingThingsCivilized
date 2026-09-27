"""enter_proposition: the pairing rule in all its branches, hidden propositions, the open-conversation limit."""
import pytest
from fsvc_testkit import enter  # noqa: E402

from fsvc_testkit import (
    assert_plain, at, counts, make_active_via_service, make_prop, make_user, make_waiting, rejection, svc,
)  # fmt: skip

pytestmark = pytest.mark.django_db


def parts(conversation):
    return list(conversation.participants.order_by("join_order"))


# --- branch 3: a new waiting conversation --------------------------------------------------------------------------------


def test_the_first_person_starts_a_waiting_conversation():
    user, topic = make_user(), make_prop()
    conv = enter(user, topic)
    conv.refresh_from_db()
    assert conv.status == "open"
    assert conv.topic_id == topic.pk
    assert conv.source == "human"
    assert conv.experiment_id is None
    assert conv.ended_by_id is None and conv.ended_at is None
    (only,) = parts(conv)
    assert only.user_id == user.pk
    assert only.join_order == 1


def test_a_waiting_conversation_has_no_messages_and_no_runs():
    from moderation.models import ModerationRun
    from forum.models import Message

    enter(make_user(), make_prop())
    assert Message.objects.count() == 0
    assert ModerationRun.objects.count() == 0


# --- branch 2: joining ---------------------------------------------------------------------------------------------------


def test_the_second_person_joins_the_waiting_conversation_and_it_becomes_active():
    topic = make_prop()
    first, second = make_user(), make_user()
    waiting = enter(first, topic)
    joined = enter(second, topic)
    assert joined.pk == waiting.pk
    joined.refresh_from_db()
    assert joined.status == "active"
    ordered = parts(joined)
    assert [p.user_id for p in ordered] == [first.pk, second.pk]
    assert [p.join_order for p in ordered] == [1, 2]
    assert sorted(p.label for p in ordered) == ["A", "B"]
    assert joined.label_seed is not None
    from forum.models import Conversation

    assert Conversation.objects.filter(topic=topic).count() == 1


def test_a_third_person_does_not_join_a_full_conversation_and_starts_their_own():
    world = make_active_via_service()
    third = make_user()
    conv = enter(third, world.topic)
    assert conv.pk != world.conv.pk
    conv.refresh_from_db()
    assert conv.status == "open"
    assert [p.user_id for p in parts(conv)] == [third.pk]
    world.conv.refresh_from_db()
    assert world.conv.participants.count() == 2


def test_the_joiner_is_never_added_to_a_conversation_with_two_participants():
    world = make_active_via_service()
    for _ in range(3):
        enter(make_user(), world.topic)
    world.conv.refresh_from_db()
    assert world.conv.participants.count() == 2
    assert world.conv.status == "active"


# --- branch 1: the user's own conversation -------------------------------------------------------------------------------


def test_entering_again_returns_the_users_own_waiting_conversation():
    user, topic = make_user(), make_prop()
    first = enter(user, topic)
    again = enter(user, topic)
    assert again.pk == first.pk
    assert first.participants.count() == 1


def test_entering_again_returns_the_users_own_active_conversation_for_both_people():
    world = make_active_via_service()
    assert enter(world.u1, world.topic).pk == world.conv.pk
    assert enter(world.u2, world.topic).pk == world.conv.pk
    world.conv.refresh_from_db()
    assert world.conv.participants.count() == 2


def test_a_user_is_never_paired_with_themself():
    user, topic = make_user(), make_prop()
    first = enter(user, topic)
    for _ in range(3):
        assert enter(user, topic).pk == first.pk
    first.refresh_from_db()
    assert first.status == "open"
    assert first.participants.count() == 1
    assert first.participants.get().user_id == user.pk


def test_the_users_own_conversation_wins_over_someone_elses_waiting_one_on_the_same_topic():
    world = make_active_via_service()  # u1 and u2 are talking
    stranger = make_user()
    waiting = enter(stranger, world.topic)  # no waiting one to join: starts their own
    assert waiting.pk != world.conv.pk
    assert enter(world.u1, world.topic).pk == world.conv.pk
    waiting.refresh_from_db()
    assert waiting.status == "open" and waiting.participants.count() == 1


def test_the_users_own_waiting_conversation_wins_over_an_older_waiting_one_of_someone_else():
    topic = make_prop()
    older = make_waiting(topic, created_at=at(1))
    mine_user = make_user()
    mine = make_waiting(topic, mine_user, created_at=at(2))
    assert enter(mine_user, topic).pk == mine.pk
    older.refresh_from_db()
    assert older.participants.count() == 1 and older.status == "open"


# --- oldest waiting first ------------------------------------------------------------------------------------------------


def test_the_oldest_waiting_conversation_is_joined_first():
    topic = make_prop()
    oldest = make_waiting(topic, created_at=at(1))
    newer = make_waiting(topic, created_at=at(2))
    newest = make_waiting(topic, created_at=at(3))
    first_joiner, second_joiner, third_joiner = make_user(), make_user(), make_user()
    assert enter(first_joiner, topic).pk == oldest.pk
    assert enter(second_joiner, topic).pk == newer.pk
    assert enter(third_joiner, topic).pk == newest.pk


def test_oldest_means_by_creation_not_by_row_order():
    """The older conversation may have been saved later (a higher id); creation time decides."""
    topic = make_prop()
    saved_first_but_newer = make_waiting(topic, created_at=at(5))
    saved_second_but_older = make_waiting(topic, created_at=at(2))
    assert saved_second_but_older.pk > saved_first_but_newer.pk
    assert enter(make_user(), topic).pk == saved_second_but_older.pk


# --- what is never joined ------------------------------------------------------------------------------------------------


def test_waiting_conversations_on_other_topics_are_not_joined():
    one, two = make_prop(), make_prop()
    waiting = enter(make_user(), one)
    other = enter(make_user(), two)
    assert other.pk != waiting.pk
    waiting.refresh_from_db()
    assert waiting.participants.count() == 1


def test_a_closed_waiting_conversation_is_not_joined():
    topic = make_prop()
    creator = make_user()
    waiting = enter(creator, topic)
    svc().end_conversation(creator, waiting)
    newcomer = make_user()
    conv = enter(newcomer, topic)
    assert conv.pk != waiting.pk
    conv.refresh_from_db()
    assert conv.status == "open" and conv.participants.count() == 1
    waiting.refresh_from_db()
    assert waiting.status == "closed" and waiting.participants.count() == 1


def test_a_synthetic_waiting_conversation_is_never_joined_by_a_person():
    from forum.models import Conversation, Participant

    topic = make_prop()
    synthetic = Conversation.objects.create(topic=topic, status="open", source="synthetic")
    Participant.objects.create(conversation=synthetic, user=None, label="A", join_order=1)
    conv = enter(make_user(), topic)
    assert conv.pk != synthetic.pk
    assert conv.source == "human"
    assert synthetic.participants.count() == 1


def test_after_ending_a_conversation_entering_the_topic_again_starts_a_fresh_one():
    world = make_active_via_service()
    svc().end_conversation(world.u1, world.conv)
    fresh = enter(world.u1, world.topic)
    assert fresh.pk != world.conv.pk
    fresh.refresh_from_db()
    assert fresh.status == "open" and fresh.participants.count() == 1
    world.conv.refresh_from_db()
    assert world.conv.status == "closed"


def test_the_ex_partner_who_enters_after_a_closed_conversation_joins_the_new_waiting_one():
    world = make_active_via_service()
    svc().end_conversation(world.u1, world.conv)
    fresh = enter(world.u1, world.topic)
    again = enter(world.u2, world.topic)
    assert again.pk == fresh.pk
    fresh.refresh_from_db()
    assert fresh.status == "active" and fresh.participants.count() == 2


def test_entering_creates_no_messages_no_runs_and_no_propositions():
    user, topic = make_user(), make_prop()
    before = counts()
    enter(user, topic)
    after = counts()
    assert after["messages"] == before["messages"] == 0
    assert after["runs"] == before["runs"] == 0
    assert after["topics"] == before["topics"]
    assert after["conversations"] == before["conversations"] + 1
    assert after["participants"] == before["participants"] + 1


# --- hidden propositions -------------------------------------------------------------------------------------------------


def test_a_hidden_proposition_cannot_be_entered():
    topic = make_prop(hidden=True)
    before = counts()
    exc = rejection(enter, make_user(), topic)
    assert exc.code == "hidden"
    assert_plain(exc)
    assert counts() == before


def test_a_hidden_proposition_is_not_joined_even_when_someone_is_waiting_on_it():
    topic = make_prop()
    waiting = enter(make_user(), topic)
    from forum.models import Topic

    Topic.objects.filter(pk=topic.pk).update(hidden=True)
    topic.refresh_from_db()
    exc = rejection(enter, make_user(), topic)
    assert exc.code == "hidden"
    waiting.refresh_from_db()
    assert waiting.status == "open" and waiting.participants.count() == 1


def test_unhiding_makes_the_proposition_enterable_again():
    from forum.models import Topic

    topic = make_prop(hidden=True)
    Topic.objects.filter(pk=topic.pk).update(hidden=False)
    topic.refresh_from_db()
    assert enter(make_user(), topic).pk


def test_the_hidden_flag_is_read_fresh_not_from_a_stale_instance():
    from forum.models import Topic

    topic = make_prop()
    Topic.objects.filter(pk=topic.pk).update(hidden=True)  # the instance in hand still says hidden=False
    assert rejection(enter, make_user(), topic).code == "hidden"


# --- too many open conversations -----------------------------------------------------------------------------------------


def open_n(user, n):
    return [enter(user, make_prop()) for _ in range(n)]


def test_there_is_no_limit_by_default(settings):
    assert settings.MAX_OPEN_CONVERSATIONS is None


def test_without_a_limit_many_conversations_can_be_open_at_once():
    user = make_user()
    assert len(open_n(user, 12)) == 12


def test_a_sixth_open_conversation_is_refused_with_the_wording_from_the_plan(settings):
    settings.MAX_OPEN_CONVERSATIONS = 5  # the limit is off by default; this is the capacity to switch it back on
    user = make_user()
    open_n(user, 5)
    topic = make_prop()
    before = counts()
    exc = rejection(enter, user, topic)
    assert exc.code == "too_many_open"
    assert "You already have 5 open conversations." in exc.message
    assert "End one before starting another." in exc.message
    assert_plain(exc)
    assert counts() == before


def test_the_fifth_is_allowed(settings):
    settings.MAX_OPEN_CONVERSATIONS = 5
    user = make_user()
    assert len(open_n(user, 5)) == 5


def test_the_number_in_the_message_comes_from_settings(settings):
    settings.MAX_OPEN_CONVERSATIONS = 2
    user = make_user()
    open_n(user, 2)
    exc = rejection(enter, user, make_prop())
    assert "You already have 2 open conversations." in exc.message
    assert "5" not in exc.message


def test_active_conversations_count_as_open(settings):
    settings.MAX_OPEN_CONVERSATIONS = 2
    user = make_user()
    for _ in range(2):
        topic = make_prop()
        enter(make_user(), topic)  # someone waiting
        joined = enter(user, topic)
        joined.refresh_from_db()
        assert joined.status == "active"
    assert rejection(enter, user, make_prop()).code == "too_many_open"


def test_joining_someone_elses_waiting_conversation_is_refused_at_the_limit_too(settings):
    settings.MAX_OPEN_CONVERSATIONS = 2
    user = make_user()
    open_n(user, 2)
    topic = make_prop()
    waiting = enter(make_user(), topic)
    assert rejection(enter, user, topic).code == "too_many_open"
    waiting.refresh_from_db()
    assert waiting.status == "open" and waiting.participants.count() == 1


def test_returning_to_an_existing_conversation_is_allowed_at_the_limit(settings):
    settings.MAX_OPEN_CONVERSATIONS = 2
    user = make_user()
    mine = open_n(user, 2)
    assert enter(user, mine[0].topic).pk == mine[0].pk


def test_closed_conversations_do_not_count(settings):
    settings.MAX_OPEN_CONVERSATIONS = 2
    user = make_user()
    first, _ = open_n(user, 2)
    svc().end_conversation(user, first)
    assert enter(user, make_prop()).pk


def test_ending_one_makes_room_again(settings):
    settings.MAX_OPEN_CONVERSATIONS = 2
    user = make_user()
    first, _ = open_n(user, 2)
    assert rejection(enter, user, make_prop()).code == "too_many_open"
    svc().end_conversation(user, first)
    assert enter(user, make_prop()).pk


def test_the_limit_is_per_user(settings):
    settings.MAX_OPEN_CONVERSATIONS = 2
    busy, free = make_user(), make_user()
    open_n(busy, 2)
    assert enter(free, make_prop()).pk


def test_someone_elses_ended_conversation_does_not_free_a_slot_for_me(settings):
    settings.MAX_OPEN_CONVERSATIONS = 2
    user = make_user()
    open_n(user, 2)
    other = make_user()
    conv = enter(other, make_prop())
    svc().end_conversation(other, conv)
    assert rejection(enter, user, make_prop()).code == "too_many_open"


def test_a_conversation_the_other_participant_ended_no_longer_counts_for_me(settings):
    settings.MAX_OPEN_CONVERSATIONS = 2
    world = make_active_via_service()
    other_topic = make_prop()
    enter(world.u1, other_topic)  # u1 now has two
    assert rejection(enter, world.u1, make_prop()).code == "too_many_open"
    svc().end_conversation(world.u2, world.conv)  # the other person ends it
    assert enter(world.u1, make_prop()).pk


def test_a_message_sent_in_a_conversation_does_not_change_what_entering_returns(clock):
    world = make_active_via_service()
    svc().post_message(world.u1, world.conv, "hello there friend")
    assert enter(world.u1, world.topic).pk == world.conv.pk


# --- architect rulings ---------------------------------------------------------------------------------------------------


def test_the_hidden_check_comes_first_even_for_a_person_who_already_has_a_conversation_there():
    """Ruling: a hidden proposition cannot be entered, even by someone with a conversation on it (they keep reading
    the conversation through its own page)."""
    from forum.models import Topic

    world = make_active_via_service()
    Topic.objects.filter(pk=world.topic.pk).update(hidden=True)
    for user in (world.u1, world.u2):
        exc = rejection(enter, user, Topic.objects.get(pk=world.topic.pk))
        assert exc.code == "hidden"
    world.conv.refresh_from_db()
    assert world.conv.status == "active"


def test_a_new_waiting_conversation_stores_its_seed_and_creators_label_at_creation():
    from forum.services import assign_labels

    conv = enter(make_user(), make_prop())
    conv.refresh_from_db()
    (only,) = parts(conv)
    assert only.join_order == 1
    assert isinstance(conv.label_seed, int)
    assert only.label == assign_labels(conv.label_seed)[0]
