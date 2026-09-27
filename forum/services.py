"""The only code that creates propositions, conversations, participants and user messages (plan sections 2 and 4).

Views never write these rows themselves. Every refusal is a ``PostRejected`` carrying a stable ``code``, a plain-words
``message`` naming the reason and what to do next, ``retry_after`` (whole seconds, or None) and ``details``. Nothing
here fails silently and no internal error text ever reaches a user.

Pairing, sides and labels (one place, ``enter_proposition``): every proposition has two positions, "pro" (the stated
proposition) and "con" (its opposing position). A person chooses a side on entering; a conversation's two participants
always hold opposite sides. A person who starts a new waiting conversation is its only participant (``join_order`` 1)
and holds the chosen side. ``label_seed`` is drawn from ``secrets`` and stored WHEN THE CONVERSATION IS CREATED, and
``assign_labels(seed)`` (a shuffle of ["A", "B"] with ``random.Random(seed)``) gives the first letter to join order 1
and the second to join order 2; the person who joins later gets the other letter and the first person's label never
changes. ``join_order`` is recorded separately and never depends on the label. Old rows stored as waiting with a null
seed (from before step 7c) are still joinable: the seed is drawn and the labels assigned at that point. A participant
whose side is blank (a row from before step 7c) counts as "pro". The moderator never sees sides or labels' meaning.

A waiting conversation is usable: its first participant may post while waiting, under the same limits as anyone.

Concurrency: the project's SQLite connection uses ``BEGIN IMMEDIATE``, so every ``transaction.atomic()`` below takes the
write lock before it reads; two simultaneous requests are serialised and only one can win a race. No transaction is
ever open during an LLM call: the moderation pipeline runs after the commit (``transaction.on_commit``).
"""

import logging
import math
import random
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone

from moderation.models import ModerationRun

from .limits import count_message_chars
from .models import Block, Conversation, Message, Participant, Topic

logger = logging.getLogger(__name__)

__all__ = [
    "PostRejected",
    "assign_labels",
    "create_proposition",
    "enter_proposition",
    "post_message",
    "validate_draft",
    "end_conversation",
    "standing_block",
    "proposition_key",
    "own_conversations",
    "waiting_groups",
    "my_conversations",
    "seeded_topics",
    "block_user",
    "unblock_user",
    "blocked_users",
    "is_blocked_between",
    "own_sides",
    "effective_side",
    "opposite_side",
]

SIDES = ("pro", "con")
POSITION_PREFIX_RE = re.compile(r"^my position is that(?=\s|$)", re.IGNORECASE)


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
    text = " ".join(unicodedata.normalize("NFC", text).split())
    # The form shows the fixed start "My position is that"; a user who types it again does not store it twice.
    text = POSITION_PREFIX_RE.sub("", text).strip()
    return text[:1].upper() + text[1:]


def opposite_side(side):
    return "con" if side == "pro" else "pro"


def effective_side(side):
    """The side a stored value stands for: a blank side (a row from before step 7c) counts as "pro"."""
    return side or "pro"


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
    """The reason this participant cannot post right now for a standing reason (closed or full), or None.

    The 30-second gap is not a standing reason and is not reported here. ``conversation`` must be current.
    """
    if conversation.status == "closed":
        return _closed_rejection(conversation, participant)
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


def _blocked_partner_ids(user_id):
    """Ids of everyone the user has blocked or who has blocked the user (blocks apply in both directions). One query."""
    ids = set()
    for blocker_id, blocked_id in Block.objects.filter(Q(blocker_id=user_id) | Q(blocked_id=user_id)).values_list(
        "blocker_id", "blocked_id"
    ):
        ids.add(blocked_id if blocker_id == user_id else blocker_id)
    return ids


def _waiting_candidates(topic_ids, viewer_id):
    """Open human conversations on the topics with exactly one participant, who is not the viewer, oldest first.

    Conversations of hidden topics are left out. Returns a queryset of Conversation.
    """
    return (
        Conversation.objects.filter(topic_id__in=topic_ids, topic__hidden=False, source="human", status="open")
        .annotate(n_participants=Count("participants"))
        .filter(n_participants=1)
        .exclude(participants__user_id=viewer_id)
        .order_by("created_at", "id")
    )


