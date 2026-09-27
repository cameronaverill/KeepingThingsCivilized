"""Helpers for the step 9a tests (tests/exports/): a rich fixture, an independent expected-bundle builder and small
comparison tools.

Not a test module and not a conftest: every test file imports it by name. Model and code-under-test imports happen inside
functions, so a missing module fails the test that needs it, not collection.

The expected bundle is built from the database rows with plain ORM reads, written independently of `moderation/queries.py`.
Representation choices are absorbed by `canon()` (datetimes as ISO text, money as a number or text) so a test compares
values, not spellings. Key names that the brief and the architect's amendment pin are used as pinned; the few that
they leave open are collected in `KEYS` below so a ruling is a one-line change.
"""
import itertools
import json
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace

_counter = itertools.count(1)

# --- Key names the brief leaves open (the amendment pins the rest) ------------------------------------------------------
KEYS = SimpleNamespace(
    issue_message="message",  # the issue's message, as a seq_no
    act_source_issues="source_issues",  # local_ids
    act_source_messages="source_messages",  # seq_nos
    run_posted="posted_seq",
    run_trigger="trigger_seq",
)

BASE = datetime(2026, 5, 4, 9, 0, 0, tzinfo=dt_timezone.utc)
SIX = Decimal("0.000001")
DT_KEYS = {"created_at", "joined_at", "claimed_at", "started_at", "finished_at", "ended_at"}
DEC_KEYS = {"cost_usd", "reserved_usd"}
PSEUDONYM_RE = re.compile(r"^p-[0-9a-f]{8}$")
ISO_8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")

RAW_KEYS = ("request", "raw_response", "parsed")

# Recognisable identities (explicit primary keys so a leaked pk is easy to find by text).
USER_A = dict(id=48211, username="quillmoth_zoltan", email="zoltan.quillmoth@mailbox.example",
              first_name="Zebulon", last_name="Quillfeather")  # fmt: skip
USER_B = dict(id=59377, username="brackenfell_ottoline", email="ottoline.brackenfell@mailbox.example",
              first_name="Ottoline", last_name="Brackenfell")  # fmt: skip
IDENTITY_STRINGS = [
    USER_A["username"], USER_A["email"], "zoltan.quillmoth", "Zebulon", "Quillfeather", str(USER_A["id"]),
    USER_B["username"], USER_B["email"], "ottoline.brackenfell", "Ottoline", "Brackenfell", str(USER_B["id"]),
]  # fmt: skip

# Texts of the rich human conversation.
MSG1 = "I believe that banning cars downtown will help shops, since everyone knows foot traffic doubles."
MSG2 = "That is not what the studies say; sales fell in most cities that tried it."
MSG3 = "Studies are “biased” and nobody serious disagrees with me on this, you people never learn."
ACT1 = "Could you point to a source for “nobody serious disagrees with me on this”?"
ACT2 = "Please keep the discussion focused on the arguments rather than on people."
ACT3_REJECTED = "Participant B, please respond too."
MSG4 = ACT1 + "\n\n" + ACT2
MSG5 = "Émile’s point about “naïve” pricing — 日本語 and an emoji \U0001f642 — deserves a look."
MSG6 = "Fine, here is my source: the city report."
MSG7 = "The city report only covers 2019, so it says nothing about later years."
ACT5 = "Which years would you like the report to cover?"
MSG8 = ACT5
MSG9 = "Thanks."
NON_ASCII_SAMPLES = ["Émile", "日本語", "\U0001f642", "“biased”", "—"]

# Money of the rich human conversation, written out by hand.
H_RUN_COSTS = {
    "r1": Decimal("0.001200"), "r2": Decimal("0.016625"), "r3": Decimal("0.003000"),
    "r4": Decimal("0.012500"), "r5": Decimal("0.009250"), "r6": Decimal("0"),
}  # fmt: skip
H_UNATTACHED_COST = Decimal("0.000500")
H_TOTAL_COST = Decimal("0.043075")  # 0.0012 + 0.016625 + 0.003 + 0.0125 + 0.00925 + 0.0005
S_TOTAL_COST = Decimal("0.005500")


def n():
    return next(_counter)


def at(minutes=0, seconds=0):
    """A whole-second UTC moment `minutes` after BASE (no microseconds, so every JSON spelling agrees)."""
    return BASE + timedelta(minutes=minutes, seconds=seconds)


# --- Building rows -----------------------------------------------------------------------------------------------------

