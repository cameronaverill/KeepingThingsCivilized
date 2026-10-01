"""The intervention preview, moderation side (docs/plan.md section 14, step 19; docs/step19_backend_brief.md).

Before a message is posted, its author's DRAFT is checked by the same Master and Intervenor that moderate live messages
(same prompts, same validation, same guards). If the moderator would step in, the author can see the note first. If the
draft is then posted unchanged, the live moderation run reuses the outputs computed here instead of calling the model
again (`claim_reusable_check`, called by `moderation.pipeline`), so the reply that appears is the one that was previewed.

Rules this module keeps:
- A check writes NOTHING to `Message`, `ModerationRun`, `Issue`, `IssueDisposition` or `InterventionAct`. Its only writes
  are its own `PreviewCheck` row, a lazily created `PreviewMode` row, and the `LLMCall` ledger rows that `llm.call` writes.
- Nothing about previews, drafts or modes enters a prompt, an export or a log line. Log lines carry ids and counts only.
- Validation is the live pipeline's own code (`pipeline.validate_issues` / `pipeline.validate_acts`) run on plain data.
- Nothing propagates to the caller: a refusal, a provider error, a structural failure or any other failure of the check
  is recorded as `unavailable` and the caller posts the message without a preview.
- The draft stands in the transcript as the newest user message of its author, with the placeholder message id -1.
- No database transaction is open during a model call (`llm.call` refuses to run inside one).
"""
import hashlib
import logging
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.db import IntegrityError
from django.db.models import Max

from moderation import agents, clock, features, pipeline, taxonomy
from moderation.errors import (
    BreakerOpen,
    BudgetExceeded,
    BudgetUnavailable,
    LLMAPIError,
    LLMDisabled,
    LLMOutputError,
    LLMRefused,
)
from moderation.models import PREVIEW_ACTIONS, PreviewCheck, PreviewMode
from moderation.schemas import IntervenorOutput, MasterOutput

logger = logging.getLogger(__name__)

PLACEHOLDER_MESSAGE_ID = -1  # the draft's message id in the transcript, in the stored outputs and in the agents' answers
RATE_WINDOW_SECONDS = 60
_SHARE_SCALE = 1_000_000


# --- Small helpers -------------------------------------------------------------------------------------------------

def normalise_draft(text):
    """The stored form of a message, exactly as `forum.limits.count_message_chars` normalises it before counting: CRLF and
    lone CR become LF, Unicode NFC, leading and trailing whitespace stripped.

    A copy, for now, of `forum.services._normalise_message`: the two MUST stay identical (the reuse lookup compares hashes
    of the two forms). A later step moves the one rule into `forum/limits.py`."""
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", normalised).strip()


def draft_sha256(text):
    """SHA-256 (hex) of the normalised text; the key that ties a live message to the draft that was checked."""
    return hashlib.sha256(normalise_draft(text).encode("utf-8")).hexdigest()


def _pk(value):
    return getattr(value, "pk", value)


# --- Mode ------------------------------------------------------------------------------------------------------------

def _draw_mode():
    """"on" with probability PREVIEW_SHARE, drawn with `secrets` (not `random`): 1.0 is always on, 0.0 always off."""
    share = min(1.0, max(0.0, float(settings.PREVIEW_SHARE)))
    return "on" if secrets.randbelow(_SHARE_SCALE) < round(share * _SHARE_SCALE) else "off"


def preview_mode(conversation_id):
    """"on" or "off" for the conversation. Drawn and stored the first time it is asked for, never redrawn afterwards, so
    changing PREVIEW_SHARE later does not change existing conversations. Both participants share it."""
    conversation_id = _pk(conversation_id)
    row = PreviewMode.objects.filter(conversation_id=conversation_id).first()
    if row is None:
        try:
            row = PreviewMode.objects.create(conversation_id=conversation_id, mode=_draw_mode(), assigned_at=clock.now())
        except IntegrityError:  # another request created it first: theirs stands
            row = PreviewMode.objects.get(conversation_id=conversation_id)
    return row.mode


# --- The check -------------------------------------------------------------------------------------------------------

