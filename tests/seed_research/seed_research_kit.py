"""Helpers for the step 15 tests (tests/seed_research/), built from docs/step15_research_eval_brief.md.

Not a test module and not a conftest: every test file imports it by name. Project imports happen inside functions, so a missing
module fails the test that needs it, not collection. The step 14 judge_kit is reused for the replayed-experiment fixtures.

Where the brief leaves a name or signature open, the small adapters below (`call_first`, `eligible`, `collect_research_cases`,
`judge_one`, `run_judging_cases`, ...) absorb it; they are the only place a signature choice is encoded.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "seed_judge"))

import judge_kit as jk  # noqa: E402

from judge_kit import (  # noqa: E402,F401
    DUMMY_KEY, EXPERIMENT, dollars_in, LAW_TRUE, RANGE_FALSE, RANGE_TRUE, files_under, ledger, read_jsonl, request_text, run_command,
)

ROOT = jk.ROOT
SECRET = "zebrafishquartz"
NOTE_TEXT = "Independent sources report that between 500 and 560 things exist."
SOURCES = [("Agency report", "https://agency.example.org/report"), ("Journal", "https://journal.example.com/paper"),
           ("News", "https://news.example.net/story")]
ELIGIBLE = ("offer_research", "correct_factual_error", "provide_information", "request_information")
NOT_ELIGIBLE = ("ask_clarification", "summarize_position", "invite_response", "acknowledge_agreement")


def jsonl_path(experiment=EXPERIMENT):
    return f"generated/judgments/research_{experiment}.jsonl"


# --- The database: a replayed experiment whose acts the research step can pick up ------------------------------------------

def add_conversation(experiment=EXPERIMENT, fact_id="range_fact", side="left", arm="l2", *, acts=(("offer_research", "Shall I look it up?"),),
                     **kwargs):
    """jk.add_conversation plus: every valid act cites message 4 (the claim) as its source message, and carries one issue.
    Returns the Conversation."""
    from forum.models import Message
    from moderation.models import InterventionAct, Issue

    conversation = jk.add_conversation(experiment, fact_id, side, arm, acts=acts, **kwargs)
    claim = Message.objects.get(conversation=conversation, seq_no=4)
    for act in InterventionAct.objects.filter(run__conversation=conversation, validity="valid"):
        act.source_messages.set([claim])
        for issue in Issue.objects.filter(run=act.run):
            act.source_issues.add(issue)
    return conversation


def acts_of(conversation):
    from moderation.models import InterventionAct

    return list(InterventionAct.objects.filter(run__conversation=conversation).order_by("run_id", "order"))


def research_runs(conversation=None):
    from moderation.models import ModerationRun

    rows = ModerationRun.objects.filter(kind="research")
    if conversation is not None:
        rows = rows.filter(conversation=conversation)
    return list(rows.order_by("pk"))


def make_research_run(conversation, act=None, *, status="pending", requested_by="A"):
    """A research run by hand (the way forum.views.request_research makes one)."""
    from forum.models import Message, Participant
    from moderation.models import ModerationRun

    act = act or acts_of(conversation)[0]
    claim = act.source_messages.order_by("seq_no").first()
    return ModerationRun.objects.create(
        conversation=conversation, trigger_message=claim, snapshot_seq=claim.seq_no, kind="research", source_act=act,
        requested_by=Participant.objects.get(conversation=conversation, label=requested_by), status=status,
    )


def finished_research(conversation, *, note=NOTE_TEXT, sources=SOURCES, act=None, status="done", cost="0.01"):
    """A research run that finished as run_research leaves it: a posted moderator message (note + Sources block) and a ledger row
    attached to the run. Returns the run."""
    from django.db import transaction
    from forum.models import Message
    from moderation.models import LLMCall, ModerationRun

    run = make_research_run(conversation, act, status=status)
    if status == "done":
        from moderation.research import _compose_message_text

        text = _compose_message_text(note, [{"title": t, "url": u} for t, u in sources])
        posted = Message.objects.create(conversation=conversation, author_type="moderator", participant=None,
                                        in_reply_to=run.trigger_message, content=text)
        ModerationRun.objects.filter(pk=run.pk).update(posted_message=posted)
        run.posted_message = posted
    return run


def requester_label(run):
    from moderation.models import ModerationRun

    return ModerationRun.objects.get(pk=run.pk).requested_by.label


def author_label(run):
    return run.trigger_message.participant.label


# --- Scripted answers ---------------------------------------------------------------------------------------------------

def research_item(text=NOTE_TEXT, confidence=0.7, sources=SOURCES, input_tokens=1500, output_tokens=200):
    """A scripted FakeLLM item for one call_with_web_search call (structured ResearchNote)."""
    from moderation.fake_llm import make_tool_message

    block = SimpleNamespace(type="web_search_tool_result", content=[SimpleNamespace(title=t, url=u) for t, u in sources])

    def item(kwargs):
        message = make_tool_message(text="", tool_results=[block], input_tokens=input_tokens, output_tokens=output_tokens)
        message.parsed_output = {"text": text, "confidence": confidence}
        return message

    return item


def research_calls(client):
    return [c for c in client.calls if getattr(c.get("output_format"), "__name__", "") == "ResearchNote"]


def judge_verdict(tag="3", verdict="disputes_claim", rationale="The note gives the correct figure."):
    return {"tag": tag, "verdict": verdict, "rationale": rationale}


def priced(payload, **kw):
    return jk.priced(payload, **kw)


def invalid_json(kwargs):
    """A scripted item: the SDK could not parse the model's text (invalid JSON)."""
    from pydantic import TypeAdapter

    TypeAdapter(int).validate_json("{")


