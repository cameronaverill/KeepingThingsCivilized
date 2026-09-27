"""The two moderation agents as plain functions (docs/step5_brief.md, "Interface between 5a and 5b"; plan sections 6 and 7).

`call_master` and `call_intervenor` build the prompt and the injection-safe input, make one guarded call through
`moderation.llm.call` and return the parsed output. They write nothing to the database except the `LLMCall` ledger rows
that `llm.call` itself writes. Validating what comes back (unknown ids, quotes, labels, caps) and storing rows is the
pipeline's job (`moderation/pipeline.py`), never this module's.

Failure handling:
- An unusable output (`LLMOutputError`: invalid or truncated JSON, a schema mismatch, a refusal, no parsed output) is
  retried ONCE with `attempt=2`; a second one raises `StructuralFailure`.
- `LLMRefused` (kill switch, breaker, budget, model), `BudgetUnavailable` and `LLMAPIError` are NOT caught: they
  propagate unchanged and are never retried here.

The transcript is a list of plain dicts (see the brief). The caller has already cut it to `TRANSCRIPT_MAX_MESSAGES`;
this module sends exactly what it is given. Only labels, message ids and text reach a prompt, never a `User`.
"""
from django.conf import settings

from moderation import llm, prompting
from moderation.errors import LLMOutputError
from moderation.schemas import IntervenorOutput, MasterOutput

MASTER_PROMPT = "master"  # "the highest version present" (see prompting.load_prompt)
INTERVENOR_PROMPT = "intervenor"


class StructuralFailure(Exception):
    """The model's output was unusable after one retry. `reason` is the last `LLMOutputError.reason`, `agent` names the
    agent and `call_ids` lists the `LLMCall` rows of both attempts."""

    def __init__(self, message="", *, agent="", reason="", call_ids=()):
        super().__init__(message)
        self.agent = agent
        self.reason = reason
        self.call_ids = tuple(call_ids)


def _messages(transcript):
    """(message_id, label, text) tuples in the order given; the label is the transcript's own label string."""
    return [(m["id"], m["label"], m["text"]) for m in transcript]


def _topic_parts(topic):
    """(title, proposition) for the prompt; a blank one is left out. Nothing else of the topic is read."""
    title = getattr(topic, "title", None) or None
    proposition = getattr(topic, "proposition", None) or None
    return title, proposition


def _call_with_retry(*, agent, prompt, model, max_tokens, output_schema, user, run):
    call_ids = []
    # A caller with no run row (the draft check, moderation/preview.py) may pass a stand-in `run` that has an
    # `llm_call_ids` list; the id of every ledger row written here is appended to it. A real run has no such attribute.
    collector = getattr(run, "llm_call_ids", None)
    last = None
    for attempt in (1, 2):
        try:
            result = llm.call(
                purpose="replay" if run.kind == "replay" else "moderation",  # replays are paid from the evaluation budget
                agent=agent,
                model=model,
                system=prompt.text,
                messages=[{"role": "user", "content": user}],
                output_schema=output_schema,
                max_tokens=max_tokens,
                prompt_version=prompt.name,
                attempt=attempt,
                run_id=run.pk,
                conversation_id=run.conversation_id,
                cache_system=True,
            )
        except LLMOutputError as err:
            last = err
            if getattr(err, "call_id", None) is not None:
                call_ids.append(err.call_id)
                if collector is not None:
                    collector.append(err.call_id)
            continue
        if collector is not None:
            collector.append(result.call_id)
        return result.parsed
    raise StructuralFailure(
        f"the {agent} output was unusable after 2 attempts: {last}",
        agent=agent,
        reason=getattr(last, "reason", ""),
        call_ids=call_ids,
    ) from last


def call_master(run, transcript, *, topic, already_raised=(), process_facts=None):
    """Run the Master Moderator over `transcript`; returns a validated `MasterOutput`."""
    prompt = prompting.load_prompt(MASTER_PROMPT)
    title, proposition = _topic_parts(topic)
    user = prompting.render_master_input(
        _messages(transcript),
        topic_title=title,
        proposition=proposition,
        already_raised=already_raised,
        process_facts=process_facts,
    )
    return _call_with_retry(
        agent="master",
        prompt=prompt,
        model=settings.MASTER_MODEL,
        max_tokens=settings.MASTER_MAX_TOKENS,
        output_schema=MasterOutput,
        user=user,
        run=run,
    )


def call_intervenor(run, transcript, *, topic, valid_issues, discussion_map=None):
    """Run the Intervenor over `transcript` and the Master's valid issues; returns a validated `IntervenorOutput`."""
    prompt = prompting.load_prompt(INTERVENOR_PROMPT)
    title, proposition = _topic_parts(topic)
    user = prompting.render_intervenor_input(
        _messages(transcript),
        valid_issues,
        topic_title=title,
        proposition=proposition,
        discussion_map=discussion_map,
    )
    return _call_with_retry(
        agent="intervenor",
        prompt=prompt,
        model=settings.INTERVENOR_MODEL,
        max_tokens=settings.INTERVENOR_MAX_TOKENS,
        output_schema=IntervenorOutput,
        user=user,
        run=run,
    )


def prompt_fingerprint():
    """{"master": {"name", "sha256"}, "intervenor": {...}} for the run's config snapshot."""
    out = {}
    for key, name in (("master", MASTER_PROMPT), ("intervenor", INTERVENOR_PROMPT)):
        prompt = prompting.load_prompt(name)
        out[key] = {"name": prompt.name, "sha256": prompt.sha256}
    return out
