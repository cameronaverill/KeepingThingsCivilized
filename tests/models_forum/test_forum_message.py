"""Message: fields, seq_no assignment, char_count, author rules, in_reply_to rules, the length limit, helpers
(brief 4a)."""
import pytest
from django.core.exceptions import ValidationError
from django.db import models

from forum_testkit import invalid, make_pair, post, post_moderator, raw_message

pytestmark = pytest.mark.django_db


def fetch(pk):
    from forum.models import Message

    return Message.objects.get(pk=pk)


# --- fields, defaults, choices ---------------------------------------------------------------------------------


def test_message_has_every_contract_field():
    from forum.models import Message

    names = {f.name for f in Message._meta.get_fields()}
    expected = {
        "conversation", "seq_no", "author_type", "participant", "in_reply_to", "content", "char_count", "planted",
        "created_at",
    }  # fmt: skip
    assert expected <= names


def test_author_type_choices_are_exactly_the_contract():
    from forum.models import Message

    assert sorted(v for v, _ in Message._meta.get_field("author_type").choices) == ["moderator", "user"]


def test_an_unknown_author_type_is_rejected_by_validation():
    conv, a, _ = make_pair()
    msg = post(conv, a)
    msg.author_type = "bot"
    with pytest.raises(ValidationError) as excinfo:
        msg.full_clean()
    assert "author_type" in excinfo.value.message_dict


def test_defaults_and_stored_values():
    conv, a, _ = make_pair()
    msg = fetch(post(conv, a, "hello there").pk)
    assert msg.planted == []
    assert msg.in_reply_to is None
    assert msg.created_at is not None
    assert msg.author_type == "user"
    assert msg.participant_id == a.pk
    assert msg.content == "hello there"


def test_planted_defaults_to_an_unshared_empty_list_and_round_trips():
    from forum.models import Message

    conv, a, _ = make_pair()
    first, second = post(conv, a), post(conv, a)
    assert Message._meta.get_field("planted").default is list
    assert first.planted == [] and second.planted == []
    first.planted.append("x")
    assert second.planted == []
    planted = [{"phrase": "everyone knows", "dimension": "factual_accuracy", "intensity": 3}]
    assert fetch(post(conv, a, planted=planted).pk).planted == planted


def test_content_is_stored_exactly_as_given_not_trimmed_or_normalized():
    conv, a, _ = make_pair()
    raw = "  caf\u00e9\r\nline two \n"
    assert fetch(post(conv, a, raw).pk).content == raw


def test_message_relations_are_optional_where_the_contract_says():
    from forum.models import Message

    assert Message._meta.get_field("conversation").null is False
    assert Message._meta.get_field("participant").null is True
    assert Message._meta.get_field("in_reply_to").null is True
    assert isinstance(Message._meta.get_field("planted"), models.JSONField)


# --- seq_no ----------------------------------------------------------------------------------------------------


def test_seq_no_is_assigned_from_one_and_strictly_increases():
    conv, a, b = make_pair()
    seqs = [post(conv, a if i % 2 else b).seq_no for i in range(8)]
    assert seqs == [1, 2, 3, 4, 5, 6, 7, 8]


def test_seq_no_counts_moderator_messages_too():
    conv, a, _ = make_pair()
    assert post(conv, a).seq_no == 1
    assert post_moderator(conv).seq_no == 2
    assert post(conv, a).seq_no == 3


def test_seq_no_is_kept_per_conversation():
    conv1, a1, _ = make_pair()
    conv2, a2, _ = make_pair()
    assert [post(conv1, a1).seq_no, post(conv2, a2).seq_no, post(conv1, a1).seq_no, post(conv2, a2).seq_no] == [1, 1, 2, 2]


def test_the_assigned_seq_no_is_saved_in_the_database():
    conv, a, _ = make_pair()
    msg = post(conv, a)
    assert fetch(msg.pk).seq_no == msg.seq_no == 1


def test_an_explicit_seq_no_greater_than_the_maximum_is_accepted():
    conv, a, _ = make_pair()
    assert post(conv, a, seq_no=5).seq_no == 5
    assert post(conv, a, seq_no=9).seq_no == 9
    assert post(conv, a).seq_no == 10  # automatic numbering continues from the maximum


def test_an_explicit_seq_no_of_one_on_an_empty_conversation_is_accepted():
    conv, a, _ = make_pair()
    assert post(conv, a, seq_no=1).seq_no == 1


