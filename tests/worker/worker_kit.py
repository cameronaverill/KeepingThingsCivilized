"""Helpers for the step 8 tests (tests/worker/): conversations, runs, a fake pipeline, a fake sleep and a trace of what the
worker did. Not a test module and not a conftest: every test file imports it by name. Imports of the code under test happen
inside functions, so a missing module fails the tests that need it, not collection.
"""
import itertools
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

_counter = itertools.count(1)

T0 = datetime(2026, 5, 4, 12, 0, 0, tzinfo=dt_timezone.utc)

QUOTE_A = "a well known fact"
TEXT_A = "I think rent control reduces the supply of housing, which is a well known fact."
QUOTE_B = "nobody disagrees on that"
TEXT_B = "Landlords always leave the market when rent is capped, nobody disagrees on that."
ACT_1 = "Could a source be given for the claim about housing supply?"
ACT_2 = "Could the claim about landlords leaving the market be supported with a figure?"

NON_CLAIMABLE = ["running", "done", "failed", "skipped_budget", "skipped_disabled"]


def n():
    return next(_counter)


class FrozenClock:
    """A callable standing in for django.utils.timezone.now (also for time.monotonic and time.time when used that way)."""

    def __init__(self, start=T0):
        self.current = start

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current = self.current + timedelta(seconds=seconds)
        return self.current

    def monotonic(self):
        return (self.current - T0).total_seconds() + 1_000_000.0

    def epoch(self):
        return self.current.timestamp()


def long_ago():
    """An hour before the real current time (a claimed_at that is stuck for any sane RUN_TIMEOUT_SECONDS)."""
    from django.utils import timezone

    return timezone.now() - timedelta(hours=1)


def at(seconds):
    """T0 plus `seconds`."""
    return T0 + timedelta(seconds=seconds)


# --- Conversations and runs ------------------------------------------------------------------------------------------------

class World:
    """A synthetic conversation with `count` user messages (labels A, B alternating); `w[1]` is the seq_no 1 message."""

    def __init__(self, conv, parts, msgs):
        self.conv, self.parts, self.msgs = conv, parts, msgs

    def __getitem__(self, seq):
        return self.msgs[seq - 1]

    def add_user(self, text="another user message"):
        from forum.models import Message

        label = "AB"[len(self.msgs) % 2]
        message = Message.objects.create(
            conversation=self.conv, author_type="user", participant=self.parts[label], content=text
        )
        self.msgs.append(message)
        return message


def world(count=1, texts=None):
    from forum.models import Conversation, Message, Participant, Topic

    topic = Topic.objects.create(
        title=f"worker topic {n()}", description="d", proposition="Cities should cap how much landlords can raise rents."
    )
    conv = Conversation.objects.create(topic=topic, source="synthetic")
    parts = {
        label: Participant.objects.create(conversation=conv, label=label, join_order=order)
        for order, label in enumerate("AB", start=1)
    }
    msgs = []
    for i in range(count):
        content = texts[i] if texts else f"user message number {i + 1} about housing"
        msgs.append(
            Message.objects.create(
                conversation=conv, author_type="user", participant=parts["AB"[i % 2]], content=content
            )
        )
    return World(conv, parts, msgs)


def run_on(message, **fields):
    """A run on `message` (default: live, pending). Any field can be overridden."""
    from moderation.models import ModerationRun

    values = dict(conversation=message.conversation, trigger_message=message, snapshot_seq=message.seq_no)
    values.update(fields)
    return ModerationRun.objects.create(**values)


def solo(created_at=None, **fields):
    """A run alone in a brand new conversation (its own single user message)."""
    w = world(1)
    if created_at is not None:
        fields["created_at"] = created_at
    return run_on(w[1], **fields)


def solos(created_ats, **fields):
    """One solo run per entry of `created_ats`, in that order (so pk order follows the list)."""
    return [solo(created_at=when, **fields) for when in created_ats]


def reload(run):
    from moderation.models import ModerationRun

    return ModerationRun.objects.get(pk=run.pk)


def row(run):
    """Every stored field of a run as a dict (for 'nothing else changed' comparisons)."""
    from moderation.models import ModerationRun

    return ModerationRun.objects.filter(pk=run.pk).values().get()