def enter_proposition(user, topic, side=None):
    """Put ``user`` into a conversation about ``topic``, holding ``side`` ("pro" or "con"), and return it.

    The pairing rule, in one place:
    1. The user's own open or active conversation on this topic is returned, whatever its sides.
    2. Otherwise (after the ``too_many_open`` check) the oldest waiting conversation on this topic with exactly one
       participant, who is not this user and holds the OPPOSITE side, is joined and becomes active. Two people
       waiting on the same side never pair. A blank side (an old row) counts as "pro".
    3. Otherwise a new waiting conversation is created with the user as its only participant, on the chosen side.

    Refuses a missing or unknown side (``invalid_side``), a hidden proposition (``hidden``) and a user with too many
    open conversations (``too_many_open``).
    """
    _require_login(user)
    if side not in SIDES:
        raise PostRejected("invalid_side", "Choose a position first.")
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

        max_open = settings.MAX_OPEN_CONVERSATIONS  # None = no limit
        open_now = (
            0
            if max_open is None
            else Conversation.objects.filter(
                source="human", status__in=["open", "active"], participants__user_id=user.pk
            ).count()
        )
        if max_open is not None and open_now >= max_open:
            raise PostRejected(
                "too_many_open",
                f"You already have {_plural(max_open, 'open conversation')}. End one before starting another.",
                details={"limit": max_open},
            )

        wanted = opposite_side(side)  # the side the waiting person must hold
        holders = ["pro", ""] if wanted == "pro" else ["con"]
        waiting = (
            _waiting_candidates([topic.pk], user.pk)
            .filter(pk__in=Participant.objects.filter(side__in=holders).values("conversation_id"))
            .exclude(participants__user_id__in=_blocked_partner_ids(user.pk))  # blocks apply in both directions
            .first()
        )
        if waiting is not None:
            first = waiting.participants.get()
            if waiting.label_seed is None:
                # An old row from before step 7c: the seed and labels are set now, as they used to be.
                seed = secrets.randbits(62)
                first_label, second_label = assign_labels(seed)
                first.label = first_label
                first.save(update_fields=["label"])
                waiting.label_seed = seed
            else:
                second_label = "B" if first.label == "A" else "A"
            Participant.objects.create(
                conversation=waiting, user=user, label=second_label, join_order=2, side=side
            )
            waiting.status = "active"
            waiting.save(update_fields=["label_seed", "status"])
            return waiting

        seed = secrets.randbits(62)
        conversation = Conversation.objects.create(topic=topic, status="open", source="human", label_seed=seed)
        Participant.objects.create(
            conversation=conversation, user=user, label=assign_labels(seed)[0], join_order=1, side=side
        )
        return conversation


# ---------------------------------------------------------------------------------------------------------------------
# What the home page may show about the viewer's own conversations
# ---------------------------------------------------------------------------------------------------------------------


def _topic_ids(topics):
    return [getattr(topic, "pk", topic) for topic in topics]


def own_sides(topics, viewer):
    """{topic_id: the side the viewer holds} for topics where the viewer has an open or active human conversation."""
    ids = _topic_ids(topics)
    if not ids or not _is_authenticated(viewer):
        return {}
    rows = Participant.objects.filter(
        user_id=viewer.pk,
        conversation__topic_id__in=ids,
        conversation__source="human",
        conversation__status__in=["open", "active"],
    ).order_by("-conversation_id").values_list("conversation__topic_id", "side")
    result = {}
    for topic_id, side in rows:  # newest first, so the oldest conversation wins (the one enter_proposition returns)
        result[topic_id] = effective_side(side)
    return result


def own_conversations(topics, viewer):
    """The ids of the given topics on which the viewer already has an open or active conversation."""
    return set(own_sides(topics, viewer))


def _matching(topics, query):
    """The topics whose proposition or opposing position contains ``query`` (case-insensitive, Unicode-safe)."""
    needle = query.casefold()
    return [t for t in topics if needle in t.proposition.casefold() or needle in t.opposing_position.casefold()]


