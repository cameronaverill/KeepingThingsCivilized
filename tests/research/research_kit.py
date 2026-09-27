"""Helpers for tests/research (step 20b: moderation/research.py::run_research and its source list, item 4).

Not a test module and not a conftest: every test file of this folder imports it by name. Imports of moderation.* and
forum.* happen inside functions, so a missing module fails the test that needs it, not collection.

Built from the written contract in docs/step20b_brief.md items 3 and 4, following tests/pipeline_run/
pipeline_run_kit.py's conventions (World-ish scenario builder, go(run), scripted FakeLLM answers) and
tests/pipeline_agents/pipeline_agents_kit.py's blinding fixtures (USER_A/USER_B, FORBIDDEN_STRINGS), since
run_research is a smaller cousin of run_moderation with the very same privacy requirement (item 3: "no participant
labels, usernames, or which side made the claim -- same blinding as Master/Intervenor input").

Expected to fail (ImportError on `moderation.research`, AttributeError on `ModerationRun.source_act` /
`requested_by` / `InterventionAct.source_issues`) until the coding agent's `moderation/research.py` lands; that is
expected and is not a bug in these tests. As of this writing `moderation/models.py` (source_act, requested_by, the
"research" kind) and `moderation/schemas.py` (ResearchNote) already carry the Group A/B fields this kit builds on.
"""
import itertools
from types import SimpleNamespace

_counter = itertools.count(1)

# Recognisable identities that must never reach a prompt, a request or a ledger row (same recipe as
# tests/pipeline_agents/pipeline_agents_kit.py's USER_A/USER_B/FORBIDDEN_STRINGS).
USER_A = dict(username="quillfeather_zx", email="quillfeather.zx@hidden-mail.example", first_name="Quillfeather", last_name="Zxandros")
USER_B = dict(username="brambleton_qq", email="brambleton.qq@hidden-mail.example", first_name="Brambleton", last_name="Qqvist")
FORBIDDEN_STRINGS = (
    "quillfeather_zx", "quillfeather.zx", "brambleton_qq", "brambleton.qq", "hidden-mail.example", "Quillfeather",
    "Zxandros", "Brambleton", "Qqvist", "@hidden",
)  # fmt: skip

CLAIM_TEXT = "A 2024 Harvard study proved that remote work reduces productivity by 40 percent."
ISSUE_EXPLANATION = "The specific study and figure could not be confidently verified from training knowledge alone."
OFFER_TEXT = "An independent factual check could be requested for this claim."

TERMINAL = {"done", "failed", "skipped_budget", "skipped_disabled"}


def n():
    return next(_counter)


# --- Building blocks ---------------------------------------------------------------------------------------------

def make_user(spec):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(password=None, **spec)  # no password: unusable, no slow hashing


def make_topic(**kwargs):
    from forum.models import Topic

    values = dict(
        title=f"research topic {n()}",
        proposition="Companies should require employees to work from the office full time.",
    )
    values.update(kwargs)
    return Topic.objects.create(**values)


def make_research_scenario(*, human=True, needs_verification=True, side_a="pro", side_b="con"):
    """A conversation with a checkable claim by Participant A, a `live` run that already reported it
    (`needs_verification`) and offered `offer_research`, and everything a research `ModerationRun` needs to point
    `source_act`/`trigger_message` at. Returns SimpleNamespace(topic, conv, users, parts, claim, live_run, issue,
    act). `human=True` gives named, recognisable users (for the blinding tests); synthetic conversations have none."""
    from forum.models import Conversation, Message, Participant
    from moderation.models import Issue, InterventionAct, ModerationRun

    topic = make_topic()
    conv = Conversation.objects.create(topic=topic, source="human" if human else "synthetic")
    users = {"A": make_user(USER_A), "B": make_user(USER_B)} if human else {}
    parts = {
        "A": Participant.objects.create(conversation=conv, user=users.get("A"), label="A", join_order=1, side=side_a),
        "B": Participant.objects.create(conversation=conv, user=users.get("B"), label="B", join_order=2, side=side_b),
    }
    claim = Message.objects.create(conversation=conv, author_type="user", participant=parts["A"], content=CLAIM_TEXT)
    live_run = ModerationRun.objects.create(
        conversation=conv, trigger_message=claim, snapshot_seq=claim.seq_no, kind="live", status="done",
    )
    issue = Issue.objects.create(
        run=live_run, local_id="i1", message=claim, issue_type="possible_factual_error",
        quote=claim.content, quote_start=0, quote_end=len(claim.content), quote_match="exact",
        explanation=ISSUE_EXPLANATION, confidence=0.55, intensity=2, needs_verification=needs_verification,
    )
    act = InterventionAct.objects.create(
        run=live_run, order=1, act_type="offer_research", tone="neutral", text=OFFER_TEXT,
        addressee="all", subject="none", validity="valid",
    )
    act.source_issues.set([issue])
    act.source_messages.set([claim])
    return SimpleNamespace(topic=topic, conv=conv, users=users, parts=parts, claim=claim, live_run=live_run, issue=issue, act=act)


