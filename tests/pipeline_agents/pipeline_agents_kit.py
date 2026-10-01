"""Helpers for tests/pipeline_agents (step 5a: moderation/agents.py and moderation/features.py).

Not a test module. Imported by name from the test files of this folder (never from a conftest). Imports of
moderation.* and forum.* happen inside functions so a missing module fails the test that needs it, not collection.

Everything here is built from the written contract in docs/step5_brief.md ("Interface between 5a and 5b").
"""
import importlib
import itertools
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

_counter = itertools.count(1)

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "moderation" / "prompts"


def latest_prompt_name(agent):
    """Highest-version prompt stem for an agent on disk, e.g. "master_v2" (what load_prompt picks)."""
    return max((p.stem for p in PROMPTS.glob(f"{agent}_v*.md")), key=lambda s: int(s.rsplit("_v", 1)[1]))
GOLDEN = ROOT / "golden" / "transcripts"

# Recognisable identities that must never reach a prompt, a request or a ledger row.
USER_A = dict(username="quillfeather_zx", email="quillfeather.zx@hidden-mail.example", first_name="Quillfeather", last_name="Zxandros")
USER_B = dict(username="brambleton_qq", email="brambleton.qq@hidden-mail.example", first_name="Brambleton", last_name="Qqvist")
FORBIDDEN_STRINGS = (
    "quillfeather_zx", "quillfeather.zx", "brambleton_qq", "brambleton.qq", "hidden-mail.example", "Quillfeather",
    "Zxandros", "Brambleton", "Qqvist", "@hidden",
)  # fmt: skip
TOPIC_TITLE = "Rent control in big cities"
TOPIC_PROPOSITION = "Cities should cap annual rent increases for existing tenants."
TOPIC_DESCRIPTION_MARKER = "DESCRIPTION-MARKER-xyzzy-should-not-be-sent"
TOPIC_LEANS = {"compass": {"pro": {"economic": -0.83, "social": 0.11}, "rationale": "LEANS-MARKER-plugh"}}

T0 = datetime(2031, 3, 4, 12, 0, 0, tzinfo=timezone.utc)  # a distinctive year, so a leaked timestamp is easy to spot


def n():
    return next(_counter)


def module(name):
    return importlib.import_module(name)


def agents():
    return module("moderation.agents")


def features():
    return module("moderation.features")


# --- Conversations, transcripts, runs -----------------------------------------------------------------------------

def make_user(spec):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(password=None, **spec)  # no password: unusable, and no slow hashing


def make_topic(**kwargs):
    from forum.models import Topic

    values = dict(
        title=f"{TOPIC_TITLE} {n()}",
        proposition=TOPIC_PROPOSITION,
        description=TOPIC_DESCRIPTION_MARKER,
        leans=TOPIC_LEANS,
    )
    values.update(kwargs)
    return Topic.objects.create(**values)


def make_human_scenario(human=True):
    """A HUMAN conversation (human=False makes a synthetic one without users, for tests that need two scenarios) with two recognisably named users. Messages: 1 A, 2 B, 3 moderator, 4 A (the trigger).
    Returns a SimpleNamespace(topic, conv, users, parts, msgs, run, transcript)."""
    from forum.models import Conversation, Message, Participant
    from moderation.models import ModerationRun

    # A spare conversation with two messages first, so that pks never coincide by accident: run.pk != conversation.pk
    # and message.pk != message.seq_no in every scenario (a mix-up of two ids would otherwise go unnoticed).
    _, spare_conv, spare_parts = make_synthetic_run()
    add_message(spare_conv, spare_parts, "A", "spare one")
    add_message(spare_conv, spare_parts, "B", "spare two")
    topic = make_topic()
    conv = Conversation.objects.create(topic=topic, source="human" if human else "synthetic")
    users = {"A": make_user(USER_A), "B": make_user(USER_B)} if human else {}
    parts = {
        label: Participant.objects.create(conversation=conv, user=users.get(label), label=label, join_order=order)
        for order, label in enumerate("AB", start=1)
    }
    m1 = Message.objects.create(conversation=conv, author_type="user", participant=parts["A"], content="Rents rose 12 percent last year, which is far too fast.")
    m2 = Message.objects.create(conversation=conv, author_type="user", participant=parts["B"], content="Caps reduce the supply of housing over time.", in_reply_to=m1)
    m3 = Message.objects.create(conversation=conv, author_type="moderator", participant=None, content="Could a source be given for the figure in message 1?", in_reply_to=m2)
    m4 = Message.objects.create(conversation=conv, author_type="user", participant=parts["A"], content='You said "supply falls", but supply of what exactly?', in_reply_to=m3)
    msgs = [m1, m2, m3, m4]
    for offset, message in zip((0, 40, 100, 130), msgs):
        Message.objects.filter(pk=message.pk).update(created_at=T0 + timedelta(seconds=offset))
    run = ModerationRun.objects.create(conversation=conv, trigger_message=m4, snapshot_seq=m4.seq_no, kind="live")
    assert run.pk != conv.pk and all(m.pk != m.seq_no for m in msgs)
    return SimpleNamespace(
        topic=topic, conv=conv, users=users, parts=parts, msgs=msgs, run=run, transcript=transcript_of(conv, m4.seq_no)
    )


