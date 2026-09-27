"""The moderation pipeline: `run_moderation(run)` (docs/plan.md sections 2, 6 and 14 step 5; docs/step5_brief.md).

One pass over one user message: re-check the trigger, ask the Master Moderator for issues, validate and store each issue,
ask the Intervenor what to do about the valid ones, validate and store dispositions and acts, post one moderator message
(live runs only) and finish the run.

Rules this module keeps:
- The no-self-reply rule, layer 3: a run whose trigger is not a user message is failed (`invalid_trigger`) before anything
  else happens (no call, no ledger row, no post). A moderator message is never a valid subject of an issue, and posting a
  moderator message never enqueues a run (this module never creates a `ModerationRun`).
- Validation is per item: a bad issue or act is stored as `rejected` with a short reason code and the run goes on. Only a
  structurally unusable model output, a refusal or a provider error ends a run early, and then the run row still ends in a
  terminal status and nothing is posted.
- No database transaction is ever open during an LLM call (`llm.call` refuses to run inside one). Every write here is one
  short `transaction.atomic()` block, or a single statement.
- Writes to the run row go through queryset `update()` (see `_update`), so the model's own `save()` validation is not
  re-run on a row that this module has just checked, and a run whose trigger was tampered with can still be marked failed.

Rejection reason codes (stored verbatim in `rejection_reason`):
  issues: unknown_message, outside_window, moderator_message, not_new, quote_not_found, duplicate_id,
          intensity_without_dimension
  acts:   empty_text, bad_label, names_participant, bad_source_issue, bad_source_message, not_time_sensitive,
          act_cap, decision_no_intervention
"""
import logging
import re
from typing import NamedTuple

from django.conf import settings
from django.db import transaction
from django.db.models import F

from moderation import agents, clock, features, label_check, quotes, taxonomy
from moderation.errors import (
    BreakerOpen,
    BudgetExceeded,
    BudgetUnavailable,
    LLMAPIError,
    LLMDisabled,
    LLMOutputError,
    LLMRefused,
    ModelNotAllowed,
)
from moderation.models import InterventionAct, Issue, IssueDisposition, ModerationRun
from moderation.scrub import scrub

logger = logging.getLogger(__name__)

RUNNABLE_STATUSES = ("pending", "running")
NO_VALID_ISSUES = "no valid issues"
_LABEL_NAME_RE = re.compile(r"^(?:Participant\s+)?([A-Z])$")


class _Stop:
    """The terminal state an expected failure leads to."""

    def __init__(self, status, failure_reason="", error=""):
        self.status = status
        self.failure_reason = failure_reason
        self.error = error