def make_user(spec=None, **extra):
    from django.contrib.auth import get_user_model

    if spec is None:
        name = f"exportuser{n():05d}"
        spec = dict(username=name, email=f"{name}@mailbox.example")
    return get_user_model().objects.create_user(**{**spec, **extra})


def set_created(model, pk, when, field="created_at"):
    model.objects.filter(pk=pk).update(**{field: when})


def make_topic(proposition=None, title="", leans=None, created_by=None):
    from forum.models import Topic

    return Topic.objects.create(
        title=title, proposition=proposition or f"Proposition number {n():05d} should be adopted.",
        leans=leans or {}, created_by=created_by,
    )


def make_conversation(topic=None, *, source="human", status="open", experiment=None, created=None, pair_id="",
                      variant="", label_seed=None, transcript_id=""):
    from forum.models import Conversation

    conv = Conversation.objects.create(
        topic=topic or make_topic(), source=source, status=status, experiment=experiment, pair_id=pair_id,
        variant=variant, label_seed=label_seed, transcript_id=transcript_id,
    )
    if created is not None:
        set_created(Conversation, conv.pk, created)
        conv.refresh_from_db()
    return conv


def make_participant(conv, label, order, user=None, joined=None):
    from forum.models import Participant

    part = Participant.objects.create(conversation=conv, label=label, join_order=order, user=user)
    if joined is not None:
        set_created(Participant, part.pk, joined, "joined_at")
        part.refresh_from_db()
    return part


def make_message(conv, kind, part, text, *, when=None, reply_to=None, planted=None):
    from forum.models import Message

    message = Message.objects.create(
        conversation=conv, author_type=kind, participant=part if kind == "user" else None, content=text,
        in_reply_to=reply_to, planted=planted or [],
    )
    if when is not None:
        set_created(Message, message.pk, when)
        message.refresh_from_db()
    return message


def make_run(conv, trigger, *, kind="live", status="done", created=None, replay_of=None, replicate=1, attempts=1,
             is_stale=False, decision="", rationale="", posted=None, failure_reason="", error="",
             config=None, dmap=None, claimed=None, started=None, finished=None):
    from moderation.models import ModerationRun

    return ModerationRun.objects.create(
        conversation=conv, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind=kind, status=status,
        created_at=created or at(0), replay_of=replay_of, replicate=replicate, attempts=attempts, is_stale=is_stale,
        decision=decision, rationale=rationale, posted_message=posted, failure_reason=failure_reason, error=error,
        config_snapshot=config or {}, discussion_map=dmap or {}, claimed_at=claimed, started_at=started,
        finished_at=finished,
    )


def make_call(run, conv, *, cost, purpose="moderation", agent="master", attempt=1, model="claude-sonnet-5",
              reserved="0.020000", tokens=(1200, 300, 0, 0), status="ok", error_code="", latency=2000, minutes=0,
              request=None, raw="", parsed=None, prompt_version="master_v1", sha="a" * 64, run_id="from_run"):
    """A ledger row. `cost` is a string or None; `run=None` makes an unattached row (naming only the conversation)."""
    from moderation.models import LLMCall

    return LLMCall.objects.create(
        purpose=purpose, run_id=(run.pk if run is not None else None) if run_id == "from_run" else run_id,
        conversation_id=conv.pk if conv is not None else None, agent=agent, attempt=attempt, model=model,
        prompt_version=prompt_version, prompt_sha256=sha, max_tokens=1500, request=request or {}, raw_response=raw,
        parsed=parsed, tokens_in=tokens[0], tokens_out=tokens[1], cache_write_tokens=tokens[2],
        cache_read_tokens=tokens[3], reserved_usd=Decimal(reserved), cost_usd=None if cost is None else Decimal(cost),
        latency_ms=latency, status=status, error_code=error_code, created_at=at(minutes),
    )


def make_issue(run, local_id, message, issue_type, quote, *, match="exact", start="find", validity="valid",
               reason="", intensity=None, confidence=0.8, explanation="The claim is stated without support.",
               disposition=None, disposition_reason=""):
    """An issue; `start="find"` locates `quote` in the message text (or uses explicit offsets when given as a pair)."""
    from moderation.models import Issue, IssueDisposition

    if match == "not_found":
        s = e = None
    elif start == "find":
        s = message.content.find(quote)
        e = s + len(quote)
    else:
        s, e = start
    issue = Issue.objects.create(
        run=run, local_id=local_id, message=message, issue_type=issue_type, quote=quote, quote_start=s, quote_end=e,
        quote_match=match, explanation=explanation, confidence=confidence, intensity=intensity, validity=validity,
        rejection_reason=reason,
    )
    if disposition is not None:
        IssueDisposition.objects.create(issue=issue, disposition=disposition, reason=disposition_reason)
    return issue