def waiting_groups(viewer, query=None):
    """One entry per (topic, side) that has a waiting conversation the viewer could join, newest group first.

    An entry is ``{"topic": Topic, "waiting_side": "pro"|"con", "waiting_username": str, "since": created_at of the
    OLDEST joinable waiting conversation in the group}``; ``waiting_username`` is the person the viewer would be paired
    with. A waiting conversation counts when it is open, human, has exactly one participant who is not the viewer and
    who is not blocked in either direction with the viewer, has room for more messages, and its topic is not hidden.
    Topics on which the viewer already has an open or active conversation are left out. A blank side counts as "pro".
    ``query`` keeps the topics whose proposition or opposing position contains it (case-insensitive). Three queries
    however many groups there are.
    """
    if not _is_authenticated(viewer):
        return []
    query = " ".join((query or "").split())
    if "\x00" in query:
        return []  # a NUL character can never be part of a proposition
    own_topics = Participant.objects.filter(
        user_id=viewer.pk, conversation__source="human", conversation__status__in=["open", "active"]
    ).values("conversation__topic_id")
    first_participant = Participant.objects.filter(conversation_id=OuterRef("pk")).order_by("join_order", "id")
    rows = list(
        Conversation.objects.filter(source="human", status="open", topic__hidden=False)
        .exclude(topic_id__in=own_topics)
        .annotate(
            n_participants=Count("participants", distinct=True),
            n_user_messages=Count("messages", filter=Q(messages__author_type="user"), distinct=True),
        )
        .filter(n_participants=1, n_user_messages__lt=settings.MAX_USER_MESSAGES_PER_CONVERSATION)
        .exclude(participants__user_id=viewer.pk)
        .annotate(
            waiter_side=Subquery(first_participant.values("side")[:1]),
            waiter_id=Subquery(first_participant.values("user_id")[:1]),
            waiter_name=Subquery(first_participant.values("user__username")[:1]),
        )
        .order_by("created_at", "id")
        .values_list("id", "topic_id", "created_at", "waiter_side", "waiter_id", "waiter_name")
    )
    if not rows:
        return []
    blocked = _blocked_partner_ids(viewer.pk)
    groups = {}  # (topic_id, side) -> {"oldest": (created_at, id, username), "newest": (created_at, id)}
    for conversation_id, topic_id, created_at, side, waiter_id, waiter_name in rows:  # oldest first
        if waiter_id in blocked:
            continue
        group = groups.setdefault(
            (topic_id, effective_side(side)),
            {"oldest": (created_at, conversation_id, waiter_name), "newest": (created_at, conversation_id)},
        )
        group["newest"] = max(group["newest"], (created_at, conversation_id))
    if not groups:
        return []
    topics = Topic.objects.filter(pk__in={topic_id for topic_id, _ in groups}, hidden=False)
    if query and query.isascii():
        topics = topics.filter(Q(proposition__icontains=query) | Q(opposing_position__icontains=query))
    by_id = {topic.pk: topic for topic in topics}
    if query and not query.isascii():
        # SQLite's LIKE only ignores case for ASCII letters, so a non-ASCII search is compared in Python.
        by_id = {topic.pk: topic for topic in _matching(by_id.values(), query)}
    ordered = sorted(
        (key for key in groups if key[0] in by_id),
        key=lambda key: (groups[key]["newest"], key[0], key[1]),
        reverse=True,
    )
    return [
        {
            "topic": by_id[topic_id],
            "waiting_side": side,
            "waiting_username": groups[(topic_id, side)]["oldest"][2],
            "since": groups[(topic_id, side)]["oldest"][0],
        }
        for topic_id, side in ordered
    ]


def my_conversations(viewer, query=None):
    """The viewer's own human conversations, newest activity first (latest message, else creation).

    Each entry is ``{"conversation", "topic", "my_side": "pro"|"con"|None, "other_username", "status":
    "waiting"|"active"|"ended"}`` (waiting = open, ended = closed; ``other_username`` is None while waiting). With no
    ``query`` only the most recent ``MY_ENDED_CONVERSATIONS_SHOWN`` ended ones are listed; with a ``query`` (compared
    casefolded, whitespace collapsed; a NUL matches nothing) every matching conversation is listed, whose topic's
    proposition or opposing position contains it. One query.
    """
    if not _is_authenticated(viewer):
        return []
    query = " ".join((query or "").split())
    if "\x00" in query:
        return []
    last_message = Message.objects.filter(conversation_id=OuterRef("conversation_id")).order_by("-created_at").values(
        "created_at"
    )[:1]
    other_name = (
        Participant.objects.filter(conversation_id=OuterRef("conversation_id"))
        .exclude(user_id=viewer.pk)
        .order_by("join_order", "id")
        .values("user__username")[:1]
    )
    rows = (
        Participant.objects.filter(user_id=viewer.pk, conversation__source="human")
        .select_related("conversation", "conversation__topic")
        .annotate(last_message_at=Subquery(last_message), other_name=Subquery(other_name))
    )
    needle = query.casefold()
    entries = []
    for participant in rows:
        conversation = participant.conversation
        topic = conversation.topic
        if needle and needle not in topic.proposition.casefold() and needle not in topic.opposing_position.casefold():
            continue
        entries.append(
            (
                participant.last_message_at or conversation.created_at,
                conversation.pk,
                {
                    "conversation": conversation,
                    "topic": topic,
                    "my_side": participant.side or None,
                    "other_username": participant.other_name,
                    "status": {"open": "waiting", "active": "active", "closed": "ended"}[conversation.status],
                },
            )
        )
    entries.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    result, ended = [], 0
    for _, _, entry in entries:
        if entry["status"] == "ended" and not needle:
            ended += 1
            if ended > settings.MY_ENDED_CONVERSATIONS_SHOWN:
                continue
        result.append(entry)
    return result


def seeded_topics():
    """The visible seeded topics (no creator), in the order they were created."""
    return list(Topic.objects.filter(hidden=False, created_by__isnull=True).order_by("id"))


