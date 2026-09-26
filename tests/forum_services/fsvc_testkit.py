"""Helpers for the step 7a tests (tests/forum_services/). Test modules import this by name (pytest puts the test
folder on sys.path); nothing imports from a conftest. Imports of the code under test happen inside functions so a
missing feature fails the tests that need it, one by one."""
import itertools
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

from django.contrib.auth import get_user_model

_counter = itertools.count(1)

T0 = datetime(2026, 3, 10, 12, 0, 0, tzinfo=dt_timezone.utc)

# Words that must never show up in text meant for people.
INTERNAL_WORDS = ("Traceback", "Exception", "Error", "None", "IntegrityError", "seq_no", "NoneType", "{", "}", "<", "pk=")

ALL_CODES = {
    "empty", "too_long", "too_fast", "conversation_full", "closed", "waiting", "not_participant",
    "daily_limit", "duplicate", "too_many_open", "hidden",
}  # fmt: skip
# Also allowed (architect ruling): a person who is not logged in, a reply to a message of another conversation, and
# a model-level refusal that slipped past the checks.
EXTRA_CODES = {"login_required", "invalid_reply", "not_saved"}


def uniq(prefix="x"):
    return f"{prefix}{next(_counter):04d}"


def svc():
    import forum.services as services

    return services


def views():
    import forum.viewmodels as viewmodels

    return viewmodels


def make_user(name=None):
    """A user with a recognisable username and email (fixed-width numbers so no name is a prefix of another)."""
    name = name or uniq("quillmoth")
    return get_user_model().objects.create_user(username=name, email=f"{name}@mailbox.example")


def make_prop(text=None, created_by=None, hidden=False, title=""):
    """A visible proposition made directly through the ORM (so tests do not depend on create_proposition)."""
    from forum.models import Topic

    return Topic.objects.create(
        title=title, proposition=text or f"Cats make better pets than dogs {uniq('n')}", created_by=created_by, hidden=hidden
    )


def participant_of(conversation, user):
    from forum.models import Participant

    return Participant.objects.get(conversation=conversation, user=user)


def other_of(conversation, user):
    from forum.models import Participant

    return Participant.objects.exclude(user=user).get(conversation=conversation)


def make_active(topic=None, labels=("A", "B")):
    """An active conversation built with the ORM so the labels are known: returns a namespace with conv, a, b
    (participants), ua, ub (users). User ua holds label labels[0] and joined first."""
    from forum.models import Conversation, Participant

    topic = topic or make_prop()
    ua, ub = make_user(), make_user()
    conv = Conversation.objects.create(topic=topic, status="active", label_seed=12345)
    a = Participant.objects.create(conversation=conv, user=ua, label=labels[0], join_order=1)
    b = Participant.objects.create(conversation=conv, user=ub, label=labels[1], join_order=2)
    return SimpleNamespace(conv=conv, a=a, b=b, ua=ua, ub=ub, topic=topic)


def make_active_via_service(topic=None):
    """An active conversation built through the pairing rule: the first user enters, the second joins."""
    s = svc()
    topic = topic or make_prop()
    u1, u2 = make_user(), make_user()
    conv = s.enter_proposition(u1, topic)
    joined = s.enter_proposition(u2, topic)
    assert joined.pk == conv.pk
    conv.refresh_from_db()
    return SimpleNamespace(conv=conv, u1=u1, u2=u2, topic=topic)


def make_waiting(topic=None, user=None, created_at=None):
    """A waiting conversation (one participant) made with the ORM. The label is a placeholder."""
    from forum.models import Conversation, Participant

    topic = topic or make_prop()
    user = user or make_user()
    conv = Conversation.objects.create(topic=topic, status="open")
    Participant.objects.create(conversation=conv, user=user, label="A", join_order=1)
    if created_at is not None:
        Conversation.objects.filter(pk=conv.pk).update(created_at=created_at)
        conv.refresh_from_db()
    return conv


def orm_user_message(conversation, participant, content="hello there", when=None):
    from forum.models import Message

    message = Message.objects.create(conversation=conversation, author_type="user", participant=participant, content=content)
    if when is not None:
        Message.objects.filter(pk=message.pk).update(created_at=when)
        message.refresh_from_db()
    return message


def orm_moderator_message(conversation, in_reply_to=None, content="a calm note from the moderator"):
    from forum.models import Message

    return Message.objects.create(
        conversation=conversation, author_type="moderator", content=content, in_reply_to=in_reply_to
    )


def orm_run(conversation, trigger, status="pending", **extra):
    from moderation.models import ModerationRun

    return ModerationRun.objects.create(
        conversation=conversation, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live", status=status, **extra
    )


def orm_act(run, addressee, subject, source_messages=(), order=1, validity="valid", **extra):
    from moderation.models import InterventionAct

    fields = dict(
        run=run, order=order, act_type="request_clarification", tone="neutral", text="Could you say more?",
        addressee=addressee, subject=subject, validity=validity,
    )  # fmt: skip
    if validity == "rejected":
        fields["rejection_reason"] = "test"
    fields.update(extra)
    act = InterventionAct.objects.create(**fields)
    if source_messages:
        act.source_messages.set(source_messages)
    return act


def counts():
    """Row counts that a rejected call must leave untouched."""
    from forum.models import Conversation, Message, Participant, Topic
    from moderation.models import ModerationRun

    return {
        "messages": Message.objects.count(),
        "runs": ModerationRun.objects.count(),
        "topics": Topic.objects.count(),
        "conversations": Conversation.objects.count(),
        "participants": Participant.objects.count(),
    }


def rejection(fn, *args, **kwargs):
    """Call fn and demand a PostRejected; return it."""
    from forum.services import PostRejected

    try:
        fn(*args, **kwargs)
    except PostRejected as exc:
        return exc
    raise AssertionError(f"{getattr(fn, '__name__', fn)} was accepted but a PostRejected was expected")


def assert_plain(exc):
    """The message is plain words for people: a sentence, no internals, no identities."""
    text = exc.message
    assert isinstance(text, str) and text.strip(), exc.code
    assert text == text.strip()
    assert text[0].isupper(), text
    assert text[-1] in ".?!", text
    for word in INTERNAL_WORDS:
        assert word not in text, (word, text)
    assert "Participant" not in text
    assert isinstance(exc.code, str) and exc.code
    assert exc.retry_after is None or (isinstance(exc.retry_after, int) and not isinstance(exc.retry_after, bool))
    assert isinstance(exc.details, dict)


def walk_strings(value):
    """Every string in a nested structure of dicts, lists and tuples, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from walk_strings(key)
            yield from walk_strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from walk_strings(item)


_LABEL_WORD = re.compile(r"(?<![A-Za-z0-9_])[A-Z](?![A-Za-z0-9_])")


def assert_no_identity(structure, *users):
    """No label letter, no capitalised 'Participant', no username or email of the given users anywhere."""
    for text in walk_strings(structure):
        assert "Participant" not in text, text
        assert not _LABEL_WORD.search(text), text
        for user in users:
            assert user.username not in text, (user.username, text)
            assert user.email not in text, (user.email, text)
            assert user.email.split("@")[0] not in text, (user.email, text)


def at(day, hour=12, minute=0, second=0, micro=0, month=3):
    return datetime(2026, month, day, hour, minute, second, micro, tzinfo=dt_timezone.utc)


def seconds(n):
    return timedelta(seconds=n)
