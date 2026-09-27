"""Helpers for the step 13 tests (tests/replay/): transcript factories, adapters onto the replay library, the command runner.

Not a test module and not a conftest: every test file imports it by name. Imports of project code happen inside functions,
so a missing module fails the test that needs it, not collection.

The library's public names are pinned by docs/step13_brief.md (`load_experiment`, `plan_runs`, `execute_runs`, `factors`,
`ReplayReport`); the shapes of `ExperimentPlan`, `RunSpec` and `ReplayReport` are not, so every access to an attribute of
those goes through ONE function here (the "adapters" block), and the tests read the database for everything else.
"""
import json
import re
from decimal import Decimal
from io import StringIO
from pathlib import Path

DUMMY_KEY = "replay-tests-dummy-key"  # secret-scan: allow
GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden" / "transcripts"

NO_ISSUE = {"issues": [], "discussion_map": {"agreements": [], "disagreements": []}}


# --- Transcript factories ----------------------------------------------------------------------------------------------

def text_of(tag, seq, extra=""):
    """A unique, plain message text (the tag makes every transcript's texts unique)."""
    return f"[{tag}] message {seq} about the topic, stated plainly and civilly. {extra}".strip()


def message(tag, seq, author, *, text=None, planted=None):
    return {
        "seq": seq, "author": author, "text": text if text is not None else text_of(tag, seq),
        "planted": planted or [],
    }  # fmt: skip


def transcript(tid, *, authors="ABAB", trigger_seq=None, pair_id=None, variant=None, topic=None, messages=None,
               series=None, computed=None):
    """A transcript dict in the golden format. `authors` is a string of "A", "B" and "M" (a scripted moderator message);
    message i is by authors[i-1]. `topic` is (title, proposition) and defaults to a topic unique to this transcript."""
    if messages is None:
        messages = []
        for seq, who in enumerate(authors, start=1):
            author = "Moderator" if who == "M" else f"Participant {who}"
            messages.append(message(tid, seq, author))
    if trigger_seq is None:
        users = [m["seq"] for m in messages if "moderator" not in m["author"].lower()]
        trigger_seq = users[-1]
    title, proposition = topic or (f"Topic of {tid}", f"The proposition of {tid} should be adopted.")
    data = {
        "id": tid, "pair_id": pair_id, "variant": variant, "description": f"test transcript {tid}",
        "topic": {"title": title, "proposition": proposition},
        "stances": {"Participant A": "pro", "Participant B": "con"},
        "messages": messages, "trigger_seq": trigger_seq,
    }  # fmt: skip
    if series is not None:
        data["series"] = series
        data["computed"] = computed
    return data


def with_planted(data, seq, phrase="the planted phrase", intensity=3):
    """Put a planted item (with the optional keys the golden files carry) on message `seq`, its text containing the phrase."""
    target = next(m for m in data["messages"] if m["seq"] == seq)
    target["text"] = f"{target['text']} It says {phrase}."
    target["planted"] = [
        {"dimension": "factual_accuracy", "phrase": phrase, "intensity": intensity,
         "correction": "the correct fact", "evidence": "a source"}
    ]  # fmt: skip
    return data


def write_dir(folder, transcripts):
    """Write each transcript as <id>.json into `folder` (created) and return the folder path."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for data in transcripts:
        (folder / f"{data['id']}.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def as_arg(transcripts):
    """What `load_experiment` takes as its `transcripts` argument, built from a list of transcript dicts."""
    return {data["id"]: data for data in transcripts}


def pair(tid, **kwargs):
    """Two matched transcripts `<tid>_left` and `<tid>_right` sharing a pair_id and a topic, opposite stances."""
    topic = (f"Topic of {tid}", f"The proposition of {tid} should be adopted.")
    left = transcript(f"{tid}_left", pair_id=tid, variant="left", topic=topic, **kwargs)
    right = transcript(f"{tid}_right", pair_id=tid, variant="right", topic=topic, **kwargs)
    return [left, right]


def load_golden():
    """{id: transcript dict} of every golden transcript, read straight from the files."""
    return {json.loads(p.read_text(encoding="utf-8"))["id"]: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(GOLDEN_DIR.glob("*.json"))}  # fmt: skip


# --- Adapters onto the library (the only place that knows attribute names) ---------------------------------------------

def load(name, transcripts, **kwargs):
    """replay.load_experiment(name, <transcripts>, **kwargs) for a list of transcript dicts; returns the ExperimentPlan."""
    from moderation import replay

    return replay.load_experiment(name, as_arg(transcripts), **kwargs)


def plan(experiment_plan, **kwargs):
    from moderation import replay

    return replay.plan_runs(experiment_plan, **kwargs)


def execute(specs, **kwargs):
    from moderation import replay

    return replay.execute_runs(specs, **kwargs)


def spec_conversation(spec):
    return spec.conversation


def spec_trigger(spec):
    return spec.trigger


def spec_replicate(spec):
    return spec.replicate


def spec_snapshot_seq(spec):
    return spec.snapshot_seq


def spec_assignment(spec):
    """"as-is" or "swapped", read from the conversation's idempotency key (pinned: "<id>" or "<id>:swapped")."""
    return "swapped" if spec_conversation(spec).transcript_id.endswith(":swapped") else "as-is"


