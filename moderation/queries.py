"""Read-only queries for research use: one conversation as a nested dict, a filtered listing, and the JSON export.

Nothing here writes to the database or calls a language model. Privacy (plan section 10, item 8): unless
``include_identities=True`` the output holds no username, email, user primary key or display name. A participant is then
identified by its conversation label and by ``pseudonym``, a stable id (``p-<8 hex>``) derived from a keyed hash of the
user's pk with the project's ``SECRET_KEY`` (the same person gets the same pseudonym in every conversation; the pk cannot
be read back from it without the key). Everything refers to messages by ``seq_no`` and to issues by ``local_id``, never
by primary key. Money is reported as floats (six decimal places at most, summed as Decimals first).
"""

import datetime as _dt
import hashlib
import hmac
import json
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from forum.models import Conversation, Experiment, Message, Participant, Topic
from moderation.models import InterventionAct, Issue, LLMCall, ModerationRun

_ZERO = Decimal("0")


class _Counts(dict):
    """A plain, JSON-serializable dict of counts where a missing key reads as 0 (``counts["failed"] == 0``)."""

    def __missing__(self, key):
        return 0


# ---------------------------------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------------------------------

def pseudonym_for_user(user_id):
    """Stable pseudonymous id ``p-<8 hex>`` for a user pk: HMAC-SHA256 keyed with the project's SECRET_KEY."""
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"), f"export-pseudonym:{int(user_id)}".encode("ascii"), hashlib.sha256
    ).hexdigest()
    return "p-" + digest[:8]


def _iso(value):
    return None if value is None else value.isoformat()


def _money(value):
    """Decimal (or None) as a float; None stays None."""
    return None if value is None else float(value)


def _total(values):
    return float(sum((v for v in values if v is not None), _ZERO))


def _preview_call_ids(conversation_id, call_ids):
    """The ids among `call_ids` that are ledger rows of a draft check of the conversation (listed in a `PreviewCheck`'s
    `llm_call_ids`).
    Their request holds the author's draft, so an export never shows their raw fields."""
    from moderation.models import PreviewCheck

    wanted = set(call_ids)
    found = set()
    for ids in PreviewCheck.objects.filter(conversation_id=conversation_id).values_list("llm_call_ids", flat=True):
        if isinstance(ids, list):
            found.update(i for i in ids if i in wanted)
    return found


def _call_dict(call, include_raw):
    data = {
        "id": call.pk,
        "purpose": call.purpose,
        "agent": call.agent,
        "attempt": call.attempt,
        "provider": call.provider,
        "model": call.model,
        "prompt_version": call.prompt_version,
        "prompt_sha256": call.prompt_sha256,
        "temperature": call.temperature,
        "max_tokens": call.max_tokens,
        "tokens_in": call.tokens_in,
        "tokens_out": call.tokens_out,
        "cache_write_tokens": call.cache_write_tokens,
        "cache_read_tokens": call.cache_read_tokens,
        "cost_usd": _money(call.cost_usd),
        "reserved_usd": _money(call.reserved_usd),
        "latency_ms": call.latency_ms,
        "stop_reason": call.stop_reason,
        "status": call.status,
        "error": call.error,
        "error_code": call.error_code,
        "created_at": _iso(call.created_at),
        "finished_at": _iso(call.finished_at),
    }
    if include_raw:
        data["request"] = call.request
        data["raw_response"] = call.raw_response
        data["parsed"] = call.parsed
    return data


# ---------------------------------------------------------------------------------------------------------------------
# one conversation
# ---------------------------------------------------------------------------------------------------------------------

