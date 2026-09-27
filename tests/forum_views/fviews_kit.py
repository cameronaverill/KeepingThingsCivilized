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
# 7c wording (final, from docs/step7c_brief.md "Two-position model")
WAITING_TITLE = "Waiting for someone to take the other position"
WAITING_BODY = (
    "You are the first one here. Whoever joins will take the opposing position and can read everything you have "
    "posted so far. You can keep posting while you wait."
)
OWN_CONVERSATION = "You already have a conversation here."
OPEN_OWN_BUTTON = "Open your conversation"
DISAGREE_BUTTON = "I disagree with this position"
POSITION_PREFIX = "My position is that"
CHOOSE_POSITION = "Choose a position first."
PROPOSE_LEAD = (
    "State a position you hold and want to talk through. Whoever joins will take the opposing view, so you will not "
    "need to argue both sides."
)
PROPOSE_LEAD_OLD_TAIL = "Your opening message will be shown, shortened, on the home page"
HOW_PARAGRAPH = (
    # Wave 16 item 7 (docs/wave16_brief.md) removed the trailing "AI moderator sees only the topic..." sentence.
    "Every conversation is between two opposing positions. The home page lists positions that someone holds and is "
    "waiting for someone to disagree with. Join one to take the other side, or start a discussion of your own with "
    "your position or one of the suggested topics. Conversations are private to their two participants."
)
HOME_HEADING = "Waiting to discuss"
HOME_LEAD = (
    "These are positions that someone holds and is waiting for someone to disagree with. Pick one to take the other "
    "side, or start a discussion of your own. An AI moderator reads along and may step in."
)
WAITING_LABEL_RE = r"(\S+) is waiting to discuss:"


def waiting_label(username):
    return f"{username} is waiting to discuss:"


NO_MATCH = "No discussion matches your search."
EMPTY_HOME = "Nobody is waiting right now. You can start a discussion of your own."
SEARCH_LABEL = "Search your discussions"
START_BUTTON = "Start a new discussion"
YOUR_DISCUSSIONS = "Your discussions"
OLD_HOME_SECTION = "Your conversations"
EMPTY_DISCUSSIONS = "You have no discussions yet. Pick a waiting position on the home page, or start one of your own."
BLOCKED_HEADING = "Blocked people"
BLOCKED_LEAD = "People you have blocked cannot see your waiting positions, and you cannot see theirs."
BLOCKED_EMPTY = "You have not blocked anyone."
WHO_IS_HERE_FORMAT = "You are talking with {name}. The moderator refers to messages by number, such as \u201cAbout your message 4\u201d."
BLOCK_EXPLAIN = "Blocking ends this conversation for both of you. You will not see each other's waiting positions or be paired again."
SELF_BLOCK = "You cannot block yourself."
# HOW_BLOCK (the username/blocking sentences that used to follow HOW_PARAGRAPH) was removed by wave 16 item 7; the
# how_it_works page no longer says this anywhere (see test_fviews_wave16_how_it_works.py).


def blocked_banner_from_conversation(name):
    return f"You blocked {name}. The conversation has ended."


def blocked_banner_from_card(name):
    return f"You blocked {name}."


def unblocked_banner(name):
    return f"You unblocked {name}."
SEEDED_HEADING = "Or start from one of these topics"
STATUS_WAITING = "Waiting for someone to take the other position"
STATUS_ACTIVE = "In discussion"
STATUS_ENDED = "Ended"
THEY_DISAGREE = "They disagree with: "
YOU_DISAGREE = "You disagree with this position: "
# Wording that must be gone from every page (old behaviour).
OLD_PHRASES = [
    "Choose a proposition",
    "All propositions",
    "Back to all propositions",
    "Back to propositions",
    "No proposition matches",
    "Propose a new one",
    "Search propositions",
    "You choose the position you hold, and you are paired with someone who holds the other one",
    "Someone who holds the other position is waiting",
    "Their opening message",
    "You can post once someone else joins",
    "Waiting for a second person",
    "When someone else picks this proposition",
    "Two people will discuss it",
    "you will not be told which side",
    "nobody is told which side you take",
    "The one exception is a conversation that is still waiting for a second person",
    "The one exception is a conversation still waiting for someone to take the other position",
    "Someone who holds the other position is waiting",
    "Shortened here",
    "Their opening message",
    "Your opening message will be shown",
    "is shown on the home page so people can see what they would be joining",
]
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