def spec_key(spec):
    """(conversation pk, trigger message pk, replicate) of a RunSpec."""
    return (spec_conversation(spec).pk, spec_trigger(spec).pk, spec_replicate(spec))


def replicates_by_conversation(specs):
    """{conversation pk: [replicate numbers]} of a list of RunSpecs."""
    grouped = {}
    for spec in specs:
        grouped.setdefault(spec_conversation(spec).pk, []).append(spec_replicate(spec))
    return grouped


def report_counts(report):
    """{status: n} of a ReplayReport (statuses of the runs the call executed or skipped)."""
    return dict(report.counts)


def report_total_cost(report):
    return Decimal(str(report.total_cost))


def report_cost_by_transcript(report):
    return {key: Decimal(str(value)) for key, value in report.cost_by_transcript.items()}


def report_not_run(report):
    return list(report.not_run)


def report_text(report):
    return str(report.summary())


# --- The database, as the tests look at it --------------------------------------------------------------------------------

def experiment(name):
    from forum.models import Experiment

    return Experiment.objects.get(name=name)


def conversations(exp):
    from forum.models import Conversation

    return list(Conversation.objects.filter(experiment=exp).order_by("pk"))


def messages_of(conv):
    return list(conv.messages.order_by("seq_no"))


def participant_label_of(msg):
    return None if msg.participant_id is None else msg.participant.label


def authors_of(conv):
    """[label or None] of a conversation's messages in order (None for a moderator message)."""
    return [participant_label_of(m) for m in messages_of(conv)]


def file_labels(data):
    """[label or None] of a transcript's messages: "Participant A" -> "A", a moderator -> None."""
    return [None if "moderator" in m["author"].lower() else m["author"].split()[-1] for m in data["messages"]]


def flipped(labels):
    return [{"A": "B", "B": "A"}.get(label) for label in labels]


def conv_for(exp, data, *, swapped=False):
    """The one conversation of `exp` made from transcript `data` under the given assignment, found by the idempotency key
    the architect pinned: Conversation.transcript_id is the golden id for "as-is" and "<id>:swapped" for "swapped"."""
    from forum.models import Conversation

    return Conversation.objects.get(experiment=exp, transcript_id=data["id"] + (":swapped" if swapped else ""))


def table_counts():
    """Row counts of every table the replay command may write, for "nothing was written" checks."""
    from forum.models import Conversation, Experiment, Message, Participant, Topic
    from moderation.models import Issue, LLMCall, ModerationRun

    return {
        model.__name__: model.objects.count()
        for model in (Experiment, Conversation, Participant, Message, Topic, ModerationRun, Issue, LLMCall)
    }


def replay_runs(exp=None):
    from moderation.models import ModerationRun

    rows = ModerationRun.objects.filter(kind="replay")
    if exp is not None:
        rows = rows.filter(conversation__experiment=exp)
    return list(rows.order_by("pk"))


def ledger():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk"))


def replay_spend():
    from moderation import budget

    return budget.spend(purposes=("replay",))


# --- Scripted FakeLLM answers ----------------------------------------------------------------------------------------------

