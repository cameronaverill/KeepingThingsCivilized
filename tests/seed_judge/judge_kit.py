"""Helpers for the step 14 tests (tests/seed_judge/), built from docs/step14_judge_brief.md.

Not a test module and not a conftest: every test file imports it by name. Imports of project code happen inside functions,
so a missing module fails the test that needs it, not collection.
"""
import json
import re
from decimal import Decimal
from io import StringIO
from pathlib import Path

DUMMY_KEY = "seed-judge-tests-dummy-key"  # secret-scan: allow
ROOT = Path(__file__).resolve().parents[2]
RUBRIC_PATH = ROOT / "rubrics" / "factual_tag_v1.md"
EXPERIMENT = "pilot_test"

RANGE_TRUE = "Between 500 and 560 things exist"
RANGE_FALSE = {("left", "l1"): "Between 550 and 616 things exist", ("left", "l2"): "Between 750 and 840 things exist",
               ("left", "l3"): "Between 1,500 and 1,680 things exist", ("right", "l1"): "Between 455 and 509 things exist",
               ("right", "l2"): "Between 333 and 373 things exist", ("right", "l3"): "Between 167 and 187 things exist"}
LAW_TRUE = "Officers may not be forced to hold anyone"
LAW_FALSE = {"left": "Officers must always hold anyone asked", "right": "Officers may never hold anyone"}


# --- Facts ------------------------------------------------------------------------------------------------------------

def range_fact():
    from seeding.facts import Fact

    return Fact(
        id="range_fact", claim_true=RANGE_TRUE, source_note="note", type="statistic",
        claim_template="Between {v0} and {v1} things exist", true_values=[500, 560], integer=True,
        owner_verified_true=True, inflate_favors="left", framing="Cited as evidence that the policy is widespread.",
        subject="how many things of a certain kind exist",
    )


def law_fact():
    from seeding.facts import Fact

    return Fact(
        id="law_fact", claim_true=LAW_TRUE, source_note="note", type="law", owner_verified_true=True, mirrors_approved=True,
        error_claims=dict(LAW_FALSE), framing="Cited as the legal background of the policy.",
        subject="what the law says about holding people on request",
    )


# --- A replayed experiment, as the database holds it -----------------------------------------------------------------

def _transcript(fact_id, side, arm, phrase, correction, level):
    tid = f"{fact_id}_{side}_{arm}"
    messages = []
    for seq, who in enumerate("ABAB", start=1):
        text = f"[{tid}] message {seq} about the topic, stated plainly and civilly."
        planted = []
        if seq == 4:
            text += f" This is the claim I am relying on. {phrase}."
            if arm != "true":
                planted = [{"dimension": "factual_accuracy", "phrase": phrase, "intensity": level or 2,
                            "correction": correction, "fact_id": fact_id, "side": side, "level": level,
                            "direction": "inflate"}]
        messages.append({"seq": seq, "author": f"Participant {who}", "text": text, "planted": planted})
    return {
        "id": tid, "pair_id": f"{fact_id}_{arm}", "variant": side, "description": f"test transcript {tid}",
        "topic": {"title": "Seeded topic", "proposition": "The policy should be adopted."},
        "stances": {"Participant A": "pro", "Participant B": "con"}, "messages": messages, "trigger_seq": 4,
    }


def claims_for(fact_id, side, arm):
    """(false claim or None, true claim) that the fixtures put in the database for this fact, side and arm."""
    if fact_id == "range_fact":
        return (None if arm == "true" else RANGE_FALSE[(side, arm)]), RANGE_TRUE
    return (None if arm == "true" else LAW_FALSE[side]), LAW_TRUE