def judge_calls(client):
    return [c for c in client.calls if getattr(c.get("output_format"), "__name__", "") == "ResearchJudgeOut"]


# --- Adapters over names the brief leaves open --------------------------------------------------------------------------

def call_first(module_name, names, *args, **kwargs):
    """Call the first of `names` that exists in the module (the brief fixes some names, not all)."""
    import importlib

    module = importlib.import_module(module_name)
    for name in names:
        if hasattr(module, name):
            return getattr(module, name)(*args, **kwargs)
    raise AssertionError(f"{module_name} has none of {names}")


def pick(obj, *names, default=None):
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def eligible(experiment=EXPERIMENT, assignment="as-is"):
    """(items, skipped_count). eligible_acts may return a list or (list, skipped)."""
    from seeding import research_eval

    # the skipped count comes from research_eval.eligible (-> (acts, skipped)); eligible_acts returns the acts alone
    items, skipped = research_eval.eligible(experiment, assignment)
    assert [act_id(i) for i in research_eval.eligible_acts(experiment, assignment)] == [act_id(i) for i in items]
    return list(items), skipped


def act_id(item):
    value = pick(item, "act_id", "act")
    return getattr(value, "pk", value)


def transcript_id(item):
    return pick(item, "conversation_id", "transcript_id")


def plan(experiment=EXPERIMENT, **kwargs):
    from seeding import research_eval

    result = research_eval.plan_research(experiment, **kwargs)
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], (list, tuple)):
        return list(result[0])
    return list(result)


def run_eval(acts, **kwargs):
    from seeding import research_eval

    kwargs.setdefault("max_usd", Decimal("100"))
    return research_eval.run_research_eval(acts, **kwargs)


def collect_research_cases(experiment=EXPERIMENT):
    from seeding import research_eval

    result = call_first("seeding.research_eval", ("collect_cases", "collect_research_cases", "collect"), experiment)
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], list):
        return result[0]
    return list(result)


def make_case(**over):
    """A ResearchCase for an error arm (range_fact, left, l2) with a posted note, unless changed."""
    from seeding import research_eval

    data = dict(
        conversation_id="range_fact_left_l2", assignment="as-is", fact_id="range_fact", arm="l2", side="left", level=2, is_error_arm=True,
        false_claim=RANGE_FALSE[("left", "l2")], true_claim=RANGE_TRUE, note_text=NOTE_TEXT, n_sources=3,
        note_words=len(NOTE_TEXT.split()), confidence=0.7, run_status="done", cost_usd=Decimal("0.01"), moderator_act_type="offer_research",
    )
    data.update(over)
    cls = research_eval.ResearchCase
    import dataclasses

    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def true_case(**over):
    base = dict(conversation_id="range_fact_right_true", arm="true", side="right", level=None, is_error_arm=False, false_claim=None)
    base.update(over)
    return make_case(**base)


def failed_case(**over):
    base = dict(note_text="", n_sources=0, note_words=0, confidence=None, run_status="failed")
    base.update(over)
    return make_case(**base)


def judge_one(case, **kwargs):
    from moderation import budget
    from seeding import research_eval

    kwargs.setdefault("session", budget.SessionBudget(Decimal("100")))
    return call_first("seeding.research_eval", ("judge_note", "judge_case", "judge_research_note"), case, **kwargs)


def run_judging_cases(cases, **kwargs):
    kwargs.setdefault("max_usd", Decimal("100"))
    return call_first("seeding.research_eval", ("run_research_judging", "run_judging"), cases, **kwargs)


def row_text(row):
    return json.dumps(row, default=str)


# --- Commands -----------------------------------------------------------------------------------------------------------

def run_research_eval_cmd(*args):
    return run_command("run_research_eval", *args)


def judge_research_cmd(*args):
    return run_command("judge_research", *args)


def summarize_research_cmd(*args):
    return run_command("summarize_research", *args)


# --- Hand-built analysis rows -------------------------------------------------------------------------------------------

def row(*, fact_id="range_fact", arm="l2", side="left", tag="3", verdict="disputes_claim", words=10, sources=2, confidence=0.5,
        is_error=None, **over):
    level = int(arm[1]) if arm in ("l1", "l2", "l3") else None
    data = dict(
        conversation_id=f"{fact_id}_{side}_{arm}", fact_id=fact_id, arm=arm, side=side, level=level,
        is_error_arm=(arm != "true") if is_error is None else is_error,
        false_claim=None if arm == "true" else "false", true_claim="true", note_text="note", n_sources=sources, note_words=words,
        confidence=confidence, run_status="done", cost_usd=0.01, moderator_act_type="offer_research", tag=tag, verdict=verdict,
        rationale="r", prompt_version="sj_r2",
    )
    data.update(over)
    return data


def total_cost():
    from moderation import budget

    return budget.spend(purposes=("moderation",))