def no_issue_script(n, *, input_tokens=None):
    """n plain Master answers with no issue (so each run makes exactly one call). With `input_tokens`, every call reports
    that many input tokens, which prices it at (input_tokens x the model's input price)."""
    from moderation.fake_llm import make_message

    if input_tokens is None:
        return [dict(NO_ISSUE) for _ in range(n)]

    def item(kwargs):
        return make_message(dict(NO_ISSUE), input_tokens=input_tokens)

    return [item for _ in range(n)]


def issue_master_answer(spec, quote):
    """A Master answer with one valid unsupported_claim issue on the spec's trigger message (`quote` must be in its text)."""
    return {
        "issues": [
            {
                "id": "i1", "message_id": spec_trigger(spec).pk, "issue_type": "unsupported_claim", "quote": quote,
                "explanation": "The claim is stated without support.", "confidence": 0.8, "intensity": None,
            }
        ],
        "discussion_map": {"agreements": [], "disagreements": []},
    }  # fmt: skip


def intervene_answer(spec):
    """An Intervenor answer that acts on issue i1 with one clean act."""
    return {
        "decision": "intervene", "rationale": "A source would help both readers.",
        "issue_dispositions": [{"issue_id": "i1", "disposition": "acted", "reason": "It matters."}],
        "acts": [
            {
                "type": "request_information", "addressee": "all", "subject": "none", "source_issue_ids": ["i1"],
                "source_message_ids": [spec_trigger(spec).pk], "tone": "neutral",
                "text": "Could a source be given for the claim in the newest message?",
            }
        ],
    }  # fmt: skip


def seed_spend(cost, *, purpose="judge"):
    """Insert an ok ledger row costing `cost` dollars (for "the budget already spent" set-ups)."""
    from moderation.models import LLMCall

    return LLMCall.objects.create(
        purpose=purpose, run_id=None, conversation_id=None, agent="master", attempt=1, model="claude-sonnet-5",
        prompt_version="seed", prompt_sha256="0" * 64, temperature=None, max_tokens=100, request={}, raw_response="",
        parsed=None, tokens_in=0, tokens_out=0, cache_write_tokens=0, cache_read_tokens=0,
        reserved_usd=Decimal(str(cost)), cost_usd=Decimal(str(cost)), latency_ms=0, stop_reason="",
        provider_request_id="", status="ok", error="", error_code="",
    )  # fmt: skip


def human_live_run():
    """A pending LIVE run on a message of a real (human) conversation; returns the run."""
    from django.contrib.auth import get_user_model

    from forum.models import Conversation, Message, Participant, Topic
    from moderation.models import ModerationRun

    topic = Topic.objects.create(title="a live topic", description="d", proposition="A live proposition.")
    conv = Conversation.objects.create(topic=topic, source="human")
    users = [get_user_model().objects.create_user(username=f"liveuser{i}", email=f"liveuser{i}@example.org") for i in (1, 2)]
    parts = [Participant.objects.create(conversation=conv, user=u, label=lab, join_order=i) for i, (u, lab) in enumerate(zip(users, "AB"), 1)]
    msg = Message.objects.create(conversation=conv, author_type="user", participant=parts[0], content="A live message.")
    return ModerationRun.objects.create(conversation=conv, trigger_message=msg, snapshot_seq=msg.seq_no, kind="live")


# --- Running the command -------------------------------------------------------------------------------------------------------

class CommandRun:
    def __init__(self, out, err, exc):
        self.out, self.err, self.exc = out, err, exc

    @property
    def text(self):
        return "\n".join(part for part in (self.out, self.err, str(self.exc) if self.exc else "") if part)


def run_command(*args):
    """manage.py replay, in process. A CommandError is captured (self.exc); any other exception propagates."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    out, err = StringIO(), StringIO()
    exc = None
    try:
        call_command("replay", *args, stdout=out, stderr=err)
    except CommandError as caught:
        assert "Unknown command" not in str(caught), f"manage.py replay does not exist: {caught}"
        exc = caught
    return CommandRun(out.getvalue(), err.getvalue(), exc)


def integers_in(text):
    return [int(m) for m in re.findall(r"(?<![\d.$])\d+(?![\d.])", text)]


def dollars_in(text):
    return [Decimal(m) for m in re.findall(r"\$\s?(\d+(?:\.\d+)?)", text)]