def make_act(run, order, act_type, text, *, tone="neutral", addressee="all", subject="none", validity="valid",
             reason="", issues=(), messages=()):
    from moderation.models import InterventionAct

    act = InterventionAct.objects.create(
        run=run, order=order, act_type=act_type, tone=tone, text=text, addressee=addressee, subject=subject,
        validity=validity, rejection_reason=reason,
    )
    # Sources are added in REVERSE order on purpose: the export must sort them, not echo insertion order.
    act.source_issues.add(*list(reversed(list(issues))))
    act.source_messages.add(*list(reversed(list(messages))))
    return act


# --- The rich fixture --------------------------------------------------------------------------------------------------

def build_world():
    """Two rich conversations and one waiting one. See the module docstring of test_exports_roundtrip.py for the map.

    S (synthetic, paired) is created first so the primary keys of H's rows differ from their seq_no."""
    from forum.models import Experiment

    world = SimpleNamespace()
    build_synthetic(world)
    build_human(world)
    exp = Experiment.objects.create(name="observational-2026", kind="observational", description="real logs")
    world.exp_obs = exp
    world.h.experiment = exp
    world.h.save()
    world.e = make_conversation(make_topic("An empty proposition to wait on."), status="open", created=at(500))
    make_participant(world.e, "A", 1, user=world.ua, joined=at(501))
    return world


def build_synthetic(world):
    from forum.models import Experiment

    experiment = Experiment.objects.create(
        name="paired-rent-set", kind="paired", description="matched pairs", config={"replicates": 2}
    )
    topic = make_topic(
        "Cities should cap annual rent increases.", title="Rent control",
        leans={"compass": {"pro": {"economic": -0.6, "social": -0.1, "rationale": "Sides with tenants."},
                           "con": {"economic": 0.6, "social": 0.1, "rationale": "Sides with landlords."}}},
    )
    s = make_conversation(topic, source="synthetic", status="closed", experiment=experiment, created=at(100),
                          pair_id="rent-1", variant="left", label_seed=424242, transcript_id="rent_obvious_left")
    a = make_participant(s, "A", 1, joined=at(101))
    b = make_participant(s, "B", 2, joined=at(101, 1))
    m1 = make_message(s, "user", a, "Everyone knows rent control always destroys housing supply.", when=at(102),
                      planted=[{"phrase": "rent control always destroys housing supply", "dimension": "factual_accuracy",
                                "intensity": 3}])
    m2 = make_message(s, "user", b, "Supply is not the whole story; tenants gain stability.", when=at(103))
    m3 = make_message(s, "user", a, "You fool, that is nonsense.", when=at(104),
                      planted=[{"phrase": "You fool", "dimension": "abusiveness", "intensity": 3}])
    m4 = make_message(s, "user", b, "Let us keep this civil.", when=at(105))
    l1 = make_run(s, m1, kind="live", status="done", created=at(110), attempts=1, decision="no_intervention",
                  rationale="no valid issues", claimed=at(110, 1), started=at(110, 2), finished=at(110, 9))
    p1 = make_run(s, m1, kind="replay", status="done", created=at(120), replay_of=l1, replicate=1, attempts=1,
                  decision="no_intervention", rationale="no valid issues", started=at(120, 2), finished=at(120, 8))
    p2 = make_run(s, m1, kind="replay", status="failed", created=at(130), replay_of=l1, replicate=2, attempts=2,
                  failure_reason="structural", error="the model answered twice with unusable output")
    make_call(l1, s, cost="0.001500", prompt_version="s_marker_v9", minutes=110)
    make_call(p1, s, cost="0.002000", purpose="replay", prompt_version="s_marker_v9", minutes=120)
    make_call(p2, s, cost="0.001000", purpose="replay", prompt_version="s_marker_v9", minutes=130)
    make_call(p2, s, cost="0.001000", purpose="replay", attempt=2, prompt_version="s_marker_v9", minutes=131)
    world.s, world.s_msgs, world.s_runs = s, [m1, m2, m3, m4], SimpleNamespace(l1=l1, p1=p1, p2=p2)
    world.exp_paired = experiment


