"""conversation_view (forum/viewmodels.py): membership, the dict's shape, kinds, polling, ended states, and the rule that no
label letter, username or email ever appears."""
from datetime import datetime

import pytest

from fsvc_testkit import (
    assert_no_identity, assert_plain, counts, make_active, make_active_via_service, make_prop, make_user, make_waiting,
    orm_act, orm_moderator_message, orm_run, orm_user_message, rejection, svc, views, walk_strings,
)  # fmt: skip

pytestmark = pytest.mark.django_db

REQUIRED = {
    "status", "proposition", "you_ended", "ended_by_other", "messages", "moderation_notice", "message_count",
    "message_limit", "waiting", "can_post", "cannot_post_reason",
}  # fmt: skip


def view(user, conv, after_seq=0):
    return views().conversation_view(user, conv, after_seq)


def reload(conv):
    from forum.models import Conversation

    return Conversation.objects.get(pk=conv.pk)


@pytest.fixture
def w(clock):
    """Active conversation with known labels: ua is A and joined first, ub is B; proposition text is recognisable."""
    return make_active(make_prop("Cats make better pets than dogs"))


# --- membership ----------------------------------------------------------------------------------------------------------


def test_a_stranger_is_refused_and_told_why(w):
    exc = rejection(views().conversation_view, make_user(), w.conv)
    assert exc.code == "not_participant"
    assert "You are not a participant in this conversation." in exc.message
    assert_plain(exc)


def test_a_stranger_is_refused_for_a_waiting_conversation_too():
    conv = make_waiting()
    assert rejection(views().conversation_view, make_user(), conv).code == "not_participant"


def test_a_participant_of_a_different_conversation_is_refused(w):
    other = make_active()
    assert rejection(views().conversation_view, other.ua, w.conv).code == "not_participant"


def test_both_participants_can_read(w):
    assert view(w.ua, w.conv)["status"] == "active"
    assert view(w.ub, w.conv)["status"] == "active"


def test_reading_changes_nothing(w):
    orm_user_message(w.conv, w.a, "hello there friend")
    before = counts()
    view(w.ua, w.conv)
    view(w.ub, w.conv, 5)
    assert counts() == before


# --- shape ---------------------------------------------------------------------------------------------------------------


def test_the_dict_has_every_key_of_the_contract(w):
    result = view(w.ua, w.conv)
    assert REQUIRED <= set(result)


def test_status_proposition_and_limits(w, settings):
    result = view(w.ua, w.conv)
    assert result["status"] == "active"
    assert result["proposition"] == "Cats make better pets than dogs"
    assert result["message_limit"] == settings.MAX_USER_MESSAGES_PER_CONVERSATION == 30
    assert result["message_count"] == 0
    assert result["messages"] == []
    assert result["waiting"] is False
    assert result["can_post"] is True
    assert result["cannot_post_reason"] is None
    assert result["moderation_notice"] is None
    assert not result["you_ended"] and not result["ended_by_other"]


def test_the_limit_comes_from_settings(w, settings):
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 7
    assert view(w.ua, w.conv)["message_limit"] == 7


def test_a_seeded_topics_title_is_never_shown_only_its_proposition():
    topic = make_prop("Rents should be capped", title="INTERNAL-ADMIN-TITLE")
    w = make_active(topic)
    result = view(w.ua, w.conv)
    assert result["proposition"] == "Rents should be capped"
    assert not any("INTERNAL-ADMIN-TITLE" in s for s in walk_strings(result))


def test_message_count_counts_user_messages_only_and_ignores_after_seq(w):
    m1 = orm_user_message(w.conv, w.a, "first thought here")
    orm_moderator_message(w.conv, in_reply_to=m1)
    orm_user_message(w.conv, w.b, "second thought here")
    assert view(w.ua, w.conv)["message_count"] == 2
    assert view(w.ua, w.conv, after_seq=3)["message_count"] == 2


# --- messages: kinds, order, text, times ---------------------------------------------------------------------------------


def test_messages_are_you_or_other_from_the_viewers_point_of_view(w):
    orm_user_message(w.conv, w.a, "from the first person")
    orm_user_message(w.conv, w.b, "from the second person")
    as_a = view(w.ua, w.conv)["messages"]
    as_b = view(w.ub, w.conv)["messages"]
    assert [m["kind"] for m in as_a] == ["you", "other"]
    assert [m["kind"] for m in as_b] == ["other", "you"]


def test_each_message_has_seq_no_kind_text_and_time_in_seq_order(w):
    orm_user_message(w.conv, w.a, "from the first person")
    orm_user_message(w.conv, w.b, "from the second person")
    orm_user_message(w.conv, w.a, "and once more")
    messages = view(w.ua, w.conv)["messages"]
    assert [m["seq_no"] for m in messages] == [1, 2, 3]
    assert [m["text"] for m in messages] == ["from the first person", "from the second person", "and once more"]
    for m in messages:
        assert {"seq_no", "kind", "text", "created_at"} <= set(m)
        assert isinstance(m["created_at"], (datetime, str))


