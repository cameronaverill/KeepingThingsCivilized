"""Helpers for the step 14a tests (tests/llm_raters/): conversations, raters, panels, scripted FakeLLM answers, adapters onto
the rater library and the command runner.

Not a test module and not a conftest: every test file imports it by name. Imports of project code happen inside functions,
so a missing module fails the test that needs it, not collection.

`docs/step14_brief.md` pins the names `rate_target`, `run_panel`, `load_rater_prompt`, `RaterFinding`, `RaterOutput`,
`RateResult.rejected` and the two commands; it does not pin the shapes of `RateResult`, `RunReport` or the rejected items.
Every access to an attribute of those goes through ONE function here (the "adapters" block), so a change of reading is a
one-line change; the tests read the database for everything else.
"""
import hashlib
import itertools
import json
import re
from decimal import Decimal
from io import StringIO
from pathlib import Path

DUMMY_KEY = "llm-raters-tests-dummy-key"  # secret-scan: allow
ROOT = Path(__file__).resolve().parents[2]
RUBRICS_DIR = ROOT / "rubrics"
DIMENSIONS = ("factual_accuracy", "abusiveness")
SONNET, HAIKU = "claude-sonnet-5", "claude-haiku-4-5"
_counter = itertools.count(1)


def n():
    return next(_counter)


def sha256_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- Conversations -------------------------------------------------------------------------------------------------------

class World:
    """A conversation with its participants and messages; `w[3]` is the message with seq_no 3 (1-based)."""

    def __init__(self, conv, parts, topic, users):
        self.conv, self.parts, self.topic, self.users, self.msgs = conv, parts, topic, users, []

    def __getitem__(self, seq):
        return self.msgs[seq - 1]

    @property
    def user_messages(self):
        return [m for m in self.msgs if m.author_type == "user"]

    def add_user(self, label, text):
        from forum.models import Message

        message = Message.objects.create(
            conversation=self.conv, author_type="user", participant=self.parts[label], content=text
        )
        self.msgs.append(message)
        return message

    def add_moderator(self, text, in_reply_to=None):
        from forum.models import Message

        message = Message.objects.create(
            conversation=self.conv, author_type="moderator", participant=None, content=text, in_reply_to=in_reply_to
        )
        self.msgs.append(message)
        return message


def build(specs, *, source=None, proposition="Cities should plant more street trees.", topic=None,
          experiment=None, usernames=None, title="", description="", leans=None, pair_id="", variant=""):
    """A conversation from `specs`, a list of (who, text) with who in "A", "B" or "mod" (a moderator message replying to the
    latest earlier user message). seq_no is the 1-based position. Real users are created (named by `usernames`,
    {"A": ..., "B": ...}, emails `<username>@mail.example`) only when `usernames` is given, and then the conversation is a
    human one (a synthetic conversation has no users); without `usernames` it is synthetic unless `source` says otherwise."""
    from forum.models import Conversation, Participant, Topic

    if topic is None:
        topic = Topic.objects.create(
            title=title, description=description, proposition=proposition, leans=leans or {}
        )
    source = source or ("human" if usernames else "synthetic")
    conv = Conversation.objects.create(
        topic=topic, source=source, experiment=experiment, pair_id=pair_id, variant=variant
    )
    parts, users = {}, {}
    for order, label in enumerate("AB", start=1):
        user = None
        if usernames:
            from django.contrib.auth import get_user_model

            user = get_user_model().objects.create_user(
                username=usernames[label], email=f"{usernames[label]}@mail.example"
            )
            users[label] = user
        parts[label] = Participant.objects.create(conversation=conv, label=label, join_order=order, user=user)
    world = World(conv, parts, topic, users)
    last_user = None
    for who, text in specs:
        if who == "mod":
            world.add_moderator(text, in_reply_to=last_user)
        else:
            last_user = world.add_user(who, text)
    return world


def single(text, *, topic=None, **kwargs):
    """A world whose only message is `text` (by A); returns (world, message)."""
    world = build([("A", text)], topic=topic, **kwargs)
    return world, world[1]