def _unavailable_reason(exc):
    """The `unavailable_reason` for an exception from the agents or the gateway."""
    if isinstance(exc, LLMDisabled):
        return "llm_disabled"
    if isinstance(exc, (BudgetExceeded, BudgetUnavailable)):
        return "budget"
    if isinstance(exc, BreakerOpen):
        return "breaker"
    if isinstance(exc, LLMRefused):
        return "refused"  # for example a model that is not on the allow-list
    if isinstance(exc, (agents.StructuralFailure, LLMOutputError)):
        return "structural"
    if isinstance(exc, LLMAPIError):
        return "api_error"  # timeouts included
    return "internal_error"  # anything unexpected: recorded, never raised, so the message still posts without a preview


def _call_ids_of(exc):
    ids = list(getattr(exc, "call_ids", ()) or ())
    call_id = getattr(exc, "call_id", None)
    if call_id is not None:
        ids.append(call_id)
    return ids


def _dedupe(ids):
    return list(dict.fromkeys(ids))


def _draft_transcript(conversation, participant, text, snapshot_seq, now):
    """The transcript `pipeline.build_transcript` would build for a live run triggered by the draft: the last
    TRANSCRIPT_MAX_MESSAGES messages, the draft last as the newest user message of its author's label."""
    from forum.models import Message

    limit = max(int(settings.TRANSCRIPT_MAX_MESSAGES) - 1, 0)
    rows = list(
        Message.objects.filter(conversation_id=conversation.pk, seq_no__lte=snapshot_seq)
        .select_related("participant")
        .order_by("-seq_no")[:limit]
    )
    rows.reverse()
    transcript = pipeline.transcript_from_messages(rows)
    transcript.append(
        {
            "id": PLACEHOLDER_MESSAGE_ID,
            "seq_no": snapshot_seq + 1,
            "author_type": "user",
            "label": "Participant " + participant.label,
            "text": text,
            "created_at": now,
        }
    )
    return transcript


def _run_agents(conversation, participant, text, snapshot_seq, now, call_ids):
    """Ask the Master and (when it found something valid) the Intervenor about the draft and validate in memory.
    Returns (outcome, note_texts, master_output, intervenor_output). Raises what the agents raise."""
    from forum.models import Message, Participant

    transcript = _draft_transcript(conversation, participant, text, snapshot_seq, now)
    draft_seq = snapshot_seq + 1
    # A stand-in for the run row the agents expect: no pk (so the ledger rows carry run_id None), never a replay, and
    # a list the agents fill with the ids of the ledger rows they write.
    stand_in = SimpleNamespace(pk=None, kind="live", conversation_id=conversation.pk, llm_call_ids=call_ids)
    topic = conversation.topic
    facts = features.process_facts(transcript)
    already = pipeline.already_raised_for(
        conversation_id=conversation.pk, kind="live", replicate=1, snapshot_seq=draft_seq
    )
    master = agents.call_master(stand_in, transcript, topic=topic, already_raised=already, process_facts=facts)

    window_ids = {m["id"] for m in transcript}
    cited = {issue.message_id for issue in master.issues} - {PLACEHOLDER_MESSAGE_ID}
    known = {
        m.pk: pipeline.PlainMessage(m.author_type, m.content, m.seq_no)
        for m in Message.objects.filter(conversation_id=conversation.pk, pk__in=cited)
    }
    known[PLACEHOLDER_MESSAGE_ID] = pipeline.PlainMessage("user", text, draft_seq)
    verdicts = pipeline.validate_issues(
        master, window_ids=window_ids, newest_user_id=PLACEHOLDER_MESSAGE_ID, messages=known, snapshot_seq=draft_seq
    )
    valid = [v for v in verdicts if v.reason is None]
    master_json = master.model_dump(mode="json")
    if not valid:
        return "no_concern", [], master_json, None  # as live: nothing valid, so no Intervenor call

    views = [
        pipeline.IssueView(
            id=v.local_id, local_id=v.local_id, pk=None, message_id=v.item.message_id,
            issue_type=v.item.issue_type, dimension=taxonomy.dimension_for(v.item.issue_type) or "",
            confidence=v.item.confidence, intensity=v.item.intensity if v.has_dimension else None,
            quote=v.item.quote, explanation=v.item.explanation,
        )
        for v in valid
    ]
    result = agents.call_intervenor(
        stand_in, transcript, topic=topic, valid_issues=views, discussion_map=master.discussion_map
    )
    # The agree/disagree note is never part of a preview (owner decision, 2026-10-01), even if the model writes one; dropping it
    # here also keeps it out of the stored output that a later live run may reuse.
    result = result.model_copy(
        update={"acts": [a for a in result.acts if a.type != "identify_agreement_disagreement"]}
    )
    labels = set(Participant.objects.filter(conversation_id=conversation.pk).values_list("label", flat=True))
    act_verdicts = pipeline.validate_acts(
        result,
        labels=labels,
        window_ids=window_ids,
        valid_local_ids={v.local_id for v in valid},
        cap=int(settings.MAX_ACTS_PER_INTERVENTION),
    )
    notes = [v.item.text for v in act_verdicts if v.reason is None]
    outcome = "concern" if (result.decision == "intervene" and notes) else "no_concern"
    return outcome, (notes if outcome == "concern" else []), master_json, result.model_dump(mode="json")


