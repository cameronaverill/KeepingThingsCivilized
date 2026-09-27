"""Helpers for the step 5b tests (tests/pipeline_run/): conversations, runs and scripted FakeLLM outputs.

Not a test module and not a conftest: every test file imports it by name. Model and pipeline imports happen inside
functions, so a missing module fails the test that needs it, not collection.

Everything a test needs to say about a scripted model answer is a plain dict shaped like `MasterOutput` /
`IntervenorOutput` (moderation/schemas.py); FakeLLM validates it. An unusable answer is a dict that does not fit the schema
(FakeLLM then reports "no parsed output") or a callable that raises pydantic's ValidationError (invalid JSON).
"""
import itertools
import json
import re
from types import SimpleNamespace

_counter = itertools.count(1)

CLEAN_TEXT = "Could a source be given for the claim in message 3?"
CLEAN_TEXT_2 = "The two messages disagree about whether landlords leave the market."
CLEAN_TEXT_3 = "Message 2 and message 3 cite no figures."

DEFAULT_SPECS = [
    ("A", "I think rent control reduces the supply of housing, which is a well known fact."),
    ("B", "The supply of homes barely changes when rents are capped, and tenants gain a lot from that."),
    ("A", "Landlords always leave the market when rent is capped, nobody disagrees on that."),
]
# Two phrases of the third message above, for issues that must be located exactly.
QUOTE_1 = "nobody disagrees on that"
QUOTE_2 = "Landlords always leave the market"

# A Master answer that does not fit the schema, and an Intervenor answer that does not fit it.
BAD_MASTER = {"issues": "this is not a list"}
BAD_INTERVENOR = {"decision": "maybe"}


def n():
    return next(_counter)


# --- Conversations ---------------------------------------------------------------------------------------------------

class World:
    """A conversation with its participants and messages; `w[3]` is the message with seq_no 3 (1-based)."""

    def __init__(self, conv, parts, msgs, topic, users=None):
        self.conv, self.parts, self.msgs, self.topic, self.users = conv, parts, msgs, topic, users or {}

    def __getitem__(self, seq):
        return self.msgs[seq - 1]

    @property
    def last(self):
        return self.msgs[-1]

    def add_user(self, label, text):
        return self._add("user", label, text)

    def add_moderator(self, text, in_reply_to=None):
        return self._add("moderator", None, text, in_reply_to)

    def _add(self, kind, label, text, in_reply_to=None):
        from forum.models import Message

        if kind == "user":
            message = Message.objects.create(
                conversation=self.conv, author_type="user", participant=self.parts[label], content=text
            )
        else:
            message = Message.objects.create(
                conversation=self.conv, author_type="moderator", participant=None, content=text, in_reply_to=in_reply_to
            )
        self.msgs.append(message)
        return message


def build(specs=None, *, human=False, names=None, proposition="Cities should cap how much landlords can raise rents each year."):
    """A conversation from `specs`, a list of (who, text) with who in "A", "B" or "mod" (a moderator message replying to the
    latest earlier user message). seq_no is the 1-based position. Synthetic (no users) unless `human`; a human conversation
    gets users named `names` ({"A": username, "B": username}, emails `<username>@example.org`)."""
    from forum.models import Conversation, Participant, Topic

    specs = DEFAULT_SPECS if specs is None else specs
    topic = Topic.objects.create(title=f"pipeline topic {n()}", description="d", proposition=proposition)
    conv = Conversation.objects.create(topic=topic, source="human" if human else "synthetic")
    parts, users = {}, {}
    for order, label in enumerate("AB", start=1):
        user = None
        if human:
            from django.contrib.auth import get_user_model

            username = (names or {}).get(label, f"pipeuser{label.lower()}{n()}")
            user = get_user_model().objects.create_user(username=username, email=f"{username}@example.org")
            users[label] = user
        parts[label] = Participant.objects.create(conversation=conv, label=label, join_order=order, user=user)
    world = World(conv, parts, [], topic, users)
    last_user = None
    for who, text in specs:
        if who == "mod":
            world.add_moderator(text, in_reply_to=last_user)
        else:
            last_user = world.add_user(who, text)
    return world


def new_run(trigger, kind="live", **kwargs):
    """A pending run on `trigger` (snapshot_seq = its seq_no)."""
    from moderation.models import ModerationRun

    values = dict(conversation=trigger.conversation, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind=kind)
    values.update(kwargs)
    return ModerationRun.objects.create(**values)


def reload(run):
    from moderation.models import ModerationRun

    return ModerationRun.objects.get(pk=run.pk)


def go(run):
    """Run the pipeline and return (the returned run, the run reloaded from the database)."""
    from moderation.pipeline import run_moderation

    returned = run_moderation(run)
    return returned, reload(run)


# --- Scripted answers ------------------------------------------------------------------------------------------------

def _mid(message):
    return message if isinstance(message, int) else message.pk


def issue_d(id, message, issue_type="unsupported_claim", quote=QUOTE_1, *, confidence=0.8, intensity=None,
            explanation="The claim is stated without support.", needs_verification=False):
    return {
        "id": id, "message_id": _mid(message), "issue_type": issue_type, "quote": quote,
        "explanation": explanation, "confidence": confidence, "intensity": intensity,
        "needs_verification": needs_verification,
    }  # fmt: skip


