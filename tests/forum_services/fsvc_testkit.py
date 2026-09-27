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
    "empty", "too_long", "too_fast", "conversation_full", "closed", "invalid_side", "not_participant",
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


def make_active(topic=None, labels=("A", "B"), sides=("pro", "con")):
    """An active conversation built with the ORM so the labels are known: returns a namespace with conv, a, b
    (participants), ua, ub (users). User ua holds label labels[0] and joined first."""
    from forum.models import Conversation, Participant

    topic = topic or make_prop()
    ua, ub = make_user(), make_user()
    conv = Conversation.objects.create(topic=topic, status="active", label_seed=12345)
    a = Participant.objects.create(conversation=conv, user=ua, label=labels[0], join_order=1, side=sides[0])
    b = Participant.objects.create(conversation=conv, user=ub, label=labels[1], join_order=2, side=sides[1])
    return SimpleNamespace(conv=conv, a=a, b=b, ua=ua, ub=ub, topic=topic)


def opposite(side):
    return {"pro": "con", "con": "pro"}[side or "pro"]


def enter(user, topic, side=None):
    """enter_proposition with a side. When no side is given: the side opposite to the oldest person waiting on the topic
    (so that the two people pair, as in the tests written before positions existed), else "pro". A legacy waiter with a
    blank side counts as "pro"."""
    from forum.models import Conversation

    if side is None:
        waiting = (
            Conversation.objects.filter(topic_id=topic.pk, source="human", status="open")
            .exclude(participants__user_id=user.pk)
            .order_by("created_at", "id")
        )
        side = "pro"
        for conv in waiting:
            people = list(conv.participants.all())
            if len(people) == 1:
                side = opposite(people[0].side)
                break
    return svc().enter_proposition(user, topic, side)


def make_active_via_service(topic=None):
    """An active conversation built through the pairing rule: the first user enters as pro, the second as con."""
    topic = topic or make_prop()
    u1, u2 = make_user(), make_user()
    conv = svc().enter_proposition(u1, topic, "pro")
    joined = svc().enter_proposition(u2, topic, "con")
    assert joined.pk == conv.pk
    conv.refresh_from_db()
    return SimpleNamespace(conv=conv, u1=u1, u2=u2, topic=topic)


def make_waiting(topic=None, user=None, created_at=None, side="", seed=None):
    """A waiting conversation (one participant) made with the ORM. By default it is a LEGACY row as stored before step
    7c: label "A", no seed, blank side. Pass side (and seed) for a row as the current code stores it."""
    from forum.models import Conversation, Participant

    topic = topic or make_prop()
    user = user or make_user()
    conv = Conversation.objects.create(topic=topic, status="open", label_seed=seed)
    label = svc().assign_labels(seed)[0] if seed is not None else "A"
    Participant.objects.create(conversation=conv, user=user, label=label, join_order=1, side=side)
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


# Step 7c revision 5: the other participant's USERNAME is shown to the viewer, in these fields only.
ALLOWED_NAME_KEYS = {"other_username", "author_name", "heading", "waiting_username"}


def name_paths(value, needle, path=()):
    """Key paths of every string (or key) in a nested structure that contains `needle`."""
    if isinstance(value, str):
        if needle in value:
            yield path
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and needle in key:
                yield path + (str(key),)
            yield from name_paths(item, needle, path + (str(key),))
    elif isinstance(value, (list, tuple, set)):
        for index, item in enumerate(value):
            yield from name_paths(item, needle, path + (str(index),))


def assert_names_only_where_allowed(structure, viewer, other=None, allowed=ALLOWED_NAME_KEYS):
    """The viewer's own username and every email never appear; the other person's username appears only under the
    allowed keys; no label letter and no capitalised 'Participant' anywhere (once the allowed name is set aside)."""
    for text in walk_strings(structure):
        assert viewer.username not in text, (viewer.username, text)
        assert viewer.email not in text and viewer.email.split("@")[0] not in text, text
        if other is not None:
            assert other.email not in text, text
    if other is not None:
        for path in name_paths(structure, other.username):
            assert path and path[-1] in allowed, (other.username, path)
    for text in walk_strings(structure):
        if other is not None:
            text = text.replace(other.username, "")
        assert "Participant" not in text, text
        assert not _LABEL_WORD.search(text), text