@pytest.mark.parametrize("seq_no", [3, 2, 1])
def test_an_explicit_seq_no_not_greater_than_the_maximum_is_refused(seq_no):
    conv, a, _ = make_pair()
    for _ in range(3):
        post(conv, a)
    with invalid():
        post(conv, a, seq_no=seq_no)


def test_an_explicit_seq_no_below_the_maximum_after_a_jump_is_refused():
    conv, a, _ = make_pair()
    post(conv, a, seq_no=10)
    with invalid():
        post(conv, a, seq_no=7)


@pytest.mark.parametrize("seq_no", [0, -1, -100])
def test_seq_no_zero_or_negative_is_refused_on_an_empty_conversation(seq_no):
    conv, a, _ = make_pair()
    with invalid():
        post(conv, a, seq_no=seq_no)


def test_a_refused_message_is_not_stored_and_does_not_consume_a_number():
    from forum.models import Message

    conv, a, _ = make_pair()
    post(conv, a)
    with invalid():
        post(conv, a, seq_no=1)
    assert Message.objects.filter(conversation=conv).count() == 1
    assert post(conv, a).seq_no == 2


def test_another_conversations_maximum_does_not_matter_for_an_explicit_seq_no():
    conv1, a1, _ = make_pair()
    conv2, a2, _ = make_pair()
    post(conv1, a1, seq_no=50)
    assert post(conv2, a2, seq_no=2).seq_no == 2


def test_resaving_an_existing_message_keeps_its_seq_no_and_is_not_refused():
    conv, a, _ = make_pair()
    first = post(conv, a)
    post(conv, a)
    first.save()  # seq 1 is not greater than the maximum 2, but this is an update, not a create
    assert fetch(first.pk).seq_no == 1


def test_seq_no_does_not_change_when_other_fields_are_edited():
    conv, a, _ = make_pair()
    msg = post(conv, a)
    post(conv, a)
    msg.content = "edited"
    msg.save()
    assert fetch(msg.pk).seq_no == 1


def test_seq_no_is_unique_per_conversation_at_model_level():
    conv, a, _ = make_pair()
    post(conv, a)
    dup = post(conv, a)
    dup.seq_no = 1
    with pytest.raises(ValidationError):
        dup.full_clean()


# --- char_count -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content, expected",
    [
        ("hello", 5),
        ("  hello  ", 5),
        ("a\r\nb", 3),
        ("a\rb", 3),
        ("e\u0301", 1),
        ("\U0001f600\U0001f600", 2),
        ("x" * 250, 250),
    ],
)
def test_char_count_is_computed_from_count_message_chars(content, expected):
    from forum.limits import count_message_chars

    conv, a, _ = make_pair()
    msg = post(conv, a, content)
    assert msg.char_count == expected == count_message_chars(content)
    assert fetch(msg.pk).char_count == expected


def test_char_count_is_always_computed_even_when_the_caller_supplies_a_wrong_one():
    conv, a, _ = make_pair()
    msg = post(conv, a, "hello", char_count=999)
    assert msg.char_count == 5
    assert fetch(msg.pk).char_count == 5
    assert post_moderator(conv, "abc", char_count=1).char_count == 3


def test_char_count_is_recomputed_when_the_content_changes():
    conv, a, _ = make_pair()
    msg = post(conv, a, "hello")
    msg.content = "hello world"
    msg.save()
    assert fetch(msg.pk).char_count == 11


def test_char_count_is_recomputed_on_resave_even_if_it_was_tampered_with():
    conv, a, _ = make_pair()
    msg = post(conv, a, "hello")
    msg.char_count = 1
    msg.save()
    assert fetch(msg.pk).char_count == 5


def test_char_count_is_set_for_moderator_messages_too():
    conv, _, _ = make_pair()
    assert post_moderator(conv, "  Please stay on topic.  ").char_count == 21


@pytest.mark.parametrize("content", ["", " ", "   \n\t ", "\r\n", "\r", "\u00a0\u3000"])
def test_content_that_is_empty_after_counting_is_refused_for_users(content):
    conv, a, _ = make_pair()
    with invalid():
        post(conv, a, content)


@pytest.mark.parametrize("content", ["", "  ", "\r\n\r\n"])
def test_content_that_is_empty_after_counting_is_refused_for_the_moderator_too(content):
    conv, _, _ = make_pair()
    with invalid():
        post_moderator(conv, content)


