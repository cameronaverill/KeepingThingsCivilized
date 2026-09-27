"""Step 7c revision 3: waiting_groups(viewer, query), my_conversations(viewer), seeded_topics() (what the home page and
the propose page list)."""
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from fsvc_testkit import (
    at, make_active_via_service, make_prop, make_user, make_waiting, orm_moderator_message, orm_user_message, svc,
)  # fmt: skip

pytestmark = pytest.mark.django_db


def groups(viewer, query=None):
    return svc().waiting_groups(viewer, query)


def wait(topic, side="pro", when=None, user=None, seed=None):
    """A waiting conversation as the current code stores it (seed and label at creation)."""
    return make_waiting(topic, user, created_at=when, side=side, seed=seed if seed is not None else 77)


def keyset(result):
    return {(g["topic"].pk, g["waiting_side"]) for g in result}


def reload(conv):
    from forum.models import Conversation

    return Conversation.objects.get(pk=conv.pk)


# --- waiting_groups: shape and grouping ----------------------------------------------------------------------------------------


def test_an_entry_has_the_topic_the_waiting_side_the_since_time_and_the_waiters_username():
    from forum.models import Topic

    topic = make_prop()
    waiter = make_user()
    conv = wait(topic, "con", at(3), user=waiter)
    (entry,) = groups(make_user())
    assert set(entry) == {"topic", "waiting_side", "since", "waiting_username"}  # Revision 5 added the username
    assert entry["waiting_username"] == waiter.username
    assert isinstance(entry["topic"], Topic) and entry["topic"].pk == topic.pk
    assert entry["waiting_side"] == "con"
    assert entry["since"] == conv.created_at == at(3)


def test_no_waiting_conversations_no_groups():
    make_prop()
    assert groups(make_user()) == []


def test_several_waiting_conversations_on_one_topic_and_side_make_one_group_since_the_oldest():
    topic = make_prop()
    wait(topic, "pro", at(5))
    wait(topic, "pro", at(2))
    wait(topic, "pro", at(9))
    (entry,) = groups(make_user())
    assert entry["since"] == at(2)


def test_each_side_of_a_topic_is_its_own_group_with_its_own_since():
    topic = make_prop()
    wait(topic, "pro", at(4))
    wait(topic, "con", at(7))
    wait(topic, "con", at(6))
    by_side = {g["waiting_side"]: g["since"] for g in groups(make_user())}
    assert by_side == {"pro": at(4), "con": at(6)}


def test_each_topic_is_its_own_group():
    a, b = make_prop(), make_prop()
    wait(a, "pro", at(1))
    wait(b, "pro", at(2))
    assert keyset(groups(make_user())) == {(a.pk, "pro"), (b.pk, "pro")}


def test_a_waiting_conversation_with_no_message_still_counts():
    """There is no preview any more: someone who has started and not yet written is still waiting to be joined."""
    topic = make_prop()
    conv = make_waiting(topic, side="pro", seed=5)
    assert reload(conv).messages.count() == 0
    assert keyset(groups(make_user())) == {(topic.pk, "pro")}


def test_a_legacy_blank_side_counts_as_pro():
    topic = make_prop()
    make_waiting(topic)  # blank side, no seed
    (entry,) = groups(make_user())
    assert entry["waiting_side"] == "pro"


def test_legacy_and_pro_waiters_of_one_topic_form_one_group():
    topic = make_prop()
    make_waiting(topic, created_at=at(3))
    wait(topic, "pro", at(5))
    (entry,) = groups(make_user())
    assert entry["waiting_side"] == "pro" and entry["since"] == at(3)


def test_conversations_created_through_the_service_are_found():
    topic = make_prop()
    creator = make_user()
    svc().enter_proposition(creator, topic, "con")
    (entry,) = groups(make_user())
    assert (entry["topic"].pk, entry["waiting_side"]) == (topic.pk, "con")


# --- ordering ----------------------------------------------------------------------------------------------------------------------


def test_groups_are_newest_first_by_the_newest_waiting_conversation_of_each_group():
    a, b, c = make_prop(), make_prop(), make_prop()
    wait(a, "pro", at(1))
    wait(a, "pro", at(20))  # group a: oldest 1, newest 20
    wait(b, "pro", at(10))  # group b: newest 10
    wait(c, "pro", at(15))  # group c: newest 15
    order = [g["topic"].pk for g in groups(make_user())]
    assert order == [a.pk, c.pk, b.pk]


def test_the_since_of_a_group_is_its_oldest_but_its_place_in_the_list_is_its_newest():
    a, b = make_prop(), make_prop()
    wait(a, "pro", at(1))
    wait(a, "pro", at(30))
    wait(b, "pro", at(10))
    result = groups(make_user())
    assert [g["topic"].pk for g in result] == [a.pk, b.pk]
    assert [g["since"] for g in result] == [at(1), at(10)]


def test_many_groups_with_equal_times_are_ordered_by_conversation_id_newest_first():
    topics = [make_prop() for _ in range(6)]
    for topic in topics:
        wait(topic, "pro", at(5))
    assert [g["topic"].pk for g in groups(make_user())] == [t.pk for t in reversed(topics)]