def rows(runs):
    return [row(r) for r in runs]


def status_of(run):
    return reload(run).status


def moderator_messages(conv):
    from forum.models import Message

    return list(Message.objects.filter(conversation=conv, author_type="moderator").order_by("seq_no"))


def drain_claims(now=None, limit=50):
    """Call claim_next_run until it returns None; returns the claimed runs in order (each stays running)."""
    from moderation import worker

    claimed = []
    for _ in range(limit):
        run = worker.claim_next_run(now=now)
        if run is None:
            break
        claimed.append(run)
    return claimed


# --- Fake pipeline -------------------------------------------------------------------------------------------------------

class PipelineStub:
    """Stands in for moderation.pipeline.run_moderation. Records, for every call, what the database said about the run at
    that moment (the claim must already be committed and visible). `behaviour(run)` decides what the stub does; the default
    finishes the run as `done`."""

    def __init__(self, behaviour=None):
        self.calls = []
        self.behaviour = behaviour or finish("done")

    def __call__(self, run, *args, **kwargs):
        from django.db import connection

        stored = row(run)
        self.calls.append(
            SimpleNamespace(
                pk=run.pk,
                status_in_db=stored["status"],
                attempts_in_db=stored["attempts"],
                started_in_db=stored["started_at"],
                claimed_in_db=stored["claimed_at"],
                object_status=run.status,
                in_atomic_block=connection.in_atomic_block,
                args=args,
                kwargs=kwargs,
            )
        )
        return self.behaviour(run)

    @property
    def pks(self):
        return [c.pk for c in self.calls]


def finish(status, reason=""):
    """A stub behaviour: the way run_moderation ends a run (status, reason, finished_at), then returns it."""
    from django.utils import timezone

    from moderation.models import ModerationRun

    def behaviour(run):
        ModerationRun.objects.filter(pk=run.pk).update(status=status, failure_reason=reason, finished_at=timezone.now())
        return reload(run)

    return behaviour


def finish_quietly(status="done"):
    """Like finish, but returns None (the worker must re-read the row itself)."""
    inner = finish(status)

    def behaviour(run):
        inner(run)

    return behaviour


def crash(exc):
    """A stub behaviour that raises `exc` (a fresh instance made by calling `exc` if it is a class)."""

    def behaviour(run):
        raise exc() if isinstance(exc, type) else exc

    return behaviour


def crash_after(status, reason, exc):
    """A stub behaviour that ends the run properly (like the real pipeline's programming-error path) and then raises."""

    def behaviour(run):
        finish(status, reason)(run)
        raise exc

    return behaviour


def crash_first_then_finish(exc, status="done"):
    """Raise on the first call, finish the following ones."""
    state = {"calls": 0}
    finisher = finish(status)

    def behaviour(run):
        state["calls"] += 1
        return crash(exc)(run) if state["calls"] == 1 else finisher(run)

    return behaviour


def install_pipeline(monkeypatch, behaviour=None):
    """Replace run_moderation everywhere the worker might look it up. Returns the PipelineStub."""
    import moderation.pipeline
    import moderation.worker

    stub = PipelineStub(behaviour)
    monkeypatch.setattr(moderation.pipeline, "run_moderation", stub)
    monkeypatch.setattr(moderation.worker, "run_moderation", stub, raising=False)
    return stub


# --- Fake sleep and the trace of a worker loop -------------------------------------------------------------------------------

class Sleeper:
    """A `sleep` that never sleeps. Records every duration; `hooks` maps a 1-based call number to a function called with
    that call number (for example to set a stop flag or to add a run while the worker 'sleeps'). `clock`, if given, is
    advanced by each duration."""

    def __init__(self, hooks=None, clock=None, trace=None):
        self.calls = []
        self.hooks = hooks or {}
        self.clock = clock
        self.trace = trace

    def __call__(self, seconds):
        self.calls.append(seconds)
        if self.trace is not None:
            self.trace.append("sleep")
        if self.clock is not None:
            self.clock.advance(seconds)
        hook = self.hooks.get(len(self.calls))
        if hook is not None:
            hook(len(self.calls))


