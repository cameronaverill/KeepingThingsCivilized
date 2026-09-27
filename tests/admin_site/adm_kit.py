"""Shared helpers for the step 9c tests (tests/admin_site/). Nothing here imports from a conftest; test modules import what
they need by name. Models are imported lazily so a missing module fails a test, not collection.

Every string that a test looks for is unique and recognisable (MARK_*), so a leak or a truncation is easy to see."""
import itertools
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"

# The models the contract puts in the admin: (app_label, model_name).
CONTRACT_MODELS = [
    ("accounts", "user"),
    ("moderation", "moderationrun"),
    ("moderation", "llmcall"),
    ("moderation", "issue"),
    ("moderation", "issuedisposition"),
    ("moderation", "interventionact"),
    ("moderation", "guardstate"),
    ("forum", "topic"),
    ("forum", "conversation"),
    ("forum", "message"),
    ("forum", "participant"),
]
# The contract's read-only models (no add, change or delete for anyone, superusers included).
READ_ONLY_MODELS = [
    ("moderation", "moderationrun"),
    ("moderation", "llmcall"),
    ("moderation", "issue"),
    ("moderation", "issuedisposition"),
    ("moderation", "interventionact"),
    ("moderation", "guardstate"),
    ("forum", "conversation"),
    ("forum", "message"),
    ("forum", "participant"),
]

# A stored password hash in Django's PBKDF2 format, made up so no hashing runs (hashing is slow). The salt and the hash
# are long recognisable strings; Django's own read-only widget masks all but the first six characters of each.
HASH_SALT = "SALTQZXW9174MARKERSALT"
HASH_DIGEST = "HASHQZXW9174MARKERDIGESTabcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH="
HASH_FULL = f"pbkdf2_sha256$1200000${HASH_SALT}${HASH_DIGEST}"

MARK_REQUEST_HEAD = "REQHEADMARK"
MARK_REQUEST_TAIL = "REQTAILMARK"
MARK_RAW_HEAD = "RAWHEADMARK"
MARK_RAW_TAIL = "RAWTAILMARK"
MARK_PARSED_HEAD = "PARSEDHEADMARK"
MARK_PARSED_TAIL = "PARSEDTAILMARK"

CONV_A_ID = 7001
CONV_B_ID = 8002

_counter = itertools.count(1)


def uniq(prefix="x"):
    return f"{prefix}{next(_counter)}"


# --- people and clients ---------------------------------------------------------------------------------------------

def make_user(name=None, email=None, hash_=None, **extra):
    """A plain user without hashing a password. `hash_` stores a made-up password hash string."""
    User = get_user_model()
    name = name or uniq("plainuser")
    user = User.objects.create_user(username=name, email=email or f"{name}@mailbox.example", **extra)
    if hash_:
        User.objects.filter(pk=user.pk).update(password=hash_)
        user.refresh_from_db()
    return user


def make_superuser(name=None):
    name = name or uniq("rootquill")
    return get_user_model().objects.create_superuser(username=name, email=f"{name}@mailbox.example", password=None)


def make_staff(perms=(), name=None, **extra):
    """A staff user (not a superuser) holding exactly the named permissions, each "app_label.codename"."""
    name = name or uniq("staffquill")
    user = get_user_model().objects.create_user(username=name, email=f"{name}@mailbox.example", is_staff=True, **extra)
    for label in perms:
        app_label, codename = label.split(".")
        user.user_permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))
    return user


def all_perms_of_apps(*apps):
    """Every permission of the given apps, as "app_label.codename" strings."""
    return [
        f"{p.content_type.app_label}.{p.codename}"
        for p in Permission.objects.filter(content_type__app_label__in=apps)
    ]


def client_for(user):
    client = Client()
    client.force_login(user, backend=MODEL_BACKEND)
    return client


# --- urls and reading pages -----------------------------------------------------------------------------------------

def url(app, model, kind, pk=None):
    """The admin URL: kind is changelist, add, change, delete or history."""
    name = f"admin:{app}_{model}_{kind}"
    return reverse(name, args=[pk] if pk is not None else [])


def listed_pks(response, app, model):
    """The primary keys of the rows on a changelist page, in page order (read from the change links)."""
    prefix = re.escape(url(app, model, "changelist"))
    return [int(pk) for pk in re.findall(rf'href="{prefix}(\d+)/change/', response.content.decode())]