def build_human(world):
    ua = make_user(USER_A)
    ub = make_user(USER_B)
    world.ua, world.ub = ua, ub
    topic = make_topic(
        "Cities should ban cars from downtown centres.", title="", created_by=ua,
        leans={"compass": {"pro": {"economic": -0.4, "social": -0.2, "rationale": "Favors public space."}}},
    )
    h = make_conversation(topic, source="human", status="closed", created=at(0), label_seed=7719)
    pa = make_participant(h, "A", 1, user=ua, joined=at(1))
    pb = make_participant(h, "B", 2, user=ub, joined=at(2))
    m1 = make_message(h, "user", pa, MSG1, when=at(3))
    m2 = make_message(h, "user", pb, MSG2, when=at(4))
    m3 = make_message(h, "user", pa, MSG3, when=at(5))
    m4 = make_message(h, "moderator", None, MSG4, when=at(6), reply_to=m3)
    m5 = make_message(h, "user", pb, MSG5, when=at(7))
    m6 = make_message(h, "user", pa, MSG6, when=at(8))
    m7 = make_message(h, "user", pb, MSG7, when=at(9))
    m8 = make_message(h, "moderator", None, MSG8, when=at(10), reply_to=m7)
    m9 = make_message(h, "user", pa, MSG9, when=at(11))
    from forum.models import Conversation

    Conversation.objects.filter(pk=h.pk).update(ended_by=pa, ended_at=at(50))
    h.refresh_from_db()

    config = {
        "models": {"master": "claude-sonnet-5", "intervenor": "claude-sonnet-5"},
        "max_tokens": {"master": 1500, "intervenor": 1500},
        "prompts": {"master": {"name": "master_v1", "sha256": "b" * 64},
                    "intervenor": {"name": "intervenor_v1", "sha256": "c" * 64}},
        "tunables": {"TRANSCRIPT_MAX_MESSAGES": 40, "MAX_ACTS_PER_INTERVENTION": 3},
    }
    dmap = {"agreements": ["Both want livelier shops."],
            "disagreements": [{"summary": "Effect on sales", "kind": "factual"}]}

    # R1: a run with no valid issue.
    r1 = make_run(h, m1, status="done", created=at(20), decision="no_intervention", rationale="no valid issues",
                  config=config, claimed=at(20, 1), started=at(20, 2), finished=at(20, 6))
    make_call(r1, None, cost="0.001200", minutes=20, run_id="from_run", request={"marker": "r1-call"},
              raw='{"issues": []}', parsed={"issues": []})
    # R2: intervened; rich issues and acts; three calls (one structural retry).
    r2 = make_run(h, m3, status="done", created=at(21), attempts=1, decision="intervene",
                  rationale="A source would help both readers.", posted=m4, config=config, dmap=dmap,
                  claimed=at(21, 1), started=at(21, 2), finished=at(21, 30))
    i1 = make_issue(r2, "i1", m3, "unsupported_claim", "nobody serious disagrees with me on this",
                    disposition="acted", disposition_reason="It matters for the discussion.")
    i2 = make_issue(r2, "i2", m3, "possible_factual_error", 'Studies are "biased"', match="normalized",
                    start=(0, len("Studies are “biased”")), intensity=2, confidence=0.55, disposition="declined", disposition_reason="Contested; left alone.")
    i3 = make_issue(r2, "i3", m3, "unsupported_claim", "the data proves it", match="not_found", validity="rejected",
                    reason="quote_not_found")
    i4 = make_issue(r2, "i4", m3, "abusive_language", "you people never learn", intensity=3, confidence=0.9,
                    explanation="A demeaning generalisation.", disposition="acted",
                    disposition_reason="Conduct needs a reminder.")
    make_act(r2, 1, "request_information", ACT1, tone="gentle", addressee="A", subject="A", issues=[i1, i2], messages=[m3])
    make_act(r2, 2, "enforce_conduct", ACT2, tone="firm", addressee="all", subject="none", issues=[i4], messages=[m1, m3])
    make_act(r2, 3, "enforce_process", ACT3_REJECTED, validity="rejected", reason="names_participant", issues=[i3],
             messages=[m3])
    make_call(r2, None, cost="0.010000", attempt=1, minutes=21, tokens=(1200, 300, 800, 0), latency=2400,
              request={"system": "Be neutral. Élan.", "messages": [{"role": "user", "content": "Participant A: ..."}]},
              raw="not json at all", parsed=None, run_id="from_run")
    make_call(r2, h, cost="0.002500", attempt=2, minutes=22, tokens=(1250, 280, 0, 800), latency=1900,
              request={"system": "Be neutral.", "messages": [{"role": "user", "content": "Participant A: ... 日本語"}]},
              raw='{"issues": [1]}', parsed={"issues": [1]})
    make_call(r2, h, cost="0.004125", agent="intervenor", attempt=1, minutes=23, tokens=(900, 200, 0, 700),
              latency=1500, prompt_version="intervenor_v1", sha="c" * 64, request={"system": "Intervene wisely."},
              raw='{"decision": "intervene"}', parsed={"decision": "intervene"})
    # R3: failed.
    r3 = make_run(h, m5, status="failed", created=at(24), attempts=2, failure_reason="structural",
                  error="The model answered twice with unusable output.", config=config,
                  claimed=at(24, 1), started=at(24, 2), finished=at(24, 40))
    make_call(r3, h, cost="0.003000", attempt=1, minutes=24, request={"marker": "r3-a"})
    make_call(r3, h, cost=None, attempt=2, minutes=25, status="error", error_code="api_error", request={"marker": "r3-b"})
    # R4: a replay of R2 (never posts).
    r4 = make_run(h, m3, kind="replay", status="done", created=at(26), replay_of=r2, replicate=2, decision="intervene",
                  rationale="Ask for a source.", config=config, dmap=dmap, started=at(26, 2), finished=at(26, 20))
    j1 = make_issue(r4, "i1", m3, "unsupported_claim", "nobody serious disagrees with me on this",
                    disposition="declined", disposition_reason="Replay declined it.")
    make_act(r4, 1, "request_information", ACT1, tone="neutral", addressee="A", subject="A", issues=[j1], messages=[m3])
    make_call(r4, h, cost="0.009000", purpose="replay", minutes=26, request={"marker": "r4-a"})
    make_call(r4, h, cost="0.003500", purpose="replay", agent="intervenor", minutes=27, request={"marker": "r4-b"})
    # R5: stale live run that posted; its second issue is on a moderator message and is rejected.
    r5 = make_run(h, m7, status="done", created=at(28), is_stale=True, decision="intervene",
                  rationale="Ask which years.", posted=m8, config=config, claimed=at(28, 1), started=at(28, 2),
                  finished=at(28, 44))
    k1 = make_issue(r5, "i1", m7, "unclear_statement", "says nothing about later years", disposition="acted",
                    disposition_reason="Worth clarifying.")
    make_issue(r5, "i2", m4, "unsupported_claim", "a source", validity="rejected", reason="moderator_message")
    make_act(r5, 1, "request_clarification", ACT5, tone="gentle", addressee="B", subject="B", issues=[k1], messages=[m7])
    make_call(r5, h, cost="0.007000", minutes=28, request={"marker": "r5-a"})
    make_call(r5, h, cost="0.002250", agent="intervenor", minutes=29, request={"marker": "r5-b"})
    # R6: skipped for budget.
    r6 = make_run(h, m9, status="skipped_budget", created=at(30), attempts=1, failure_reason="budget_exceeded",
                  config=config, claimed=at(30, 1))
    make_call(r6, h, cost=None, status="refused_budget", reserved="0.004000", minutes=30, request={"marker": "r6"})
    # A ledger row that names only the conversation (no run).
    make_call(None, h, cost="0.000500", purpose="golden", prompt_version="unattached_v1", minutes=40,
              request={"marker": "unattached-raw"})
    world.h = h
    world.h_parts = {"A": pa, "B": pb}
    world.h_msgs = [m1, m2, m3, m4, m5, m6, m7, m8, m9]
    world.h_runs = SimpleNamespace(r1=r1, r2=r2, r3=r3, r4=r4, r5=r5, r6=r6)
    world.h_issues = SimpleNamespace(i1=i1, i2=i2, i3=i3, i4=i4)