def test_a_moderator_message_has_kind_moderator_and_its_text(w):
    m1 = orm_user_message(w.conv, w.a, "from the first person")
    mod = orm_moderator_message(w.conv, in_reply_to=m1, content="Both of you may wish to define the term.")
    for user in (w.ua, w.ub):
        moderator = [m for m in view(user, w.conv)["messages"] if m["seq_no"] == mod.seq_no]
        assert len(moderator) == 1
        assert moderator[0]["kind"] == "moderator"
        assert moderator[0]["text"] == "Both of you may wish to define the term."


def test_messages_from_two_conversations_are_never_mixed(w):
    other = make_active(w.topic)
    orm_user_message(other.conv, other.a, "a different conversation entirely")
    orm_user_message(w.conv, w.a, "this one")
    assert [m["text"] for m in view(w.ua, w.conv)["messages"]] == ["this one"]


# --- after_seq (polling) -------------------------------------------------------------------------------------------------


def test_after_seq_returns_only_newer_messages(w):
    for i in range(5):
        orm_user_message(w.conv, w.a if i % 2 == 0 else w.b, f"message number {i + 1}")
    assert [m["seq_no"] for m in view(w.ua, w.conv, after_seq=0)["messages"]] == [1, 2, 3, 4, 5]
    assert [m["seq_no"] for m in view(w.ua, w.conv, after_seq=2)["messages"]] == [3, 4, 5]
    assert [m["seq_no"] for m in view(w.ua, w.conv, after_seq=4)["messages"]] == [5]
    assert view(w.ua, w.conv, after_seq=5)["messages"] == []
    assert view(w.ua, w.conv, after_seq=99)["messages"] == []


def test_after_seq_is_strictly_greater_not_greater_or_equal(w):
    orm_user_message(w.conv, w.a, "only message")
    assert view(w.ua, w.conv, 1)["messages"] == []
    assert len(view(w.ua, w.conv, 0)["messages"]) == 1


def test_after_seq_still_reports_the_current_state(w):
    orm_user_message(w.conv, w.a, "only message")
    result = view(w.ua, w.conv, after_seq=1)
    assert result["status"] == "active" and result["can_post"] is True and REQUIRED <= set(result)


def test_after_seq_picks_up_a_moderator_post_that_arrived_later(w):
    m1 = orm_user_message(w.conv, w.a, "from the first person")
    seen = view(w.ub, w.conv)["messages"][-1]["seq_no"]
    mod = orm_moderator_message(w.conv, in_reply_to=m1)
    newer = view(w.ub, w.conv, after_seq=seen)["messages"]
    assert [(m["seq_no"], m["kind"]) for m in newer] == [(mod.seq_no, "moderator")]


def test_after_seq_defaults_to_everything(w):
    orm_user_message(w.conv, w.a, "only message")
    assert len(views().conversation_view(w.ua, w.conv)["messages"]) == 1


# --- waiting -------------------------------------------------------------------------------------------------------------


def test_a_waiting_conversation_says_so_and_offers_no_composer():
    user = make_user()
    conv = make_waiting(make_prop("Trains should be free"), user)
    result = view(user, conv)
    assert result["waiting"] is True
    assert result["status"] == "open"
    assert result["can_post"] is False
    assert result["cannot_post_reason"]["code"] == "waiting"
    assert "You can post once someone else joins." in result["cannot_post_reason"]["message"]
    assert result["messages"] == []
    assert result["proposition"] == "Trains should be free"
    assert_no_identity(result, user)


def test_waiting_turns_off_when_someone_joins():
    topic = make_prop()
    first, second = make_user(), make_user()
    conv = svc().enter_proposition(first, topic)
    assert view(first, conv)["waiting"] is True
    svc().enter_proposition(second, topic)
    result = view(first, reload(conv))
    assert result["waiting"] is False and result["can_post"] is True and result["status"] == "active"


# --- ended and closed states ---------------------------------------------------------------------------------------------


def test_the_person_who_ended_it_sees_you_ended(w):
    svc().end_conversation(w.ua, w.conv)
    mine = view(w.ua, reload(w.conv))
    assert mine["status"] == "closed"
    assert mine["you_ended"] is True
    assert mine["ended_by_other"] is False
    assert mine["can_post"] is False
    reason = mine["cannot_post_reason"]
    assert reason["code"] == "closed"
    assert "This conversation is closed." in reason["message"]
    assert "You ended this conversation." in reason["message"]