def make_topic(proposition=None, created_by=None, title="", hidden=False, minutes_ago=0, opposing=""):
    from forum.models import Topic

    proposition = proposition or uniq("Proposition number ")
    topic = Topic.objects.create(
        title=title, proposition=proposition, created_by=created_by, hidden=hidden, opposing_position=opposing
    )
    if minutes_ago:
        Topic.objects.filter(pk=topic.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
    topic.refresh_from_db()
    return topic


def enter(user, topic, side="pro"):
    from forum import services

    return services.enter_proposition(user, topic, side)


def participant_of(conv, user):
    return conv.participants.get(user=user)


class Duo:
    """Two signed-in browsers in one active conversation about one proposition."""

    def __init__(self, proposition="Cats make better pets than dogs.", csrf=False, names=(NAME_A, NAME_B),
                 opposing="", sides=("pro", "con")):
        self.ua = make_user(names[0], f"{names[0]}@leakcheck.example")
        self.ub = make_user(names[1], f"{names[1]}@leakcheck.example")
        self.topic = make_topic(proposition, created_by=self.ua, opposing=opposing)
        self.sides = sides
        self.conv = enter(self.ua, self.topic, sides[0])
        self.conv = enter(self.ub, self.topic, sides[1])
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
    re.compile(r"(?i)\bdata-(?:label|participant)\b"),
]


def leaks(page_html, viewer, other, own_name_in_header_ok=False, other_name_ok=False):
    """Everything in a rendered page that identifies a person or shows an internal label. Empty list = clean.
    Revision 5: the OTHER person's username is shown to participants and on waiting cards (`other_name_ok=True`); emails
    are never shown. Conversation pages still never carry the viewer's OWN username; on the other pages the site header
    shows it (`own_name_in_header_ok=True`)."""
    problems = []
    for token in ((other.email, viewer.email) if other_name_ok else (other.username, other.email, viewer.email)):
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


def json_leaks(data, viewer, other, other_name_ok=True):
    """Leaks in a polling response: the viewer's own name, emails, label letters as values, label-ish keys. The other
    participant's username is allowed (Revision 5) unless `other_name_ok=False`."""
    problems = []
    blob = json.dumps(data)
    for token in (viewer.username, viewer.email, other.email) + (() if other_name_ok else (other.username,)):
        if token in blob:
            problems.append(f"contains {token!r}")

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                if re.search(r"(?i)label|participant|email|user_id", key):
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


def enter_view(client, topic, side="pro", **extra):
    """POST the enter form the way a home-page button does."""
    data = {} if side is None else {"side": side}
    return client.post(reverse("forum:enter", args=[topic.pk]), data, **extra)


def conv_id(response):
    found = re.fullmatch(r"/c/(\d+)/", response["Location"])
    assert found, f"expected a redirect into a conversation, got {response.status_code} {response.get('Location')}"
    return int(found.group(1))


def wait_on(topic, side="pro", user=None, minutes_ago=None):
    """Someone (a fresh user unless given) waits on `topic` holding `side`. Returns (conversation, user)."""
    from forum.models import Conversation

    user = user or make_user()
    conv = enter(user, topic, side)
    if minutes_ago is not None:
        Conversation.objects.filter(pk=conv.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
    return conv, user


def raw_waiting(topic, side, user=None, minutes_ago=None):
    """A waiting conversation written straight into the database, for states the pairing rule never produces by itself
    (for example waiters on both sides of one topic). Returns (conversation, user)."""
    import secrets

    from forum.models import Conversation, Participant

    user = user or make_user()
    conv = Conversation.objects.create(topic=topic, status="open", label_seed=secrets.randbits(62))
    Participant.objects.create(conversation=conv, user=user, label="A", join_order=1, side=side)
    if minutes_ago is not None:
        Conversation.objects.filter(pk=conv.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
    return conv, user