def sources_out_of_order():
    """A conversation whose primary-key order differs from seq_no order (message ids 9001 then 9000) and from local_id order
    (issue "z1" is created before "a1"), with one act citing both messages and both issues."""
    from forum.models import Message

    conv = make_conversation(make_topic(), source="synthetic", status="closed", created=at(0))
    a = make_participant(conv, "A", 1)
    b = make_participant(conv, "B", 2)
    first = Message.objects.create(id=9001, conversation=conv, author_type="user", participant=a, content="Alpha claim.")
    second = Message.objects.create(id=9000, conversation=conv, author_type="user", participant=b,
                                    content="Second claim and third claim.")
    run = make_run(conv, second, status="done", created=at(1), decision="intervene", rationale="r")
    z1 = make_issue(run, "z1", second, "unsupported_claim", "Second claim")
    a1 = make_issue(run, "a1", second, "unsupported_claim", "third claim")
    make_act(run, 1, "request_information", "Could you cite sources?", issues=[z1, a1], messages=[first, second])
    return SimpleNamespace(conv=conv, first=first, second=second, run=run)


# --- Light conversations for the listing tests ---------------------------------------------------------------------------

def light(*, source="human", status="open", experiment=None, created=None, user_messages=2, moderator_messages=0,
          run_statuses=(), cost=None, pair_id="", variant="", proposition=None, topic=None):
    """A small conversation. One live run per entry of `run_statuses` (each on its own user message); `cost` is the
    cost of one ledger row on the first run."""
    conv = make_conversation(topic or make_topic(proposition), source=source, status=status, experiment=experiment,
                             created=created, pair_id=pair_id, variant=variant)
    users = [make_user() if source == "human" else None for _ in "AB"]
    parts = [make_participant(conv, label, order, user=users[order - 1]) for order, label in enumerate("AB", start=1)]
    msgs = [make_message(conv, "user", parts[i % 2], f"Message {i + 1} of light conversation {conv.pk}.")
            for i in range(user_messages)]
    for i in range(moderator_messages):
        make_message(conv, "moderator", None, f"Moderator note {i + 1}.", reply_to=msgs[0])
    runs = [make_run(conv, msgs[i], status=status_, created=at(i)) for i, status_ in enumerate(run_statuses)]
    if cost is not None:
        make_call(runs[0], conv, cost=cost)
    return SimpleNamespace(conv=conv, msgs=msgs, runs=runs, parts=parts, users=users)


