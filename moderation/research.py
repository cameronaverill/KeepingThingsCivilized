"""The Research component: `run_research(run)` (docs/plan.md section 2 "Factual research via web search";
docs/step20b_brief.md item 3). Fulfils one `offer_research` act with one `web_search`-backed call.

Mirrors `moderation/pipeline.py`'s shape (docs/step20b_brief.md: "the shape to mirror, much smaller"): re-check the
run, build an injection-safe, blinded prompt, make one guarded call, handle failure with the SAME reason-code
vocabulary `pipeline.run_moderation` already uses (see `pipeline._stop_for`), and on success post one moderator
message and finish the run. Unlike the main pipeline there is nothing to validate item-by-item: the output is one
note, not a list of claims, so there is no analogue of `_store_issues`/`_store_intervenor`.

Blinding: exactly like the Master/Intervenor input, the prompt sent here carries no participant label, username or
side — this call has no more reason to know who said something than they do (docs/step20b_brief.md item 3.2).

Source list: `extract_sources` turns the real `WebSearchResult.tool_blocks` into a short, deduped-by-domain list of
plain `{"title", "url"}` dicts, computed in code, never asked of the model (docs/step20b_brief.md item 4).

Storage note (a decision flagged back to Claude, docs/step20b_brief.md item 4's own "flag back if neither fits
cleanly"): the source list is folded into the posted message's plain-text `content` (`_compose_message_text`) and
is NOT also duplicated into a new structured field on any model. Reasoning: item 6 (the forum layer, Group C's
slice) settled on "the resulting note is just the next moderator message in the normal list -- no special
rendering needed for the 'after' state", so nothing downstream actually reads a structured source list right now;
inventing a new JSON field on `ModerationRun` or `forum.Message` for a consumer that does not exist yet would mean
touching `moderation/models.py` (explicitly off limits for this slice) for no present benefit. `extract_sources`
is still a pure, independently useful function (see its own tests) -- if a future step wants a "source pill" UI,
its output can be persisted then, on whichever field that step's own brief settles on.
"""
import logging
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.db.models import F

from moderation import clock, llm, pipeline
from moderation.errors import LLMOutputError
from moderation.models import ModerationRun
from moderation.prompting import load_prompt
from moderation.schemas import ResearchNote

logger = logging.getLogger(__name__)

RUNNABLE_STATUSES = pipeline.RUNNABLE_STATUSES  # ("pending", "running") -- kept as one constant, not retyped
RESEARCH_PROMPT = "research"  # "the highest version present" (see moderation.prompting.load_prompt)


# --- Small helpers, mirroring pipeline.py's own (kept local so this module has no private cross-module coupling
# beyond `pipeline._stop_for`, which item 3.4 of the brief explicitly asks this module to reuse) -------------------

def _update(run, **fields):
    """Write fields of the run row with one UPDATE (no model validation) and mirror them on the object."""
    ModerationRun.objects.filter(pk=run.pk).update(**fields)
    for name, value in fields.items():
        setattr(run, name, value)


def _terminate(run, *, status, failure_reason="", error=""):
    from moderation.scrub import scrub

    _update(run, status=status, failure_reason=failure_reason, error=scrub(error), finished_at=clock.now())
    log = logger.info if status in ("skipped_budget", "skipped_disabled") else logger.warning
    log(
        "research run %s terminated: conversation=%s source_act=%s status=%s reason=%s",
        run.pk, run.conversation_id, run.source_act_id, status, failure_reason,
    )


