"""The only code that creates propositions, conversations, participants and user messages (plan sections 2 and 4).

Views never write these rows themselves. Every refusal is a ``PostRejected`` carrying a stable ``code``, a plain-words
``message`` naming the reason and what to do next, ``retry_after`` (whole seconds, or None) and ``details``. Nothing
here fails silently and no internal error text ever reaches a user.

Pairing and labels (one place, ``enter_proposition``): a person who starts a conversation is stored as its only
participant with ``join_order`` 1 and the PROVISIONAL label "A" (the database needs a label, and a waiting conversation
has no moderator activity, so nothing ever reads it). When a second person joins, ``label_seed`` is drawn from
``secrets`` and stored, and ``assign_labels(seed)`` decides which person gets "A" and which "B": it shuffles ["A", "B"]
with ``random.Random(seed)`` and gives the first letter to join order 1 and the second to join order 2. The first
person's label is then rewritten (the provisional "A" is replaced) and the joiner is created with the other letter, in
one transaction. ``join_order`` is recorded separately and never depends on the label. A waiting conversation therefore
has ``label_seed`` null, and analysis of label assignment must only use conversations that have a seed.

Concurrency: the project's SQLite connection uses ``BEGIN IMMEDIATE``, so every ``transaction.atomic()`` below takes the
write lock before it reads; two simultaneous requests are serialised and only one can win a race. No transaction is
ever open during an LLM call: the moderation pipeline runs after the commit (``transaction.on_commit``).
"""

import logging
import math
import random
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from moderation.models import ModerationRun

from .limits import count_message_chars
from .models import Conversation, Message, Participant, Topic

logger = logging.getLogger(__name__)

__all__ = [
    "PostRejected",
    "assign_labels",
    "create_proposition",
    "enter_proposition",
    "post_message",
    "end_conversation",
    "standing_block",
    "proposition_key",
]