def test_editing_content_to_empty_is_refused():
    conv, a, _ = make_pair()
    msg = post(conv, a, "hello")
    msg.content = "   "
    with invalid():
        msg.save()
    assert fetch(msg.pk).content == "hello"


def test_a_message_with_one_character_of_content_is_fine():
    conv, a, _ = make_pair()
    assert post(conv, a, "x").char_count == 1


# --- author rules ---------------------------------------------------------------------------------------------


def test_a_user_message_needs_a_participant():
    from forum.models import Message

    conv, _, _ = make_pair()
    with invalid():
        Message.objects.create(conversation=conv, author_type="user", participant=None, content="hi")


def test_a_user_message_needs_a_participant_of_the_same_conversation():
    conv1, a1, _ = make_pair()
    conv2, a2, _ = make_pair()
    with invalid():
        post(conv1, a2)


def test_a_participant_of_the_same_conversation_may_post_whoever_they_are():
    conv, a, b = make_pair()
    assert post(conv, a).participant_id == a.pk
    assert post(conv, b).participant_id == b.pk


def test_a_moderator_message_must_have_no_participant():
    from forum.models import Message

    conv, a, _ = make_pair()
    with invalid():
        Message.objects.create(conversation=conv, author_type="moderator", participant=a, content="hi")


def test_a_moderator_message_without_a_participant_is_stored():
    conv, _, _ = make_pair()
    msg = post_moderator(conv)
    assert fetch(msg.pk).participant_id is None
    assert fetch(msg.pk).author_type == "moderator"


def test_a_moderator_message_with_another_conversations_participant_is_also_refused():
    from forum.models import Message

    conv1, _, _ = make_pair()
    conv2, a2, _ = make_pair()
    with invalid():
        Message.objects.create(conversation=conv1, author_type="moderator", participant=a2, content="hi")


def test_changing_the_author_type_to_break_the_rule_on_resave_is_refused():
    conv, a, _ = make_pair()
    msg = post(conv, a)
    msg.author_type = "moderator"  # still has a participant
    with invalid():
        msg.save()
    assert fetch(msg.pk).author_type == "user"


def test_a_synthetic_conversations_messages_use_participants_without_users():
    conv, a, b = make_pair("synthetic")
    assert a.user_id is None
    assert post(conv, a, "pro point").participant_id == a.pk
    assert post_moderator(conv).author_type == "moderator"


def test_refused_author_rule_messages_are_not_stored():
    from forum.models import Message

    conv1, a1, _ = make_pair()
    conv2, a2, _ = make_pair()
    with invalid():
        post(conv1, a2)
    with invalid():
        Message.objects.create(conversation=conv1, author_type="moderator", participant=a1, content="hi")
    assert Message.objects.count() == 0


# --- in_reply_to ----------------------------------------------------------------------------------------------


def test_a_reply_to_an_earlier_message_of_the_same_conversation_is_accepted():
    conv, a, b = make_pair()
    first = post(conv, a)
    reply = post(conv, b, in_reply_to=first)
    assert fetch(reply.pk).in_reply_to_id == first.pk


def test_a_user_can_reply_to_a_moderator_message_and_the_moderator_to_a_user_message():
    conv, a, b = make_pair()
    user_msg = post(conv, a)
    mod_msg = post_moderator(conv, in_reply_to=user_msg)
    assert mod_msg.in_reply_to_id == user_msg.pk
    reply = post(conv, b, in_reply_to=mod_msg)
    assert reply.in_reply_to_id == mod_msg.pk


def test_in_reply_to_is_optional():
    conv, a, _ = make_pair()
    assert post(conv, a).in_reply_to is None


def test_a_reply_to_a_message_of_another_conversation_is_refused():
    conv1, a1, _ = make_pair()
    conv2, a2, _ = make_pair()
    other = post(conv2, a2)  # seq 1 there; the new message below gets seq 4 here, so only the conversation rule refuses
    for _ in range(3):
        post(conv1, a1)
    with invalid():
        post(conv1, a1, in_reply_to=other)


def test_a_reply_to_a_message_with_a_greater_seq_no_is_refused():
    conv, a, _ = make_pair()
    first = post(conv, a)
    later = post(conv, a)
    first.in_reply_to = later
    with invalid():
        first.save()
    assert fetch(first.pk).in_reply_to_id is None