def test_the_other_person_sees_that_the_other_participant_ended_it(w):
    svc().end_conversation(w.ua, w.conv)
    theirs = view(w.ub, reload(w.conv))
    assert theirs["status"] == "closed"
    assert theirs["you_ended"] is False
    assert theirs["ended_by_other"] is True
    assert theirs["can_post"] is False
    assert "The other participant ended this conversation." in theirs["cannot_post_reason"]["message"]
    assert "You ended this conversation." not in theirs["cannot_post_reason"]["message"]


def test_the_reason_shown_is_the_reason_post_message_would_give(w):
    svc().end_conversation(w.ub, w.conv)
    for user in (w.ua, w.ub):
        shown = view(user, reload(w.conv))["cannot_post_reason"]
        raised = rejection(svc().post_message, user, reload(w.conv), "hello there friend")
        assert shown["code"] == raised.code == "closed"
        assert shown["message"] == raised.message


def test_a_conversation_nobody_ended_has_neither_ended_flag_set(w, settings):
    from forum.models import Conversation

    Conversation.objects.filter(pk=w.conv.pk).update(status="closed")  # closed without anyone ending it (the cap)
    result = view(w.ua, reload(w.conv))
    assert not result["you_ended"] and not result["ended_by_other"]
    assert result["can_post"] is False


def test_a_waiting_conversation_ended_by_its_creator_reads_as_ended_by_you():
    user = make_user()
    conv = make_waiting(user=user)
    svc().end_conversation(user, conv)
    result = view(user, reload(conv))
    assert result["you_ended"] is True and result["ended_by_other"] is False
    assert result["waiting"] is False  # it is closed now, not waiting
    assert result["can_post"] is False


def test_an_ended_conversation_is_still_readable_by_both(w):
    orm_user_message(w.conv, w.a, "hello there friend")
    orm_user_message(w.conv, w.b, "hello to you as well")
    svc().end_conversation(w.ua, w.conv)
    for user in (w.ua, w.ub):
        assert len(view(user, reload(w.conv))["messages"]) == 2


def test_a_full_conversation_cannot_be_posted_to(w, clock, settings):
    from datetime import timedelta

    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 3
    for i in range(3):
        orm_user_message(w.conv, w.a if i % 2 == 0 else w.b, f"message number {i}", when=clock() - timedelta(hours=1))
    result = view(w.ua, w.conv)  # status is still active here; the count is what blocks
    assert result["can_post"] is False
    assert result["cannot_post_reason"]["code"] == "conversation_full"
    assert "limit of 3 messages" in result["cannot_post_reason"]["message"]
    assert result["message_count"] == 3 and result["message_limit"] == 3


def test_cannot_post_reason_is_a_plain_dict_with_code_and_message(w):
    svc().end_conversation(w.ua, w.conv)
    reason = view(w.ub, reload(w.conv))["cannot_post_reason"]
    assert isinstance(reason, dict)
    assert isinstance(reason["code"], str) and isinstance(reason["message"], str)


# --- no label letter, username or email ----------------------------------------------------------------------------------


def test_no_label_username_or_email_appears_anywhere_in_a_busy_conversation(w):
    m1 = orm_user_message(w.conv, w.a, "from the first person")
    m2 = orm_user_message(w.conv, w.b, "from the second person")
    mod = orm_moderator_message(w.conv, in_reply_to=m2, content="Both of you may wish to define the term.")
    run = orm_run(w.conv, m2, status="done", posted_message=mod)
    orm_act(run, "A", "B", [m2])
    for user in (w.ua, w.ub):
        result = view(user, reload(w.conv))
        assert_no_identity(result, w.ua, w.ub)
        assert_no_identity(view(user, reload(w.conv), after_seq=1), w.ua, w.ub)


@pytest.mark.parametrize("labels", [("A", "B"), ("B", "A")])
def test_no_identity_in_the_ended_states_either(labels):
    w = make_active(labels=labels)
    orm_user_message(w.conv, w.a, "from the first person")
    svc().end_conversation(w.ua, w.conv)
    for user in (w.ua, w.ub):
        assert_no_identity(view(user, reload(w.conv)), w.ua, w.ub)


def test_no_identity_in_the_waiting_state():
    user = make_user()
    conv = make_waiting(user=user)
    assert_no_identity(view(user, conv), user)


def test_the_identity_check_itself_would_notice_a_leak():
    """Guard against a vacuous check: the helper does flag a label, a username and an email when they are present."""
    user = make_user()
    for leaked in ({"x": "Participant"}, {"x": ["A"]}, {"x": user.username}, {"y": {"z": user.email}}, {"A": 1}):
        with pytest.raises(AssertionError):
            assert_no_identity(leaked, user)


def test_the_view_reads_the_current_state_not_a_stale_object(w):
    stale = w.conv  # says active in memory
    svc().end_conversation(w.ua, reload(w.conv))
    assert stale.status == "active"
    result = view(w.ub, stale)
    assert result["status"] == "closed" and result["can_post"] is False and result["ended_by_other"] is True
