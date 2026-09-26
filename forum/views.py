"""Pages of the forum (step 7b): home, propose, enter, conversation, post, end, polling, How this works.

Views are thin. Every rule (limits, pairing, membership, who ended what) lives in ``forum.services`` and
``forum.viewmodels`` (step 7a); a view only asks, then shows the answer. Consequences:

- Every ``PostRejected`` is shown next to the form it belongs to, with the person's text kept, and nothing else changes.
- A conversation that does not exist and a conversation the user is not in produce the very same 404 page, so nothing
  reveals which conversations exist.
- Views never create ``Message`` rows or conversations themselves, and never put a label or a name in a page.
"""

import logging

from django.conf import settings
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
from .models import Conversation, Topic

logger = logging.getLogger(__name__)

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


def _matching_topics(query):
    if "\x00" in query:
        return []  # a NUL character can never be part of a proposition
    topics = Topic.objects.filter(hidden=False).exclude(proposition="").order_by("-created_at", "-id")
    if not query:
        return list(topics.only("id", "proposition"))
    if query.isascii():
        return list(topics.filter(proposition__icontains=query).only("id", "proposition"))
    # SQLite's LIKE only ignores case for ASCII, so a non-ASCII search is compared in Python.
    needle = query.casefold()
    return [topic for topic in topics.only("id", "proposition") if needle in topic.proposition.casefold()]


def _render_home(request, *, error=None, status=200):
    form = SearchForm(request.GET or None)
    if form.is_valid():
        query = form.cleaned_data["q"]
    else:  # for example a NUL character: search for exactly that, which finds nothing, rather than showing everything
        query = " ".join(request.GET.get("q", "").split())
    context = {
        "topics": _matching_topics(query),
        "query": query,
        "error": error,
        **_limits(),
    }
    return render(request, "forum/home.html", context, status=status)


@login_required
@require_GET
def home(request):
    return _render_home(request)


# --- Propose and enter ---------------------------------------------------------------------------------------


def _render_propose(request, *, draft="", error=None, status=200):
    context = {
        "draft": draft,
        "draft_count": count_message_chars(draft),
        "error": error,
        "duplicate_topic_id": error["details"].get("topic_id") if error is not None else None,
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
            conversation = services.enter_proposition(request.user, topic)
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
    try:
        conversation = services.enter_proposition(request.user, topic)
    except services.PostRejected as exc:
        return _render_home(request, error=_error_context(exc))
    return redirect("forum:conversation", conversation_id=conversation.pk)


# --- A conversation ------------------------------------------------------------------------------------------


def _load_conversation(conversation_id):
    return Conversation.objects.select_related("topic").filter(pk=conversation_id).first()


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
        "conversation": conversation,
        "view": view,
        "reason": reason,
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
        services.post_message(request.user, found, text)
    except services.PostRejected as exc:
        if exc.code == "not_participant":
            return _not_found(request)
        return _render_conversation(request, found, error=_error_context(exc), draft=text)
    except Exception:
        # Details go to the log, never to the page. The person's text stays in the box.
        logger.exception("post_message failed for conversation %s", conversation_id)
        return _render_conversation(request, found, error=_plain_error(OUR_SIDE_FAILED), draft=text, status=500)
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