def get_conversation_bundle(conversation_id, *, include_identities=False, include_raw=False):
    """One conversation as a JSON-serializable nested dict (see docs/step9_brief.md, section 9a).

    Top-level keys: ``conversation``, ``topic``, ``participants``, ``messages``, ``runs`` (each with ``issues``,
    ``acts`` and ``llm_calls``), ``unattached_llm_calls`` (ledger rows that name this conversation but none of its runs)
    and ``cost_usd`` (the total of every ledger row of the conversation: its runs' calls plus the unattached ones).
    ``request``/``raw_response``/``parsed`` of the calls appear only with ``include_raw=True``. Raises
    ``Conversation.DoesNotExist`` for an unknown id. The number of queries is constant (it does not grow with the
    number of messages, runs or calls).
    """
    conversation = (
        Conversation.objects.select_related("topic", "experiment", "ended_by").get(pk=conversation_id)
    )
    topic = conversation.topic
    experiment = conversation.experiment

    participant_qs = Participant.objects.filter(conversation_id=conversation.pk).order_by("join_order", "id")
    if include_identities:
        participant_qs = participant_qs.select_related("user")
    participants = list(participant_qs)
    label_by_participant = {p.pk: p.label for p in participants}

    messages = list(Message.objects.filter(conversation_id=conversation.pk).order_by("seq_no"))
    seq_by_message = {m.pk: m.seq_no for m in messages}

    runs = list(ModerationRun.objects.filter(conversation_id=conversation.pk).order_by("created_at", "id"))
    run_ids = [r.pk for r in runs]

    issues_by_run, acts_by_run = {}, {}
    if runs:
        issue_qs = (
            Issue.objects.filter(run_id__in=run_ids).select_related("disposition").order_by("run_id", "id")
        )
        for issue in issue_qs:
            disposition = getattr(issue, "disposition", None)
            issues_by_run.setdefault(issue.run_id, []).append(
                {
                    "local_id": issue.local_id,
                    "message": seq_by_message.get(issue.message_id),
                    "issue_type": issue.issue_type,
                    "dimension": issue.dimension,
                    "quote": issue.quote,
                    "quote_start": issue.quote_start,
                    "quote_end": issue.quote_end,
                    "quote_match": issue.quote_match,
                    "explanation": issue.explanation,
                    "confidence": issue.confidence,
                    "intensity": issue.intensity,
                    "validity": issue.validity,
                    "rejection_reason": issue.rejection_reason,
                    "disposition": disposition.disposition if disposition else None,
                    "disposition_reason": disposition.reason if disposition else None,
                }
            )
        act_qs = (
            InterventionAct.objects.filter(run_id__in=run_ids)
            .prefetch_related("source_issues", "source_messages")
            .order_by("run_id", "order", "id")
        )
        for act in act_qs:
            acts_by_run.setdefault(act.run_id, []).append(
                {
                    "order": act.order,
                    "act_type": act.act_type,
                    "tone": act.tone,
                    "text": act.text,
                    "addressee": act.addressee,
                    "subject": act.subject,
                    "validity": act.validity,
                    "rejection_reason": act.rejection_reason,
                    "features": {
                        "char_len": act.char_len,
                        "word_count": act.word_count,
                        "is_question": act.is_question,
                        "quotes_participant": act.quotes_participant,
                    },
                    "source_issues": sorted(i.local_id for i in act.source_issues.all()),
                    "source_messages": sorted(
                        seq_by_message.get(m.pk, m.seq_no) for m in act.source_messages.all()
                    ),
                }
            )

    calls = list(
        LLMCall.objects.filter(Q(run_id__in=run_ids) | Q(conversation_id=conversation.pk)).order_by("id")
    )
    calls_by_run, unattached = {}, []
    run_id_set = set(run_ids)
    for call in calls:
        if call.run_id in run_id_set:
            calls_by_run.setdefault(call.run_id, []).append(call)
        else:
            unattached.append(call)
    preview_calls = _preview_call_ids(conversation.pk, [c.pk for c in unattached])

    run_dicts = []
    for run in runs:
        run_calls = calls_by_run.get(run.pk, [])
        run_dicts.append(
            {
                "id": run.pk,
                "kind": run.kind,
                "replicate": run.replicate,
                "replay_of": run.replay_of_id,
                "status": run.status,
                "failure_reason": run.failure_reason,
                "attempts": run.attempts,
                "is_stale": run.is_stale,
                "decision": run.decision,
                "rationale": run.rationale,
                "trigger_seq": seq_by_message.get(run.trigger_message_id),
                "snapshot_seq": run.snapshot_seq,
                "posted_seq": seq_by_message.get(run.posted_message_id) if run.posted_message_id else None,
                "config_snapshot": run.config_snapshot,
                "discussion_map": run.discussion_map,
                "timings": {
                    "created_at": _iso(run.created_at),
                    "claimed_at": _iso(run.claimed_at),
                    "started_at": _iso(run.started_at),
                    "finished_at": _iso(run.finished_at),
                },
                "error": run.error,
                "cost_usd": _total(c.cost_usd for c in run_calls),
                "issues": issues_by_run.get(run.pk, []),
                "acts": acts_by_run.get(run.pk, []),
                "llm_calls": [_call_dict(c, include_raw) for c in run_calls],
            }
        )

    participant_dicts = []
    for p in participants:
        entry = {
            "label": p.label,
            "join_order": p.join_order,
            "joined_at": _iso(p.joined_at),
            "pseudonym": pseudonym_for_user(p.user_id) if p.user_id is not None else None,
        }
        if include_identities:
            entry["username"] = p.user.username if p.user_id is not None else None
            entry["email"] = p.user.email if p.user_id is not None else None
        participant_dicts.append(entry)

    return {
        "conversation": {
            "id": conversation.pk,
            "status": conversation.status,
            "source": conversation.source,
            "experiment": (
                {"name": experiment.name, "kind": experiment.kind} if experiment is not None else None
            ),
            "pair_id": conversation.pair_id,
            "variant": conversation.variant,
            "transcript_id": conversation.transcript_id,
            "label_seed": conversation.label_seed,
            "created_at": _iso(conversation.created_at),
            "ended_at": _iso(conversation.ended_at),
            "ended_by": conversation.ended_by.label if conversation.ended_by_id else None,
        },
        "topic": {
            "id": topic.pk,
            "proposition": topic.proposition,
            "title": topic.title,
            "description": topic.description,
            "leans": topic.leans,
        },
        "participants": participant_dicts,
        "messages": [
            {
                "seq_no": m.seq_no,
                "author_type": m.author_type,
                "participant": label_by_participant.get(m.participant_id) if m.participant_id else None,
                "in_reply_to": seq_by_message.get(m.in_reply_to_id) if m.in_reply_to_id else None,
                "content": m.content,
                "char_count": m.char_count,
                "planted": m.planted,
                "created_at": _iso(m.created_at),
            }
            for m in messages
        ],
        "runs": run_dicts,
        "unattached_llm_calls": [_call_dict(c, include_raw and c.pk not in preview_calls) for c in unattached],
        "cost_usd": _total(c.cost_usd for c in calls),
    }