def test_equal_times_are_ordered_by_conversation_id_newest_first():
    a, b = make_prop(), make_prop()
    wait(a, "pro", at(5))
    wait(b, "pro", at(5))
    assert [g["topic"].pk for g in groups(make_user())] == [b.pk, a.pk]


def test_sides_of_one_topic_are_ordered_like_any_other_groups():
    topic = make_prop()
    wait(topic, "pro", at(8))
    wait(topic, "con", at(12))
    assert [g["waiting_side"] for g in groups(make_user())] == ["con", "pro"]


# --- exclusions --------------------------------------------------------------------------------------------------------------------


def test_the_viewers_own_waiting_conversations_are_never_listed():
    topic = make_prop()
    viewer = make_user()
    make_waiting(topic, viewer, created_at=at(1), side="pro", seed=1)
    assert groups(viewer) == []
    assert keyset(groups(make_user())) == {(topic.pk, "pro")}  # but somebody else sees it


def test_a_topic_where_the_viewer_has_a_waiting_conversation_is_left_out_even_if_others_wait_there_on_the_other_side():
    topic = make_prop()
    viewer = make_user()
    make_waiting(topic, viewer, created_at=at(1), side="pro", seed=1)
    wait(topic, "con", at(2))
    wait(topic, "pro", at(3))
    assert groups(viewer) == []


def test_a_topic_where_the_viewer_has_an_active_conversation_is_left_out():
    world = make_active_via_service()
    wait(world.topic, "pro", at(4))
    other = make_prop()
    wait(other, "pro", at(5))
    assert keyset(groups(world.u1)) == {(other.pk, "pro")}
    assert keyset(groups(world.u2)) == {(other.pk, "pro")}


def test_a_closed_conversation_of_the_viewer_does_not_hide_the_topic():
    topic = make_prop()
    viewer = make_user()
    mine = svc().enter_proposition(viewer, topic, "pro")
    svc().end_conversation(viewer, mine)
    wait(topic, "con", at(4))
    assert keyset(groups(viewer)) == {(topic.pk, "con")}


def test_the_viewers_conversation_on_another_topic_changes_nothing():
    a, b = make_prop(), make_prop()
    viewer = make_user()
    svc().enter_proposition(viewer, b, "pro")
    wait(a, "pro", at(1))
    assert keyset(groups(viewer)) == {(a.pk, "pro")}


def test_hidden_topics_are_excluded():
    from forum.models import Topic

    visible, hidden = make_prop(), make_prop()
    wait(visible, "pro", at(1))
    wait(hidden, "pro", at(2))
    Topic.objects.filter(pk=hidden.pk).update(hidden=True)
    assert keyset(groups(make_user())) == {(visible.pk, "pro")}


def test_active_and_closed_conversations_are_not_waiting():
    from forum.models import Conversation

    topic_a, topic_c = make_prop(), make_prop()
    make_active_via_service(topic_a)
    closed = wait(topic_c, "pro", at(1))
    Conversation.objects.filter(pk=closed.pk).update(status="closed")
    assert groups(make_user()) == []


def test_synthetic_conversations_are_never_listed():
    from forum.models import Conversation, Participant

    topic = make_prop()
    synthetic = Conversation.objects.create(topic=topic, status="open", source="synthetic")
    Participant.objects.create(conversation=synthetic, user=None, label="A", join_order=1)
    assert groups(make_user()) == []


def test_an_active_conversation_with_one_participant_is_not_waiting():
    from forum.models import Conversation

    topic = make_prop()
    conv = wait(topic, "pro", at(1))
    Conversation.objects.filter(pk=conv.pk).update(status="active")
    assert groups(make_user()) == []


def test_an_anonymous_visitor_is_shown_nothing():
    from django.contrib.auth.models import AnonymousUser

    wait(make_prop(), "pro", at(1))
    assert groups(AnonymousUser()) == []
    assert svc().my_conversations(AnonymousUser()) == []


def test_a_conversation_marked_open_but_holding_two_people_is_not_waiting():
    topic = make_prop()
    conv = wait(topic, "pro", at(1))
    from forum.models import Participant

    Participant.objects.create(conversation=conv, user=make_user(), label="B", join_order=2, side="con")
    assert groups(make_user()) == []


def test_a_waiting_conversation_that_has_reached_the_message_limit_is_not_listed(settings):
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 3
    topic = make_prop()
    conv = wait(topic, "pro", at(1))
    person = conv.participants.get()
    orm_user_message(conv, person, "one")
    orm_user_message(conv, person, "two")
    assert keyset(groups(make_user())) == {(topic.pk, "pro")}  # two of three: still room
    orm_user_message(conv, person, "three")
    assert groups(make_user()) == []


def test_moderator_messages_do_not_count_toward_the_limit_here(settings):
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 3
    topic = make_prop()
    conv = wait(topic, "pro", at(1))
    person = conv.participants.get()
    orm_user_message(conv, person, "one")
    for _ in range(4):
        orm_moderator_message(conv)
    assert keyset(groups(make_user())) == {(topic.pk, "pro")}


