"""Helpers for tests/models_moderation: small factories built only from the brief's contract (docs/step4_brief.md).

Everything imports the models lazily, so a missing app fails inside the test that needs it, not at collection.
Conversations are synthetic (participants without users), which keeps the factories independent of the accounts app.
"""
import itertools

from django.db import connection

_counter = itertools.count(1)


def n():
    return next(_counter)


def make_conversation(labels="AB", source="synthetic"):
    """A conversation (synthetic, so participants need no user) with one participant per label. Returns (conv, {label: participant})."""
    from forum.models import Conversation, Participant, Topic

    topic = Topic.objects.create(title=f"topic {n()}", description="d", proposition="p")
    conv = Conversation.objects.create(topic=topic, source=source)
    participants = {}
    for order, label in enumerate(labels, start=1):
        participants[label] = Participant.objects.create(conversation=conv, label=label, join_order=order)
    return conv, participants


def user_msg(conv, participants, label="A", content=None, in_reply_to=None, seq_no=None):
    from forum.models import Message

    return Message.objects.create(
        conversation=conv,
        author_type="user",
        participant=participants[label],
        content=content if content is not None else f"user message number {n()} about the topic",
        in_reply_to=in_reply_to,
        seq_no=seq_no,
    )


def mod_msg(conv, in_reply_to=None, content=None, seq_no=None):
    from forum.models import Message

    return Message.objects.create(
        conversation=conv,
        author_type="moderator",
        participant=None,
        content=content if content is not None else f"moderator note {n()}",
        in_reply_to=in_reply_to,
        seq_no=seq_no,
    )


def make_run(trigger, **kwargs):
    """A valid live run on `trigger` (a user message) unless kwargs say otherwise."""
    from moderation.models import ModerationRun

    values = dict(conversation=trigger.conversation, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live")
    values.update(kwargs)
    return ModerationRun.objects.create(**values)


def unsaved_run(trigger, **kwargs):
    """An unsaved run instance with every default applied (for raw INSERTs and bulk_create)."""
    from moderation.models import ModerationRun

    values = dict(conversation=trigger.conversation, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live")
    values.update(kwargs)
    return ModerationRun(**values)


def scenario(labels="AB"):
    """(conv, parts, first user message, moderator reply, second user message): seq 1, 2, 3."""
    conv, parts = make_conversation(labels)
    first = user_msg(conv, parts, "A", "The moon is made of green cheese, everybody knows that.")
    reply = mod_msg(conv, in_reply_to=first, content="Please cite a source for that claim.")
    second = user_msg(conv, parts, "B", "That is simply not true, and you are being ridiculous.", in_reply_to=first)
    return conv, parts, first, reply, second


def make_issue(run, message, local_id=None, **kwargs):
    from moderation.models import Issue

    values = dict(
        run=run,
        local_id=local_id or f"i{n()}",
        message=message,
        issue_type="unsupported_claim",
        quote="",
        quote_start=None,
        quote_end=None,
        quote_match="not_found",
        explanation="because",
        confidence=0.5,
    )
    values.update(kwargs)
    return Issue.objects.create(**values)


def unsaved_issue(run, message, local_id=None, **kwargs):
    from moderation.models import Issue

    values = dict(
        run=run,
        local_id=local_id or f"i{n()}",
        message=message,
        issue_type="unsupported_claim",
        quote="",
        quote_match="not_found",
        explanation="because",
        confidence=0.5,
    )
    values.update(kwargs)
    return Issue(**values)


def make_act(run, order=1, **kwargs):
    from moderation.models import InterventionAct

    values = dict(
        run=run, order=order, act_type="request_information", tone="neutral", text="Could you share a source?",
        addressee="A", subject="none",
    )  # fmt: skip
    values.update(kwargs)
    return InterventionAct.objects.create(**values)


def unsaved_act(run, order=1, **kwargs):
    from moderation.models import InterventionAct

    values = dict(
        run=run, order=order, act_type="request_information", tone="neutral", text="Could you share a source?",
        addressee="A", subject="none",
    )  # fmt: skip
    values.update(kwargs)
    return InterventionAct(**values)


def raw_insert(instance):
    """INSERT `instance` (an unsaved model) with connection.cursor(), skipping Django's save() and model validation.
    Every concrete column except the auto primary key is written, from the instance's (default-filled) values."""
    opts = instance._meta
    fields = [f for f in opts.concrete_fields if not f.primary_key]
    columns = ", ".join(connection.ops.quote_name(f.column) for f in fields)
    marks = ", ".join(["%s"] * len(fields))
    values = [f.get_db_prep_save(f.pre_save(instance, True), connection) for f in fields]
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {connection.ops.quote_name(opts.db_table)} ({columns}) VALUES ({marks})", values)
        return cursor.lastrowid


def raw_update(table, pk, **columns):
    sets = ", ".join(f"{connection.ops.quote_name(c)} = %s" for c in columns)
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {connection.ops.quote_name(table)} SET {sets} WHERE id = %s", [*columns.values(), pk])


def triggers(table=None):
    """Names and SQL of the SQLite triggers (optionally only those on `table`)."""
    sql = "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger'"
    params = []
    if table:
        sql += " AND tbl_name = %s"
        params.append(table)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def moderation_triggers():
    """Every trigger the moderation migration owns, wherever it sits (the run table or forum_message): their SQL names moderation_."""
    return [t for t in triggers() if "moderation_" in (t[0] + (t[2] or "")).lower()]
