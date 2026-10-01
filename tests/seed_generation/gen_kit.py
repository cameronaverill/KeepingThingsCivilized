"""Helpers for the step 12 tests (tests/seed_generation/), built only from docs/step12_generator_brief.md.

Not a test module and not a conftest: every test file imports it by name. Imports of project code happen inside
functions, so a missing module fails the test that needs it, not collection.

HAND-WORKED NUMBERS (from tests/seeding/seeding_kit.py, factors 1.10 / 1.50 / 3.00), for the integer range fact
true [500, 560] whose `inflate_favors` is "left":
  left  (inflate)  L1 "Between 550 and 616"   L2 "Between 750 and 840"   L3 "Between 1,500 and 1,680"
  right (deflate)  L1 "Between 455 and 509"   L2 "Between 333 and 373"   L3 "Between 167 and 187"
"""
import copy
import json
import re
from decimal import Decimal
from io import StringIO
from pathlib import Path

DUMMY_KEY = "seed-generation-tests-dummy-key"  # secret-scan: allow
ROOT = Path(__file__).resolve().parents[2]
MARKER = "[[CLAIM]]"
LEAD_IN = "This is the claim I am relying on."

# The expected claims for RANGE_FACT, by (side, arm).
RANGE_CLAIMS = {
    ("left", "true"): "Between 500 and 560 things exist",
    ("left", "l1"): "Between 550 and 616 things exist",
    ("left", "l2"): "Between 750 and 840 things exist",
    ("left", "l3"): "Between 1,500 and 1,680 things exist",
    ("right", "true"): "Between 500 and 560 things exist",
    ("right", "l1"): "Between 455 and 509 things exist",
    ("right", "l2"): "Between 333 and 373 things exist",
    ("right", "l3"): "Between 167 and 187 things exist",
}
STAT_ARMS = ("true", "l1", "l2", "l3")
ARM_ORDER = [(side, arm) for side in ("left", "right") for arm in STAT_ARMS]


# --- Facts ----------------------------------------------------------------------------------------------------------------

def range_fact(**over):
    from seeding.facts import Fact

    data = dict(
        id="range_fact", claim_true="Between 500 and 560 things exist", source_note="note", type="statistic",
        claim_template="Between {v0} and {v1} things exist", true_values=[500, 560], integer=True,
        owner_verified_true=True, inflate_favors="left",
        framing="Cited as evidence that the policy is widespread.",
        subject="how many things of a certain kind exist",
    )
    data.update(over)
    return Fact(**data)


def law_fact(**over):
    from seeding.facts import Fact

    data = dict(
        id="law_fact", claim_true="Officers may not be forced to hold anyone", source_note="note", type="law",
        owner_verified_true=True, mirrors_approved=True,
        error_claims={"left": "Officers must always hold anyone asked", "right": "Officers may never hold anyone"},
        framing="Cited as the legal background of the policy.",
        subject="what the law says about holding people on request",
    )
    data.update(over)
    return Fact(**data)


def unready_fact():
    return range_fact(id="unready_fact", owner_verified_true=False)


# --- Bases ----------------------------------------------------------------------------------------------------------------

ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth"}


def filler(side, index, words=20):
    """Message text with no digit (a base may contain none)."""
    word = {"left": "thing", "right": "matter"}.get(side, "thing")
    return f"The {ORDINALS[index]} turn of the {side} conversation. " + " ".join([word] * words) + "."


LAST = {
    "left": "That is why I hold my position so firmly. [[CLAIM]].",
    "right": "Quite the opposite seems right to me. [[CLAIM]].",
}
GEN_VERSION, MIRROR_VERSION = "gen_v7", "mirror_v7"


def stamped(source, version=None):
    """A copy of a base carrying the prompt_version the generator records (gen_v3 for a left base, mirror_v3 for a right one)."""
    out = copy.deepcopy(source)
    out["prompt_version"] = version or (GEN_VERSION if source["side"] == "left" else MIRROR_VERSION)
    return out


def base(side="left", fact_id="range_fact", count=4, last=None):
    """A valid stub base: `count` alternating messages from Participant A, the last (by Participant B) holding the marker."""
    last = last or LAST[side]
    messages = []
    for i in range(1, count + 1):
        author = "Participant A" if i % 2 == 1 else "Participant B"
        text = last if i == count else filler(side, i)
        messages.append({"seq": i, "author": author, "text": text})
    return {"side": side, "fact_id": fact_id, "messages": messages}


def pair(fact_id="range_fact", count=4):
    return base("left", fact_id, count), base("right", fact_id, count)


def with_lengths(side, lengths, fact_id="range_fact"):
    """A base whose message i has exactly lengths[i] characters (the marker counted as its own 9 characters)."""
    tail = ". [[CLAIM]]."
    messages = []
    for i, length in enumerate(lengths, start=1):
        author = "Participant A" if i % 2 == 1 else "Participant B"
        char = "x" if side == "left" else "q"
        text = char * (length - len(tail)) + tail if i == len(lengths) else char * length
        assert len(text) == length
        messages.append({"seq": i, "author": author, "text": text})
    return {"side": side, "fact_id": fact_id, "messages": messages}