class PostRejected(Exception):
    """A refusal with a reason the user can read. ``message`` is safe to show as it is."""

    def __init__(self, code, message, retry_after=None, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.details = dict(details) if details else {}

    def __str__(self):
        return self.message

    def __repr__(self):
        return f"PostRejected(code={self.code!r}, message={self.message!r}, retry_after={self.retry_after!r})"

    def as_dict(self):
        return {
            "code": self.code,
            "message": self.message,
            "retry_after": self.retry_after,
            "details": dict(self.details),
        }


# ---------------------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------------------


def _plural(n, singular, plural=None):
    return f"{n:,} {singular if n == 1 else (plural or singular + 's')}"


def _is_authenticated(user):
    return bool(getattr(user, "is_authenticated", False)) and getattr(user, "pk", None) is not None


def _require_login(user):
    if not _is_authenticated(user):
        raise PostRejected("login_required", "Please log in first.")


def _participant_of(user, conversation):
    """The user's Participant row in the conversation, or None."""
    if not _is_authenticated(user):
        return None
    return Participant.objects.filter(conversation_id=conversation.pk, user_id=user.pk).first()


def _not_participant():
    return PostRejected("not_participant", "You are not a participant in this conversation.")


def assign_labels(seed):
    """The labels for join orders 1 and 2, reproducibly from the stored seed: ``("A", "B")`` or ``("B", "A")``."""
    labels = ["A", "B"]
    random.Random(seed).shuffle(labels)
    return tuple(labels)


def proposition_key(text):
    """Comparison form of a proposition: NFKC, casefold, NFKC again, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text or "")
    text = unicodedata.normalize("NFKC", text.casefold())
    return " ".join(text.split())


def _normalise_proposition(text):
    if not isinstance(text, str):
        return ""
    return " ".join(unicodedata.normalize("NFC", text).split())


def _normalise_message(text):
    """The stored form of a message: the form ``count_message_chars`` counts, so its length equals ``char_count``."""
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text).strip()


def _utc_day_bounds(now):
    start = now.astimezone(dt_timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _closed_rejection(conversation, participant):
    """The ``closed`` refusal, saying who ended the conversation when a participant did."""
    if conversation.ended_by_id is not None:
        if participant is not None and conversation.ended_by_id == participant.pk:
            who = "You ended this conversation."
        else:
            who = "The other participant ended this conversation."
        text = f"This conversation is closed. {who} You can start a new conversation by choosing a proposition."
        ended = "you" if who.startswith("You") else "other"
    else:
        limit = settings.MAX_USER_MESSAGES_PER_CONVERSATION
        text = (
            f"This conversation is closed. It reached its limit of {limit} messages. "
            "You can start a new one by choosing a proposition."
        )
        ended = None
    return PostRejected("closed", text, details={"ended_by": ended})


def _user_message_count(conversation):
    return Message.objects.filter(conversation_id=conversation.pk, author_type="user").count()


def _full_rejection():
    limit = settings.MAX_USER_MESSAGES_PER_CONVERSATION
    return PostRejected(
        "conversation_full",
        f"This conversation has reached its limit of {limit} messages and is closed. You can start a new one.",
        details={"limit": limit},
    )


def standing_block(conversation, participant):
    """The reason this participant cannot post right now for a standing reason (closed, waiting, full), or None.

    The 30-second gap is not a standing reason and is not reported here. ``conversation`` must be current.
    """
    if conversation.status == "closed":
        return _closed_rejection(conversation, participant)
    if conversation.status == "open":
        return PostRejected("waiting", "You can post once someone else joins.")
    if _user_message_count(conversation) >= settings.MAX_USER_MESSAGES_PER_CONVERSATION:
        return _full_rejection()
    return None


# ---------------------------------------------------------------------------------------------------------------------
# Propositions
# ---------------------------------------------------------------------------------------------------------------------


def create_proposition(user, text):
    """Create a visible proposition for ``user``. Raises PostRejected (empty, too_long, duplicate, daily_limit)."""
    _require_login(user)
    text = _normalise_proposition(text)
    limit = settings.MAX_PROPOSITION_CHARS
    count = count_message_chars(text)
    if count == 0:
        raise PostRejected(
            "empty",
            f"Your message is empty. Write the proposition you want people to discuss, in up to {limit:,} characters.",
        )
    if count > limit:
        excess = count - limit
        raise PostRejected(
            "too_long",
            f"Your proposition is {count:,} characters; the limit is {limit:,}. Please shorten it by "
            f"{_plural(excess, 'character')}. Propositions are capped so the list stays easy to read.",
            details={"count": count, "limit": limit, "excess": excess},
        )
    with transaction.atomic():
        key = proposition_key(text)
        for topic_id, existing in Topic.objects.filter(hidden=False).exclude(proposition="").values_list(
            "id", "proposition"
        ):
            if proposition_key(existing) == key:
                raise PostRejected(
                    "duplicate",
                    "This proposition already exists. Choose it from the list.",
                    details={"topic_id": topic_id},
                )
        now = timezone.now()
        day_start, day_end = _utc_day_bounds(now)
        daily = settings.MAX_PROPOSITIONS_PER_USER_PER_DAY
        made_today = Topic.objects.filter(created_by_id=user.pk, created_at__gte=day_start, created_at__lt=day_end).count()
        if made_today >= daily:
            raise PostRejected(
                "daily_limit",
                f"You have created {_plural(daily, 'proposition')} today, which is the daily limit. Try again "
                "tomorrow (UTC), or pick an existing proposition to discuss.",
                retry_after=max(1, math.ceil((day_end - now).total_seconds())),
                details={"limit": daily},
            )
        return Topic.objects.create(title="", description="", proposition=text, leans={}, created_by=user)


# ---------------------------------------------------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------------------------------------------------


def enter_proposition(user, topic):
    """Put ``user`` into a conversation about ``topic`` and return it (the pairing rule, in one place).

    1. The user's own open or active conversation on this topic is returned.
    2. Otherwise the oldest waiting conversation on this topic with exactly one participant, who is not this user, is
       joined: the labels are assigned at random and the conversation becomes active.
    3. Otherwise a new waiting conversation is created with the user as its only participant.

    Refuses a hidden proposition (``hidden``) and a user with too many open conversations (``too_many_open``).
    """
    _require_login(user)
    with transaction.atomic():
        topic = Topic.objects.get(pk=topic.pk)
        if topic.hidden:
            raise PostRejected("hidden", "This proposition is not available.", details={"topic_id": topic.pk})

        mine = (
            Conversation.objects.filter(
                topic_id=topic.pk, source="human", status__in=["open", "active"], participants__user_id=user.pk
            )
            .order_by("id")
            .first()
        )
        if mine is not None:
            return mine

        max_open = settings.MAX_OPEN_CONVERSATIONS
        open_now = Conversation.objects.filter(
            source="human", status__in=["open", "active"], participants__user_id=user.pk
        ).count()
        if open_now >= max_open:
            raise PostRejected(
                "too_many_open",
                f"You already have {_plural(max_open, 'open conversation')}. End one before starting another.",
                details={"limit": max_open},
            )

        waiting = (
            Conversation.objects.filter(topic_id=topic.pk, source="human", status="open")
            .annotate(n_participants=Count("participants"))
            .filter(n_participants=1)
            .exclude(participants__user_id=user.pk)
            .order_by("created_at", "id")
            .first()
        )
        if waiting is not None:
            first = waiting.participants.get()
            seed = secrets.randbits(62)
            first_label, second_label = assign_labels(seed)
            first.label = first_label
            first.save(update_fields=["label"])
            Participant.objects.create(conversation=waiting, user=user, label=second_label, join_order=2)
            waiting.label_seed = seed
            waiting.status = "active"
            waiting.save(update_fields=["label_seed", "status"])
            return waiting

        conversation = Conversation.objects.create(topic=topic, status="open", source="human")
        Participant.objects.create(conversation=conversation, user=user, label="A", join_order=1)
        return conversation


def end_conversation(user, conversation):
    """A participant closes an open or active conversation for both people. Read-only afterwards."""
    with transaction.atomic():
        participant = _participant_of(user, conversation)
        if participant is None:
            raise _not_participant()
        fresh = Conversation.objects.get(pk=conversation.pk)
        if fresh.status == "closed":
            raise _closed_rejection(fresh, participant)
        fresh.status = "closed"
        fresh.ended_by = participant
        fresh.ended_at = timezone.now()
        fresh.save(update_fields=["status", "ended_by", "ended_at"])
    conversation.status = fresh.status
    conversation.ended_by = participant
    conversation.ended_at = fresh.ended_at
    return conversation


# ---------------------------------------------------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------------------------------------------------


def _run_sync(run_id):
    """Sync mode only: run the moderation pipeline for one run, after the post is committed. Never raises."""
    try:
        from moderation.pipeline import run_moderation  # imported here: step 5 builds it separately

        run_moderation(ModerationRun.objects.get(pk=run_id))
    except Exception:  # the message is already saved; moderation problems must never fail the post
        logger.exception("Moderation run %s failed to start in sync mode", run_id)


def post_message(user, conversation, text, in_reply_to=None):
    """Post a user message and enqueue exactly one live moderation run for it.

    Checks, in order: not a participant, closed, waiting, empty, too long, too fast, conversation full. A refused post
    saves nothing and creates no run. When the conversation's 30th user message is posted the conversation is closed.
    """
    with transaction.atomic():
        participant = _participant_of(user, conversation)
        if participant is None:
            raise _not_participant()
        fresh = Conversation.objects.get(pk=conversation.pk)
        if fresh.status == "closed":
            raise _closed_rejection(fresh, participant)
        if fresh.status == "open":
            raise PostRejected("waiting", "You can post once someone else joins.")

        content = _normalise_message(text)
        count = count_message_chars(content)
        limit = settings.MAX_MESSAGE_CHARS
        if count == 0:
            raise PostRejected("empty", "Your message is empty.")
        if count > limit:
            excess = count - limit
            raise PostRejected(
                "too_long",
                f"Your message is {count:,} characters; the limit is {limit:,}. Please shorten it by "
                f"{_plural(excess, 'character')}. Messages are capped to keep the discussion readable and to keep the "
                "AI moderator's running costs low.",
                details={"count": count, "limit": limit, "excess": excess},
            )

        gap = settings.MIN_SECONDS_BETWEEN_MESSAGES
        previous = (
            Message.objects.filter(conversation_id=fresh.pk, participant_id=participant.pk, author_type="user")
            .order_by("-seq_no")
            .values_list("created_at", flat=True)
            .first()
        )
        if previous is not None:
            elapsed = (timezone.now() - previous).total_seconds()
            if elapsed < gap:
                wait = min(gap, max(1, math.ceil(gap - elapsed)))
                raise PostRejected(
                    "too_fast",
                    f"Please wait {_plural(wait, 'more second')} before posting again. Messages are limited to one "
                    f"every {gap} seconds to keep the discussion readable and the AI moderator's running costs low.",
                    retry_after=wait,
                    details={"wait_seconds": wait, "gap_seconds": gap},
                )

        max_messages = settings.MAX_USER_MESSAGES_PER_CONVERSATION
        so_far = _user_message_count(fresh)
        if so_far + 1 > max_messages:
            raise _full_rejection()

        if in_reply_to is not None and in_reply_to.conversation_id != fresh.pk:
            raise PostRejected("invalid_reply", "The message you are replying to is not in this conversation.")

        try:
            message = Message.objects.create(
                conversation=fresh,
                author_type="user",
                participant=participant,
                in_reply_to=in_reply_to,
                content=content,
            )
        except ValidationError:
            # Every rule the model checks was checked above; if one still fires, say so plainly (details go to the log).
            logger.exception("A message passed the service checks but failed model validation")
            raise PostRejected(
                "not_saved",
                "Something went wrong on our side and your message was not sent. Your text is still in the box; "
                "please try again.",
            )
        run = ModerationRun.objects.create(
            conversation=fresh, trigger_message=message, snapshot_seq=message.seq_no, kind="live", status="pending"
        )
        if so_far + 1 >= max_messages:
            fresh.status = "closed"
            fresh.save(update_fields=["status"])
            conversation.status = "closed"
        if settings.MODERATION_RUN_MODE == "sync":
            transaction.on_commit(lambda run_id=run.pk: _run_sync(run_id))
    return message
