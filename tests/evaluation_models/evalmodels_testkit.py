"""Helpers for tests/evaluation_models (step 12, first wave). Built only from docs/step12_brief.md and docs/plan.md.

Nothing imports from a conftest. Models are imported lazily, so a missing feature fails inside the test that needs it
and not at collection. A few field names are not fixed by the brief (the JSON field on Rating that lists the covered
dimensions, the M2M from ConsensusFinding to Finding, the FK from CalibrationItem to its set): the helpers find them by
their shape in the model's meta, so the tests do not depend on a guess.
"""
import itertools

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection

_counter = itertools.count(1)

# A save can be refused by the model (friendly ValidationError) or by the database (IntegrityError).
REFUSED = (ValidationError, IntegrityError)


def n():
    return next(_counter)


# --- forum and moderation rows -----------------------------------------------------------------------------------------
def make_user(name=None):
    name = name or f"evaluser{n()}"
    return get_user_model().objects.create_user(username=name, email=f"{name}@example.com")


def make_conversation(labels="AB", **extra):
    """A synthetic conversation (participants need no user) with one participant per label. Returns (conv, {label: participant})."""
    from forum.models import Conversation, Participant, Topic

    topic = extra.pop("topic", None) or Topic.objects.create(title=f"evaltopic {n()}", description="d", proposition="p")
    conv = Conversation.objects.create(topic=topic, source="synthetic", **extra)
    parts = {}
    for order, label in enumerate(labels, start=1):
        parts[label] = Participant.objects.create(conversation=conv, label=label, join_order=order)
    return conv, parts


def user_msg(conv, parts, label="A", content=None, **extra):
    from forum.models import Message

    return Message.objects.create(
        conversation=conv,
        author_type="user",
        participant=parts[label],
        content=content if content is not None else f"a user message number {n()} about the topic",
        **extra,
    )


def mod_msg(conv, in_reply_to=None, content=None):
    from forum.models import Message

    return Message.objects.create(
        conversation=conv,
        author_type="moderator",
        participant=None,
        content=content if content is not None else f"moderator note {n()}",
        in_reply_to=in_reply_to,
    )