# ---------------------------------------------------------------------------------------------------------------------
# Blocking (step 7c, revision 5)
# ---------------------------------------------------------------------------------------------------------------------


def block_user(blocker, target):
    """Block ``target`` and end every open or active conversation the two share. Returns the ended conversation ids.

    Idempotent. Refuses an anonymous blocker (``login_required``), and yourself or an unknown user
    (``invalid_block``). The conversations are ended exactly as ``end_conversation`` ends them: closed for both,
    read-only, ``ended_by`` the blocker's participant.
    """
    _require_login(blocker)
    if target is None or getattr(target, "pk", None) is None:
        raise PostRejected("invalid_block", "That person was not found.")
    if target.pk == blocker.pk:
        raise PostRejected("invalid_block", "You cannot block yourself.")
    with transaction.atomic():
        if not get_user_model().objects.filter(pk=target.pk).exists():
            raise PostRejected("invalid_block", "That person was not found.")
        Block.objects.get_or_create(blocker_id=blocker.pk, blocked_id=target.pk)
        shared = list(
            Conversation.objects.filter(source="human", status__in=["open", "active"])
            .filter(participants__user_id=blocker.pk)
            .filter(participants__user_id=target.pk)
            .order_by("id")
        )
        now = timezone.now()
        ended = []
        for conversation in shared:
            mine = Participant.objects.get(conversation_id=conversation.pk, user_id=blocker.pk)
            conversation.status = "closed"
            conversation.ended_by = mine
            conversation.ended_at = now
            conversation.save(update_fields=["status", "ended_by", "ended_at"])
            ended.append(conversation.pk)
    return ended


def unblock_user(blocker, target):
    """Remove the block (idempotent). Nothing is reopened. Returns True if a block was removed."""
    _require_login(blocker)
    if target is None or getattr(target, "pk", None) is None:
        return False
    removed, _ = Block.objects.filter(blocker_id=blocker.pk, blocked_id=target.pk).delete()
    return removed > 0


def blocked_users(viewer):
    """The people the viewer has blocked, newest first: ``[{"username", "since"}]``."""
    if not _is_authenticated(viewer):
        return []
    return [
        {"username": block.blocked.username, "since": block.created_at}
        for block in Block.objects.filter(blocker_id=viewer.pk).select_related("blocked").order_by("-created_at", "-id")
    ]


def is_blocked_between(a, b):
    """True if either person has blocked the other."""
    if not _is_authenticated(a) or not _is_authenticated(b):
        return False
    return Block.objects.filter(
        Q(blocker_id=a.pk, blocked_id=b.pk) | Q(blocker_id=b.pk, blocked_id=a.pk)
    ).exists()


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


class Draft:
    """What ``validate_draft`` learned about an acceptable draft (nothing about it is stored)."""

    __slots__ = ("participant", "conversation", "content", "char_count", "user_message_count")

    def __init__(self, participant, conversation, content, char_count, user_message_count):
        self.participant = participant  # the author's Participant row
        self.conversation = conversation  # the conversation as it is now
        self.content = content  # the text exactly as it would be stored (normalised)
        self.char_count = char_count
        self.user_message_count = user_message_count  # user messages already in the conversation


def validate_draft(user, conversation, text):
    """Apply every posting rule to a draft and create nothing. Returns a ``Draft``; raises ``PostRejected``.

    Checks, in order: not a participant, closed, empty, too long, too fast, conversation full. ``post_message`` uses
    this same function, so a draft accepted here can only be refused later because something changed meanwhile.
    """
    participant = _participant_of(user, conversation)
    if participant is None:
        raise _not_participant()
    fresh = Conversation.objects.get(pk=conversation.pk)
    if fresh.status == "closed":
        raise _closed_rejection(fresh, participant)

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
            f"{_plural(excess, 'character')}. Messages are capped to keep the discussion readable.",
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
                f"every {gap} seconds to keep the discussion readable.",
                retry_after=wait,
                details={"wait_seconds": wait, "gap_seconds": gap},
            )

    so_far = _user_message_count(fresh)
    if so_far + 1 > settings.MAX_USER_MESSAGES_PER_CONVERSATION:
        raise _full_rejection()
    return Draft(participant, fresh, content, count, so_far)


def post_message(user, conversation, text, in_reply_to=None):
    """Post a user message and enqueue exactly one live moderation run for it.

    Checks (in ``validate_draft``), in order: not a participant, closed, empty, too long, too fast, conversation full.
    A refused post saves nothing and creates no run. When the conversation's 30th user message is posted the
    conversation is closed.
    """
    with transaction.atomic():
        draft = validate_draft(user, conversation, text)
        participant, fresh, content = draft.participant, draft.conversation, draft.content
        so_far = draft.user_message_count
        max_messages = settings.MAX_USER_MESSAGES_PER_CONVERSATION

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