def edited(source, **changes):
    """A deep copy of a base with top-level keys replaced."""
    out = copy.deepcopy(source)
    out.update(changes)
    return out


def with_message(source, index, **changes):
    """A deep copy of a base with message `index` (0-based) changed."""
    out = copy.deepcopy(source)
    out["messages"][index].update(changes)
    return out


# --- Building transcripts -----------------------------------------------------------------------------------------------

def transcripts(fact=None, left=None, right=None):
    from seeding import arms

    fact = fact or range_fact()
    left, right = (left, right) if left is not None else pair(fact.id)
    return arms.build_transcripts(fact, left, right)


def by_id(items):
    return {t["id"]: t for t in items}


def validate(item):
    from moderation.transcripts import validate_transcript

    validate_transcript(Path(item["id"] + ".json"), item)


# --- Scripted model answers ---------------------------------------------------------------------------------------------

def answer(count=4, side="left", last=None):
    """A valid model answer (a dict for BaseOut) with `count` alternating messages, the last holding the marker."""
    b = base(side, count=count, last=last)
    return {"messages": [{"text": m["text"]} for m in b["messages"]]}


def answer_from(source):
    return {"messages": [{"text": m["text"]} for m in source["messages"]]}


def priced(payload, *, input_tokens=1000, output_tokens=100):
    """A script item that answers `payload` and reports these token counts (so it has a known price)."""
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


def system_text(call):
    system = call["system"]
    return system if isinstance(system, str) else "".join(block.get("text", "") for block in system)


def ledger():
    """The ledger rows of the generation calls only (the stance audits are in audit_ledger())."""
    from moderation.models import LLMCall

    return list(LLMCall.objects.filter(agent="generator").order_by("pk"))


def audit_ledger():
    from moderation.models import LLMCall

    return list(LLMCall.objects.filter(agent="generator_audit").order_by("pk"))


def all_ledger():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk"))


def labels(side="left", flip=(), mixed=(), count=4):
    """The audit labels that pass for `side` (A argues con on the left base, pro on the right one), with the 0-based positions in
    `flip` turned to the other side and those in `mixed` labelled mixed."""
    a, b = ("con", "pro") if side == "left" else ("pro", "con")
    out = [a if i % 2 == 0 else b for i in range(count)]
    for i in flip:
        out[i] = "pro" if out[i] == "con" else "con"
    for i in mixed:
        out[i] = "mixed"
    return out


class RoutedFake:
    """A FakeLLM whose stance-audit calls are answered separately from the generation calls (see conftest.fake)."""

    def __init__(self, script, audits=None):
        from moderation.fake_llm import FakeLLM, _FakeMessages, make_message

        client = self
        base_parse = _FakeMessages.parse

        class Router(_FakeMessages):
            def parse(self, **kwargs):
                if kwargs["output_format"].__name__ == "AuditOut":
                    return client._audit(kwargs)
                client._note_generation(kwargs)
                return base_parse(self, **kwargs)

        self._inner = FakeLLM(script)
        self._inner.messages = Router(self._inner)
        self.messages = self._inner.messages
        self.audits = list(audits or [])
        self.audit_calls = []
        self._side = "left"
        self._make_message = make_message

    @property
    def calls(self):
        return self._inner.calls

    @property
    def script(self):
        return self._inner.script

    @property
    def call_methods(self):
        return self._inner.call_methods

    def _note_generation(self, kwargs):
        text = request_text(kwargs)
        match = re.search(r"Participant A argues (in favor|against)", text)
        if match:
            self._side = "left" if match.group(1) == "against" else "right"

    def _audit(self, kwargs):
        self.audit_calls.append(kwargs)
        item = self.audits.pop(0) if self.audits else None
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            message = item(kwargs)
        else:
            if item is None:
                count = len(re.findall(r"^Message \d+:", kwargs["messages"][0]["content"], re.M))
                item = labels(self._side, count=count)
            payload = item if isinstance(item, dict) else {"labels": item}
            message = self._make_message(payload)
        schema = kwargs["output_format"]
        if isinstance(message.parsed_output, dict):
            from pydantic import ValidationError

            try:
                message.parsed_output = schema.model_validate(message.parsed_output)
            except ValidationError:
                message.parsed_output = None
        return message


# --- Running the command -------------------------------------------------------------------------------------------------

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


def generate_conversations(*args):
    return run_command("generate_conversations", *args)


def files_under(directory):
    directory = Path(directory)
    return sorted(str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()) if directory.exists() else []


def dollars_in(text):
    import re

    return [Decimal(m) for m in re.findall(r"\$\s?(\d+(?:\.\d+)?)", text)]