def check_draft(conversation, participant, draft_text, *, now=None):
    """Check a draft before it is posted and return the stored `PreviewCheck`. Never raises for a failure of the check.

    Precondition (the caller validates it): the draft passes the rules of a post (participant of an open conversation,
    non-empty, not too long). Nothing here repeats those rules."""
    from forum.models import Message

    now = now or clock.now()
    normalised = normalise_draft(draft_text)
    snapshot_seq = Message.objects.filter(conversation_id=conversation.pk).aggregate(m=Max("seq_no"))["m"] or 0
    mode = preview_mode(conversation.pk)
    fields = dict(
        conversation_id=conversation.pk,
        participant_id=participant.pk,
        draft_text=draft_text,
        char_count=len(normalised),
        draft_sha256=hashlib.sha256(normalised.encode("utf-8")).hexdigest(),
        snapshot_seq=snapshot_seq,
        mode=mode,
        created_at=now,
    )

    def unavailable(reason, call_ids=()):
        return PreviewCheck.objects.create(
            **fields, outcome="unavailable", unavailable_reason=reason, llm_call_ids=_dedupe(call_ids)
        )

    if mode != "on":
        return unavailable("off")

    # The per-participant cap. Refused checks are not counted, so a caller that keeps pressing does not extend its own wait.
    recent = (
        PreviewCheck.objects.filter(participant_id=participant.pk, created_at__gt=now - timedelta(seconds=RATE_WINDOW_SECONDS))
        .exclude(unavailable_reason="rate_limited")
        .count()
    )
    if recent >= int(settings.PREVIEW_MAX_CHECKS_PER_MINUTE):
        return unavailable("rate_limited")

    call_ids = []
    try:
        outcome, notes, master_json, intervenor_json = _run_agents(
            conversation, participant, normalised, snapshot_seq, now, call_ids
        )
    except Exception as exc:
        reason = _unavailable_reason(exc)
        check = unavailable(reason, list(call_ids) + _call_ids_of(exc))
        # The check id and the exception's class only: never its text, which could quote the draft.
        logger.warning("preview check %s unavailable: reason=%s error=%s", check.pk, reason, type(exc).__name__)
        return check
    check = PreviewCheck.objects.create(
        **fields,
        outcome=outcome,
        note_texts=notes,
        master_output=master_json,
        intervenor_output=intervenor_json,
        llm_call_ids=_dedupe(call_ids),
    )
    logger.info(
        "preview check %s: conversation=%s participant=%s outcome=%s notes=%d",
        check.pk, conversation.pk, participant.pk, outcome, len(notes),
    )
    return check


# --- Resolving a check -----------------------------------------------------------------------------------------------