def scaled(n_messages, n_runs):
    """A conversation whose size is set by the arguments, with every kind of row on every run (for query counts)."""
    conv = make_conversation(make_topic(), source="human", status="active", created=at(0))
    users = [make_user(), make_user()]
    parts = [make_participant(conv, "AB"[i], i + 1, user=users[i]) for i in range(2)]
    msgs = [make_message(conv, "user", parts[i % 2], f"Scaled message {i + 1}: nobody disagrees with this claim.")
            for i in range(n_messages)]
    for r in range(n_runs):
        trigger = msgs[r]
        run = make_run(conv, trigger, status="done", created=at(r), decision="intervene", rationale="scaled")
        issue = make_issue(run, "i1", trigger, "unsupported_claim", "nobody disagrees", disposition="acted")
        make_act(run, 1, "request_information", "Could you cite a source?", issues=[issue], messages=[trigger])
        make_call(run, conv, cost="0.001000", request={"marker": f"scaled-{r}"}, raw="x", parsed={"a": 1})
        make_call(run, conv, cost="0.001000", agent="intervenor", request={"marker": f"scaled-{r}b"})
    return conv


# --- The expected bundle, read straight from the database ----------------------------------------------------------------

def _call_expected(call, raw):
    d = {
        "agent": call.agent, "attempt": call.attempt, "model": call.model, "prompt_version": call.prompt_version,
        "prompt_sha256": call.prompt_sha256, "tokens_in": call.tokens_in, "tokens_out": call.tokens_out,
        "cache_write_tokens": call.cache_write_tokens, "cache_read_tokens": call.cache_read_tokens,
        "cost_usd": call.cost_usd, "reserved_usd": call.reserved_usd, "latency_ms": call.latency_ms,
        "status": call.status, "error_code": call.error_code,
    }  # fmt: skip
    if raw:
        d.update(request=call.request, raw_response=call.raw_response, parsed=call.parsed)
    return d


def _dec_sum(values):
    return sum((v for v in values if v is not None), Decimal("0"))