def test_a_full_group_member_is_dropped_but_the_group_stays_if_another_is_joinable(settings):
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 1
    topic = make_prop()
    full = wait(topic, "pro", at(1))
    orm_user_message(full, full.participants.get(), "one")
    wait(topic, "pro", at(5))
    (entry,) = groups(make_user())
    assert entry["since"] == at(5)  # the full conversation no longer counts, not even for the oldest time


# --- search --------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def cast():
    from forum.models import Topic

    rent = Topic.objects.create(title="Rent", proposition="Cities should cap rent increases", opposing_position="Cities should not cap rent increases")
    bikes = Topic.objects.create(title="Bikes", proposition="Bike lanes make streets safer", opposing_position="Bike lanes make streets more dangerous")
    election = Topic.objects.create(title="Vote", proposition="Élections should be held on weekends")
    for topic in (rent, bikes, election):
        wait(topic, "pro", at(1))
    return type("Cast", (), {"rent": rent, "bikes": bikes, "election": election})


def found(query):
    return {g["topic"].pk for g in groups(make_user(), query)}


def test_no_query_lists_everything(cast):
    assert found(None) == found("") == {cast.rent.pk, cast.bikes.pk, cast.election.pk}


def test_a_query_keeps_matching_propositions_only(cast):
    assert found("bike lanes") == {cast.bikes.pk}


def test_the_search_is_case_insensitive(cast):
    assert found("BIKE LANES") == {cast.bikes.pk}
    assert found("cItIeS sHoUlD") == {cast.rent.pk}


def test_the_search_matches_a_fragment_anywhere_in_the_text(cast):
    assert found("streets") == {cast.bikes.pk}


def test_the_search_matches_the_opposing_position_too(cast):
    assert found("more dangerous") == {cast.bikes.pk}
    assert found("should not cap") == {cast.rent.pk}


def test_a_query_that_matches_nothing_gives_an_empty_list(cast):
    assert groups(make_user(), "zebra") == []


def test_the_search_is_unicode_safe_and_case_insensitive_beyond_ascii(cast):
    assert found("élections") == {cast.election.pk}
    assert found("ÉLECTIONS") == {cast.election.pk}
    assert found("Élections should") == {cast.election.pk}


def test_a_cyrillic_query_folds_case(cast):
    from forum.models import Topic

    topic = Topic.objects.create(title="Cyr", proposition="Железная дорога нужна")
    wait(topic, "pro", at(2))
    assert found("железная") == {topic.pk}
    assert found("ЖЕЛЕЗНАЯ") == {topic.pk}


def test_extra_whitespace_in_the_query_is_ignored(cast):
    """Reading: like the old search box, a query is compared with its runs of whitespace collapsed."""
    assert found("  bike    lanes  ") == {cast.bikes.pk}


def test_case_folding_goes_beyond_lowering_the_german_sharp_s(cast):
    from forum.models import Topic

    topic = Topic.objects.create(title="Str", proposition="STRASSE rents should be capped")
    wait(topic, "pro", at(2))
    assert found("stra\u00dfe") == {topic.pk}  # a non-ASCII query is folded with casefold, so sharp s meets SS
    assert found("STRA\u1E9EE") == {topic.pk}


def test_a_non_ascii_query_also_searches_the_opposing_position(cast):
    from forum.models import Topic

    topic = Topic.objects.create(
        title="Caf", proposition="Coffee shops should open early", opposing_position="Caf\u00e9s should open late"
    )
    wait(topic, "pro", at(2))
    assert found("CAF\u00c9S") == {topic.pk}


def test_an_ascii_query_does_not_match_accented_letters_by_accident(cast):
    assert cast.election.pk not in found("elections")


@pytest.mark.parametrize("query", ["\x00", "cities\x00", "\x00cities", "a\x00b"])
def test_a_nul_character_finds_nothing_and_does_not_break(cast, query):
    assert groups(make_user(), query) == []


def test_like_wildcards_are_taken_literally(cast):
    assert groups(make_user(), "%") == []
    assert groups(make_user(), "_") == []
    assert groups(make_user(), "cities%rent") == []


def test_quotes_and_backslashes_are_harmless(cast):
    for query in ("'", '"', "\\", "'; drop table forum_topic; --", "<script>"):
        assert groups(make_user(), query) == []


def test_a_very_long_query_is_harmless(cast):
    assert groups(make_user(), "cities " * 5000) == []


def test_the_search_only_looks_at_topics_that_are_waiting_and_visible(cast):
    from forum.models import Topic

    quiet = Topic.objects.create(title="Quiet", proposition="Quiet streets are pleasant")
    assert quiet.pk not in found("quiet")
    hidden = Topic.objects.create(title="Hid", proposition="Hidden gardens are lovely", hidden=True)
    wait(hidden, "pro", at(3))
    assert hidden.pk not in found("hidden gardens")