def resolve_check(check_id, action, *, message_id=None):
    """Record what the author did with a check: `posted_as_written`, `edited` or `abandoned`. Idempotent for the same
    action; raises ValueError for an unknown check, an unknown action, or a second, different action."""
    if action not in PREVIEW_ACTIONS:
        raise ValueError(f"unknown preview action {action!r}; expected one of {PREVIEW_ACTIONS}")
    check = PreviewCheck.objects.filter(pk=check_id).first()
    if check is None:
        raise ValueError(f"unknown preview check {check_id!r}")
    if check.action:
        if check.action != action:
            raise ValueError(f"preview check {check_id} is already resolved as {check.action!r}, not {action!r}")
        return check
    changed = PreviewCheck.objects.filter(pk=check.pk, action="").update(
        action=action, resolved_at=clock.now(), resulting_message_id=message_id
    )
    check.refresh_from_db()
    if not changed and check.action != action:  # lost a race to a different action
        raise ValueError(f"preview check {check_id} is already resolved as {check.action!r}, not {action!r}")
    return check


# --- Reuse in the live pipeline --------------------------------------------------------------------------------------

@dataclass
class ReusedOutputs:
    """What a live run takes from a check: the validated outputs, with the draft's placeholder id already replaced by the
    message's own id. `intervenor` is None when the check found no valid issue (the live run then makes no Intervenor call)."""

    check_id: int
    master: MasterOutput
    intervenor: IntervenorOutput | None


def _remapped_outputs(check, message_id):
    """(MasterOutput, IntervenorOutput | None) from a check's stored JSON with the placeholder id replaced by
    `message_id`, or None when anything stored is unusable. Never raises: the stored structure is validated as a whole,
    strictly, against the agents' own schemas (so a list instead of a dict, an issue that is not a dict, or a wrong-typed
    field all give None), and the caller then falls back to a fresh model call."""
    try:
        if check.master_output is None:
            return None
        master = MasterOutput.model_validate(check.master_output, strict=True).model_dump(mode="json")
        for issue in master["issues"]:
            if issue["message_id"] == PLACEHOLDER_MESSAGE_ID:
                issue["message_id"] = message_id
        intervenor = None
        if check.intervenor_output is not None:
            intervenor = IntervenorOutput.model_validate(check.intervenor_output, strict=True).model_dump(mode="json")
            for act in intervenor["acts"]:
                act["source_message_ids"] = [
                    message_id if m == PLACEHOLDER_MESSAGE_ID else m for m in act["source_message_ids"]
                ]
        return (
            MasterOutput.model_validate(master),
            None if intervenor is None else IntervenorOutput.model_validate(intervenor),
        )
    except Exception:
        return None


def claim_reusable_check(run):
    """For the live run of a user message: the outputs of a check of the same text, or None (then the run behaves as it
    always did). A check is reusable when it is for the same conversation and author, has the same normalised text, was
    made when the newest message was the one just before this one (nobody, the moderator included, posted in between),
    found an answer (`concern` or `no_concern`), is not older than PREVIEW_REUSE_SECONDS and is not yet used. The check is
    claimed (`reused_by_run_id`) in one UPDATE, so two runs can never share it. A retry of the same run finds its own claim
    again and reuses it (whatever its age), so a run taken again after a crash does not spend a second call. Replays never
    reuse."""
    if run.kind != "live":
        return None
    message = run.trigger_message
    if message.author_type != "user" or message.participant_id is None:
        return None
    candidates = PreviewCheck.objects.filter(
        conversation_id=run.conversation_id,
        participant_id=message.participant_id,
        draft_sha256=draft_sha256(message.content),
        snapshot_seq=message.seq_no - 1,
        outcome__in=("concern", "no_concern"),
    )
    horizon = clock.now() - timedelta(seconds=int(settings.PREVIEW_REUSE_SECONDS))
    own = candidates.filter(reused_by_run_id=run.pk).order_by("-created_at", "-pk").first()
    if own is not None:
        outputs = _remapped_outputs(own, message.pk)
        return None if outputs is None else ReusedOutputs(own.pk, *outputs)
    for check in candidates.filter(reused_by_run_id__isnull=True, created_at__gte=horizon).order_by("-created_at", "-pk"):
        outputs = _remapped_outputs(check, message.pk)
        if outputs is None:
            continue
        claimed = PreviewCheck.objects.filter(pk=check.pk, reused_by_run_id__isnull=True).update(reused_by_run_id=run.pk)
        if claimed:
            return ReusedOutputs(check.pk, *outputs)
    return None