def page(response):
    return response.content.decode()


def longest_run(text, char):
    """The length of the longest unbroken run of `char` in the text."""
    runs = re.findall(re.escape(char) + "+", text)
    return max((len(r) for r in runs), default=0)


def column_classes(html):
    """The set of column-<name> classes on a changelist page's header row."""
    return set(re.findall(r"column-([A-Za-z0-9_]+)", html))


def field_html(html, name):
    """The HTML of the change-page form row for one field (empty string when the page has none)."""
    match = re.search(rf'field-{re.escape(name)}"(.*?)(?=form-row|</fieldset>)', html, re.S)
    return match.group(1) if match else ""


def readonly_text(html, name):
    """The text of a read-only field's value on a change page, whitespace collapsed (empty when there is none)."""
    match = re.search(r'class="readonly">(.*?)</div>', field_html(html, name), re.S)
    return " ".join(match.group(1).split()) if match else ""


# --- the world ------------------------------------------------------------------------------------------------------

class World:
    """Realistic rows for every contract model. Two conversations with fixed ids (so an id search cannot match by
    substring): 7001 (human, active, two users, a moderator reply) and 8002 (synthetic, closed)."""

    def __init__(self):
        from forum.models import Conversation, Experiment, Message, Participant, Topic
        from moderation.models import (
            GuardState, InterventionAct, Issue, IssueDisposition, LLMCall, ModerationRun,
        )

        # --- users, with a made-up stored hash so a leak of it is detectable
        self.user_a = make_user("zelda_mox", "zelda_mox@leakcheck.example", hash_=HASH_FULL)
        self.user_b = make_user("quincy_ray", "quincy_ray@leakcheck.example", hash_=HASH_FULL + "b")
        self.user_a.email_verified_at = self.user_a.date_joined
        self.user_a.save()

        # --- conversation 7001: human, active
        self.topic = Topic.objects.create(proposition="Cats make better pets than dogs.", created_by=self.user_a)
        self.seeded_topic = Topic.objects.create(
            title="Trains", proposition="Trains should be free.", description="seeded", leans={"left": {"x": 1}}
        )
        self.experiment = Experiment.objects.create(name="obs-1", kind="observational")
        self.conv = Conversation.objects.create(
            id=CONV_A_ID, topic=self.topic, status="active", source="human", experiment=self.experiment
        )
        self.pa = Participant.objects.create(conversation=self.conv, user=self.user_a, label="A", join_order=1)
        self.pb = Participant.objects.create(conversation=self.conv, user=self.user_b, label="B", join_order=2)
        self.m1 = Message.objects.create(
            conversation=self.conv, author_type="user", participant=self.pa,
            content="Everyone knows cats are cleaner than dogs, it is a proven fact.",
        )
        self.mod = Message.objects.create(
            conversation=self.conv, author_type="moderator", in_reply_to=self.m1,
            content="Moderator note: could you say where that figure comes from?",
        )
        self.m3 = Message.objects.create(
            conversation=self.conv, author_type="user", participant=self.pb, in_reply_to=self.m1,
            content="That is nonsense and you are being ridiculous.",
        )

        # --- conversation 8002: synthetic, closed
        self.conv2 = Conversation.objects.create(
            id=CONV_B_ID, topic=self.seeded_topic, status="closed", source="synthetic", transcript_id="t-1"
        )
        self.qa = Participant.objects.create(conversation=self.conv2, label="A", join_order=1)
        self.qb = Participant.objects.create(conversation=self.conv2, label="B", join_order=2)
        self.conv2.ended_by = self.qb
        self.conv2.save()
        self.n1 = Message.objects.create(
            conversation=self.conv2, author_type="user", participant=self.qa,
            content="Free trains would pay for themselves within ten years.",
        )

        # --- runs
        self.run_done = ModerationRun.objects.create(
            conversation=self.conv, trigger_message=self.m1, snapshot_seq=self.m1.seq_no, kind="live",
            status="done", decision="intervene", rationale="RATIONALE-MARK done run", attempts=1,
            posted_message=self.mod,
        )
        self.run_failed = ModerationRun.objects.create(
            conversation=self.conv, trigger_message=self.m3, snapshot_seq=self.m3.seq_no, kind="live",
            status="failed", failure_reason="invalid_output", attempts=3, is_stale=True, error="ERROR-MARK boom",
        )
        self.run_replay = ModerationRun.objects.create(
            conversation=self.conv, trigger_message=self.m1, snapshot_seq=self.m1.seq_no, kind="replay",
            replay_of=self.run_done, replicate=2, status="done", decision="no_intervention",
        )
        self.run_other = ModerationRun.objects.create(
            conversation=self.conv2, trigger_message=self.n1, snapshot_seq=self.n1.seq_no, kind="live",
            status="pending",
        )
        self.runs = [self.run_done, self.run_failed, self.run_replay, self.run_other]

        # --- issues, dispositions, acts on the done run; one issue on the other run
        content = self.m1.content
        quote = "it is a proven fact"
        start = content.index(quote)
        self.issue_ok = Issue.objects.create(
            run=self.run_done, local_id="I-QZ71", message=self.m1, issue_type="possible_factual_error",
            quote=quote, quote_start=start, quote_end=start + len(quote), quote_match="exact",
            explanation="EXPLAIN-MARK no source is given", confidence=0.8, intensity=2,
        )
        self.issue_bad = Issue.objects.create(
            run=self.run_done, local_id="I-QZ72", message=self.m1, issue_type="unclear_statement",
            explanation="explain two", confidence=0.4, validity="rejected", rejection_reason="REJECT-MARK vague",
        )
        self.issue_other = Issue.objects.create(
            run=self.run_other, local_id="I-OTHER9", message=self.n1, issue_type="unsupported_claim",
            explanation="other run issue", confidence=0.5,
        )
        self.disposition = IssueDisposition.objects.create(
            issue=self.issue_ok, disposition="acted", reason="DISPOSITION-MARK asked for a source"
        )
        self.act_text = 'ACT-TEXT-MARK Could you tell us, "it is a proven fact", where the figure comes from exactly?'
        self.act = InterventionAct.objects.create(
            run=self.run_done, order=1, act_type="request_information", tone="neutral", text=self.act_text,
            addressee="A", subject="none",
        )
        self.act.source_issues.add(self.issue_ok)
        self.act.source_messages.add(self.m1)
        self.act_other = InterventionAct.objects.create(
            run=self.run_other, order=1, act_type="clarify_argument", tone="gentle", text="OTHER-ACT-TEXT hello?",
            addressee="all", subject="both",
        )

        # --- LLM calls: two on the done run, one on another run, one with no run
        self.call1 = LLMCall.objects.create(
            purpose="moderation", run_id=self.run_done.pk, conversation_id=self.conv.pk, agent="agent-zz1", attempt=3,
            model="model-zz1", max_tokens=1000, tokens_in=12345, tokens_out=6789, cost_usd="0.012345",
            status="ok", request={"head": MARK_REQUEST_HEAD, "long": "x" * 20000, "tail": MARK_REQUEST_TAIL},
            raw_response="q" * 20000 + MARK_RAW_TAIL, parsed={"head": MARK_PARSED_HEAD, "long": "y" * 20000, "tail": MARK_PARSED_TAIL},
        )
        self.call2 = LLMCall.objects.create(
            purpose="moderation", run_id=self.run_done.pk, conversation_id=self.conv.pk, agent="agent-zz2", attempt=4,
            model="model-zz2", max_tokens=1000, status="error", error="CALL-ERROR-MARK", error_code="overloaded",
            request={"head": "small"}, raw_response="",
        )
        self.call_other = LLMCall.objects.create(
            purpose="moderation", run_id=self.run_other.pk, conversation_id=self.conv2.pk, agent="agent-other",
            model="model-other", max_tokens=1000, status="ok", cost_usd="0.000100",
        )
        self.call_norun = LLMCall.objects.create(
            purpose="golden", agent="agent-norun", model="model-norun", max_tokens=1000, status="refused_budget",
        )
        self.calls = [self.call1, self.call2, self.call_other, self.call_norun]

        self.guard = GuardState.load()

    def rows(self, app, model):
        """All rows of a contract model, oldest first."""
        from django.apps import apps

        return list(apps.get_model(app, model).objects.order_by("pk"))