class IssueView(dict):
    """A stored valid issue as the Intervenor sees it: `id` is the Master's own local id (what the Intervenor cites in
    `issue_dispositions` and `source_issue_ids`), not the database key. Readable both as a dict and by attribute."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


_IssueView = IssueView


# --- Small helpers -------------------------------------------------------------------------------------------------

def _update(run, **fields):
    """Write fields of the run row with one UPDATE (no model validation) and mirror them on the object."""
    ModerationRun.objects.filter(pk=run.pk).update(**fields)
    for name, value in fields.items():
        setattr(run, name, value)


def _config_snapshot():
    return {
        "models": {"master": settings.MASTER_MODEL, "intervenor": settings.INTERVENOR_MODEL},
        "max_tokens": {"master": settings.MASTER_MAX_TOKENS, "intervenor": settings.INTERVENOR_MAX_TOKENS},
        "prompts": agents.prompt_fingerprint(),
        "tunables": {
            "TRANSCRIPT_MAX_MESSAGES": settings.TRANSCRIPT_MAX_MESSAGES,
            "MAX_ACTS_PER_INTERVENTION": settings.MAX_ACTS_PER_INTERVENTION,
        },
    }


def build_transcript(run):
    """The last `TRANSCRIPT_MAX_MESSAGES` messages with seq_no <= run.snapshot_seq, oldest first, as plain dicts (the
    interface with moderation/agents.py). Only labels and text: no user object or username is ever read."""
    limit = int(settings.TRANSCRIPT_MAX_MESSAGES)
    rows = list(
        run.conversation.messages_up_to(run.snapshot_seq).select_related("participant").order_by("-seq_no")[:limit]
    )
    rows.reverse()
    return transcript_from_messages(rows)


def transcript_from_messages(rows):
    """Plain transcript dicts for `rows` (Message objects, oldest first, participants loaded). Shared by the live run and
    the draft check (moderation/preview.py), so both send the agents exactly the same shape."""
    transcript = []
    for message in rows:
        if message.author_type == "moderator":
            label = "Moderator"
        else:
            label = "Participant " + message.participant.label
        transcript.append(
            {
                "id": message.pk,
                "seq_no": message.seq_no,
                "author_type": message.author_type,
                "label": label,
                "text": message.content,
                "created_at": message.created_at,
            }
        )
    return transcript


def _already_raised(run):
    """Valid issues from this conversation's earlier finished runs of the same kind (and replicate), with what the
    Intervenor did about each, so the Master does not raise them again."""
    return already_raised_for(
        conversation_id=run.conversation_id,
        kind=run.kind,
        replicate=run.replicate,
        snapshot_seq=run.snapshot_seq,
        exclude_run_id=run.pk,
    )


def already_raised_for(*, conversation_id, kind, replicate, snapshot_seq, exclude_run_id=None):
    """`_already_raised` on plain values, so the draft check can ask the same question for a message not yet posted."""
    earlier = (
        Issue.objects.filter(
            run__conversation_id=conversation_id,
            run__kind=kind,
            run__replicate=replicate,
            run__status="done",
            run__snapshot_seq__lt=snapshot_seq,
            validity="valid",
        )
        .exclude(run_id=exclude_run_id)
        .select_related("disposition")
        .order_by("run__snapshot_seq", "run_id", "pk")
    )
    out = []
    for issue in earlier:
        disposition = getattr(issue, "disposition", None)
        out.append(
            {
                "id": f"prior{issue.run_id}_{issue.local_id}",
                "message_id": issue.message_id,
                "issue_type": issue.issue_type,
                "confidence": issue.confidence,
                "intensity": issue.intensity,
                "time_sensitive": issue.time_sensitive,
                "quote": issue.quote,
                "explanation": issue.explanation,
                "outcome": disposition.disposition if disposition is not None else "declined",
            }
        )
    return out


# --- Master output: validate and store issues ----------------------------------------------------------------------

def _unique_local_id(base, taken):
    base = (base or "issue")[:40]
    n = 2
    while f"{base}#dup{n}" in taken:
        n += 1
    return f"{base}#dup{n}"


class PlainMessage(NamedTuple):
    """What issue validation needs to know about one message: plain data, so a draft that is not yet a message can be
    validated by the same code (moderation/preview.py)."""

    author_type: str
    content: str
    seq_no: int


class IssueVerdict(NamedTuple):
    """The outcome of validating one issue of the Master's output. `reason` is None for a valid issue. `location` is the
    quote's place in the cited message (None when the message was not looked at). `anchored` is True when the cited message
    is a message of the conversation that is not later than the snapshot (so an Issue may hang on it); otherwise the stored
    issue is anchored on the trigger message."""

    item: object
    local_id: str
    reason: str | None
    location: object
    has_dimension: bool
    anchored: bool


def validate_issues(output, *, window_ids, newest_user_id, messages, snapshot_seq):
    """Validate every issue of a Master output on plain data; one `IssueVerdict` per issue, in order. Touches no database.

    `window_ids`: ids of the messages the Master saw. `newest_user_id`: id of the newest user message in that window.
    `messages`: {id: PlainMessage} for every message of the conversation the output may cite (missing id: unknown).
    `snapshot_seq`: the run's snapshot; a cited message later than it is not anchorable."""
    taken = set()
    verdicts = []
    for item in output.issues:
        message = messages.get(item.message_id)
        reason = None
        location = None
        if message is None:
            reason = "unknown_message"
        elif item.message_id not in window_ids:
            reason = "outside_window"
        elif message.author_type == "moderator":
            reason = "moderator_message"
        if message is not None and item.message_id in window_ids:
            location = quotes.locate_quote(message.content, item.quote)
        if reason is None and item.issue_type not in taxonomy.CROSS_MESSAGE_ISSUE_TYPES and item.message_id != newest_user_id:
            reason = "not_new"
        if reason is None and location.match == quotes.NOT_FOUND:
            reason = "quote_not_found"
        if reason is None and item.id in taken:
            reason = "duplicate_id"
        has_dimension = taxonomy.dimension_for(item.issue_type) is not None
        if reason is None and item.intensity is not None and not has_dimension:
            reason = "intensity_without_dimension"

        local_id = item.id
        if item.id in taken:
            local_id = _unique_local_id(item.id, taken)
        taken.add(local_id)

        anchored = message is not None and message.seq_no <= snapshot_seq
        if not anchored:
            location = None
        verdicts.append(IssueVerdict(item, local_id, reason, location, has_dimension, anchored))
    return verdicts


