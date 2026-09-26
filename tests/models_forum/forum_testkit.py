"""Helpers for the step 4a tests (tests/models_forum/). Nothing here imports from a conftest; each test file imports
this module by name (pytest puts the test folder on sys.path)."""
import itertools
from contextlib import contextmanager

import pytest

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

_counter = itertools.count(1)

# A save can be refused by the model (ValidationError) or, for a pure uniqueness rule, by the database (IntegrityError).
REFUSED = (ValidationError, IntegrityError)


def uniq(prefix="x"):
    return f"{prefix}{next(_counter)}"


def make_user(name=None):
    """A user without a usable password (fast: no password hashing). Usernames are ASCII letters and digits."""
    name = name or uniq("user")
    return get_user_model().objects.create_user(username=name, email=f"{name}@example.com")


def make_topic(title=None):
    from forum.models import Topic

    return Topic.objects.create(title=title or uniq("topic"), description="d", proposition="p")


def make_experiment(name=None, kind="paired"):
    from forum.models import Experiment

    return Experiment.objects.create(name=name or uniq("exp"), kind=kind)


def make_conversation(source="human", topic=None, **extra):
    from forum.models import Conversation

    return Conversation.objects.create(topic=topic or make_topic(), source=source, **extra)


def add_participant(conversation, label="A", join_order=1, user="auto"):
    """Add a participant. user='auto' means: a fresh user for a human conversation, none for a synthetic one."""
    from forum.models import Participant

    if user == "auto":
        user = make_user() if conversation.source == "human" else None
    return Participant.objects.create(conversation=conversation, user=user, label=label, join_order=join_order)


def make_pair(source="human"):
    """(conversation, participant A, participant B)."""
    conv = make_conversation(source)
    return conv, add_participant(conv, "A", 1), add_participant(conv, "B", 2)


def post(conversation, participant, content="hello", **extra):
    from forum.models import Message

    return Message.objects.create(
        conversation=conversation, author_type="user", participant=participant, content=content, **extra
    )


def post_moderator(conversation, content="a moderator note", **extra):
    from forum.models import Message

    return Message.objects.create(conversation=conversation, author_type="moderator", content=content, **extra)


def raw_message(conversation, seq_no, author_type="user", participant=None, content="x", char_count=1, **extra):
    """An unsaved Message with every column given, for bulk_create (which skips save() and its validation)."""
    from forum.models import Message

    return Message(
        conversation=conversation, seq_no=seq_no, author_type=author_type, participant=participant, content=content,
        char_count=char_count, **extra,
    )  # fmt: skip


@contextmanager
def refused():
    """Demand that the block is refused (ValidationError or IntegrityError); the savepoint keeps the test transaction
    usable afterwards."""
    with pytest.raises(REFUSED), transaction.atomic():
        yield


@contextmanager
def invalid():
    """Demand a model-level ValidationError (the friendly kind) from the block."""
    with pytest.raises(ValidationError) as excinfo, transaction.atomic():
        yield excinfo


def refused_by_database(action):
    """Run action() and demand an IntegrityError from the database itself; the savepoint keeps the test transaction
    usable afterwards."""
    with pytest.raises(IntegrityError), transaction.atomic():
        action()