def shared_topic(proposition="Cities should plant more street trees."):
    from forum.models import Topic

    return Topic.objects.create(title="", description="", proposition=proposition)


def experiment(name, kind="replay"):
    from forum.models import Experiment

    return Experiment.objects.create(name=name, kind=kind, description="test experiment", config={})


def act_on(message, text):
    """An InterventionAct with `text`, on a fresh replay-free live run triggered by `message`."""
    from moderation.models import InterventionAct, ModerationRun

    run = ModerationRun.objects.create(
        conversation=message.conversation, trigger_message=message, snapshot_seq=message.seq_no, kind="live"
    )
    return InterventionAct.objects.create(
        run=run, order=1, act_type="request_information", tone="neutral", text=text, addressee="A", subject="none"
    )


# --- Raters and panels -----------------------------------------------------------------------------------------------------

def make_rater(name, model=SONNET, *, active=True):
    from evaluation.models import Rater

    return Rater.objects.create(name=name, kind="llm", provider="anthropic", model=model, temperature=0.0, active=active)


def make_human(name=None):
    from django.contrib.auth import get_user_model

    from evaluation.models import Rater

    name = name or f"human{n()}"
    user = get_user_model().objects.create_user(username=f"user{name}", email=f"user{name}@mail.example")
    return Rater.objects.create(name=name, kind="human", user=user)


def panel_dimensions(dimensions=DIMENSIONS, rubrics_dir=RUBRICS_DIR):
    return {
        d: {"rubric": f"{d}_v1", "sha256": sha256_of(Path(rubrics_dir) / f"{d}_v1.md")} for d in dimensions
    }


def make_panel(raters, *, name="panel", version="1", dimensions=DIMENSIONS, **extra):
    from evaluation.models import Panel

    panel = Panel.objects.create(name=name, version=version, dimensions=panel_dimensions(dimensions), **extra)
    panel.raters.add(*raters)
    return panel


def two_raters():
    """Two active LLM raters. Created in the reverse of their name order, so "by rater name" differs from creation order:
    `first` ("rater-a", Haiku) sorts before `second` ("rater-b", Sonnet)."""
    second = make_rater("rater-b", SONNET)
    first = make_rater("rater-a", HAIKU)
    return first, second


# --- Scripted FakeLLM answers ---------------------------------------------------------------------------------------------

def finding(local_id, dimension, quote, intensity=2, *, reason=None, confidence=0.9, detail=None):
    """One RaterFinding as a plain dict. `intensity=None` needs a `reason` to be a valid finding."""
    return {
        "local_id": local_id, "dimension": dimension, "quote": quote, "intensity": intensity,
        "not_scorable_reason": reason, "confidence": confidence,
        "detail": detail if detail is not None else {"claim": "a claim", "correct_fact": "a fact"},
    }  # fmt: skip


def answer(*findings, no_issues=()):
    return {"findings": list(findings), "no_issues_in": list(no_issues)}


def nothing(dimensions=DIMENSIONS):
    """A valid answer with no finding."""
    return answer(no_issues=list(dimensions))


def priced(payload, *, input_tokens=1000, output_tokens=100):
    """A script item that answers `payload` and reports these token counts (so it has a known price)."""
    from moderation.fake_llm import make_message

    def item(kwargs):
        return make_message(payload, input_tokens=input_tokens, output_tokens=output_tokens)

    return item


# An answer that does not fit the schema: FakeLLM reports "no parsed output", which the gateway raises as LLMOutputError.
BAD_ANSWER = {"findings": "this is not a list", "no_issues_in": []}


# --- The database, as the tests look at it --------------------------------------------------------------------------------

def ledger():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk"))


def judge_spend():
    from moderation import budget

    return budget.spend(purposes=("judge",))


def ratings(**filters):
    from evaluation.models import Rating

    return list(Rating.objects.filter(**filters).order_by("pk"))


def findings_of(rating):
    return list(rating.findings.order_by("pk"))


def all_findings():
    from evaluation.models import Finding

    return list(Finding.objects.order_by("pk"))


