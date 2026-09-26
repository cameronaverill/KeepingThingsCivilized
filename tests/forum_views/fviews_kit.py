"""Shared helpers for the step 7b tests (tests/forum_views/). Nothing here imports from a conftest; test modules import
what they need from this module by name. Services are imported lazily so a missing module fails a test, not collection."""
import itertools
import json
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

import fviews_html as H

User = get_user_model()
MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"

NOT_FOUND_TEXT = "This conversation was not found, or you are not a participant."
WAITING_TEXT = "You can post once someone else joins."
CLOSED_TEXT = "This conversation is closed."
YOU_ENDED = "You ended this conversation."
OTHER_ENDED = "The other participant ended this conversation."

# Recognisable names so a leak is easy to spot anywhere in a page.
NAME_A, NAME_B = "zelda_mox", "quincy_ray"
EMAIL_A, EMAIL_B = "zelda_mox@leakcheck.example", "quincy_ray@leakcheck.example"

_counter = itertools.count(1)


def uniq(prefix="x"):
    return f"{prefix}{next(_counter)}"


# --- accounts, clients, clock ---------------------------------------------------------------------------------------

def make_user(name=None, email=None):
    name = name or uniq("plainuser")
    # No password: hashing (Argon2) is slow and every test signs in with force_login.
    return User.objects.create_user(username=name, email=email or f"{name}@example.com")


def client_for(user, csrf=False):
    client = Client(enforce_csrf_checks=csrf)
    client.force_login(user, backend=MODEL_BACKEND)
    return client


def csrf_token(client):
    """Make the browser hold a CSRF cookie (any page with a form does that) and return its value."""
    if "csrftoken" not in client.cookies:
        client.get(reverse("forum:how_it_works"))
    if "csrftoken" not in client.cookies:
        client.get(reverse("accounts:login"))
    return client.cookies["csrftoken"].value


def csrf_post(client, url, data=None, **extra):
    """POST with a valid CSRF token the way a browser form would."""
    payload = dict(data or {})
    payload["csrfmiddlewaretoken"] = csrf_token(client)
    return client.post(url, payload, **extra)


class FClock:
    """Frozen server time that only moves when a test says so. Patched over django.utils.timezone.now."""

    def __init__(self):
        self.base = timezone.now().replace(microsecond=0)
        self.offset = timedelta(0)

    def now(self):
        return self.base + self.offset

    def advance(self, seconds):
        self.offset += timedelta(seconds=seconds)


# --- forum data -----------------------------------------------------------------------------------------------------