def make_run(trigger, **extra):
    from moderation.models import ModerationRun

    values = dict(conversation=trigger.conversation, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live")
    values.update(extra)
    return ModerationRun.objects.create(**values)


def make_issue(run, message, **extra):
    from moderation.models import Issue

    values = dict(
        run=run, local_id=f"i{n()}", message=message, issue_type="unsupported_claim", quote="", quote_match="not_found",
        explanation="because", confidence=0.5,
    )  # fmt: skip
    values.update(extra)
    return Issue.objects.create(**values)


def make_act(run, order=1, **extra):
    from moderation.models import InterventionAct

    values = dict(
        run=run, order=order, act_type="request_information", tone="neutral", text="Could you share a source?",
        addressee="A", subject="none",
    )  # fmt: skip
    values.update(extra)
    return InterventionAct.objects.create(**values)


def make_llm_call():
    from moderation.models import LLMCall

    return LLMCall.objects.create(purpose="judge", model="fake-model", max_tokens=100, status="ok")


def message_with_text(text, labels="AB"):
    """(message, conversation): a single user message whose content is exactly `text`."""
    conv, parts = make_conversation(labels)
    return user_msg(conv, parts, "A", text), conv


def act_with_text(text):
    """An intervention act (on a fresh run) whose text is exactly `text`."""
    conv, parts = make_conversation()
    trigger = user_msg(conv, parts, "A", "a message that triggered the run")
    return make_act(make_run(trigger), text=text)


def target_type_of(target):
    return "message" if target._meta.label == "forum.Message" else "intervention_act"


def target_ref(target):
    return dict(target_type=target_type_of(target), target_id=target.pk)


def text_of(target):
    return target.content if target._meta.label == "forum.Message" else target.text


# --- evaluation rows ---------------------------------------------------------------------------------------------------
def make_rater(kind="llm", name=None, **extra):
    from evaluation.models import Rater

    name = name or f"rater{n()}"
    if kind == "llm":
        values = dict(name=name, kind="llm", provider="anthropic", model="claude-sonnet-5", temperature=0.0)
    else:
        values = dict(name=name, kind="human", user=make_user())
    values.update(extra)
    return Rater.objects.create(**values)


def make_panel(raters=(), name=None, version=1, **extra):
    from evaluation.models import Panel

    panel = Panel.objects.create(name=name or f"panel{n()}", version=version, **extra)
    if raters:
        panel.raters.add(*raters)
    return panel


def covered_field_name():
    """The Rating field that holds the dimensions covered (the brief says 'dimensions covered JSON')."""
    from evaluation.models import Rating

    names = [f.name for f in Rating._meta.concrete_fields if "dimension" in f.name]
    assert len(names) == 1, f"expected exactly one dimensions field on Rating, found {names}"
    return names[0]


def make_rating(rater, target, dimensions=("factual_accuracy", "abusiveness"), **extra):
    from evaluation.models import Rating

    values = dict(rater=rater, **target_ref(target))
    values[covered_field_name()] = list(dimensions)
    values.update(extra)
    return Rating.objects.create(**values)


def unsaved_rating(rater, target, dimensions=("factual_accuracy", "abusiveness"), **extra):
    from evaluation.models import Rating

    values = dict(rater=rater, **target_ref(target))
    values[covered_field_name()] = list(dimensions)
    values.update(extra)
    return Rating(**values)


def rating_target_text(rating):
    from forum.models import Message
    from moderation.models import InterventionAct

    if rating.target_type == "message":
        return Message.objects.get(pk=rating.target_id).content
    return InterventionAct.objects.get(pk=rating.target_id).text


def finding_values(rating, start, end, dimension="factual_accuracy", intensity=2, quote=None, local_id=None, **extra):
    values = dict(
        rating=rating, local_id=local_id or f"f{n()}", dimension=dimension, start=start, end=end,
        quote=rating_target_text(rating)[start:end] if quote is None else quote, intensity=intensity,
    )  # fmt: skip
    if intensity is None:
        values.setdefault("not_scorable_reason", "contested")
    values.update(extra)
    return values


def make_finding(rating, start, end, **kwargs):
    from evaluation.models import Finding

    return Finding.objects.create(**finding_values(rating, start, end, **kwargs))


def unsaved_finding(rating, start, end, **kwargs):
    from evaluation.models import Finding

    return Finding(**finding_values(rating, start, end, **kwargs))


def findings_field():
    """The many-to-many on ConsensusFinding that lists the Findings it merges."""
    from evaluation.models import ConsensusFinding, Finding

    found = [f for f in ConsensusFinding._meta.many_to_many if f.related_model is Finding]
    assert len(found) == 1, f"expected exactly one M2M to Finding on ConsensusFinding, found {found}"
    return found[0]


def merged_findings(consensus):
    return getattr(consensus, findings_field().name)


def reverse_consensus_accessor(finding):
    return getattr(finding, findings_field().remote_field.get_accessor_name())


def make_consensus(panel, target, dimension="factual_accuracy", start=0, end=5, **extra):
    from evaluation.models import ConsensusFinding

    values = dict(panel=panel, dimension=dimension, start=start, end=end, n_raters=1, **target_ref(target))
    values.update(extra)
    return ConsensusFinding.objects.create(**values)


def item_set_field_name():
    """The FK on CalibrationItem that points at its CalibrationSet."""
    from evaluation.models import CalibrationItem, CalibrationSet

    found = [f.name for f in CalibrationItem._meta.concrete_fields if f.is_relation and f.related_model is CalibrationSet]
    assert len(found) == 1, found
    return found[0]


def items_of(calibration_set):
    from evaluation.models import CalibrationItem

    return CalibrationItem.objects.filter(**{item_set_field_name(): calibration_set})


def field_names(model):
    return {f.name for f in model._meta.concrete_fields}


def choice_values(model, field_name):
    return {value for value, _label in model._meta.get_field(field_name).choices}


def db_tables():
    return set(connection.introspection.table_names())


def triggers_matching(fragment):
    """SQLite triggers whose name, table or SQL mention `fragment` (lower case)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger'")
        rows = cursor.fetchall()
    return [r for r in rows if fragment in (r[0] + r[1] + (r[2] or "")).lower()]
