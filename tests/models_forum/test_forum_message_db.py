"""Message and Participant constraints enforced by the database itself, bypassing save() (bulk_create, queryset
update): seq_no >= 1, char_count >= 1, moderator messages have no participant, user messages have one, unique
(conversation, seq_no). Brief 4a."""
import pytest

from forum_testkit import make_pair, post, post_moderator, raw_message, refused_by_database

pytestmark = pytest.mark.django_db


def bulk(*messages):
    from forum.models import Message

    return Message.objects.bulk_create(list(messages))


def test_a_well_formed_bulk_created_message_is_accepted_so_the_refusals_below_are_about_the_rule():
    conv, a, _ = make_pair()
    made = bulk(
        raw_message(conv, 1, "user", a, "hello", 5),
        raw_message(conv, 2, "moderator", None, "note", 4),
    )
    assert [m.pk is not None for m in made] == [True, True]


@pytest.mark.parametrize("seq_no", [0, -1])
def test_the_database_refuses_seq_no_below_one_on_insert(seq_no):
    conv, a, _ = make_pair()
    refused_by_database(lambda: bulk(raw_message(conv, seq_no, "user", a)))


def test_the_database_refuses_seq_no_below_one_on_update():
    conv, a, _ = make_pair()
    msg = post(conv, a)
    from forum.models import Message

    refused_by_database(lambda: Message.objects.filter(pk=msg.pk).update(seq_no=0))


@pytest.mark.parametrize("char_count", [0, -1])
def test_the_database_refuses_char_count_below_one_on_insert(char_count):
    conv, a, _ = make_pair()
    refused_by_database(lambda: bulk(raw_message(conv, 1, "user", a, char_count=char_count)))


def test_the_database_refuses_char_count_below_one_on_update():
    from forum.models import Message

    conv, a, _ = make_pair()
    msg = post(conv, a)
    refused_by_database(lambda: Message.objects.filter(pk=msg.pk).update(char_count=0))


def test_the_database_refuses_a_moderator_message_with_a_participant_on_insert():
    conv, a, _ = make_pair()
    refused_by_database(lambda: bulk(raw_message(conv, 1, "moderator", a)))


def test_the_database_refuses_a_user_message_without_a_participant_on_insert():
    conv, _, _ = make_pair()
    refused_by_database(lambda: bulk(raw_message(conv, 1, "user", None)))


def test_the_database_refuses_removing_the_participant_of_a_user_message_by_update():
    from forum.models import Message

    conv, a, _ = make_pair()
    msg = post(conv, a)
    refused_by_database(lambda: Message.objects.filter(pk=msg.pk).update(participant=None))


def test_the_database_refuses_giving_a_moderator_message_a_participant_by_update():
    from forum.models import Message

    conv, a, _ = make_pair()
    msg = post_moderator(conv)
    refused_by_database(lambda: Message.objects.filter(pk=msg.pk).update(participant=a))


def test_the_database_refuses_turning_a_user_message_into_a_moderator_message_by_update():
    from forum.models import Message

    conv, a, _ = make_pair()
    msg = post(conv, a)
    refused_by_database(lambda: Message.objects.filter(pk=msg.pk).update(author_type="moderator"))


def test_the_database_refuses_turning_a_moderator_message_into_a_user_message_by_update():
    from forum.models import Message

    conv, _, _ = make_pair()
    msg = post_moderator(conv)
    refused_by_database(lambda: Message.objects.filter(pk=msg.pk).update(author_type="user"))


def test_the_database_refuses_a_repeated_seq_no_in_one_conversation():
    conv, a, _ = make_pair()
    post(conv, a)
    refused_by_database(lambda: bulk(raw_message(conv, 1, "user", a)))


def test_the_database_refuses_a_repeated_seq_no_within_one_bulk_insert():
    conv, a, _ = make_pair()
    refused_by_database(lambda: bulk(raw_message(conv, 7, "user", a), raw_message(conv, 7, "user", a)))


def test_the_database_refuses_a_repeated_seq_no_created_by_update():
    from forum.models import Message

    conv, a, _ = make_pair()
    post(conv, a)
    second = post(conv, a)
    refused_by_database(lambda: Message.objects.filter(pk=second.pk).update(seq_no=1))


def test_the_same_seq_no_is_fine_in_different_conversations():
    conv1, a1, _ = make_pair()
    conv2, a2, _ = make_pair()
    bulk(raw_message(conv1, 1, "user", a1), raw_message(conv2, 1, "user", a2))


def test_a_refused_bulk_insert_stores_nothing_from_that_batch():
    from forum.models import Message

    conv, a, _ = make_pair()
    refused_by_database(
        lambda: bulk(raw_message(conv, 1, "user", a), raw_message(conv, 2, "moderator", a))  # the second breaks the rule
    )
    assert Message.objects.count() == 0


def test_seq_no_cannot_be_left_empty_when_bypassing_save():
    """save() fills in a missing seq_no; bulk_create does not go through save(), and the column is NOT NULL."""
    from forum.models import Message

    conv, a, _ = make_pair()
    refused_by_database(
        lambda: Message.objects.bulk_create([Message(conversation=conv, author_type="user", participant=a, content="x", char_count=1)])
    )


def test_seq_no_is_required_at_model_level_only_through_save_assignment():
    from forum.models import Message

    conv, a, _ = make_pair()
    msg = Message(conversation=conv, author_type="user", participant=a, content="hello")
    assert msg.seq_no is None
    msg.save()
    assert msg.seq_no == 1