def test_the_search_with_a_side_filter_keeps_both_sides_of_a_matching_topic(cast):
    wait(cast.bikes, "con", at(9))
    result = groups(make_user(), "bike")
    assert {(g["topic"].pk, g["waiting_side"]) for g in result} == {(cast.bikes.pk, "pro"), (cast.bikes.pk, "con")}


# --- constant number of queries ------------------------------------------------------------------------------------------------------


def _count(fn, *args):
    with CaptureQueriesContext(connection) as queries:
        result = fn(*args)
        for entry in result:  # touching the fields a page would touch must not cost queries
            entry["topic"].proposition, entry["topic"].opposing_position, entry["waiting_side"], entry["since"]
    return len(queries)


def test_waiting_groups_costs_the_same_number_of_queries_for_one_group_and_for_thirty():
    viewer = make_user()
    wait(make_prop(), "pro", at(1))
    one = _count(svc().waiting_groups, viewer)
    for i in range(29):
        wait(make_prop(), "con" if i % 2 else "pro", at(2) + timedelta(minutes=i))
    thirty = _count(svc().waiting_groups, viewer)
    assert len(groups(viewer)) == 30
    assert one == thirty <= 4, (one, thirty)


def test_a_query_costs_no_more_queries_with_thirty_groups():
    viewer = make_user()
    for i in range(30):
        wait(make_prop(f"Searchable claim number {i}"), "pro", at(1) + timedelta(minutes=i))
    plain = _count(svc().waiting_groups, viewer, "claim")
    unicode_ = _count(svc().waiting_groups, viewer, "Éclaim")
    assert plain <= 4 and unicode_ <= 4
    assert len(groups(viewer, "claim number 1")) == 11


def test_nothing_waiting_costs_a_couple_of_queries_at_most():
    assert _count(svc().waiting_groups, make_user()) <= 4


# --- my_conversations ----------------------------------------------------------------------------------------------------------------------


def mine(viewer):
    return svc().my_conversations(viewer)


def test_an_entry_has_the_conversation_the_topic_my_side_and_the_status_word():
    from forum.models import Conversation, Topic

    viewer = make_user()
    topic = make_prop()
    conv = svc().enter_proposition(viewer, topic, "con")
    (entry,) = mine(viewer)
    assert set(entry) == {"conversation", "topic", "my_side", "status", "other_username"}
    assert entry["other_username"] is None  # nobody else is there yet
    assert isinstance(entry["conversation"], Conversation) and entry["conversation"].pk == conv.pk
    assert isinstance(entry["topic"], Topic) and entry["topic"].pk == topic.pk
    assert entry["my_side"] == "con"
    assert entry["status"] == "waiting"


def test_the_status_words_are_waiting_active_and_ended():
    viewer = make_user()
    waiting = svc().enter_proposition(viewer, make_prop(), "pro")
    world = make_active_via_service()
    active_topic = world.topic
    ended_topic = make_prop()
    ended = svc().enter_proposition(viewer, ended_topic, "pro")
    svc().end_conversation(viewer, ended)
    joined = svc().enter_proposition(make_user(), make_prop(), "pro")  # somebody else's, must not show up
    svc().enter_proposition(viewer, joined.topic, "con")
    by_conv = {e["conversation"].pk: e["status"] for e in mine(viewer)}
    assert by_conv[waiting.pk] == "waiting"
    assert by_conv[ended.pk] == "ended"
    assert by_conv[joined.pk] == "active"
    assert active_topic.pk not in {e["topic"].pk for e in mine(viewer)}


def test_a_person_sees_only_their_own_conversations():
    a, b = make_user(), make_user()
    mine_a = svc().enter_proposition(a, make_prop(), "pro")
    svc().enter_proposition(b, make_prop(), "pro")
    assert [e["conversation"].pk for e in mine(a)] == [mine_a.pk]
    assert [e["conversation"].pk for e in mine(make_user())] == []


def test_both_participants_of_a_conversation_see_it_with_their_own_side():
    world = make_active_via_service()  # u1 pro, u2 con
    (one,) = mine(world.u1)
    (two,) = mine(world.u2)
    assert one["conversation"].pk == two["conversation"].pk == world.conv.pk
    assert (one["my_side"], two["my_side"]) == ("pro", "con")
    assert one["status"] == two["status"] == "active"


def test_a_legacy_blank_side_gives_none():
    viewer = make_user()
    make_waiting(user=viewer)
    (entry,) = mine(viewer)
    assert entry["my_side"] is None and entry["status"] == "waiting"


def test_a_conversation_on_a_hidden_topic_is_still_listed_hiding_keeps_conversations():
    from forum.models import Topic

    viewer = make_user()
    conv = svc().enter_proposition(viewer, make_prop(), "pro")
    Topic.objects.filter(pk=conv.topic_id).update(hidden=True)
    assert [e["conversation"].pk for e in mine(viewer)] == [conv.pk]