def new_research_run(scenario, *, requested_by_label="B", **fields):
    """A pending research ModerationRun for `scenario` (docs/step20b_brief.md item 1's contract: trigger_message is
    the claim's own message, source_act is the offer_research act, requested_by is whoever clicked)."""
    from moderation.models import ModerationRun

    values = dict(
        conversation=scenario.conv, trigger_message=scenario.claim, snapshot_seq=scenario.claim.seq_no,
        kind="research", source_act=scenario.act, requested_by=scenario.parts[requested_by_label],
    )
    values.update(fields)
    return ModerationRun.objects.create(**values)


def reload(run):
    from moderation.models import ModerationRun

    return ModerationRun.objects.get(pk=run.pk)


def go(run):
    """Run research.run_research and return (the returned run, the run reloaded from the database)."""
    from moderation.research import run_research

    returned = run_research(run)
    return returned, reload(run)


def message_count(conv):
    from forum.models import Message

    return Message.objects.filter(conversation=conv).count()


def moderator_messages(conv):
    from forum.models import Message

    return list(Message.objects.filter(conversation=conv, author_type="moderator").order_by("seq_no"))


def ledger(run=None):
    from moderation.models import LLMCall

    rows = LLMCall.objects.all() if run is None else LLMCall.objects.filter(run_id=run.pk)
    return list(rows.order_by("pk"))


# --- Scripted answers for FakeLLM (client.messages.parse(..., tools=..., output_format=ResearchNote)) -------------

def research_note_d(text="Independent sources broadly disagree with the specific figure cited.", confidence=0.65):
    return {"text": text, "confidence": confidence}


def tool_result_block(pairs):
    """A `web_search_tool_result` content block built from `[(title, url), ...]`, in the shape
    `moderation.llm.call_with_web_search`'s `WebSearchResult.tool_blocks` carries (docs/step20b_spike_brief.md:
    confirmed against the real API, `.content` is a list of objects with real `.title`/`.url`)."""
    return SimpleNamespace(type="web_search_tool_result", content=[SimpleNamespace(title=t, url=u) for t, u in pairs])


def malformed_result(**kwargs):
    """One search-result item missing whichever of `.title`/`.url` `kwargs` leaves out (item 4: "a malformed result
    (missing title or url) is skipped, not fatal")."""
    return SimpleNamespace(**kwargs)


def success_item(note=None, tool_results=(), text=""):
    """A scripted FakeLLM item for `call_with_web_search(..., output_schema=ResearchNote)`'s
    `client.messages.parse(tools=..., output_format=ResearchNote)` call: a `make_tool_message`-shaped response
    (tool_results as web_search_tool_result blocks) whose `.parsed_output` is set to a dict shaped like
    `ResearchNote`; `moderation/fake_llm.py`'s `_FakeMessages.parse()` validates a dict `.parsed_output` against
    `kwargs["output_format"]` on the way out, exactly like it does for `moderation.llm.call()`."""
    from moderation.fake_llm import make_tool_message

    note = research_note_d() if note is None else note

    def item(kwargs):
        message = make_tool_message(text=text, tool_results=list(tool_results))
        message.parsed_output = dict(note)
        return message

    return item


def invalid_json(kwargs):
    """A scripted item: the SDK could not parse the text (invalid JSON), which raises pydantic's ValidationError --
    the same helper as tests/pipeline_run/pipeline_run_kit.py's `invalid_json`, reproduced here so this folder does
    not depend on that one (that file is owned by a different slice of this step's work)."""
    from pydantic import TypeAdapter

    TypeAdapter(int).validate_json("{")


def extract_sources(tool_blocks, cap):
    """`moderation.research.extract_sources(tool_blocks, *, cap)` (item 4), by its real, now-landed name and
    signature -- kept as a thin wrapper here (rather than importing it in every test) so a future signature change
    only needs updating in this one place."""
    from moderation.research import extract_sources as real

    return real(tool_blocks, cap=cap)


def calls_of(fake):
    """The recorded `.calls` that are structured-output research calls (output_format is ResearchNote), across
    either `.messages.parse` or `.messages.create` (call_methods, moderation/fake_llm.py, records both)."""
    return [c for c in fake.calls if getattr(c.get("output_format"), "__name__", "") == "ResearchNote"]


def user_input(call):
    return call["messages"][0]["content"]


def all_request_text(calls):
    """Every recorded call's system + messages, flattened to one string (for a blanket 'never appears anywhere'
    check), mirroring tests/pipeline_agents/pipeline_agents_kit.py's `all_request_text(fake.calls)`."""
    import json

    return json.dumps([{"system": c.get("system"), "messages": c.get("messages")} for c in calls], default=str)