def export_conversation(conversation_id, *, include_identities=False, include_raw=False):
    """The bundle as deterministic JSON text (sorted keys, two-space indent, real Unicode) with a trailing newline."""
    bundle = get_conversation_bundle(
        conversation_id, include_identities=include_identities, include_raw=include_raw
    )
    return dumps(bundle)


def dumps(data):
    """The one JSON format of every export file."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------------------------------------------------
# the listing
# ---------------------------------------------------------------------------------------------------------------------

def _as_datetime(name, value):
    if isinstance(value, str):
        try:
            value = _dt.datetime.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{name} is not a valid ISO date or datetime: {value!r}") from None
    if isinstance(value, _dt.datetime):
        pass
    elif isinstance(value, _dt.date):
        value = _dt.datetime(value.year, value.month, value.day)
    else:
        raise ValueError(f"{name} must be a datetime, a date or an ISO string, not {type(value).__name__}.")
    if timezone.is_naive(value):
        value = value.replace(tzinfo=_dt.timezone.utc)
    return value


def list_conversations(
    *, source=None, status=None, experiment=None, topic_id=None, has_runs=None, since=None, until=None, limit=None
):
    """Conversations, newest first, as flat dicts (filters combine with AND; ``None`` means no filter).

    Each row: ``id``, ``topic_id``, ``proposition``, ``source``, ``status``, ``experiment`` (name or None), ``pair_id``,
    ``variant``, ``user_messages``, ``moderator_messages``, ``runs`` (count per status; a status with no runs reads 0),
    ``cost_usd`` (total of the ledger rows of the conversation, as in the bundle) and ``created_at`` (ISO text).
    ``experiment`` is an experiment name; ``since`` and ``until`` are inclusive bounds on ``created_at`` (naive values
    count as UTC). An unknown source, status, experiment name or topic id, a non-bool ``has_runs``, a bad date or a
    ``limit`` that is not an integer of at least 1 raises ``ValueError``.
    """
    qs = Conversation.objects.select_related("topic", "experiment")
    if source is not None:
        if source not in dict(Conversation.SOURCE_CHOICES):
            raise ValueError(f"Unknown source {source!r}; use one of {sorted(dict(Conversation.SOURCE_CHOICES))}.")
        qs = qs.filter(source=source)
    if status is not None:
        if status not in dict(Conversation.STATUS_CHOICES):
            raise ValueError(f"Unknown status {status!r}; use one of {sorted(dict(Conversation.STATUS_CHOICES))}.")
        qs = qs.filter(status=status)
    if experiment is not None:
        if not isinstance(experiment, str) or not Experiment.objects.filter(name=experiment).exists():
            raise ValueError(f"Unknown experiment {experiment!r}.")
        qs = qs.filter(experiment__name=experiment)
    if topic_id is not None:
        if isinstance(topic_id, bool) or not isinstance(topic_id, int) or not Topic.objects.filter(pk=topic_id).exists():
            raise ValueError(f"Unknown topic id {topic_id!r}.")
        qs = qs.filter(topic_id=topic_id)
    if has_runs is not None:
        if not isinstance(has_runs, bool):
            raise ValueError(f"has_runs must be True, False or None, not {has_runs!r}.")
        has_any = Exists(ModerationRun.objects.filter(conversation_id=OuterRef("pk")))
        qs = qs.filter(has_any) if has_runs else qs.filter(~has_any)
    if since is not None:
        qs = qs.filter(created_at__gte=_as_datetime("since", since))
    if until is not None:
        qs = qs.filter(created_at__lte=_as_datetime("until", until))
    qs = qs.order_by("-created_at", "-id")
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(f"limit must be an integer of at least 1, not {limit!r}.")
        qs = qs[:limit]
    conversations = list(qs)
    ids = [c.pk for c in conversations]
    if not ids:
        return []

    author_counts = {}
    for row in (
        Message.objects.filter(conversation_id__in=ids).values("conversation_id", "author_type").annotate(n=Count("id"))
    ):
        author_counts[(row["conversation_id"], row["author_type"])] = row["n"]

    run_counts, conversation_by_run = {}, {}
    for row in ModerationRun.objects.filter(conversation_id__in=ids).values_list("id", "conversation_id", "status"):
        run_id, conversation_id, run_status = row
        conversation_by_run[run_id] = conversation_id
        counts = run_counts.setdefault(conversation_id, _Counts())
        counts[run_status] = counts.get(run_status, 0) + 1

    # The same rule as the bundle: every ledger row naming the conversation, or belonging to one of its runs, once.
    cost = {}
    seen = set()
    for call_id, call_conversation, call_run, call_cost in LLMCall.objects.filter(
        Q(conversation_id__in=ids) | Q(run_id__in=list(conversation_by_run))
    ).values_list("id", "conversation_id", "run_id", "cost_usd"):
        if call_id in seen:
            continue
        seen.add(call_id)
        owner = conversation_by_run.get(call_run) if call_run in conversation_by_run else call_conversation
        if owner is None:
            continue
        cost[owner] = cost.get(owner, _ZERO) + (call_cost or _ZERO)

    return [
        {
            "id": c.pk,
            "topic_id": c.topic_id,
            "proposition": c.topic.proposition,
            "source": c.source,
            "status": c.status,
            "experiment": c.experiment.name if c.experiment_id else None,
            "pair_id": c.pair_id,
            "variant": c.variant,
            "user_messages": author_counts.get((c.pk, "user"), 0),
            "moderator_messages": author_counts.get((c.pk, "moderator"), 0),
            "runs": run_counts.get(c.pk, _Counts()),
            "cost_usd": float(cost.get(c.pk, _ZERO)),
            "created_at": _iso(c.created_at),
        }
        for c in conversations
    ]
