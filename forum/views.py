"""Pages of the forum (step 7b): home, propose, enter, conversation, post, end, polling, How this works.

Views are thin. Every rule (limits, pairing, membership, who ended what) lives in ``forum.services`` and
``forum.viewmodels`` (step 7a); a view only asks, then shows the answer. Consequences:

- Every ``PostRejected`` is shown next to the form it belongs to, with the person's text kept, and nothing else changes.
- A conversation that does not exist and a conversation the user is not in produce the very same 404 page, so nothing
  reveals which conversations exist.
- Views never create ``Message`` rows or conversations themselves, and never put a label or a name in a page.
"""

import logging
from types import SimpleNamespace

from django.conf import settings
from django.contrib import messages as flash
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import services, viewmodels
from .forms import SearchForm, TextForm
from .limits import count_message_chars
from .models import Conversation, Participant, Topic
from .templatetags import forum_text

logger = logging.getLogger(__name__)

SIDES = ("pro", "con")
CHOOSE_A_POSITION = "Choose a position first."
CONVERSATION_NOT_FOUND = "This conversation was not found, or you are not a participant."
OUR_SIDE_FAILED = (
    "Something went wrong on our side and your message was not sent. "
    "Your text is still in the box; please try again."
)
PROPOSITION_OUR_SIDE_FAILED = (
    "Something went wrong on our side and your proposition was not created. "
    "Your text is still in the box; please try again."
)


def _limits():
    """The numbers shown to people, always read from settings (never typed into a template)."""
    return {
        "max_message_chars": settings.MAX_MESSAGE_CHARS,
        "max_proposition_chars": settings.MAX_PROPOSITION_CHARS,
        "min_seconds": settings.MIN_SECONDS_BETWEEN_MESSAGES,
        "max_messages": settings.MAX_USER_MESSAGES_PER_CONVERSATION,
        "max_propositions_per_day": settings.MAX_PROPOSITIONS_PER_USER_PER_DAY,
    }


def _error_context(exc):
    """What a template needs to show a ``PostRejected``: plain words, a stable code, and the details."""
    return {
        "code": exc.code,
        "message": exc.message,
        "retry_after": exc.retry_after,
        "details": exc.details or {},
    }


def _plain_error(message, code="server_error"):
    """The same shape as ``_error_context`` for a failure on our side (details go to the log, never the page)."""
    return {"code": code, "message": message, "retry_after": None, "details": {}}


def _not_found(request):
    """The one 404 for a missing conversation and for a conversation of other people."""
    return render(request, "404.html", {"not_found_text": CONVERSATION_NOT_FOUND}, status=404)


# --- Home ----------------------------------------------------------------------------------------------------

STATUS_WORDS = {
    "waiting": "Waiting for someone to take the other position",
    "active": "In discussion",
    "ended": "Ended",
}
DISAGREE_BUTTON = "I disagree with this position"


def _own_position_line(side, topic):
    """The viewer's own position as one line (the same rule on the conversation page and in "Your conversations")."""
    if side == "con":
        if topic.opposing_position:  # a seeded topic: the opposing wording exists
            return f"Your position: {topic.opposing_position}"
        return f"You disagree with this position: {topic.proposition}"
    return f"Your position: {topic.proposition}"  # pro, or an old row with a blank side


def _side_choices(topic):
    """The two buttons that start a conversation on ``topic``: (side, label) for pro and con."""
    con_label = (
        f"My position is that {forum_text.position_phrase(topic.opposing_position)}"
        if topic.opposing_position
        else DISAGREE_BUTTON
    )
    return [
        ("pro", f"My position is that {forum_text.position_phrase(topic.proposition)}"),
        ("con", con_label),
    ]


def _waiting_cards(groups):
    """One card per waiting group: who is waiting, their position, and the ONE button that takes the opposite side."""
    cards = []
    for group in groups:
        topic, waiter = group["topic"], group["waiting_side"]
        if waiter == "con":
            quote = topic.opposing_position or f"They disagree with: {topic.proposition}"
        else:
            quote = topic.proposition
        join_side = "pro" if waiter == "con" else "con"
        label = dict(_side_choices(topic))[join_side]
        cards.append(
            {
                "topic_id": topic.pk,
                "quote": quote,
                "join_side": join_side,
                "join_label": label,
                "username": group.get("waiting_username") or "",
            }
        )
    return cards


