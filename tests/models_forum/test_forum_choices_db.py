"""Choice fields are also checked by the database (coordinator clarification for 4a): Experiment.kind,
Conversation.status, Conversation.source, Message.author_type. Bypasses save() with bulk_create and queryset update."""
import pytest

from forum_testkit import make_conversation, make_experiment, make_pair, make_topic, post, refused_by_database

pytestmark = pytest.mark.django_db


def test_the_database_refuses_an_unknown_experiment_kind_on_insert():
    from forum.models import Experiment

    refused_by_database(lambda: Experiment.objects.bulk_create([Experiment(name="e", kind="nonsense")]))


def test_the_database_refuses_an_unknown_experiment_kind_on_update():
    from forum.models import Experiment

    exp = make_experiment()
    refused_by_database(lambda: Experiment.objects.filter(pk=exp.pk).update(kind="nonsense"))


@pytest.mark.parametrize("kind", ["paired", "series", "replay", "warmup", "observational"])
def test_the_database_accepts_every_experiment_kind(kind):
    from forum.models import Experiment

    Experiment.objects.bulk_create([Experiment(name=f"e-{kind}", kind=kind)])


def test_the_database_refuses_an_unknown_conversation_status_on_insert_and_update():
    from forum.models import Conversation

    topic = make_topic()
    refused_by_database(lambda: Conversation.objects.bulk_create([Conversation(topic=topic, status="archived")]))
    conv = make_conversation(topic=topic)
    refused_by_database(lambda: Conversation.objects.filter(pk=conv.pk).update(status="archived"))


def test_the_database_refuses_an_unknown_conversation_source_on_insert_and_update():
    from forum.models import Conversation

    topic = make_topic()
    refused_by_database(lambda: Conversation.objects.bulk_create([Conversation(topic=topic, source="robot")]))
    conv = make_conversation(topic=topic)
    refused_by_database(lambda: Conversation.objects.filter(pk=conv.pk).update(source="robot"))


@pytest.mark.parametrize("status", ["open", "active", "closed"])
@pytest.mark.parametrize("source", ["human", "synthetic"])
def test_the_database_accepts_every_valid_status_and_source(status, source):
    from forum.models import Conversation

    Conversation.objects.bulk_create([Conversation(topic=make_topic(), status=status, source=source)])


def test_the_database_refuses_an_unknown_author_type_on_insert_and_update():
    from forum.models import Message

    from forum_testkit import raw_message

    conv, a, _ = make_pair()
    refused_by_database(lambda: Message.objects.bulk_create([raw_message(conv, 1, "bot", a)]))
    refused_by_database(lambda: Message.objects.bulk_create([raw_message(conv, 1, "bot", None)]))
    msg = post(conv, a)
    refused_by_database(lambda: Message.objects.filter(pk=msg.pk).update(author_type="bot"))