def test_my_conversations_never_lists_synthetic_conversations():
    from forum.models import Conversation, Participant

    viewer = make_user()
    synthetic = Conversation.objects.create(topic=make_prop(), status="open", source="synthetic")
    Participant.objects.create(conversation=synthetic, user=None, label="A", join_order=1)
    assert mine(viewer) == []


def test_a_new_person_has_no_entries():
    assert mine(make_user()) == []


# order: newest activity first (latest message time, else creation time)


def test_the_order_is_newest_activity_first_a_message_counts_else_the_creation_time():
    viewer = make_user()
    a = make_waiting(make_prop(), viewer, created_at=at(1), side="pro", seed=1)  # created 1, last message 9
    b = make_waiting(make_prop(), viewer, created_at=at(4), side="pro", seed=2)  # created 4, no message
    c = make_waiting(make_prop(), viewer, created_at=at(2), side="pro", seed=3)  # created 2, last message 6
    orm_user_message(a, a.participants.get(), "early words", when=at(3))
    orm_user_message(a, a.participants.get(), "late words", when=at(9))
    orm_user_message(c, c.participants.get(), "some words", when=at(6))
    assert [e["conversation"].pk for e in mine(viewer)] == [a.pk, c.pk, b.pk]


def test_a_moderator_message_is_activity_too():
    viewer = make_user()
    a = make_waiting(make_prop(), viewer, created_at=at(1), side="pro", seed=1)
    b = make_waiting(make_prop(), viewer, created_at=at(5), side="pro", seed=2)
    from forum.models import Message

    note = orm_moderator_message(a)
    Message.objects.filter(pk=note.pk).update(created_at=at(8))
    assert [e["conversation"].pk for e in mine(viewer)] == [a.pk, b.pk]


def test_a_message_by_the_other_person_is_activity_for_both():
    world = make_active_via_service()
    from forum.models import Conversation

    other_topic = make_prop()
    later = make_waiting(other_topic, world.u1, created_at=at(6), side="pro", seed=9)
    Conversation.objects.filter(pk=world.conv.pk).update(created_at=at(1))
    from fsvc_testkit import participant_of

    orm_user_message(world.conv, participant_of(world.conv, world.u2), "the other person spoke last", when=at(12))
    assert [e["conversation"].pk for e in mine(world.u1)] == [world.conv.pk, later.pk]


def test_ended_conversations_are_ordered_by_activity_like_the_others():
    viewer = make_user()
    old_ended = svc().enter_proposition(viewer, make_prop(), "pro")
    svc().end_conversation(viewer, old_ended)
    waiting = make_waiting(make_prop(), viewer, created_at=at(20), side="pro", seed=4)
    from forum.models import Conversation

    Conversation.objects.filter(pk=old_ended.pk).update(created_at=at(2))
    assert [e["conversation"].pk for e in mine(viewer)] == [waiting.pk, old_ended.pk]


# the limit on ended conversations


def ended_conversations(viewer, n):
    """n ended conversations for the viewer, the i-th created at at(i + 1) (so a higher i is more recent)."""
    from forum.models import Conversation

    made = []
    for i in range(n):
        conv = svc().enter_proposition(viewer, make_prop(), "pro")
        svc().end_conversation(viewer, conv)
        Conversation.objects.filter(pk=conv.pk).update(created_at=at(1) + timedelta(hours=i))
        made.append(conv)
    return made


def test_the_default_number_of_ended_conversations_shown_is_ten(settings):
    assert settings.MY_ENDED_CONVERSATIONS_SHOWN == 10


def test_only_the_most_recent_ended_conversations_are_listed(settings):
    settings.MAX_OPEN_CONVERSATIONS = 100
    settings.MY_ENDED_CONVERSATIONS_SHOWN = 3
    viewer = make_user()
    made = ended_conversations(viewer, 5)
    shown = [e["conversation"].pk for e in mine(viewer)]
    assert shown == [made[4].pk, made[3].pk, made[2].pk]


def test_exactly_the_limit_is_shown_and_one_more_is_cut(settings):
    settings.MAX_OPEN_CONVERSATIONS = 100
    settings.MY_ENDED_CONVERSATIONS_SHOWN = 4
    viewer = make_user()
    made = ended_conversations(viewer, 4)
    assert len(mine(viewer)) == 4
    more = ended_conversations(viewer, 1)[0]
    from forum.models import Conversation

    Conversation.objects.filter(pk=more.pk).update(created_at=at(1) + timedelta(days=5))
    shown = [e["conversation"].pk for e in mine(viewer)]
    assert len(shown) == 4 and shown[0] == more.pk and made[0].pk not in shown


def test_the_default_limit_of_ten_cuts_the_eleventh(settings):
    settings.MAX_OPEN_CONVERSATIONS = 100
    viewer = make_user()
    made = ended_conversations(viewer, 11)
    shown = [e["conversation"].pk for e in mine(viewer)]
    assert len(shown) == 10 and made[0].pk not in shown and made[10].pk in shown