def _my_rows(conversations):
    """Rows of "Your discussions": own position, who with, a status word and the link. Ended ones come last, quieter."""
    rows = [
        {
            "id": item["conversation"].pk,
            "position": _own_position_line(item.get("my_side"), item["topic"]),
            "status": item["status"],
            "status_word": STATUS_WORDS.get(item["status"], ""),
            "other_username": item.get("other_username") or "",
        }
        for item in conversations
    ]
    return [r for r in rows if r["status"] != "ended"] + [r for r in rows if r["status"] == "ended"]


def _render_home(request, *, error=None, status=200):
    """The waiting list only. A ``q`` parameter is ignored: searching lives on "Your discussions"."""
    context = {
        "cards": _waiting_cards(services.waiting_groups(request.user)),
        "error": error,
        **_limits(),
    }
    return render(request, "forum/home.html", context, status=status)


@login_required
@require_GET
def home(request):
    return _render_home(request)


@login_required
@require_GET
def mine(request):
    """"Your discussions": the viewer's own conversations, searchable. Nothing about anyone else's."""
    form = SearchForm(request.GET or None)
    if form.is_valid():
        query = form.cleaned_data["q"]
    else:  # for example a NUL character: search for exactly that, which finds nothing, rather than showing everything
        query = " ".join(request.GET.get("q", "").split())
    context = {
        "rows": _my_rows(services.my_conversations(request.user, query or None)),
        "query": query,
        **_limits(),
    }
    return render(request, "forum/mine.html", context)


# --- Blocking ------------------------------------------------------------------------------------------------


def _target_user(username):
    User = get_user_model()
    return User._default_manager.filter(**{User.USERNAME_FIELD: username}).first()


@login_required
@require_POST
def block(request, username):
    target = _target_user(username)
    if target is None:
        return render(request, "404.html", status=404)
    if target.pk == request.user.pk:
        return _render_home(request, error=_plain_error("You cannot block yourself.", code="invalid_block"))
    try:
        ended = services.block_user(request.user, target)
    except services.PostRejected as exc:
        return _render_home(request, error=_error_context(exc))
    name = target.get_username()
    if ended:  # a conversation they shared has ended
        flash.success(request, f"You blocked {name}. The conversation has ended.")
        return redirect("forum:mine")
    flash.success(request, f"You blocked {name}.")
    return redirect("forum:home")


@login_required
@require_POST
def unblock(request, username):
    target = _target_user(username)
    if target is None:
        return render(request, "404.html", status=404)
    try:
        services.unblock_user(request.user, target)
    except services.PostRejected as exc:
        return _render_home(request, error=_error_context(exc))
    flash.success(request, f"You unblocked {target.get_username()}.")
    return redirect("forum:blocked")


@login_required
@require_GET
def blocked(request):
    return render(request, "forum/blocked.html", {"rows": services.blocked_users(request.user)})


# --- Propose and enter ---------------------------------------------------------------------------------------


def _seeded_cards(user):
    """The suggested topics on the propose page: two side buttons each, or the viewer's own conversation."""
    topics = services.seeded_topics()
    own = services.own_sides(topics, user) if topics else {}
    return [
        {
            "id": topic.pk,
            "proposition": topic.proposition,
            "own": topic.pk in own,
            "own_side": own.get(topic.pk, "pro"),
            "choices": _side_choices(topic),
        }
        for topic in topics
    ]


def _render_propose(request, *, draft="", error=None, status=200):
    context = {
        "draft": draft,
        "draft_count": count_message_chars(draft),
        "error": error,
        "duplicate_topic_id": error["details"].get("topic_id") if error is not None else None,
        "seeded": _seeded_cards(request.user),
        **_limits(),
    }
    return render(request, "forum/propose.html", context, status=status)


@login_required
@require_http_methods(["GET", "POST"])
def propose(request):
    if request.method == "GET":
        return _render_propose(request)
    form = TextForm(request.POST)
    text = form.cleaned_data["text"] if form.is_valid() else ""
    try:
        # Publishing and starting its conversation are one action: if the conversation is refused (for example too many
        # open ones) nothing is published, the text stays in the box, and the person can simply try again.
        with transaction.atomic():
            topic = services.create_proposition(request.user, text)
            # The person writes their own position, so they enter holding it ("pro"); whoever joins holds the other.
            conversation = services.enter_proposition(request.user, topic, "pro")
    except services.PostRejected as exc:
        return _render_propose(request, draft=text, error=_error_context(exc))
    except Exception:
        logger.exception("create_proposition failed")
        return _render_propose(request, draft=text, error=_plain_error(PROPOSITION_OUR_SIDE_FAILED), status=500)
    return redirect("forum:conversation", conversation_id=conversation.pk)


