"""What a conversation page shows one of its two participants (plan sections 2 and 10).

``conversation_view`` returns plain data. It never contains a participant label letter, a username or an email: the
viewer is "You", the other person is "The other participant", and a moderator post carries a heading worked out per
viewer from the acts that produced it.
"""

from moderation.models import InterventionAct, ModerationRun

from django.conf import settings

from .models import Conversation, Message, Participant
from .services import PostRejected, effective_side, opposite_side, standing_block

__all__ = ["conversation_view", "moderation_heading", "moderation_notice_for"]

HEADING_BOTH = "For both of you"
HEADING_CONVERSATION = "About the conversation"

_PAUSED_GENERIC = "AI moderation is paused right now and will resume when it can; messages are still posted."
_PAUSED_DAY = "AI moderation is paused for today and will resume tomorrow; messages are still posted."
_PAUSED_CONVERSATION = "The AI moderator will not comment further in this conversation; messages are still posted."
_PAUSED_SITE = "AI moderation is paused right now and will resume later; messages are still posted."
_PAUSED_BREAKER = (
    "The AI moderator is paused after repeated problems and will resume by itself; messages are still posted."
)
_SWITCHED_OFF = "AI moderation is switched off right now; your messages are still posted."
_FAILED = "The AI moderator ran into a problem on the last message; your messages are still posted."


def moderation_notice_for(conversation):
    """The plain-words notice about the moderator's latest finished run, or None. Never any internal error text.

    In-flight runs (pending or running) are skipped so the notice does not flicker while a new message is processed.
    """
    run = (
        ModerationRun.objects.filter(conversation_id=conversation.pk, kind="live")
        .exclude(status__in=["pending", "running"])
        .order_by("-id")
        .first()
    )
    if run is None:
        return None
    if run.status == "skipped_disabled":
        return _SWITCHED_OFF
    if run.status == "failed":
        return _FAILED
    if run.status == "skipped_budget":
        reason = (run.failure_reason or "").lower()
        if "breaker" in reason:
            return _PAUSED_BREAKER
        if "conversation" in reason:
            return _PAUSED_CONVERSATION
        if "day" in reason or "daily" in reason:
            return _PAUSED_DAY
        if "site" in reason or "total" in reason:
            return _PAUSED_SITE
        return _PAUSED_GENERIC
    return None


def _act_heading(act, viewer, source_message, names):
    """The heading for one act, for this viewer. ``source_message`` is the message the act is about, or None.
    ``names`` maps a participant label to that person's username (used for the other person's messages)."""
    if act.addressee == "all" or act.subject == "both":
        return HEADING_BOTH
    if act.subject == "none":
        return HEADING_CONVERSATION
    mine = act.subject == viewer.label
    whose = "your" if mine else f"{names.get(act.subject) or 'the other participant'}'s"
    if source_message is not None:
        return f"About {whose} message {source_message.seq_no}"
    return f"About {whose} messages"


def moderation_heading(message, viewer):
    """The heading of a moderator post for ``viewer`` (a Participant), from the acts of the run that posted it."""
    run = ModerationRun.objects.filter(posted_message_id=message.pk).first()
    if run is None:
        return HEADING_CONVERSATION
    acts = list(InterventionAct.objects.filter(run_id=run.pk, validity="valid").order_by("order"))
    if not acts:
        return HEADING_CONVERSATION
    people = list(Participant.objects.filter(conversation_id=run.conversation_id).values_list("id", "label", "user__username"))
    label_by_participant = {pid: label for pid, label, _ in people}
    names = {label: username for _, label, username in people if username}
    headings = set()
    for act in acts:
        source = None
        if act.subject not in ("both", "none") and act.addressee != "all":
            sources = list(act.source_messages.order_by("seq_no"))
            # Prefer a source message written by the person the act is about.
            about = [m for m in sources if m.participant_id and label_by_participant.get(m.participant_id) == act.subject]
            source = (about or [None])[0]
            if source is None and run.trigger_message.participant_id and (
                label_by_participant.get(run.trigger_message.participant_id) == act.subject
            ):
                source = run.trigger_message
        headings.add(_act_heading(act, viewer, source, names))
    return headings.pop() if len(headings) == 1 else HEADING_BOTH


def conversation_view(user, conversation, after_seq=0):
    """The page data for ``user`` in ``conversation``; raises PostRejected("not_participant") for anyone else."""
    viewer = None
    if getattr(user, "is_authenticated", False) and getattr(user, "pk", None) is not None:
        viewer = Participant.objects.filter(conversation_id=conversation.pk, user_id=user.pk).first()
    if viewer is None:
        raise PostRejected("not_participant", "You are not a participant in this conversation.")

    conversation = Conversation.objects.select_related("topic").get(pk=conversation.pk)
    topic = conversation.topic

    you_ended = ended_by_other = None
    if conversation.status == "closed" and conversation.ended_by_id is not None:
        you_ended = conversation.ended_by_id == viewer.pk
        ended_by_other = not you_ended

    other = Participant.objects.filter(conversation_id=conversation.pk).exclude(pk=viewer.pk).select_related("user").first()
    other_username = other.user.username if other is not None and other.user is not None else None

    messages = []
    queryset = Message.objects.filter(conversation_id=conversation.pk, seq_no__gt=after_seq or 0).order_by("seq_no")
    for message in queryset:
        if message.author_type == "moderator":
            item = {
                "seq_no": message.seq_no,
                "kind": "moderator",
                "text": message.content,
                "paragraphs": [
                    para.strip()
                    for para in message.content.split("\n\n")
                    if para.strip()
                ],
                "created_at": message.created_at,
                "heading": moderation_heading(message, viewer),
                "author_name": None,
            }
        else:
            kind = "you" if message.participant_id == viewer.pk else "other"
            item = {
                "seq_no": message.seq_no,
                "kind": kind,
                "text": message.content,
                "created_at": message.created_at,
                "author_name": other_username if kind == "other" else None,
            }
        messages.append(item)

    my_side, other_side = viewer.side or None, (other.side or None) if other else None
    if my_side is None and other_side is None:
        # Old rows have blank sides: a lone (waiting) person counts as "pro"; an old pair has no known sides.
        my_side = effective_side("") if other is None else None
    elif my_side is None:
        my_side = opposite_side(other_side)
    if other is not None and other_side is None and my_side is not None:
        other_side = opposite_side(my_side)

    message_count = Message.objects.filter(conversation_id=conversation.pk, author_type="user").count()
    block = standing_block(conversation, viewer)
    return {
        "status": conversation.status,
        "proposition": topic.proposition or topic.title,
        "you_ended": you_ended,
        "ended_by_other": ended_by_other,
        "messages": messages,
        "moderation_notice": moderation_notice_for(conversation),
        "message_count": message_count,
        "message_limit": settings.MAX_USER_MESSAGES_PER_CONVERSATION,
        "waiting": conversation.status == "open",
        "waiting_for_second": conversation.status == "open",
        "other_username": other_username,
        "my_side": my_side,
        "other_side": other_side,
        "position_texts": {"pro": topic.proposition or topic.title, "con": topic.opposing_position or None},
        "can_post": block is None,
        "cannot_post_reason": block.as_dict() if block is not None else None,
    }