def expected_bundle(conv, *, identities=False, raw=False):
    """What `get_conversation_bundle` must contain (a subset comparison: extra keys in the real bundle are allowed)."""
    from forum.models import Message, Participant
    from moderation.models import LLMCall, ModerationRun

    seq = {m.pk: m.seq_no for m in Message.objects.filter(conversation=conv)}
    label = {p.pk: p.label for p in Participant.objects.filter(conversation=conv)}
    runs = list(ModerationRun.objects.filter(conversation=conv).order_by("created_at", "pk"))
    run_ids = {r.pk for r in runs}
    exp_runs = []
    for r in runs:
        calls = list(LLMCall.objects.filter(run_id=r.pk).order_by("pk"))
        issues = []
        for i in r.issues.order_by("pk"):
            d = getattr(i, "disposition", None)
            entry = {
                "local_id": i.local_id, KEYS.issue_message: seq[i.message_id], "issue_type": i.issue_type,
                "dimension": i.dimension, "quote": i.quote, "quote_start": i.quote_start, "quote_end": i.quote_end,
                "quote_match": i.quote_match, "explanation": i.explanation, "confidence": i.confidence,
                "intensity": i.intensity, "validity": i.validity, "rejection_reason": i.rejection_reason,
                "disposition": d.disposition if d else None,
            }  # fmt: skip
            if d:
                entry["disposition_reason"] = d.reason
            issues.append(entry)
        acts = []
        for a in r.acts.order_by("order"):
            acts.append({
                "order": a.order, "act_type": a.act_type, "tone": a.tone, "text": a.text, "addressee": a.addressee,
                "subject": a.subject, "validity": a.validity, "rejection_reason": a.rejection_reason,
                "features": {"char_len": a.char_len, "word_count": a.word_count, "is_question": a.is_question,
                             "quotes_participant": a.quotes_participant},
                KEYS.act_source_issues: sorted(x.local_id for x in a.source_issues.all()),
                KEYS.act_source_messages: sorted(seq[x.pk] for x in a.source_messages.all()),
            })  # fmt: skip
        exp_runs.append({
            "id": r.pk, "kind": r.kind, "replicate": r.replicate, "replay_of": r.replay_of_id, "status": r.status,
            "failure_reason": r.failure_reason, "attempts": r.attempts, "is_stale": r.is_stale,
            "decision": r.decision, "rationale": r.rationale, KEYS.run_trigger: seq[r.trigger_message_id],
            "snapshot_seq": r.snapshot_seq, KEYS.run_posted: seq[r.posted_message_id] if r.posted_message_id else None,
            "config_snapshot": r.config_snapshot, "discussion_map": r.discussion_map,
            "timings": {"created_at": r.created_at, "claimed_at": r.claimed_at, "started_at": r.started_at,
                        "finished_at": r.finished_at},
            "error": r.error, "cost_usd": _dec_sum(c.cost_usd for c in calls),
            "issues": issues, "acts": acts, "llm_calls": [_call_expected(c, raw) for c in calls],
        })  # fmt: skip
    unattached = [c for c in LLMCall.objects.filter(conversation_id=conv.pk).order_by("pk") if c.run_id not in run_ids]
    total_calls = LLMCall.objects.filter(conversation_id=conv.pk) | LLMCall.objects.filter(run_id__in=run_ids)
    exp_participants = []
    for p in Participant.objects.filter(conversation=conv).order_by("join_order"):
        d = {"label": p.label, "join_order": p.join_order, "joined_at": p.joined_at}
        if p.user_id is None:
            d["pseudonym"] = None
        if identities:
            d["username"] = p.user.username if p.user_id else None
            d["email"] = p.user.email if p.user_id else None
        exp_participants.append(d)
    topic = conv.topic
    return {
        "conversation": {
            "id": conv.pk, "status": conv.status, "source": conv.source,
            "experiment": {"name": conv.experiment.name, "kind": conv.experiment.kind} if conv.experiment_id else None,
            "pair_id": conv.pair_id, "variant": conv.variant, "label_seed": conv.label_seed,
            "created_at": conv.created_at, "ended_by": label[conv.ended_by_id] if conv.ended_by_id else None,
        },
        "topic": {"id": topic.pk, "proposition": topic.proposition, "title": topic.title, "leans": topic.leans},
        "participants": exp_participants,
        "messages": [
            {"seq_no": m.seq_no, "author_type": m.author_type,
             "participant": label[m.participant_id] if m.participant_id else None,
             "in_reply_to": seq[m.in_reply_to_id] if m.in_reply_to_id else None, "content": m.content,
             "char_count": m.char_count, "planted": m.planted, "created_at": m.created_at}
            for m in Message.objects.filter(conversation=conv).order_by("seq_no")
        ],  # fmt: skip
        "runs": exp_runs,
        "unattached_llm_calls": [_call_expected(c, raw) for c in unattached],
        "cost_usd": _dec_sum(c.cost_usd for c in total_calls.distinct()),
    }  # fmt: skip