def add_conversation(experiment=EXPERIMENT, fact_id="range_fact", side="left", arm="l2", *, assignment="as-is",
                     acts=(), issues=(), run="done", rejected_acts=()):
    """Load one conversation into `experiment` and, unless run is None, give it a replay run with status `run`, the valid
    acts `acts` = [(act_type, text), ...], rejected acts and issues (quotes). Returns the Conversation."""
    from forum.models import Message
    from moderation import replay
    from moderation.models import InterventionAct, Issue, ModerationRun

    false_claim, true_claim = claims_for(fact_id, side, arm)
    level = int(arm[1]) if arm in ("l1", "l2", "l3") else None
    data = _transcript(fact_id, side, arm, false_claim or true_claim, true_claim, level)
    plan = replay.load_experiment(experiment, {data["id"]: data}, assignments=assignment)
    conversation = plan.conversations[0].conversation
    if run is None:
        return conversation
    trigger = Message.objects.get(conversation=conversation, seq_no=4)
    made = ModerationRun.objects.create(conversation=conversation, trigger_message=trigger, snapshot_seq=4, kind="replay",
                                        status=run)
    order = 0
    for act_type, text in acts:
        order += 1
        InterventionAct.objects.create(run=made, order=order, act_type=act_type, tone="neutral", text=text,
                                       addressee="B", subject="B")
    for text in rejected_acts:
        order += 1
        InterventionAct.objects.create(run=made, order=order, act_type="provide_information", tone="neutral", text=text,
                                       addressee="B", subject="B", validity="rejected", rejection_reason="too long")
    for number, quote in enumerate(issues, start=1):
        Issue.objects.create(run=made, local_id=f"i{number}", message=trigger, issue_type="possible_factual_error",
                             quote=quote, quote_match="not_found")
    return conversation


# --- Cases and judge output -------------------------------------------------------------------------------------------

def make_case(**over):
    """A Case for an error arm (range_fact, left, l2) with a posted response, unless changed."""
    from seeding.judge import Case

    data = dict(
        conversation_id="range_fact_left_l2", assignment="as-is", fact_id="range_fact", arm="l2", side="left", level=2,
        is_error_arm=True, false_claim=RANGE_FALSE[("left", "l2")], true_claim=RANGE_TRUE, run_status="done",
        intervened=True, n_issues=1, n_acts=1, response_text="Please check that figure: the sources say 500 to 560.",
        response_words=10, act_types=["correct_factual_error"], issue_quotes=["Between 750 and 840 things exist"],
    )
    data.update(over)
    return Case(**data)


def true_case(**over):
    base = dict(conversation_id="range_fact_right_true", arm="true", side="right", level=None, is_error_arm=False,
                false_claim=None, response_text="Could you say more about where that figure comes from?")
    base.update(over)
    return make_case(**base)


def silent_case(**over):
    base = dict(intervened=False, n_issues=0, n_acts=0, response_text="", response_words=0, act_types=[], issue_quotes=[])
    base.update(over)
    return make_case(**base)


def verdict(tag="3", unseeded=0, rationale="It named the sources."):
    return {"tag": tag, "unseeded_flagged": unseeded, "rationale": rationale}


BAD_VERDICT = {"tag": "9", "unseeded_flagged": 0, "rationale": "not a valid tag"}


def priced(payload, *, input_tokens=1000, output_tokens=100):
    from moderation.fake_llm import make_message

    def item(kwargs):
        return make_message(payload, input_tokens=input_tokens, output_tokens=output_tokens)

    return item


def request_text(call):
    """Everything the model was sent: system plus messages, as one string."""
    system = call["system"]
    if not isinstance(system, str):
        system = "".join(block.get("text", "") for block in system)
    parts = [system]
    for message in call["messages"]:
        content = message["content"]
        parts.append(content if isinstance(content, str) else "".join(block.get("text", "") for block in content))
    return "\n".join(parts)


def ledger():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk"))


def judge_spend():
    from moderation import budget

    return budget.spend(purposes=("judge",))


def judge(case, **kwargs):
    """judge_case(case, session=...) -> (JudgeOut, LLMResult)."""
    from moderation import budget
    from seeding import judge as module

    kwargs.setdefault("session", budget.SessionBudget(Decimal("100")))
    return module.judge_case(case, **kwargs)


def run(cases, **kwargs):
    from seeding import judge as module

    kwargs.setdefault("max_usd", Decimal("100"))
    return module.run_judging(cases, **kwargs)


def reserved_for_one_call(fake, case):
    """The worst-case reservation of one judge call for this case (read from the ledger after a real, scripted call)."""
    client = fake(priced(verdict()))
    judge(case)
    assert len(client.calls) == 1
    return ledger()[-1].reserved_usd


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


# --- Running the commands ---------------------------------------------------------------------------------------------

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


def judge_responses(*args):
    return run_command("judge_responses", *args)


def summarize_pilot(*args):
    return run_command("summarize_pilot", *args)


def files_under(directory="."):
    directory = Path(directory)
    return sorted(str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()) if directory.exists() else []


def dollars_in(text):
    return [Decimal(m) for m in re.findall(r"\$\s?(\d+(?:\.\d+)?)", text)]
