"""Helpers for the front-end fixes tests (docs/frontend_fixes_brief.md): a moderator post with a research-eligible act, research
runs in any state, and readers for the research slot. Not a test module. Model imports happen inside functions."""
import json
import re

from django.urls import reverse

import fviews_html as H
import fviews_kit as K

FAILED_TEXT = "The background check could not be completed."
RETRY_BUTTON = "Try again"
OFFER_BUTTON = "Provide factual background"
PENDING_TEXT = "Checking — this may take a moment"
BANNED = ("cost", "spend", "spending", "budget", "api cost", "running cost")

# Every status a research run can have (ModerationRun.STATUS_CHOICES) and the slot state each must show.
FAILED_STATUSES = ("failed", "skipped_budget", "skipped_disabled")
IN_FLIGHT_STATUSES = ("pending", "running")


def post_with_act(conv, trigger, *, act_type="offer_research", marker="ff-marker", text=None, order=1):
    """A moderator message replying to `trigger`, its done live run and one valid act on it. Returns (message, act)."""
    from forum.models import Message
    from moderation.models import InterventionAct, ModerationRun

    run = ModerationRun.objects.filter(trigger_message=trigger, kind="live").first()
    if run is None:
        run = ModerationRun.objects.create(
            conversation=conv, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live", status="pending"
        )
    content = text if text is not None else f"{marker}: an independent check could be requested."
    mod = Message.objects.create(conversation=conv, author_type="moderator", content=content, in_reply_to=trigger)
    run.status, run.decision, run.posted_message = "done", "intervene", mod
    run.save()
    act = InterventionAct.objects.create(
        run=run, order=order, act_type=act_type, tone="neutral", text=content, addressee="all", subject="none",
        validity="valid",
    )
    act.source_messages.set([trigger])
    return mod, act


def make_run(act, *, status="pending", posted_message=None, requested_by=None, attempts=0, failure_reason="", error=""):
    from moderation.models import ModerationRun

    trigger = act.source_messages.order_by("seq_no").first()
    return ModerationRun.objects.create(
        conversation=act.run.conversation, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="research",
        source_act=act, requested_by=requested_by, status=status, posted_message=posted_message, attempts=attempts,
        failure_reason=failure_reason, error=error,
    )


def note_message(conv, trigger, text="research-note: sourced answer."):
    from forum.models import Message

    return Message.objects.create(conversation=conv, author_type="moderator", content=text, in_reply_to=trigger)


def make_state(act, state, requested_by=None):
    """Put the act's research run into the named slot state ('none' makes no run)."""
    if state == "none":
        return None
    trigger = act.source_messages.order_by("seq_no").first()
    if state == "pending":
        return make_run(act, status="pending", requested_by=requested_by)
    if state == "failed":
        return make_run(act, status="failed", requested_by=requested_by, attempts=2, failure_reason="internal_error",
                        error="boom")
    if state == "done":
        return make_run(act, status="done", posted_message=note_message(act.run.conversation, trigger),
                        requested_by=requested_by)
    raise ValueError(state)


def url_for(conv, act):
    return reverse("forum:request_research", args=[conv.pk, act.pk])


def body(response):
    assert response["Content-Type"].startswith("application/json"), response["Content-Type"]
    return json.loads(response.content)


def reload_run(run):
    from moderation.models import ModerationRun

    return ModerationRun.objects.get(pk=run.pk)


def research_runs(act):
    from moderation.models import ModerationRun

    return list(ModerationRun.objects.filter(kind="research", source_act=act))


def slots(root):
    return [n for n in root.walk() if n.tag == "div" and "research-slot" in (n.get("class") or "").split()]


def slot_for(root, act):
    found = [s for s in slots(root) if s.get("data-act-id") == str(act.pk)]
    assert len(found) <= 1, "one slot per act"
    return found[0] if found else None


def has_class(node, name):
    return name in (node.get("class") or "").split()


def descendants(node, tag=None, cls=None):
    return [n for n in node.walk() if (tag is None or n.tag == tag) and (cls is None or has_class(n, cls))]


def research_payload(client, conv):
    """The `research` list of the poll endpoint (checks the key exists)."""
    _, data = K.poll_json(client, conv)
    assert "research" in data, "the poll payload must carry a `research` key"
    return data["research"]


def item_for(items, act):
    found = [i for i in items if i["act_id"] == act.pk]
    assert len(found) <= 1
    return found[0] if found else None


def fragment(html):
    """Parse an HTML fragment (a slot's inner html) under a dummy root."""
    return H.parse(f"<html><body><div id='frag'>{html}</div></body></html>").find("div", id="frag")


def strip_csrf(html):
    return re.sub(r'value="[^"]*"', 'value=""', html)


def post_with_acts(conv, trigger, specs, marker="ff-multi"):
    """One moderator message whose paragraphs are produced by several acts of one run. `specs` is a list of act types.
    Returns (message, [acts])."""
    from forum.models import Message
    from moderation.models import InterventionAct, ModerationRun

    run = ModerationRun.objects.create(
        conversation=conv, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live", status="pending"
    )
    texts = [f"{marker}-{i}: paragraph number {i}." for i in range(len(specs))]
    mod = Message.objects.create(conversation=conv, author_type="moderator", content="\n\n".join(texts),
                                 in_reply_to=trigger)
    run.status, run.decision, run.posted_message = "done", "intervene", mod
    run.save()
    acts = []
    for order, (act_type, text) in enumerate(zip(specs, texts), 1):
        act = InterventionAct.objects.create(
            run=run, order=order, act_type=act_type, tone="neutral", text=text, addressee="all", subject="none",
            validity="valid",
        )
        act.source_messages.set([trigger])
        acts.append(act)
    return mod, acts