def _escape(value):
    """Same escaping rule as moderation/prompting.py's `_escape`: & < > and the double quote, so a participant's
    text can never close its own block or forge a new one. Kept local rather than importing prompting's private
    helper, so this module's injection-safety rule does not depend on another module's internals."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# --- Prompt building --------------------------------------------------------------------------------------------

def build_research_input(*, offer_text, claim_text, issues):
    """The injection-safe user turn for the Research agent (docs/step20b_brief.md item 3.2): the offer's own text,
    the claim's message text, and the issue(s) it cites (quote/explanation/issue_type) -- no participant label,
    message id, username or side, same blinding as the Master/Intervenor input. `issues` is an iterable of Issue
    rows (or anything with `.issue_type`/`.quote`/`.explanation`)."""
    parts = ["<claim>", _escape(claim_text), "</claim>", "", "<offer>", _escape(offer_text), "</offer>"]
    issues = list(issues)
    if issues:
        parts += ["", "<issues>"]
        for issue in issues:
            parts.append(
                f'<issue issue_type="{_escape(issue.issue_type)}">\n'
                f"<quote>{_escape(issue.quote)}</quote>\n"
                f"<explanation>{_escape(issue.explanation)}</explanation>\n"
                "</issue>"
            )
        parts.append("</issues>")
    parts += ["", "Verify the claim, following your instructions."]
    return "\n".join(parts)


# --- Source extraction (docs/step20b_brief.md item 4) -----------------------------------------------------------

def extract_sources(tool_blocks, *, cap):
    """Flatten every `web_search_tool_result` block's `.content` items (each with `.title`/`.url`), in the order the
    API returned them, deduplicate by domain (`urlparse(url).netloc`, first occurrence wins), cap at `cap`. A
    missing `tool_blocks`/`.content`, or a malformed item (blank/missing `.title` or `.url`), is skipped, never
    fatal. Returns a plain list of `{"title": ..., "url": ...}` dicts, in order, at most `cap` long."""
    seen_domains = set()
    sources = []
    for block in tool_blocks or []:
        for item in getattr(block, "content", None) or []:
            title = getattr(item, "title", None)
            url = getattr(item, "url", None)
            if not title or not url:
                continue
            domain = urlparse(url).netloc
            if not domain or domain in seen_domains:
                continue
            if len(sources) >= cap:
                return sources
            seen_domains.add(domain)
            sources.append({"title": title, "url": url})
    return sources


def _compose_message_text(note_text, sources):
    """The posted message's plain-text body: the note, then a short rendered source list. This is the only place the
    source list is rendered anywhere (see this module's docstring, "Storage note") -- no separate structured field
    is written for a template to read."""
    lines = [note_text.strip()]
    if sources:
        lines.append("")
        lines.append("Sources:")
        lines += [f"- {source['title']} ({source['url']})" for source in sources]
    return "\n".join(lines)


# --- The guarded call, with the same retry-once-on-LLMOutputError behavior as agents._call_with_retry ------------

def _call_research(run, prompt, user_text, purpose="moderation"):
    """One guarded, web_search-enabled, structured-output call, retried once on `LLMOutputError` (invalid or
    truncated JSON, a schema mismatch, a refusal) exactly like `agents._call_with_retry` retries the Master/
    Intervenor -- `agents._call_with_retry` itself is not reused because it returns only `.parsed`, discarding the
    `tool_blocks` this module needs for item 4. `LLMRefused`, `BudgetUnavailable` and `LLMAPIError` are not caught:
    they propagate unchanged, same as `agents._call_with_retry`."""
    last = None
    for attempt in (1, 2):
        try:
            return llm.call_with_web_search(
                purpose=purpose,
                agent="research",
                model=settings.RESEARCH_MODEL,
                system=prompt.text,
                messages=[{"role": "user", "content": user_text}],
                max_tokens=settings.RESEARCH_MAX_TOKENS,
                max_uses=settings.RESEARCH_MAX_USES,
                output_schema=ResearchNote,
                prompt_version=prompt.name,
                attempt=attempt,
                conversation_id=run.conversation_id,
                run_id=run.pk,
                cache_system=True,
            )
        except LLMOutputError as err:
            last = err
            continue
    raise last


# --- The entry point ----------------------------------------------------------------------------------------------

def run_research(run, *, purpose="moderation"):
    """Run the research pipeline for `run` and return it. Never raises for an expected failure (the run row records
    it, using the same reason-code vocabulary as `pipeline.run_moderation`: `skipped_budget`, `skipped_disabled`,
    `failed` with a reason); a programming error marks the run failed (`internal_error`) and is re-raised.

    A run that is not `pending` or `running` is returned unchanged, so calling this twice is harmless. `run.kind`
    is asserted, not gracefully handled: `claim_next_run`/`process_one` (moderation/worker.py) never hand a
    non-research run to this function, so a mismatch here is a dispatch bug, not an expected condition -- the
    assertion mirrors `run_moderation`'s own "belt-and-suspenders" re-check style without inventing a new terminal
    state for a case that should never happen."""
    if run.status not in RUNNABLE_STATUSES:
        return run
    assert run.kind == "research", f"run_research called on a {run.kind!r} run (id={run.pk})"

    now = clock.now()
    claimed = ModerationRun.objects.filter(pk=run.pk, status__in=RUNNABLE_STATUSES).update(
        status="running", started_at=now, attempts=F("attempts") + 1, failure_reason="", error="",
    )
    if not claimed:
        run.refresh_from_db(fields=["status", "failure_reason", "error", "finished_at", "attempts"])
        return run
    run.status, run.started_at = "running", now
    run.attempts = (run.attempts or 0) + 1
    run.failure_reason, run.error = "", ""

    try:
        _run(run, purpose)
    except Exception as exc:
        stop = pipeline._stop_for(exc)
        if stop is not None:
            _terminate(run, status=stop.status, failure_reason=stop.failure_reason, error=stop.error)
        else:
            logger.exception("research run %s crashed", run.pk)
            _terminate(run, status="failed", failure_reason="internal_error", error=f"{type(exc).__name__}: {exc}")
            raise
    return run


def _run(run, purpose="moderation"):
    from forum.models import Message

    act = run.source_act
    issues = list(act.source_issues.all())
    user_text = build_research_input(offer_text=act.text, claim_text=run.trigger_message.content, issues=issues)
    prompt = load_prompt(RESEARCH_PROMPT)

    result = _call_research(run, prompt, user_text, purpose)
    note = result.parsed
    sources = extract_sources(result.tool_blocks, cap=int(settings.RESEARCH_MAX_SOURCES_SHOWN))
    content = _compose_message_text(note.text, sources)

    with transaction.atomic():
        posted = Message.objects.create(
            conversation_id=run.conversation_id,
            author_type="moderator",
            participant=None,
            in_reply_to=run.trigger_message,
            content=content,
        )
        _update(run, posted_message=posted, status="done", finished_at=clock.now())
    logger.info(
        "research run %s done: conversation=%s source_act=%s sources=%d",
        run.pk, run.conversation_id, run.source_act_id, len(sources),
    )