def _store_issues(run, transcript, output):
    """Validate every issue of the Master's output and store all of them. Returns {local_id: Issue} for ALL stored issues
    (valid and rejected); the valid ones are the ones with validity == "valid"."""
    from forum.models import Message

    window_ids = {m["id"] for m in transcript}
    newest_user_id = next((m["id"] for m in reversed(transcript) if m["author_type"] == "user"), None)
    cited = {issue.message_id for issue in output.issues}
    in_conversation = {
        m.pk: m for m in Message.objects.filter(conversation_id=run.conversation_id, pk__in=cited)
    }
    verdicts = validate_issues(
        output,
        window_ids=window_ids,
        newest_user_id=newest_user_id,
        messages={pk: PlainMessage(m.author_type, m.content, m.seq_no) for pk, m in in_conversation.items()},
        snapshot_seq=run.snapshot_seq,
    )
    trigger = run.trigger_message
    rows = []
    for verdict in verdicts:
        item, location = verdict.item, verdict.location
        # The issue must hang on a message of this run's conversation that is not later than the snapshot. An id that is
        # not a message of the conversation (or is later than the snapshot) is stored on the trigger message instead,
        # rejected; what the model cited is preserved in its LLMCall row.
        anchor = in_conversation[item.message_id] if verdict.anchored else trigger
        rows.append(
            Issue(
                run=run,
                local_id=verdict.local_id,
                message=anchor,
                issue_type=item.issue_type,
                quote=item.quote,
                quote_start=None if location is None or location.match == quotes.NOT_FOUND else location.start,
                quote_end=None if location is None or location.match == quotes.NOT_FOUND else location.end,
                quote_match=quotes.NOT_FOUND if location is None else location.match,
                explanation=item.explanation,
                confidence=item.confidence,
                intensity=item.intensity if verdict.has_dimension else None,
                time_sensitive=item.time_sensitive,
                validity="valid" if verdict.reason is None else "rejected",
                rejection_reason=verdict.reason or "",
            )
        )
    stored = {}
    with transaction.atomic():
        for issue in rows:
            issue.save()
            stored[issue.local_id] = issue
    return stored


# --- Intervenor output: validate and store dispositions and acts ---------------------------------------------------

def _label_value(value, labels, allowed_words):
    """The stored form of an addressee or subject ("A", "all", ...), or None when it is not one of the conversation's
    labels or an allowed word. "Participant A" (how the Intervenor writes it) and "A" both give "A"."""
    text = (value or "").strip()
    if text.casefold() in allowed_words:
        return text.casefold()
    match = _LABEL_NAME_RE.match(text)
    if match and match.group(1) in labels:
        return match.group(1)
    return None