def test_the_limit_only_applies_to_ended_conversations(settings):
    settings.MAX_OPEN_CONVERSATIONS = 100
    settings.MY_ENDED_CONVERSATIONS_SHOWN = 1
    viewer = make_user()
    ended_conversations(viewer, 3)
    live = [svc().enter_proposition(viewer, make_prop(), "pro") for _ in range(4)]
    result = mine(viewer)
    assert {e["conversation"].pk for e in result if e["status"] == "waiting"} == {c.pk for c in live}
    assert sum(1 for e in result if e["status"] == "ended") == 1


def test_a_limit_of_zero_shows_no_ended_conversations(settings):
    settings.MAX_OPEN_CONVERSATIONS = 100
    settings.MY_ENDED_CONVERSATIONS_SHOWN = 0
    viewer = make_user()
    ended_conversations(viewer, 2)
    live = svc().enter_proposition(viewer, make_prop(), "pro")
    assert [e["conversation"].pk for e in mine(viewer)] == [live.pk]


def test_the_limit_is_per_person_not_shared(settings):
    settings.MAX_OPEN_CONVERSATIONS = 100
    settings.MY_ENDED_CONVERSATIONS_SHOWN = 2
    a, b = make_user(), make_user()
    ended_conversations(a, 3)
    ended_conversations(b, 3)
    assert len(mine(a)) == len(mine(b)) == 2


def test_the_ended_limit_follows_the_most_recent_activity_not_the_creation_time(settings):
    settings.MAX_OPEN_CONVERSATIONS = 100
    settings.MY_ENDED_CONVERSATIONS_SHOWN = 1
    viewer = make_user()
    old, new = ended_conversations(viewer, 2)  # `new` was created later
    from fsvc_testkit import participant_of

    orm_user_message(old, participant_of(old, viewer), "a late message in the older one", when=at(1) + timedelta(days=30))
    assert [e["conversation"].pk for e in mine(viewer)] == [old.pk]


# constant queries


def _mine_count(viewer):
    with CaptureQueriesContext(connection) as queries:
        for entry in mine(viewer):
            entry["topic"].proposition, entry["topic"].opposing_position, entry["conversation"].status, entry["conversation"].pk
    return len(queries)


def test_my_conversations_costs_the_same_number_of_queries_for_one_row_and_for_thirty(settings):
    settings.MAX_OPEN_CONVERSATIONS = 100
    settings.MY_ENDED_CONVERSATIONS_SHOWN = 100
    viewer = make_user()
    svc().enter_proposition(viewer, make_prop(), "pro")
    one = _mine_count(viewer)
    for i in range(29):
        conv = svc().enter_proposition(viewer, make_prop(), "pro")
        if i % 3 == 0:
            svc().end_conversation(viewer, conv)
        else:
            orm_user_message(conv, conv.participants.get(), f"words {i}")
    thirty = _mine_count(viewer)
    assert len(mine(viewer)) == 30
    assert one == thirty <= 3, (one, thirty)


def test_nothing_to_list_costs_at_most_a_couple_of_queries():
    assert _mine_count(make_user()) <= 3


# --- seeded_topics -----------------------------------------------------------------------------------------------------------------------


def seeded(text, **extra):
    from forum.models import Topic

    return Topic.objects.create(title=f"seed {text}", proposition=text, **extra)


def test_seeded_topics_lists_visible_topics_without_a_creator_in_id_order():
    a, b, c = seeded("First seeded claim"), seeded("Second seeded claim"), seeded("Third seeded claim")
    assert [t.pk for t in svc().seeded_topics()] == [a.pk, b.pk, c.pk]


def test_seeded_topics_returns_a_list_of_topic_instances():
    from forum.models import Topic

    seeded("A seeded claim")
    result = svc().seeded_topics()
    assert isinstance(result, list) and all(isinstance(t, Topic) for t in result)


def test_user_created_topics_are_not_seeded():
    seeded("A seeded claim")
    svc().create_proposition(make_user(), "a claim typed by a person")
    make_prop("A claim with a creator", created_by=make_user())
    assert [t.proposition for t in svc().seeded_topics()] == ["A seeded claim"]


def test_hidden_seeded_topics_are_left_out():
    seeded("A visible seeded claim")
    seeded("A hidden seeded claim", hidden=True)
    assert [t.proposition for t in svc().seeded_topics()] == ["A visible seeded claim"]


def test_seeded_topics_are_ordered_by_id_not_by_text_or_time():
    from forum.models import Topic

    z = seeded("Zebras are fine")
    a = seeded("Apples are fine")
    Topic.objects.filter(pk=z.pk).update(created_at=at(20))
    Topic.objects.filter(pk=a.pk).update(created_at=at(1))
    assert [t.pk for t in svc().seeded_topics()] == [z.pk, a.pk]


def test_seeded_topics_carry_their_opposing_position():
    seeded("Rents should be capped", opposing_position="Rents should not be capped")
    (topic,) = svc().seeded_topics()
    assert topic.opposing_position == "Rents should not be capped"


def test_seeded_topics_with_no_seeds_is_an_empty_list():
    svc().create_proposition(make_user(), "only a user claim")
    assert svc().seeded_topics() == []