@login_required
@require_POST
def enter(request, topic_id):
    topic = get_object_or_404(Topic, pk=topic_id)
    side = request.POST.get("side", "")
    if side not in SIDES:
        # Nothing is created: the person is told to choose, and the list is shown again.
        return _render_home(request, error=_plain_error(CHOOSE_A_POSITION, code="invalid_side"))
    try:
        conversation = services.enter_proposition(request.user, topic, side)
    except services.PostRejected as exc:
        return _render_home(request, error=_error_context(exc))
    return redirect("forum:conversation", conversation_id=conversation.pk)


# --- A conversation ------------------------------------------------------------------------------------------


def _load_conversation(conversation_id):
    return Conversation.objects.select_related("topic").filter(pk=conversation_id).first()


def _preview_mode(conversation, view):
    """"on" or "off" for the composer (``data-preview``), or None when there is no composer to check.

    Asked only for a participant (``conversation_view`` already refused everyone else) of an open or active
    conversation that can be posted in. Any failure of the moderation side gives "off": the composer then behaves as an
    ordinary form."""
    if view.get("status") not in ("open", "active") or not view.get("can_post"):
        return None
    try:
        from moderation import preview

        return "on" if preview.preview_mode(conversation.pk) == "on" else "off"
    except Exception:
        logger.exception("preview_mode failed for conversation %s", conversation.pk)
        return "off"


def _position_lines(view):
    """The viewer's own position, shown under the title (owner wording). Nothing about the other participant."""
    texts = view.get("position_texts") or {}
    mine = view.get("my_side")
    proposition = texts.get("pro")
    if mine not in SIDES or not proposition:
        return []
    topic = SimpleNamespace(proposition=proposition, opposing_position=texts.get("con") or "")
    return [_own_position_line(mine, topic)]


def _render_conversation(request, conversation, *, error=None, draft="", status=200):
    try:
        view = viewmodels.conversation_view(request.user, conversation)
    except services.PostRejected as exc:
        if exc.code == "not_participant":
            return _not_found(request)
        raise
    messages_list = view["messages"]
    reason = view.get("cannot_post_reason")
    context = {
        "preview_mode": _preview_mode(conversation, view),
        "check_url": reverse("forum:check", args=[conversation.pk]),
        "check_timeout_ms": int(settings.PREVIEW_CLIENT_TIMEOUT_SECONDS * 1000),
        "conversation": conversation,
        "view": view,
        "reason": reason,
        "position_lines": _position_lines(view),
        "error": error,
        "draft": draft,
        "draft_count": count_message_chars(draft),
        "last_seq": max((m["seq_no"] for m in messages_list), default=0),
        "poll_seconds": settings.POLL_SECONDS,
        "poll_url": reverse("forum:messages", args=[conversation.pk]),
        **_limits(),
    }
    return render(request, "forum/conversation.html", context, status=status)


@login_required
@require_GET
@never_cache
def conversation(request, conversation_id):
    found = _load_conversation(conversation_id)
    if found is None:
        return _not_found(request)
    return _render_conversation(request, found)


def _my_participant(request, conversation):
    return Participant.objects.filter(conversation_id=conversation.pk, user_id=request.user.pk).first()


def _resolve_posted_check(request, conversation, message):
    """After a successful post: when the form carried a ``check_id`` of this poster's own check of this very text, record
    "posted as written". Anything else is ignored silently, and nothing here can change whether the post succeeded."""
    raw = request.POST.get("check_id", "")
    if not raw:
        return
    try:
        check_id = int(raw)
        from moderation import preview
        from moderation.models import PreviewCheck

        participant = _my_participant(request, conversation)
        check = PreviewCheck.objects.filter(
            pk=check_id, conversation=conversation.pk, participant=getattr(participant, "pk", None)
        ).first()
        if check is not None and check.draft_sha256 == preview.draft_sha256(message.content):
            preview.resolve_check(check_id, "posted_as_written", message_id=message.pk)
    except Exception:
        logger.exception("could not resolve preview check for conversation %s", conversation.pk)