class ActVerdict(NamedTuple):
    """The outcome of validating one act: the stored forms of addressee and subject, and `reason` (None when valid)."""

    item: object
    order: int
    addressee: str | None
    subject: str | None
    reason: str | None


def validate_dispositions(output, valid_local_ids, notes):
    """{local_id: disposition item} for the dispositions of an Intervenor output that count: one per valid issue. The
    ones that do not count (unknown or rejected issue, second disposition) are described in `notes`."""
    dispositions = {}
    for item in output.issue_dispositions:
        if item.issue_id not in valid_local_ids:
            notes.append(f"ignored a disposition for issue {item.issue_id!r}: not a valid issue of this run")
        elif item.issue_id in dispositions:
            notes.append(f"ignored a second disposition for issue {item.issue_id!r}")
        else:
            dispositions[item.issue_id] = item
    return dispositions


def validate_acts(output, *, labels, window_ids, valid_local_ids, cap):
    """Validate every act of an Intervenor output on plain data; one `ActVerdict` per act, in order. Touches no database.

    `labels`: the conversation's participant labels. `window_ids`: ids of the messages the agents saw. `valid_local_ids`:
    the Master's local ids of the valid issues. `cap`: most valid acts allowed."""
    intervene = output.decision == "intervene"
    verdicts = []
    valid_count = 0
    for order, act in enumerate(output.acts, start=1):
        addressee = _label_value(act.addressee, labels, ("all",))
        subject = _label_value(act.subject, labels, ("both", "none"))
        reason = None
        if not intervene:
            reason = "decision_no_intervention"
        elif not act.text.strip():
            reason = "empty_text"
        elif addressee is None or subject is None:
            reason = "bad_label"
        elif label_check.names_a_label(act.text):
            reason = "names_participant"
        elif any(i not in valid_local_ids for i in act.source_issue_ids):
            reason = "bad_source_issue"
        elif any(m not in window_ids for m in act.source_message_ids):
            reason = "bad_source_message"
        elif act.type == "offer_research" and not (
            act.source_issue_ids and all(valid_local_ids[i].time_sensitive for i in act.source_issue_ids)
        ):
            reason = "not_time_sensitive"
        elif valid_count >= cap:
            reason = "act_cap"
        if reason is None:
            valid_count += 1
        verdicts.append(ActVerdict(act, order, addressee, subject, reason))
    return verdicts


def _store_intervenor(run, transcript, output, stored_issues, notes):
    """Validate and store dispositions and acts. Returns the list of valid acts in order."""
    from forum.models import Participant

    labels = set(Participant.objects.filter(conversation_id=run.conversation_id).values_list("label", flat=True))
    window_ids = {m["id"] for m in transcript}
    valid_by_local = {lid: issue for lid, issue in stored_issues.items() if issue.validity == "valid"}
    cap = int(settings.MAX_ACTS_PER_INTERVENTION)

    dispositions = validate_dispositions(output, valid_by_local, notes)
    verdicts = validate_acts(
        output, labels=labels, window_ids=window_ids, valid_local_ids=valid_by_local, cap=cap
    )

    valid_acts = []
    with transaction.atomic():
        for local_id, issue in valid_by_local.items():
            item = dispositions.get(local_id)
            IssueDisposition.objects.create(
                issue=issue,
                disposition=item.disposition if item else "declined",
                reason=item.reason if item else "no_disposition",
            )
        for verdict in verdicts:
            act, reason = verdict.item, verdict.reason
            source_issues = [stored_issues[i] for i in dict.fromkeys(act.source_issue_ids) if i in stored_issues]
            row = InterventionAct(
                run=run,
                order=verdict.order,
                act_type=act.type,
                tone=act.tone,
                text=act.text,
                addressee=verdict.addressee or "all",
                subject=verdict.subject or "none",
                validity="valid" if reason is None else "rejected",
                rejection_reason=reason or "",
            )
            row.save()
            row.source_issues.set(source_issues)
            row.source_messages.set(
                _messages_in_conversation(run, [m for m in dict.fromkeys(act.source_message_ids)])
            )
            if reason is None:
                valid_acts.append(row)
    return valid_acts