def make_topic(proposition=None, created_by=None, title="", hidden=False, minutes_ago=0):
    from forum.models import Topic

    proposition = proposition or uniq("Proposition number ")
    topic = Topic.objects.create(title=title, proposition=proposition, created_by=created_by, hidden=hidden)
    if minutes_ago:
        Topic.objects.filter(pk=topic.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
    topic.refresh_from_db()
    return topic


def enter(user, topic):
    from forum import services

    return services.enter_proposition(user, topic)


def participant_of(conv, user):
    return conv.participants.get(user=user)


class Duo:
    """Two signed-in browsers in one active conversation about one proposition."""

    def __init__(self, proposition="Cats make better pets than dogs.", csrf=False, names=(NAME_A, NAME_B)):
        self.ua = make_user(names[0], f"{names[0]}@leakcheck.example")
        self.ub = make_user(names[1], f"{names[1]}@leakcheck.example")
        self.topic = make_topic(proposition, created_by=self.ua)
        self.conv = enter(self.ua, self.topic)
        self.conv = enter(self.ub, self.topic)
        assert self.conv.status == "active", "the second person joining must make the conversation active"
        self.ca = client_for(self.ua, csrf)
        self.cb = client_for(self.ub, csrf)
        self.pa = participant_of(self.conv, self.ua)
        self.pb = participant_of(self.conv, self.ub)
        self.url = reverse("forum:conversation", args=[self.conv.pk])
        self.post_url = reverse("forum:post", args=[self.conv.pk])
        self.end_url = reverse("forum:end", args=[self.conv.pk])
        self.poll_url = reverse("forum:messages", args=[self.conv.pk])

    def seed(self, participant, text, minutes_ago=None):
        return seed_message(self.conv, participant, text, minutes_ago)


def seed_message(conv, participant, text, minutes_ago=None):
    """A user message written straight into the database (no run, no rate limit): history for a page to show."""
    from forum.models import Message

    msg = Message.objects.create(conversation=conv, author_type="user", participant=participant, content=text)
    if minutes_ago is not None:
        Message.objects.filter(pk=msg.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
    return msg


def add_moderator_post(conv, trigger, text, acts):
    """A moderator post as the pipeline leaves it: a message replying to `trigger`, the done run that posted it, and
    its acts. `acts` is a list of (addressee, subject, [source messages]) with addressee/subject as internal labels,
    'all', 'both' or 'none'."""
    from forum.models import Message
    from moderation.models import InterventionAct, ModerationRun

    run = ModerationRun.objects.filter(trigger_message=trigger, kind="live").first()
    if run is None:
        run = ModerationRun.objects.create(
            conversation=conv, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live", status="pending"
        )
    mod = Message.objects.create(conversation=conv, author_type="moderator", content=text, in_reply_to=trigger)
    run.status, run.decision, run.posted_message = "done", "intervene", mod
    run.save()
    for order, (addressee, subject, sources) in enumerate(acts, 1):
        act = InterventionAct.objects.create(
            run=run, order=order, act_type="request_clarification", tone="neutral", text=text,
            addressee=addressee, subject=subject,
        )
        act.source_messages.set(sources)
    return mod


def set_last_run(conv, trigger, status, failure_reason=""):
    from moderation.models import ModerationRun

    run, _ = ModerationRun.objects.get_or_create(
        conversation=conv, trigger_message=trigger, kind="live",
        defaults={"snapshot_seq": trigger.seq_no, "status": status},
    )
    run.status, run.failure_reason = status, failure_reason
    run.save()
    return run


def runs(conv=None):
    from moderation.models import ModerationRun

    qs = ModerationRun.objects.all()
    return qs.filter(conversation=conv) if conv is not None else qs


def user_messages(conv):
    return conv.messages.filter(author_type="user").order_by("seq_no")


def fresh(obj):
    obj.refresh_from_db()
    return obj


# --- reading pages --------------------------------------------------------------------------------------------------

def page_text(response):
    return H.doc(response).text()


def poll_json(client, conv_or_url, after=None):
    url = conv_or_url if isinstance(conv_or_url, str) else reverse("forum:messages", args=[conv_or_url.pk])
    response = client.get(url + ("" if after is None else f"?after={after}"))
    assert response.status_code == 200, response.content[:300]
    return response, json.loads(response.content)


def find_block(root, token, needles):
    """The smallest ancestor of the element holding `token` whose text mentions any of `needles`; None if there is none."""
    holder = None
    for node in root.walk():
        if any(isinstance(c, str) and token in c for c in node.children):
            holder = node
            break
    if holder is None:
        return None
    node = holder
    while node is not None:
        text = node.text()
        if any(n in text for n in needles):
            return node
        node = node.parent
    return None


# --- leak checks ----------------------------------------------------------------------------------------------------

_LABEL_PATTERNS = [
    re.compile(r">\s*[AB]\s*<"),  # an element whose whole text is a lone label letter
    re.compile(r"(?i)\b(?:participant|user|speaker|side|person|member|player)[\s_-]*[AB]\b"),
    re.compile(r"(?i)\(\s*[AB]\s*\)"),
    re.compile(r'(?i)class="[^"]*\b(?:label|participant|side|speaker|user)[-_][ab]\b'),
    re.compile(r"(?i)\bdata-(?:label|participant|side)\b"),
]


def leaks(page_html, viewer, other, own_name_in_header_ok=False):
    """Everything in a rendered page that identifies a person or shows an internal label. Empty list = clean.
    Conversation pages must not carry even the viewer's own username (the brief: no username of either person); on the
    other pages the site header shows it (`own_name_in_header_ok=True`)."""
    problems = []
    for token in (other.username, other.email, viewer.email):
        if token and token.lower() in page_html.lower():
            problems.append(f"contains {token!r}")
    body = H.without_header(page_html) if own_name_in_header_ok else page_html
    if viewer.username in body:
        problems.append(f"contains the viewer's own username {viewer.username!r}")
    if "Participant" in page_html:
        problems.append("contains the word 'Participant'")
    for pattern in _LABEL_PATTERNS:
        found = pattern.search(page_html)
        if found:
            problems.append(f"label-like text {found.group(0)!r}")
    return problems


def json_leaks(data, viewer, other):
    """Leaks in a polling response: names, emails, label letters as values, label-ish keys."""
    problems = []
    blob = json.dumps(data)
    for token in (viewer.username, other.username, viewer.email, other.email):
        if token in blob:
            problems.append(f"contains {token!r}")

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                if re.search(r"(?i)label|participant|username|email|user_id|author", key):
                    problems.append(f"key {path}/{key}")
                walk(item, f"{path}/{key}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}]")
        elif isinstance(value, str) and value in ("A", "B"):
            problems.append(f"value {value!r} at {path}")

    walk(data)
    return problems


def install_fake_pipeline(monkeypatch, behaviour=None):
    """Put a fake `moderation.pipeline.run_moderation(run)` in place (step 5 builds the real one in parallel).
    Returns the list of runs it was called with. `behaviour(run)` may create the moderator post."""
    import importlib
    import sys
    import types

    calls = []

    def fake(run):
        calls.append(run)
        if behaviour:
            behaviour(run)

    try:
        module = importlib.import_module("moderation.pipeline")
        monkeypatch.setattr(module, "run_moderation", fake, raising=False)
    except ImportError:
        module = types.ModuleType("moderation.pipeline")
        module.run_moderation = fake
        monkeypatch.setitem(sys.modules, "moderation.pipeline", module)
        import moderation

        monkeypatch.setattr(moderation, "pipeline", module, raising=False)
    return calls