def _check_json(payload):
    response = JsonResponse(payload)
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
@never_cache
def check(request, conversation_id):
    """Check a draft before it is posted: the posting rules first (nothing is created), then the moderator's preview."""
    found = _load_conversation(conversation_id)
    if found is None:
        return _not_found(request)
    form = TextForm(request.POST)
    text = form.cleaned_data["text"] if form.is_valid() else ""
    try:
        services.validate_draft(request.user, found, text)
    except services.PostRejected as exc:
        if exc.code == "not_participant":
            return _not_found(request)
        return _check_json(
            {"status": "refused", "code": exc.code, "message": exc.message, "retry_after": exc.retry_after}
        )
    unavailable = {"status": "unavailable", "check_id": None, "notes": []}
    try:
        from moderation import preview

        stored = preview.check_draft(found, _my_participant(request, found), text)
        outcome = stored.outcome if stored.outcome in ("no_concern", "concern", "unavailable") else "unavailable"
        notes = [str(note) for note in (stored.note_texts or [])] if outcome == "concern" else []
        return _check_json({"status": outcome, "check_id": stored.pk, "notes": notes})
    except Exception:
        logger.exception("check_draft failed for conversation %s", conversation_id)
        return _check_json(unavailable)


@login_required
@require_POST
@never_cache
def check_edit(request, conversation_id, check_id):
    """The author chose to edit after seeing a concern. Only the owner of the check, in its own conversation."""
    found = _load_conversation(conversation_id)
    if found is None:
        return _not_found(request)
    participant = _my_participant(request, found)
    if participant is None:
        return _not_found(request)
    from moderation.models import PreviewCheck

    owned = PreviewCheck.objects.filter(pk=check_id, conversation=found.pk, participant=participant.pk).exists()
    if not owned:
        return _not_found(request)
    try:
        from moderation import preview

        preview.resolve_check(check_id, "edited")
    except Exception:
        logger.exception("could not record the edit of check %s", check_id)
    return _check_json({"ok": True})


@login_required
@require_POST
@never_cache
def post(request, conversation_id):
    found = _load_conversation(conversation_id)
    if found is None:
        return _not_found(request)
    form = TextForm(request.POST)
    text = form.cleaned_data["text"] if form.is_valid() else ""
    try:
        message = services.post_message(request.user, found, text)
    except services.PostRejected as exc:
        if exc.code == "not_participant":
            return _not_found(request)
        return _render_conversation(request, found, error=_error_context(exc), draft=text)
    except Exception:
        # Details go to the log, never to the page. The person's text stays in the box.
        logger.exception("post_message failed for conversation %s", conversation_id)
        return _render_conversation(request, found, error=_plain_error(OUR_SIDE_FAILED), draft=text, status=500)
    _resolve_posted_check(request, found, message)
    return redirect("forum:conversation", conversation_id=found.pk)


@login_required
@require_POST
@never_cache
def end(request, conversation_id):
    found = _load_conversation(conversation_id)
    if found is None:
        return _not_found(request)
    try:
        services.end_conversation(request.user, found)
    except services.PostRejected as exc:
        if exc.code == "not_participant":
            return _not_found(request)
        return _render_conversation(request, found, error=_error_context(exc))
    return redirect("forum:conversation", conversation_id=found.pk)


@login_required
@require_GET
@never_cache
def messages(request, conversation_id):
    """JSON for polling: messages newer than ``?after=<seq_no>`` and the conversation's current state."""
    found = _load_conversation(conversation_id)
    if found is None:
        return _not_found(request)
    try:
        after = max(0, int(request.GET.get("after", "0")))
    except (TypeError, ValueError):
        after = 0
    try:
        view = viewmodels.conversation_view(request.user, found, after_seq=after)
    except services.PostRejected as exc:
        if exc.code == "not_participant":
            return _not_found(request)
        raise
    payload = dict(view)
    payload["messages"] = [
        # "html" is the same partial the page uses, rendered (and escaped) by the server; poll.js only inserts it.
        {**message, "html": render_to_string("forum/_message.html", {"m": message})}
        for message in view["messages"]
    ]
    reason = view.get("cannot_post_reason")
    payload["cannot_post_reason"] = dict(reason) if reason else None
    return JsonResponse(payload)


# --- How this works ------------------------------------------------------------------------------------------


@require_GET
def how_it_works(request):
    return render(request, "forum/how_it_works.html", _limits())