def _messages_in_conversation(run, ids):
    from forum.models import Message

    return list(Message.objects.filter(conversation_id=run.conversation_id, pk__in=ids))


# --- Posting and finishing ------------------------------------------------------------------------------------------

def _finish(run, *, decision, rationale, discussion_map, valid_acts, notes):
    """Post the moderator message (live runs, decision intervene) and mark the run done, in one short transaction."""
    from forum.models import Message

    with transaction.atomic():
        posted = None
        already_posted = ModerationRun.objects.filter(pk=run.pk, posted_message__isnull=False).exists()
        if decision == "intervene" and run.kind == "live" and not already_posted:
            posted = Message.objects.create(
                conversation_id=run.conversation_id,
                author_type="moderator",
                participant=None,
                in_reply_to=run.trigger_message,
                content="\n\n".join(act.text for act in valid_acts),
            )
        stale = Message.objects.filter(
            conversation_id=run.conversation_id, author_type="user", seq_no__gt=run.snapshot_seq
        ).exists()
        fields = dict(
            status="done",
            finished_at=clock.now(),
            discussion_map=discussion_map,
            decision=decision,
            rationale=rationale,
            is_stale=stale,
            error="\n".join(notes),
        )
        if posted is not None:
            fields["posted_message"] = posted
        _update(run, **fields)


def _terminate(run, *, status, failure_reason="", error=""):
    _update(
        run,
        status=status,
        failure_reason=failure_reason,
        error=scrub(error),
        finished_at=clock.now(),
    )


def _stop_for(exc):
    """Map an exception from an agent or the gateway to the terminal state of the run, or None if it is not one of the
    expected failures."""
    if isinstance(exc, (agents.StructuralFailure, LLMOutputError)):
        return _Stop("failed", "structural", str(exc))
    if isinstance(exc, BreakerOpen):
        return _Stop("skipped_budget", "breaker_open", str(exc))
    if isinstance(exc, LLMDisabled):
        return _Stop("skipped_disabled", "llm_disabled", str(exc))
    if isinstance(exc, ModelNotAllowed):
        return _Stop("failed", "model_not_allowed", str(exc))
    if isinstance(exc, BudgetExceeded):
        return _Stop("skipped_budget", "budget_exceeded", str(exc))
    if isinstance(exc, BudgetUnavailable):
        return _Stop("skipped_budget", "budget_unavailable", str(exc))
    if isinstance(exc, LLMRefused):
        return _Stop("skipped_budget", "refused", str(exc))
    if isinstance(exc, LLMAPIError):
        detail = f"{exc.error_type or 'api_error'}: {exc}"
        if exc.status_code is not None:
            detail = f"HTTP {exc.status_code} {detail}"
        return _Stop("failed", "api_error", detail)
    return None


def _clear_earlier_attempt(run):
    """A run taken again after an attempt died half way starts clean: the dead attempt's issues, dispositions and acts are
    removed so the new attempt's rows do not collide with them (a posted message is never touched; a run that already has
    its post never posts a second one, see `_finish`)."""
    with transaction.atomic():
        IssueDisposition.objects.filter(issue__run_id=run.pk).delete()
        InterventionAct.objects.filter(run_id=run.pk).delete()
        Issue.objects.filter(run_id=run.pk).delete()


# --- The entry point ------------------------------------------------------------------------------------------------