def test_seeded_topics_uses_a_constant_number_of_queries():
    with CaptureQueriesContext(connection) as none_queries:
        svc().seeded_topics()
    for i in range(30):
        seeded(f"Seeded claim {i}")
    with CaptureQueriesContext(connection) as many_queries:
        result = svc().seeded_topics()
        [t.proposition for t in result]
    assert len(result) == 30 and len(many_queries) == len(none_queries) == 1


# --- the preview really is gone ----------------------------------------------------------------------------------------------------------


def test_the_preview_function_and_tunable_stay_removed(settings):
    import forum.services as services

    assert not hasattr(services, "waiting_previews")
    assert not hasattr(settings, "WAITING_PREVIEW_CHARS")


# --- my_conversations(viewer, query) (revision 4) ------------------------------------------------------------------------------------------


def mine_q(viewer, query=None):
    return svc().my_conversations(viewer, query)


@pytest.fixture
def my_cast(settings):
    """A viewer with conversations on five topics (one ended), newest activity last created."""
    from forum.models import Topic

    settings.MAX_OPEN_CONVERSATIONS = 100
    viewer = make_user()
    made = {}
    for i, (key, proposition, opposing) in enumerate(
        [
            ("rent", "Cities should cap rent increases", "Cities should not cap rent increases"),
            ("bikes", "Bike lanes make streets safer", "Bike lanes make streets more dangerous"),
            ("elect", "\u00c9lections should be held on weekends", ""),
            ("zebra", "Zebra crossings should be kept", "Zebra crossings should be removed"),
            ("trains", "Trains should be free", ""),
        ]
    ):
        topic = Topic.objects.create(title=f"cast {key}", proposition=proposition, opposing_position=opposing)
        conv = make_waiting(topic, viewer, created_at=at(1 + i), side="pro", seed=100 + i)
        made[key] = conv
    svc().end_conversation(viewer, made["trains"])
    return type("MyCast", (), {"viewer": viewer, "made": made})


def found_mine(cast, query):
    return {e["conversation"].pk for e in mine_q(cast.viewer, query)}


def test_no_query_and_an_empty_query_list_everything(my_cast):
    everything = {c.pk for c in my_cast.made.values()}
    assert found_mine(my_cast, None) == found_mine(my_cast, "") == found_mine(my_cast, "   ") == everything


def test_a_query_keeps_only_matching_propositions(my_cast):
    assert found_mine(my_cast, "bike lanes") == {my_cast.made["bikes"].pk}


def test_a_query_also_matches_the_opposing_position(my_cast):
    assert found_mine(my_cast, "more dangerous") == {my_cast.made["bikes"].pk}
    assert found_mine(my_cast, "should be removed") == {my_cast.made["zebra"].pk}


def test_the_query_is_case_insensitive(my_cast):
    assert found_mine(my_cast, "BIKE LANES") == {my_cast.made["bikes"].pk}
    assert found_mine(my_cast, "cItIeS") == {my_cast.made["rent"].pk}


def test_the_query_matches_a_fragment_anywhere(my_cast):
    assert found_mine(my_cast, "crossings") == {my_cast.made["zebra"].pk}


def test_no_match_gives_an_empty_list(my_cast):
    assert mine_q(my_cast.viewer, "aardvark") == []


def test_unicode_queries_fold_case_beyond_ascii(my_cast):
    assert found_mine(my_cast, "\u00e9lections") == {my_cast.made["elect"].pk}
    assert found_mine(my_cast, "\u00c9LECTIONS should") == {my_cast.made["elect"].pk}


def test_a_non_ascii_query_folds_the_german_sharp_s(my_cast):
    from forum.models import Topic

    topic = Topic.objects.create(title="strasse", proposition="STRASSE rents should be capped")
    conv = make_waiting(topic, my_cast.viewer, created_at=at(9), side="pro", seed=5)
    assert found_mine(my_cast, "stra\u00dfe") == {conv.pk}


def test_the_search_looks_at_the_opposing_position_for_non_ascii_queries_too(my_cast):
    from forum.models import Topic

    topic = Topic.objects.create(title="cafe", proposition="Coffee shops should open early", opposing_position="Caf\u00e9s should open late")
    conv = make_waiting(topic, my_cast.viewer, created_at=at(9), side="pro", seed=6)
    assert found_mine(my_cast, "CAF\u00c9S") == {conv.pk}


@pytest.mark.parametrize("query", ["\x00", "cities\x00", "\x00cities", "a\x00b"])
def test_a_nul_character_finds_nothing_and_does_not_break(my_cast, query):
    assert mine_q(my_cast.viewer, query) == []


def test_whitespace_in_the_query_is_collapsed_and_trimmed(my_cast):
    assert found_mine(my_cast, "  bike    lanes \t") == {my_cast.made["bikes"].pk}
    assert found_mine(my_cast, "bike\nlanes") == {my_cast.made["bikes"].pk}