# --- Comparison tools ----------------------------------------------------------------------------------------------------

def canon(value, key=None):
    """Normalise spellings: known datetime keys to aware datetimes, known money keys to Decimals at six places."""
    if isinstance(value, dict):
        return {k: canon(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [canon(v) for v in value]
    if key in DT_KEYS and isinstance(value, str):
        return datetime.fromisoformat(value)
    if key in DEC_KEYS and value is not None and not isinstance(value, bool):
        return Decimal(str(value)).quantize(SIX)
    return value


def assert_contains(actual, expected, path="bundle"):
    """`expected` must be contained in `actual`: every expected key present with an equal value, lists of equal length,
    extra keys in `actual` allowed. Raises AssertionError naming the path of the first difference."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected an object, got {type(actual).__name__}"
        for key, value in expected.items():
            assert key in actual, f"{path}: missing key {key!r} (has {sorted(actual)})"
            assert_contains(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected a list, got {type(actual).__name__}"
        assert len(actual) == len(expected), f"{path}: {len(actual)} items, expected {len(expected)}"
        for index, value in enumerate(expected):
            assert_contains(actual[index], value, f"{path}[{index}]")
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


def bundle(conv, **flags):
    from moderation.queries import get_conversation_bundle

    return get_conversation_bundle(conv.pk, **flags)


def export_text(conv, **flags):
    from moderation.queries import export_conversation

    return export_conversation(conv.pk, **flags)


def parsed(conv, **flags):
    """The export text parsed back into Python, with spellings normalised by `canon`."""
    return canon(json.loads(export_text(conv, **flags)))


def walk(value, path=()):
    """Yield (path, leaf) for every leaf of nested dicts and lists."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk(item, path + (str(key),))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from walk(item, path + (str(index),))
    else:
        yield path, value


def all_keys(value):
    """Every dict key anywhere in a nested structure."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from all_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from all_keys(item)


def keys_sorted(value):
    """True when every object in the nested structure lists its keys in sorted order (as parsed, in file order)."""
    if isinstance(value, dict):
        return list(value) == sorted(value) and all(keys_sorted(v) for v in value.values())
    if isinstance(value, list):
        return all(keys_sorted(v) for v in value)
    return True


def db_state():
    """Every row of the tables an export reads, so a test can prove an export changed nothing."""
    from forum.models import Conversation, Message, Participant, Topic
    from moderation.models import InterventionAct, Issue, IssueDisposition, LLMCall, ModerationRun

    models = (Topic, Conversation, Participant, Message, ModerationRun, Issue, IssueDisposition, InterventionAct,
              LLMCall)  # fmt: skip
    return [list(model.objects.order_by("pk").values()) for model in models]


def write_files(directory, files):
    """Create `directory` and write each `name: text` of `files` into it."""
    from pathlib import Path

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")


def dotenv_mentions(paths):
    """Every import of a dotenv module and every string constant that looks like an `.env` file name, in the given files."""
    import ast
    from pathlib import Path

    found = []
    for path in paths:
        for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found += [a.name for a in node.names if "dotenv" in a.name]
            if isinstance(node, ast.ImportFrom):
                found += [node.module] if node.module and "dotenv" in node.module else []
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found += [node.value] if node.value.strip().startswith(".env") else []
    return found


def open_spy(real_open, log):
    """A stand-in for `open` that records the path of every file opened by name (as text) and then opens it."""
    import os

    def wrapper(file, *args, **kwargs):
        log.append(os.fspath(file) if isinstance(file, (str, os.PathLike)) else "")
        return real_open(file, *args, **kwargs)

    return wrapper


def altered(text):
    """A different string of the same kind (used to change the project's signing key in a test)."""
    return text[::-1] + "-changed"


def timestamp_strings(value, key=None):
    """Every non-null value stored under a timestamp key (created_at, started_at, ...) anywhere in `value`."""
    if isinstance(value, dict):
        return [x for k, v in value.items() for x in timestamp_strings(v, k)]
    if isinstance(value, list):
        return [x for v in value for x in timestamp_strings(v)]
    return [value] if key in DT_KEYS and value is not None else []