def test_a_message_cannot_reply_to_itself():
    conv, a, _ = make_pair()
    msg = post(conv, a)
    msg.in_reply_to = msg
    with invalid():
        msg.save()


def test_a_reply_to_the_message_just_before_it_and_to_an_older_one_are_both_fine():
    conv, a, b = make_pair()
    m1 = post(conv, a)
    m2 = post(conv, b)
    m3 = post(conv, a, in_reply_to=m2)
    m4 = post(conv, b, in_reply_to=m1)
    assert (m3.in_reply_to_id, m4.in_reply_to_id) == (m2.pk, m1.pk)


def test_a_moderator_message_with_a_reply_target_in_another_conversation_is_refused():
    conv1, a1, _ = make_pair()
    conv2, a2, _ = make_pair()
    other = post(conv2, a2)
    for _ in range(3):
        post(conv1, a1)
    with invalid():
        post_moderator(conv1, in_reply_to=other)


def test_a_refused_reply_is_not_stored():
    from forum.models import Message

    conv1, a1, _ = make_pair()
    conv2, a2, _ = make_pair()
    other = post(conv2, a2)
    post(conv1, a1)
    post(conv1, a1)
    with invalid():
        post(conv1, a1, in_reply_to=other)
    assert Message.objects.filter(conversation=conv1).count() == 2


# --- the length limit ------------------------------------------------------------------------------------------


def test_a_user_message_of_exactly_the_limit_is_accepted(settings):
    settings.MAX_MESSAGE_CHARS = 20
    conv, a, _ = make_pair()
    msg = post(conv, a, "x" * 20)
    assert msg.char_count == 20


def test_a_user_message_one_over_the_limit_is_refused(settings):
    from forum.models import Message

    settings.MAX_MESSAGE_CHARS = 20
    conv, a, _ = make_pair()
    with invalid():
        post(conv, a, "x" * 21)
    assert Message.objects.count() == 0


def test_the_refused_message_does_not_use_up_a_seq_no(settings):
    settings.MAX_MESSAGE_CHARS = 5
    conv, a, _ = make_pair()
    post(conv, a, "12345")
    with invalid():
        post(conv, a, "123456")
    assert post(conv, a, "ok").seq_no == 2


def test_the_default_limit_is_3000_and_the_boundary_holds_at_the_default():
    from django.conf import settings as live_settings

    assert live_settings.MAX_MESSAGE_CHARS == 3000
    conv, a, _ = make_pair()
    assert post(conv, a, "y" * 3000).char_count == 3000
    with invalid():
        post(conv, a, "y" * 3001)


def test_the_limit_is_counted_with_the_counting_rule_not_len(settings):
    settings.MAX_MESSAGE_CHARS = 5
    conv, a, _ = make_pair()
    assert post(conv, a, "  12345  ").char_count == 5  # trimmed
    assert post(conv, a, "ab\r\ncd").char_count == 5  # CRLF counts once (raw length is 6)
    assert post(conv, a, "e\u0301" * 5).char_count == 5  # composed by NFC (raw length is 10)
    assert post(conv, a, "\U0001f600" * 5).char_count == 5  # emoji count once
    with invalid():
        post(conv, a, "ab\r\ncde")  # 6 characters
    with invalid():
        post(conv, a, "\U0001f600" * 6)
    with invalid():
        post(conv, a, "  123456  ")


def test_the_limit_follows_the_setting_at_the_moment_of_saving(settings):
    conv, a, _ = make_pair()
    settings.MAX_MESSAGE_CHARS = 10
    with invalid():
        post(conv, a, "x" * 11)
    settings.MAX_MESSAGE_CHARS = 11
    assert post(conv, a, "x" * 11).char_count == 11


def test_a_moderator_message_is_not_limited(settings):
    settings.MAX_MESSAGE_CHARS = 10
    conv, _, _ = make_pair()
    assert post_moderator(conv, "m" * 11).char_count == 11
    assert post_moderator(conv, "m" * 5000).char_count == 5000


def test_editing_a_user_message_past_the_limit_is_refused_and_keeps_the_old_content(settings):
    settings.MAX_MESSAGE_CHARS = 10
    conv, a, _ = make_pair()
    msg = post(conv, a, "short")
    msg.content = "z" * 11
    with invalid():
        msg.save()
    fresh = fetch(msg.pk)
    assert (fresh.content, fresh.char_count) == ("short", 5)