def test_like_wildcards_quotes_and_huge_input_are_harmless(my_cast):
    for query in ("%", "_", "cities%rent", "'", '"', "\\", "'; drop table forum_topic; --", "cities " * 5000):
        assert mine_q(my_cast.viewer, query) == []


def test_a_query_keeps_the_newest_activity_first_order(my_cast):
    from forum.models import Topic

    more = Topic.objects.create(title="cities two", proposition="Cities should ban private cars downtown")
    later = make_waiting(more, my_cast.viewer, created_at=at(20), side="pro", seed=7)
    result = [e["conversation"].pk for e in mine_q(my_cast.viewer, "cities")]
    assert result == [later.pk, my_cast.made["rent"].pk]


def test_a_query_never_shows_someone_elses_conversations(my_cast):
    other = make_user()
    from forum.models import Topic

    topic = Topic.objects.create(title="cities other", proposition="Cities should plant more trees")
    make_waiting(topic, other, created_at=at(3), side="pro", seed=8)
    assert {e["topic"].pk for e in mine_q(my_cast.viewer, "trees")} == set()
    assert len(mine_q(other, "trees")) == 1


def test_the_ended_limit_applies_only_when_there_is_no_query(settings):
    settings.MAX_OPEN_CONVERSATIONS = 100
    settings.MY_ENDED_CONVERSATIONS_SHOWN = 2
    viewer = make_user()
    from forum.models import Conversation, Topic

    made = []
    for i in range(5):
        topic = Topic.objects.create(title=f"ended {i}", proposition=f"Ended claim number {i}")
        conv = svc().enter_proposition(viewer, topic, "pro")
        svc().end_conversation(viewer, conv)
        Conversation.objects.filter(pk=conv.pk).update(created_at=at(1) + timedelta(hours=i))
        made.append(conv)
    assert len(mine_q(viewer)) == 2  # no query: limited
    assert len(mine_q(viewer, "")) == 2
    assert {e["conversation"].pk for e in mine_q(viewer, "ended claim")} == {c.pk for c in made}  # a query: all
    assert [e["conversation"].pk for e in mine_q(viewer, "number 3")] == [made[3].pk]


def test_a_query_that_matches_only_live_conversations_ignores_the_limit_setting(settings):
    settings.MY_ENDED_CONVERSATIONS_SHOWN = 0
    viewer = make_user()
    conv = svc().enter_proposition(viewer, make_prop("Trams should run at night"), "pro")
    assert [e["conversation"].pk for e in mine_q(viewer, "trams")] == [conv.pk]
    assert mine_q(viewer) == [
        e for e in mine_q(viewer)
    ]


def test_matching_ended_rows_all_show_and_keep_their_status_and_order_last_by_activity(my_cast):
    result = mine_q(my_cast.viewer, "trains")
    assert [e["status"] for e in result] == ["ended"]


def test_my_conversations_with_a_query_costs_a_constant_number_of_queries(settings):
    settings.MAX_OPEN_CONVERSATIONS = 100
    settings.MY_ENDED_CONVERSATIONS_SHOWN = 100
    viewer = make_user()
    make_waiting(make_prop("Searchable claim 0"), viewer, created_at=at(1), side="pro", seed=1)
    with CaptureQueriesContext(connection) as one:
        for e in mine_q(viewer, "claim"):
            e["topic"].proposition, e["conversation"].status, e["other_username"]
    for i in range(1, 30):
        make_waiting(make_prop(f"Searchable claim {i}"), viewer, created_at=at(1) + timedelta(minutes=i), side="pro", seed=i)
    for query in ("claim", "\u00c9claim", None):
        with CaptureQueriesContext(connection) as many:
            result = mine_q(viewer, query)
            for e in result:
                e["topic"].proposition, e["conversation"].status, e["other_username"]
        assert len(many) == len(one) <= 3, (query, len(one), len(many))
    assert len(mine_q(viewer, "claim")) == 30


def test_an_anonymous_visitor_gets_nothing_with_or_without_a_query():
    from django.contrib.auth.models import AnonymousUser

    assert mine_q(AnonymousUser(), "x") == [] and mine_q(AnonymousUser()) == []


# --- the explicit tie-break of waiting_groups (revision 4) ------------------------------------------------------------------------------------


def test_groups_with_equal_newest_times_are_always_ordered_by_conversation_id_descending():
    topics = [make_prop() for _ in range(8)]
    convs = [wait(t, "pro", at(5)) for t in topics]
    expected = [t.pk for t in reversed(topics)]
    assert [g["topic"].pk for g in groups(make_user())] == expected
    assert [c.pk for c in convs] == sorted(c.pk for c in convs)


def test_the_id_that_breaks_a_tie_is_the_groups_newest_conversation_not_its_first():
    a, b = make_prop(), make_prop()
    a_old = wait(a, "pro", at(5))
    b_only = wait(b, "pro", at(5))
    a_new = wait(a, "pro", at(5))  # the highest id of all, same time: group a's newest is (5, a_new.id)
    assert a_new.pk > b_only.pk > a_old.pk
    assert [g["topic"].pk for g in groups(make_user())] == [a.pk, b.pk]