def consensus_rows(**filters):
    from evaluation.models import ConsensusFinding

    return list(ConsensusFinding.objects.filter(**filters).order_by("pk"))


def make_done_rating(rater, target, dimensions=DIMENSIONS, found=(), status="done", replicate=1):
    """A finished rating by `rater` (used for a human rater) with `found` = [(start, end, dimension, intensity), ...]."""
    from evaluation.models import Finding, Rating

    from evaluation.targets import target_ref, target_text

    target_type, target_id = target_ref(target)
    rating = Rating.objects.create(
        rater=rater, target_type=target_type, target_id=target_id, dimensions=list(dimensions), status=status,
        replicate=replicate,
    )
    text = target_text(target)
    for index, (start, end, dimension, intensity) in enumerate(found, start=1):
        Finding.objects.create(
            rating=rating, local_id=f"h{index}", dimension=dimension, start=start, end=end, quote=text[start:end],
            intensity=intensity,
        )
    return rating


def table_counts():
    """Row counts of every table the rater commands may write, for "nothing was written" checks."""
    from evaluation.models import ConsensusFinding, Finding, IssueFindingLink, Panel, Rater, Rating
    from moderation.models import LLMCall

    return {
        model.__name__: model.objects.count()
        for model in (Rater, Panel, Rating, Finding, ConsensusFinding, IssueFindingLink, LLMCall)
    }


# --- Adapters onto the library (the only place that knows attribute names) ----------------------------------------------

def rate(rater, target, **kwargs):
    """evaluation.llm_rater.rate_target(...); returns whatever it returns (a Rating or a RateResult)."""
    from evaluation import llm_rater

    return llm_rater.rate_target(rater, target, **kwargs)


def rating_of(result):
    """The stored Rating out of what `rate_target` returned: the value itself, or its `.rating`."""
    return getattr(result, "rating", result)


def rejected_of(result):
    """[(local_id, reason), ...] of the findings that were not stored, whatever the shape of the items (a dict, a tuple, an
    object)."""
    pairs = []
    for item in result.rejected:
        if isinstance(item, dict):
            pairs.append((item["local_id"], item["reason"]))
        elif isinstance(item, (tuple, list)):
            pairs.append((item[0], item[1]))
        else:
            pairs.append((item.local_id, item.reason))
    return pairs


def run_panel(panel, targets, **kwargs):
    from evaluation import llm_rater

    kwargs.setdefault("max_usd", Decimal("100"))
    return llm_rater.run_panel(panel, targets, **kwargs)


def report_counts(report):
    """{status: n} of a RunReport: "done", "failed", "skipped_existing" and "not_run" (the shape is the builder's)."""
    return dict(report.counts)


def report_total_cost(report):
    return Decimal(str(report.cost_total))


def report_cost_by_rater(report):
    return {key: Decimal(str(value)) for key, value in report.cost_by_rater.items()}


def report_not_run(report):
    return list(report.not_run)


def report_not_run_reasons(report):
    """[reason, ...] of the not-run entries, in order (each entry is a dict with a "reason" key)."""
    return [entry["reason"] for entry in report.not_run]


def report_rejected(report):
    """The number of findings the runner dropped, in total."""
    return report.rejected_total


def report_text(report):
    return str(report)


def load_prompt(dimensions=None, **kwargs):
    from evaluation import llm_rater

    return llm_rater.load_rater_prompt(list(DIMENSIONS if dimensions is None else dimensions), **kwargs)


# --- Reading what the model was sent ------------------------------------------------------------------------------------------

def system_text(call):
    """The system prompt of one recorded `messages.parse` call as a string, whether it was sent as text or as blocks."""
    system = call["system"]
    return system if isinstance(system, str) else "".join(block["text"] for block in system)


def user_text(call):
    """Everything the model was sent as user turns, joined."""
    parts = []
    for message in call["messages"]:
        content = message["content"]
        parts.append(content if isinstance(content, str) else "".join(block.get("text", "") for block in content))
    return "\n".join(parts)