def master_d(*issues, agreements=(), disagreements=()):
    return {
        "issues": list(issues),
        "discussion_map": {"agreements": list(agreements), "disagreements": list(disagreements)},
    }


def disp_d(issue_id, disposition="acted", reason="It matters for the discussion."):
    return {"issue_id": issue_id, "disposition": disposition, "reason": reason}


def act_d(text=CLEAN_TEXT, type="request_information", addressee="all", subject="none", issues=(), messages=(),
          tone="neutral"):
    return {
        "type": type, "addressee": addressee, "subject": subject,
        "source_issue_ids": list(issues), "source_message_ids": [_mid(m) for m in messages],
        "tone": tone, "text": text,
    }  # fmt: skip


def interv_d(decision="intervene", rationale="A source would help both readers.", dispositions=(), acts=()):
    return {
        "decision": decision, "rationale": rationale,
        "issue_dispositions": list(dispositions), "acts": list(acts),
    }  # fmt: skip


def invalid_json(kwargs):
    """A scripted item: the SDK could not parse the text (invalid JSON), which raises pydantic's ValidationError."""
    from pydantic import TypeAdapter

    TypeAdapter(int).validate_json("{")


def reply_with(parsed, before=None):
    """A scripted callable item: runs `before(kwargs)` (a side effect or a probe), then answers with `parsed`."""
    from moderation.fake_llm import make_message

    def item(kwargs):
        if before is not None:
            before(kwargs)
        return make_message(parsed)

    return item


def simple_success(world, *, texts=(CLEAN_TEXT,), issue_id="i1"):
    """(script, ...) for a normal intervene run on the last message of `world`: one valid issue on the last message
    (needs QUOTE_1 in it, as in DEFAULT_SPECS), one act per text."""
    trigger = world.last
    master = master_d(issue_d(issue_id, trigger, "unsupported_claim", QUOTE_1))
    acts = [act_d(t, issues=[issue_id], messages=[trigger], addressee="A", subject="A") for t in texts]
    return [master, interv_d(dispositions=[disp_d(issue_id)], acts=acts)]


# --- Looking at what the model was sent --------------------------------------------------------------------------------

def calls_of(fake, agent):
    """The recorded calls whose output schema belongs to `agent` ("master" or "intervenor")."""
    name = {"master": "MasterOutput", "intervenor": "IntervenorOutput"}[agent]
    return [c for c in fake.calls if c["output_format"].__name__ == name]


def user_input(call):
    return call["messages"][0]["content"]


_MESSAGE_RE = re.compile(r'<message id="(\d+)" participant="([^"]*)">')


def rendered_messages(call):
    """[(message id, participant label)] of the transcript block in a call's user input, oldest first."""
    return [(int(i), label) for i, label in _MESSAGE_RE.findall(user_input(call))]


def block(text, tag):
    """The text between <tag> and </tag> (first occurrence), or None."""
    start, end = text.find(f"<{tag}>"), text.find(f"</{tag}>")
    if start == -1 or end == -1:
        return None
    return text[start + len(tag) + 2:end]


def whole_request_text(fake):
    """Everything sent to the model in every recorded call, as one string."""
    return json.dumps([{"system": c.get("system"), "messages": c.get("messages")} for c in fake.calls], default=str)


def walk(value, path=()):
    """Yield (path, leaf value) for every leaf of nested dicts and lists."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk(item, path + (str(key),))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from walk(item, path + (str(index),))
    else:
        yield path, value


def paths_of(value, needle):
    """The key paths (joined, lower case) of every leaf of `value` equal to `needle`."""
    return [".".join(path).lower() for path, leaf in walk(value) if leaf == needle]


def issues_of(run):
    return list(run.issues.order_by("pk"))


def acts_of(run):
    return list(run.acts.order_by("order"))


def moderator_messages(conv):
    from forum.models import Message

    return list(Message.objects.filter(conversation=conv, author_type="moderator").order_by("seq_no"))


def message_count(conv):
    from forum.models import Message

    return Message.objects.filter(conversation=conv).count()


def ledger(run=None):
    from moderation.models import LLMCall

    rows = LLMCall.objects.all() if run is None else LLMCall.objects.filter(run_id=run.pk)
    return list(rows.order_by("pk"))


def other_world_message():
    """A user message of some other conversation (for ids that exist but belong elsewhere)."""
    return build([("A", "A message in another conversation about parking."), ("B", "Another one.")])[1]


def issue_summary(run):
    """[(local_id, validity, rejection_reason)] of a run's issues, oldest first."""
    return [(i.local_id, i.validity, i.rejection_reason) for i in issues_of(run)]


def act_summary(run):
    """[(order, validity, rejection_reason)] of a run's acts."""
    return [(a.order, a.validity, a.rejection_reason) for a in acts_of(run)]


TERMINAL = {"done", "failed", "skipped_budget", "skipped_disabled"}

# Texts that name a participant or use a viewer-relative reference (label_check must flag them).
NAMING_TEXTS = [
    "Participant B, please cite a source.",
    "Participants A and B should keep to the topic.",
    "A's claim is unsupported.",
    "B's messages are short.",
    "The other participant raised a fair point.",
    "Please respond to the other person.",
    "Another participant already answered.",
]

Ns = SimpleNamespace