def make_synthetic_run(labels="AB"):
    """(topic, conv, parts, run-less helper) for a synthetic conversation; use add_user_message / make_run."""
    from forum.models import Conversation, Participant

    topic = make_topic()
    conv = Conversation.objects.create(topic=topic, source="synthetic")
    parts = {
        label: Participant.objects.create(conversation=conv, label=label, join_order=order)
        for order, label in enumerate(labels, start=1)
    }
    return topic, conv, parts


def add_message(conv, parts, label, text, author_type="user"):
    from forum.models import Message

    if author_type == "moderator":
        return Message.objects.create(conversation=conv, author_type="moderator", participant=None, content=text)
    return Message.objects.create(conversation=conv, author_type="user", participant=parts[label], content=text)


def make_run(conv, trigger):
    from moderation.models import ModerationRun

    return ModerationRun.objects.create(conversation=conv, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live")


def transcript_of(conv, upto_seq=None):
    """The interface's transcript (plan: list of dicts, oldest first), built from the database rows."""
    from forum.models import Message

    qs = Message.objects.filter(conversation=conv).select_related("participant").order_by("seq_no")
    if upto_seq is not None:
        qs = qs.filter(seq_no__lte=upto_seq)
    out = []
    for m in qs:
        out.append(
            {
                "id": m.pk,
                "seq_no": m.seq_no,
                "author_type": m.author_type,
                "label": ("Participant " + m.participant.label) if m.author_type == "user" else "Moderator",
                "text": m.content,
                "created_at": m.created_at,
            }
        )
    return out


def hand_transcript(labels, *, start_id=1, gap_seconds=30, gaps=None, texts=None):
    """A transcript built by hand (no database). `labels` is a sequence like "AABAB" (a letter is a participant, "M" is
    the moderator). `gaps` is an optional list of seconds between consecutive messages (len(labels) - 1 entries)."""
    out = []
    moment = T0
    for index, letter in enumerate(labels):
        if index:
            moment = moment + timedelta(seconds=(gaps[index - 1] if gaps is not None else gap_seconds))
        is_mod = letter == "M"
        out.append(
            {
                "id": start_id + index,
                "seq_no": index + 1,
                "author_type": "moderator" if is_mod else "user",
                "label": "Moderator" if is_mod else f"Participant {letter}",
                "text": texts[index] if texts else f"message text {index + 1} by {letter}",
                "created_at": moment,
            }
        )
    return out


def golden_transcript(name):
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


def make_conversation_from_golden(name):
    """A synthetic conversation from golden/transcripts/<name>.json. Returns (data, topic, conv, run, transcript)."""
    from forum.models import Conversation, Message, Participant, Topic

    data = golden_transcript(name)
    topic = Topic.objects.create(title=f"{data['topic']['title']} {n()}", proposition=data["topic"]["proposition"])
    conv = Conversation.objects.create(topic=topic, source="synthetic")
    parts = {}
    for order, label in enumerate(sorted({m["author"] for m in data["messages"] if "Moderator" not in m["author"]}), start=1):
        parts[label] = Participant.objects.create(conversation=conv, label=label.split()[-1], join_order=order)
    trigger = None
    for m in data["messages"]:
        if "Moderator" in m["author"]:
            row = Message.objects.create(conversation=conv, author_type="moderator", participant=None, content=m["text"])
        else:
            row = Message.objects.create(conversation=conv, author_type="user", participant=parts[m["author"]], content=m["text"])
        if m["seq"] == data["trigger_seq"]:
            trigger = row
    run = make_run(conv, trigger)
    return data, topic, conv, run, transcript_of(conv, trigger.seq_no)


def make_issue(run, message, local_id="i1", **kwargs):
    from moderation.models import Issue

    values = dict(
        run=run,
        local_id=local_id,
        message=message,
        issue_type="possible_factual_error",
        quote=message.content[:10],
        quote_start=0,
        quote_end=10,
        quote_match="exact",
        explanation="The figure is not what the records show.",
        confidence=0.8,
        intensity=3,
    )
    values.update(kwargs)
    return Issue.objects.create(**values)


# --- Scripted model outputs ---------------------------------------------------------------------------------------

def issue_d(id="i1", message_id=1, issue_type="unsupported_claim", quote="a quote", explanation="why", confidence=0.6, intensity=None, needs_verification=False):
    return {
        "id": id, "message_id": message_id, "issue_type": issue_type, "quote": quote, "explanation": explanation,
        "confidence": confidence, "intensity": intensity, "needs_verification": needs_verification,
    }  # fmt: skip


def master_out(issues=(), agreements=(), disagreements=()):
    return {
        "issues": list(issues),
        "discussion_map": {"agreements": list(agreements), "disagreements": list(disagreements)},
    }


def disposition_d(issue_id="i1", disposition="acted", reason="because"):
    return {"issue_id": issue_id, "disposition": disposition, "reason": reason}


def act_d(type="request_information", addressee="all", subject="none", source_issue_ids=(), source_message_ids=(), tone="neutral", text="Could a source be given for the figure in message 1?"):
    return {
        "type": type, "addressee": addressee, "subject": subject, "source_issue_ids": list(source_issue_ids),
        "source_message_ids": list(source_message_ids), "tone": tone, "text": text,
    }  # fmt: skip


def intervenor_out(decision="no_intervention", rationale="nothing to do", dispositions=(), acts=()):
    return {"decision": decision, "rationale": rationale, "issue_dispositions": list(dispositions), "acts": list(acts)}


def raises_invalid_json(kwargs):
    """A scripted reply that makes the SDK's parse raise pydantic.ValidationError (invalid or truncated JSON)."""
    from pydantic import TypeAdapter

    TypeAdapter(dict).validate_json('{"issues": [ {"id": "i1", ')
    raise AssertionError("unreachable")


def empty_reply(kwargs):
    from moderation.fake_llm import make_message

    return make_message(None)


def refusal_reply(kwargs):
    from moderation.fake_llm import make_message

    return make_message(None, stop_reason="refusal")


def truncated_reply(valid):
    def reply(kwargs):
        from moderation.fake_llm import make_message

        return make_message(valid, stop_reason="max_tokens")

    return reply


SCHEMA_MISMATCH = {"issues": "this is not a list", "discussion_map": 7}  # a dict that does not fit either schema

# name -> a scripted structurally-bad reply (each becomes LLMOutputError inside llm.call)
BAD_REPLIES = {
    "schema_mismatch": SCHEMA_MISMATCH,
    "invalid_json": raises_invalid_json,
    "no_parsed_output": empty_reply,
    "refusal": refusal_reply,
}


# --- Database snapshots -------------------------------------------------------------------------------------------

def db_snapshot():
    """Row counts and row contents of every forum and moderation table EXCEPT the LLMCall ledger."""
    from django.forms.models import model_to_dict

    from forum import models as forum_models
    from moderation import models as mod_models

    out = {}
    for model in (
        forum_models.Topic, forum_models.Conversation, forum_models.Participant, forum_models.Message,
        mod_models.ModerationRun, mod_models.Issue, mod_models.IssueDisposition, mod_models.InterventionAct,
    ):  # fmt: skip
        out[model.__name__] = sorted(
            (json.dumps(model_to_dict(row), default=str, sort_keys=True) for row in model.objects.all())
        )
    return out


def ledger(run):
    from moderation.models import LLMCall

    return list(LLMCall.objects.filter(run_id=run.pk).order_by("pk"))


def all_request_text(calls):
    """Everything that was sent, as one string: every fake-client call plus every ledger row's stored request."""
    return json.dumps([{k: v for k, v in c.items() if k != "output_format"} for c in calls], default=str)


# --- Process-facts accessors --------------------------------------------------------------------------------------
# docs/step5_brief.md names the four facts but not the dictionary keys. The names below are the builder's (read from
# moderation/features.py); each accessor also tries a few plausible synonyms so a rename is a one-place edit here.

MISSING = object()


def _lookup(facts, names):
    lowered = {str(k).lower(): v for k, v in facts.items()}
    for name in names:
        if name in lowered:
            return lowered[name]
    return MISSING


def fact_counts(facts):
    """{label without the 'Participant ' prefix: count}."""
    value = _lookup(facts, ("messages_per_label", "message_counts", "counts_per_label", "messages_per_participant", "counts"))
    assert value is not MISSING, f"no per-label message counts in {sorted(facts)}"
    return {str(k).replace("Participant ", ""): v for k, v in value.items()}


def fact_current_run(facts):
    value = _lookup(facts, ("current_run_length", "current_run", "consecutive_current", "latest_run_length"))
    assert value is not MISSING, f"no current-run fact in {sorted(facts)}"
    return value


def fact_longest_run(facts):
    value = _lookup(facts, ("longest_run_length", "longest_run", "max_run_length", "max_consecutive"))
    assert value is not MISSING, f"no longest-run fact in {sorted(facts)}"
    return value


def fact_gap_seconds(facts):
    """The seconds between the last two messages; None when the fact is None, zero or absent."""
    value = _lookup(
        facts, ("seconds_between_last_two_messages", "seconds_between_last_messages", "gap_seconds", "seconds_since_previous")
    )
    return None if value is MISSING else value