class StopAfter:
    """A `stop` callable that turns true once `count` sleeps have happened (attach `.sleeper()` as the sleep)."""

    def __init__(self, count, clock=None, trace=None):
        self.count = count
        self.sleeper = Sleeper(clock=clock, trace=trace)

    def __call__(self):
        return len(self.sleeper.calls) >= self.count


def fail_first(real, exc, failures=1):
    """Wrap `real` so its first `failures` calls raise `exc` (an instance) and the later ones call through."""
    state = {"calls": 0}

    def wrapper(*args, **kwargs):
        state["calls"] += 1
        return _raise(exc) if state["calls"] <= failures else real(*args, **kwargs)

    wrapper.state = state
    return wrapper


def _raise(exc):
    raise exc


def trace_worker(monkeypatch):
    """Wrap claim_next_run and reap_stuck_runs in moderation.worker so a loop leaves a trace. Returns the event list:
    "reap", "claim-none", "claim-run", and (from a Sleeper given this list) "sleep"."""
    import moderation.worker as worker

    events = []
    real_claim, real_reap = worker.claim_next_run, worker.reap_stuck_runs

    def claim(*args, **kwargs):
        result = real_claim(*args, **kwargs)
        events.append("claim-none" if result is None else "claim-run")
        return result

    def reap(*args, **kwargs):
        events.append("reap")
        return real_reap(*args, **kwargs)

    monkeypatch.setattr(worker, "claim_next_run", claim)
    monkeypatch.setattr(worker, "reap_stuck_runs", reap)
    return events


def follows_every(events, first, then):
    """True when every `first` event in `events` is immediately followed by `then` or is the last event."""
    return all(b == then for a, b in zip(events, events[1:]) if a == first)


def patch_time(monkeypatch, clock):
    """Make the wall clock and the monotonic clock follow a FrozenClock (a worker may use any of them for its reaper timer)."""
    monkeypatch.setattr("django.utils.timezone.now", clock)
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    monkeypatch.setattr(time, "time", clock.epoch)


# --- End to end: real users, real post_message, scripted model answers ---------------------------------------------------------

def active_pair(prefix="workeruser"):
    """An active conversation with two named users; returns a namespace (conv, ua, ub, pa, pb, names)."""
    from django.contrib.auth import get_user_model

    from forum.models import Conversation, Participant, Topic

    tag = f"{n():04d}"
    users = [
        get_user_model().objects.create_user(username=f"{prefix}{c}{tag}", email=f"{prefix}{c}{tag}@mailbox.example")
        for c in "ab"
    ]
    topic = Topic.objects.create(title="", proposition=f"Rent caps are a good idea number {tag}")
    conv = Conversation.objects.create(topic=topic, status="active", label_seed=4242)
    pa = Participant.objects.create(conversation=conv, user=users[0], label="A", join_order=1)
    pb = Participant.objects.create(conversation=conv, user=users[1], label="B", join_order=2)
    return SimpleNamespace(conv=conv, ua=users[0], ub=users[1], pa=pa, pb=pb, names=[u.username for u in users],
                           emails=[u.email for u in users])


def issue_d(message, quote, issue_id="i1"):
    return {
        "id": issue_id, "message_id": message.pk, "issue_type": "unsupported_claim", "quote": quote,
        "explanation": "The claim is stated without support.", "confidence": 0.8, "intensity": None,
        "time_sensitive": False,
    }  # fmt: skip


def master_d(*issues):
    return {"issues": list(issues), "discussion_map": {"agreements": [], "disagreements": []}}


def intervene_d(message, act_text, issue_id="i1"):
    return {
        "decision": "intervene", "rationale": "A source would help both readers.",
        "issue_dispositions": [{"issue_id": issue_id, "disposition": "acted", "reason": "It matters for the discussion."}],
        "acts": [{
            "type": "request_information", "addressee": "A", "subject": "A", "source_issue_ids": [issue_id],
            "source_message_ids": [message.pk], "tone": "neutral", "text": act_text,
        }],
    }  # fmt: skip


def scripted_pair(message, quote, act_text):
    """The two scripted model answers (Master, Intervenor) that make a normal intervention on `message`."""
    return [master_d(issue_d(message, quote)), intervene_d(message, act_text)]


def error_records(caplog):
    """The ERROR log records that carry an exception (what logger.exception writes)."""
    import logging

    return [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info]