def whole_request(call):
    """The serialized request (system and messages): what a forbidden string must be absent from."""
    return json.dumps({"system": call["system"], "messages": call["messages"]}, sort_keys=True)


# --- Running the commands -------------------------------------------------------------------------------------------------------

class CommandRun:
    def __init__(self, out, err, exc):
        self.out, self.err, self.exc = out, err, exc

    @property
    def text(self):
        return "\n".join(part for part in (self.out, self.err, str(self.exc) if self.exc else "") if part)


def run_command(name, *args):
    """manage.py <name>, in process. A CommandError is captured (self.exc); any other exception propagates."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    out, err = StringIO(), StringIO()
    exc = None
    try:
        call_command(name, *args, stdout=out, stderr=err)
    except CommandError as caught:
        assert "Unknown command" not in str(caught), f"manage.py {name} does not exist: {caught}"
        exc = caught
    return CommandRun(out.getvalue(), err.getvalue(), exc)


def run_raters(*args):
    return run_command("run_raters", *args)


def seed_panel(*args):
    return run_command("seed_panel", *args)


def integers_in(text):
    return [int(m) for m in re.findall(r"(?<![\d.$])\d+(?!\d|\.\d)", text)]


def dollars_in(text):
    return [Decimal(m) for m in re.findall(r"\$\s?(\d+(?:\.\d+)?)", text)]


def seed_spend(cost, *, purpose="judge"):
    """Insert an ok ledger row costing `cost` dollars (for "the budget already spent" set-ups)."""
    from moderation.models import LLMCall

    return LLMCall.objects.create(
        purpose=purpose, run_id=None, conversation_id=None, agent="rater", attempt=1, model=SONNET,
        prompt_version="seed", prompt_sha256="0" * 64, temperature=None, max_tokens=100, request={}, raw_response="",
        parsed=None, tokens_in=0, tokens_out=0, cache_write_tokens=0, cache_read_tokens=0,
        reserved_usd=Decimal(str(cost)), cost_usd=Decimal(str(cost)), latency_ms=0, stop_reason="",
        provider_request_id="", status="ok", error="", error_code="",
    )  # fmt: skip


# --- Rubric folders, pricing probes and odd answers --------------------------------------------------------------------------

def write_rubrics(folder, **texts):
    """Write rubric files into `folder` (created): each keyword is a file stem ("factual_accuracy_v1") and its text."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for stem, text in texts.items():
        (folder / f"{stem}.md").write_text(text, encoding="utf-8")
    return folder


def copy_real_rubrics(folder):
    """Copy the two real rubric files (byte for byte) into `folder`; returns it."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for dimension in DIMENSIONS:
        (folder / f"{dimension}_v1.md").write_bytes((RUBRICS_DIR / f"{dimension}_v1.md").read_bytes())
    return folder


def invalid_json_answer(kwargs):
    """A script item that fails the way the real SDK does on invalid JSON: pydantic's ValidationError from inside parse()."""
    from evaluation.schemas import RaterOutput

    return RaterOutput.model_validate_json("{this is not json")


def identical_targets(count, text, *, topic=None):
    """`count` user messages with the same text, each the first message of its own conversation on one topic, so that a
    rating call for any of them is priced identically (same instructions, same input, same model)."""
    topic = topic or shared_topic()
    return [single(text, topic=topic)[1] for _ in range(count)]


def probe_costs(text, model=SONNET, *, input_tokens=1000, output_tokens=100):
    """Rate one message with `text` through a panel of one rater of `model`, so the ledger holds one call priced with
    `input_tokens`/`output_tokens`; returns (reserved, cost) of that call: the worst-case estimate a call of this shape
    has and what it really cost. The caller must have installed a FakeLLM whose FIRST script item is the probe's answer."""
    probe_rater = make_rater(f"probe-{n()}", model)
    probe_panel = make_panel([probe_rater], name=f"probe-panel-{n()}")
    (target,) = identical_targets(1, text)
    run_panel(probe_panel, [target])
    row = ledger()[-1]
    return row.reserved_usd, row.cost_usd