def test_the_limit_holds_for_synthetic_conversations_too(settings):
    settings.MAX_MESSAGE_CHARS = 8
    conv, a, _ = make_pair("synthetic")
    post(conv, a, "12345678")
    with invalid():
        post(conv, a, "123456789")


def test_the_limit_holds_however_the_message_is_built(settings):
    """Code that skips any posting function and calls Message(...).save() directly is still held to the limit."""
    from forum.models import Message

    settings.MAX_MESSAGE_CHARS = 4
    conv, a, _ = make_pair()
    with invalid():
        Message(conversation=conv, author_type="user", participant=a, content="12345").save()
    msg = Message(conversation=conv, author_type="user", participant=a, content="1234")
    msg.save()
    assert msg.char_count == 4


def test_refused_ones_from_every_rule_leave_the_next_seq_untouched(settings):
    settings.MAX_MESSAGE_CHARS = 3
    conv, a, _ = make_pair()
    with invalid():
        post(conv, a, "toolong")
    with invalid():
        post(conv, a, "   ")
    assert conv.next_seq() == 1


# --- helpers on Conversation ------------------------------------------------------------------------------------


def test_next_seq_is_one_for_an_empty_conversation_and_max_plus_one_after():
    conv, a, _ = make_pair()
    assert conv.next_seq() == 1
    post(conv, a)
    assert conv.next_seq() == 2
    post(conv, a, seq_no=9)
    assert conv.next_seq() == 10


def test_next_seq_does_not_save_anything_and_is_repeatable():
    from forum.models import Message

    conv, a, _ = make_pair()
    post(conv, a)
    assert conv.next_seq() == conv.next_seq() == 2
    assert Message.objects.count() == 1


def test_next_seq_matches_the_number_the_next_message_gets():
    conv, a, b = make_pair()
    for i in range(5):
        expected = conv.next_seq()
        assert post(conv, a if i % 2 else b).seq_no == expected


def test_next_seq_is_per_conversation():
    conv1, a1, _ = make_pair()
    conv2, _, _ = make_pair()
    for _ in range(3):
        post(conv1, a1)
    assert conv1.next_seq() == 4
    assert conv2.next_seq() == 1


def test_messages_up_to_returns_messages_from_the_start_through_that_seq_no_in_order():
    conv, a, b = make_pair()
    msgs = [post(conv, a if i % 2 else b) for i in range(6)]
    up_to = conv.messages_up_to(4)
    assert [m.seq_no for m in up_to] == [1, 2, 3, 4]
    assert [m.pk for m in up_to] == [m.pk for m in msgs[:4]]


def test_messages_up_to_edges():
    conv, a, _ = make_pair()
    for _ in range(3):
        post(conv, a)
    assert list(conv.messages_up_to(0)) == []
    assert [m.seq_no for m in conv.messages_up_to(1)] == [1]
    assert [m.seq_no for m in conv.messages_up_to(3)] == [1, 2, 3]
    assert [m.seq_no for m in conv.messages_up_to(99)] == [1, 2, 3]


def test_messages_up_to_returns_a_queryset_only_of_that_conversation():
    conv1, a1, _ = make_pair()
    conv2, a2, _ = make_pair()
    for _ in range(3):
        post(conv1, a1)
        post(conv2, a2)
    result = conv1.messages_up_to(3)
    assert isinstance(result, models.QuerySet)
    assert {m.conversation_id for m in result} == {conv1.pk}
    assert result.count() == 3
    assert result.filter(author_type="user").count() == 3  # still chainable


def test_messages_up_to_is_ordered_by_seq_no_not_by_insertion():
    from forum.models import Message

    conv, a, _ = make_pair()
    Message.objects.bulk_create([raw_message(conv, s, participant=a) for s in (5, 3, 1, 4, 2)])
    assert [m.seq_no for m in conv.messages_up_to(4)] == [1, 2, 3, 4]
    assert [m.seq_no for m in conv.messages_up_to(99)] == [1, 2, 3, 4, 5]


def test_messages_up_to_includes_moderator_messages():
    conv, a, _ = make_pair()
    post(conv, a)
    post_moderator(conv)
    assert [m.author_type for m in conv.messages_up_to(2)] == ["user", "moderator"]


def test_messages_up_to_on_a_conversation_without_messages_is_empty():
    conv, _, _ = make_pair()
    assert conv.messages_up_to(5).count() == 0
