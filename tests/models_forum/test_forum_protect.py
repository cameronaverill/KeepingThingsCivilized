"""on_delete=PROTECT: nothing cascades silently (brief decision 2)."""
import pytest
from django.db.models import ProtectedError

from forum_testkit import (
    add_participant, make_conversation, make_experiment, make_pair, make_topic, make_user, post, post_moderator,
)  # fmt: skip

pytestmark = pytest.mark.django_db


def test_a_topic_with_a_conversation_cannot_be_deleted():
    from forum.models import Conversation, Topic

    topic = make_topic()
    make_conversation(topic=topic)
    with pytest.raises(ProtectedError):
        topic.delete()
    assert Topic.objects.filter(pk=topic.pk).exists()
    assert Conversation.objects.count() == 1


def test_a_topic_without_conversations_can_be_deleted():
    from forum.models import Topic

    topic = make_topic()
    topic.delete()
    assert Topic.objects.count() == 0


def test_an_experiment_with_a_conversation_cannot_be_deleted():
    from forum.models import Experiment

    exp = make_experiment()
    make_conversation("synthetic", experiment=exp)
    with pytest.raises(ProtectedError):
        exp.delete()
    assert Experiment.objects.filter(pk=exp.pk).exists()


def test_an_experiment_without_conversations_can_be_deleted():
    exp = make_experiment()
    exp.delete()


def test_a_conversation_with_participants_cannot_be_deleted():
    from forum.models import Conversation

    conv = make_conversation("synthetic")
    add_participant(conv, "A", 1)
    with pytest.raises(ProtectedError):
        conv.delete()
    assert Conversation.objects.filter(pk=conv.pk).exists()


def test_a_conversation_with_only_a_moderator_message_cannot_be_deleted():
    from forum.models import Conversation

    conv = make_conversation("human")
    post_moderator(conv)
    with pytest.raises(ProtectedError):
        conv.delete()
    assert Conversation.objects.filter(pk=conv.pk).exists()


def test_a_conversation_with_messages_cannot_be_deleted_and_nothing_is_lost():
    from forum.models import Conversation, Message, Participant

    conv, a, b = make_pair()
    post(conv, a)
    post(conv, b)
    with pytest.raises(ProtectedError):
        conv.delete()
    assert Conversation.objects.filter(pk=conv.pk).exists()
    assert Message.objects.count() == 2
    assert Participant.objects.count() == 2


def test_a_queryset_delete_is_protected_too():
    from forum.models import Conversation, Topic

    topic = make_topic()
    make_conversation(topic=topic)
    with pytest.raises(ProtectedError):
        Topic.objects.filter(pk=topic.pk).delete()
    with pytest.raises(ProtectedError):
        Topic.objects.all().delete()
    assert Conversation.objects.count() == 1


def test_a_user_who_is_a_participant_cannot_be_deleted():
    from django.contrib.auth import get_user_model

    conv = make_conversation("human")
    user = make_user()
    add_participant(conv, "A", 1, user=user)
    with pytest.raises(ProtectedError):
        user.delete()
    assert get_user_model().objects.filter(pk=user.pk).exists()


def test_a_user_who_is_not_a_participant_can_still_be_deleted():
    from django.contrib.auth import get_user_model

    user = make_user()
    user.delete()
    assert get_user_model().objects.count() == 0


def test_a_participant_with_messages_cannot_be_deleted():
    from forum.models import Message, Participant

    conv, a, _ = make_pair()
    post(conv, a)
    with pytest.raises(ProtectedError):
        a.delete()
    assert Participant.objects.filter(pk=a.pk).exists()
    assert Message.objects.count() == 1


def test_a_participant_without_messages_can_be_deleted():
    from forum.models import Participant

    conv, a, b = make_pair()
    b.delete()
    assert Participant.objects.filter(conversation=conv).count() == 1


def test_a_message_that_has_a_reply_cannot_be_deleted():
    from forum.models import Message

    conv, a, b = make_pair()
    first = post(conv, a)
    reply = post(conv, b, in_reply_to=first)
    with pytest.raises(ProtectedError):
        first.delete()
    assert Message.objects.count() == 2
    assert Message.objects.get(pk=reply.pk).in_reply_to_id == first.pk


def test_a_message_without_replies_can_be_deleted():
    from forum.models import Message

    conv, a, b = make_pair()
    first = post(conv, a)
    reply = post(conv, b, in_reply_to=first)
    reply.delete()
    first.delete()
    assert Message.objects.count() == 0


def test_a_message_reply_chain_is_protected_at_every_link():
    conv, a, b = make_pair()
    m1 = post(conv, a)
    m2 = post(conv, b, in_reply_to=m1)
    m3 = post_moderator(conv, in_reply_to=m2)
    for message in (m1, m2):
        with pytest.raises(ProtectedError):
            message.delete()
    m3.delete()
    m2.delete()
    m1.delete()