def run_moderation(run):
    """Run the pipeline for `run` and return it. Never raises for an expected failure (the run row records it); a
    programming error marks the run failed (`internal_error`) and is re-raised.

    A run that is not `pending` or `running` is returned unchanged, so calling this twice is harmless."""
    if run.status not in RUNNABLE_STATUSES:
        return run

    # 1. The no-self-reply rule, layer 3: before any call, any ledger row, anything.
    if run.trigger_message.author_type != "user":
        _terminate(
            run,
            status="failed",
            failure_reason="invalid_trigger",
            error="the trigger message is not a user message; a moderator message is never moderated",
        )
        return run

    # 2. Claim: only if the row is still runnable in the database (a stale copy of a finished run does nothing).
    now = clock.now()
    snapshot = _config_snapshot()
    claimed = ModerationRun.objects.filter(pk=run.pk, status__in=RUNNABLE_STATUSES).update(
        status="running", started_at=now, attempts=F("attempts") + 1, config_snapshot=snapshot,
        failure_reason="", error="",
    )
    if not claimed:
        run.refresh_from_db(fields=["status", "failure_reason", "error", "finished_at", "decision", "attempts"])
        return run
    _clear_earlier_attempt(run)
    run.status, run.started_at = "running", now
    run.attempts = (run.attempts or 0) + 1
    run.config_snapshot = snapshot
    run.failure_reason, run.error = "", ""

    try:
        _run(run)
    except Exception as exc:
        stop = _stop_for(exc)
        if stop is not None:
            _terminate(run, status=stop.status, failure_reason=stop.failure_reason, error=stop.error)
        else:
            logger.exception("moderation run %s crashed", run.pk)
            _terminate(run, status="failed", failure_reason="internal_error", error=f"{type(exc).__name__}: {exc}")
            raise
    return run


def _run(run):
    transcript = build_transcript(run)
    topic = run.conversation.topic
    facts = features.process_facts(transcript)
    notes = []

    # 3. Master. A live run for a message the author already previewed unchanged (moderation/preview.py) uses the model
    # outputs computed for the preview instead of asking again; everything after this point is the same either way.
    from moderation import preview

    reuse = preview.claim_reusable_check(run)
    if reuse is None:
        output = agents.call_master(
            run, transcript, topic=topic, already_raised=_already_raised(run), process_facts=facts
        )
    else:
        output = reuse.master
        _update(run, config_snapshot={**(run.config_snapshot or {}), "preview_check_id": reuse.check_id})
    stored = _store_issues(run, transcript, output)
    discussion_map = output.discussion_map.model_dump(mode="json")
    _update(run, discussion_map=discussion_map)
    valid_issues = [issue for issue in stored.values() if issue.validity == "valid"]

    # 4. Intervenor, only when there is something valid to act on.
    if not valid_issues:
        _finish(
            run, decision="no_intervention", rationale=NO_VALID_ISSUES, discussion_map=discussion_map,
            valid_acts=[], notes=notes,
        )
        return
    views = [
        IssueView(
            id=issue.local_id, local_id=issue.local_id, pk=issue.pk, message_id=issue.message_id,
            issue_type=issue.issue_type, dimension=issue.dimension, confidence=issue.confidence,
            intensity=issue.intensity, quote=issue.quote, explanation=issue.explanation,
        )
        for issue in valid_issues
    ]
    if reuse is not None and reuse.intervenor is not None:
        result = reuse.intervenor
    else:
        result = agents.call_intervenor(
            run, transcript, topic=topic, valid_issues=views, discussion_map=output.discussion_map
        )
    valid_acts = _store_intervenor(run, transcript, result, stored, notes)

    # 5 and 6. Post (live only) and finish.
    decision = "intervene" if (result.decision == "intervene" and valid_acts) else "no_intervention"
    _finish(
        run, decision=decision, rationale=result.rationale, discussion_map=discussion_map,
        valid_acts=valid_acts, notes=notes,
    )
