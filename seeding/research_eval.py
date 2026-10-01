"""Evaluate the web-search research step on the seeded debates (docs/step15_research_eval_brief.md).

`eligible_acts` / `plan_research` find the moderator acts of a replay experiment that the forum would offer research on;
`run_research_eval` runs `moderation.research.run_research` on them under a spending cap; `collect_cases` reads the posted
notes back; `judge_research` / `run_judging` grade each note against the true fact through the gateway (`purpose="judge"`) and
append JSON Lines to `generated/judgments/research_<experiment>.jsonl`.

The research prompt never sees who clicked, a label, a side or a stance, so there are no "who clicked" variants: `requested_by`
is set to the participant who did NOT write the trigger message and is only recorded. The judge is never told side, level,
direction, fact id or arm. Never call inside `transaction.atomic`.
"""
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal

from django.conf import settings
from pydantic import BaseModel, ConfigDict

from config import tunables
from moderation import budget, llm
from moderation.errors import BudgetUnavailable, LLMAPIError, LLMDisabled, LLMOutputError, LLMRefused  # noqa: F401 (re-exported)
from moderation.models import LLMCall, ModerationRun
from moderation.prompting import load_prompt
from moderation.research import RESEARCH_PROMPT, build_research_input, run_research
from moderation.schemas import ResearchNote
from seeding import judge as seeded_judge

PROMPT_DIR = seeded_judge.PROMPT_DIR
RUBRIC_PATH = seeded_judge.RUBRIC_PATH
DEFAULT_DIR = seeded_judge.DEFAULT_DIR
PURPOSE = "judge"
RESEARCH_PURPOSE = "replay"  # research calls count against the evaluation budget (EVAL_PURPOSES), not the site caps
AGENT = "research_judge"
PROMPT_VERSION = "sj_r1"
SOURCES_MARKER = "\n\nSources:\n"
RESEARCH_ELIGIBLE_ACT_TYPES = ("offer_research", "correct_factual_error", "provide_information", "request_information")
# moderation/pricing.py does not price web searches yet. Anthropic's list price is $10 per 1,000 searches; each search's
# results also enter the context as input tokens (allowance below, priced like the dearest input). Both are worst-case
# allowances used only for the cap and the dry run, not the ledger.
SEARCH_FEE_USD = Decimal("0.01")
SEARCH_RESULT_TOKENS_ALLOWANCE = 5000
_ZERO = Decimal("0")
_RETRYABLE = ("failed", "skipped_budget", "skipped_disabled")


class ResearchJudgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tag: Literal["0", "1", "2", "3", "N/A"]
    verdict: Literal["confirms_claim", "disputes_claim", "unclear"]
    rationale: str


@dataclass
class EligibleAct:
    act_id: int
    conversation_pk: int
    conversation_id: str  # transcript id without any ":swapped" suffix
    assignment: str
    fact_id: str
    arm: str
    side: str
    level: int | None
    is_error_arm: bool
    false_claim: str | None
    true_claim: str
    act_type: str
    trigger_message_id: int


@dataclass
class ResearchCase:
    conversation_id: str
    assignment: str
    fact_id: str
    arm: str
    side: str
    level: int | None
    is_error_arm: bool
    false_claim: str | None
    true_claim: str
    note_text: str
    n_sources: int
    note_words: int
    confidence: float | None
    run_status: str
    cost_usd: Decimal
    moderator_act_type: str


# --- Eligibility and planning -----------------------------------------------------------------------------------------

def eligible(experiment_name, assignment="as-is"):
    """(acts, skipped): one valid eligible act per conversation of the experiment (the first by act id, among the acts of its
    finished replay runs); `skipped` counts the further eligible acts left out."""
    from forum.models import Conversation
    from moderation.models import InterventionAct

    cases, _ = seeded_judge.collect(experiment_name)
    by_id = {c.conversation_id: c for c in cases if c.assignment == assignment}
    conversations = {c.pk: c for c in Conversation.objects.filter(experiment__name=experiment_name)}
    acts = []
    skipped = 0
    seen = set()
    qs = (InterventionAct.objects.filter(
        validity="valid", act_type__in=RESEARCH_ELIGIBLE_ACT_TYPES, run__kind="replay", run__status="done",
        run__conversation_id__in=list(conversations)).select_related("run").order_by("id"))
    for act in qs:
        conversation = conversations[act.run.conversation_id]
        case = by_id.get(conversation.transcript_id.partition(":")[0])
        if case is None or conversation.transcript_id.partition(":")[2] != ("swapped" if assignment == "swapped" else ""):
            continue
        if conversation.pk in seen:
            skipped += 1
            continue
        trigger = act.source_messages.order_by("seq_no").first()
        if trigger is None:
            continue
        seen.add(conversation.pk)
        acts.append(EligibleAct(
            act_id=act.pk, conversation_pk=conversation.pk, conversation_id=case.conversation_id, assignment=assignment,
            fact_id=case.fact_id, arm=case.arm, side=case.side, level=case.level, is_error_arm=case.is_error_arm,
            false_claim=case.false_claim, true_claim=case.true_claim, act_type=act.act_type, trigger_message_id=trigger.pk,
        ))
    acts.sort(key=lambda a: (a.conversation_id, a.act_id))
    return acts, skipped


def eligible_acts(experiment_name, assignment="as-is"):
    return eligible(experiment_name, assignment)[0]


def plan_research(experiment_name, assignment="as-is", *, retry_failed=False, sets=()):
    """The acts still to run: none with a finished research run; a failed (or skipped) one only with `retry_failed`. `sets` are
    optional globs on the transcript id."""
    existing = {r.source_act_id: r.status for r in ModerationRun.objects.filter(kind="research")}
    plan = []
    for act in eligible_acts(experiment_name, assignment):
        if sets and not any(fnmatchcase(act.conversation_id, pattern) for pattern in sets):
            continue
        status = existing.get(act.act_id)
        if status == "done" or (status in _RETRYABLE and not retry_failed):
            continue
        plan.append(act)
    return plan


# --- Cost -------------------------------------------------------------------------------------------------------------

def _research_request(act):
    """(system, user_text) exactly as `moderation.research._run` builds them."""
    from moderation.models import InterventionAct

    row = InterventionAct.objects.get(pk=act.act_id)
    user_text = build_research_input(offer_text=row.text, claim_text=row.source_messages.order_by("seq_no").first().content,
                                     issues=list(row.source_issues.all()))
    return load_prompt(RESEARCH_PROMPT).text, user_text


def estimate_research_usd(act) -> Decimal:
    """Worst case of one research call: tokens (input estimate plus a search-result allowance at the dearest input price, plus
    the full output allowance) plus the search fee for every search allowed."""
    system, user_text = _research_request(act)
    uses = int(settings.RESEARCH_MAX_USES)
    tokens = budget.estimate_input_tokens(system=system, messages=[{"role": "user", "content": user_text}], schema=ResearchNote)
    tokens += uses * SEARCH_RESULT_TOKENS_ALLOWANCE
    return (budget.reservation_usd(settings.RESEARCH_MODEL, estimated_input_tokens=tokens,
                                   max_tokens=int(settings.RESEARCH_MAX_TOKENS)) + uses * SEARCH_FEE_USD)


# --- Running ----------------------------------------------------------------------------------------------------------

@dataclass
class ResearchReport:
    max_usd: Decimal
    results: list = field(default_factory=list)  # (EligibleAct, run status, cost_usd)
    cost_total: Decimal = _ZERO  # ledger token cost
    search_fee_allowance: Decimal = _ZERO  # worst-case search fees counted against the cap (not in the ledger)
    stopped_reason: str | None = None

    def summary(self):
        done = sum(1 for _, status, _ in self.results if status == "done")
        lines = [f"Research runs: {len(self.results)} ({done} done, {len(self.results) - done} not done).",
                 f"Token cost: ${self.cost_total:.6f}; search-fee allowance counted against the cap: "
                 f"${self.search_fee_allowance:.6f}; limit ${self.max_usd:.6f}."]
        for act, status, _ in self.results:
            if status != "done":
                lines.append(f"Not done: {act.conversation_id} ({act.assignment}): {status}.")
        if self.stopped_reason:
            lines.append(f"Stopped early: {self.stopped_reason}.")
        return "\n".join(lines)

    __str__ = summary


class _Stop(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _requester(act):
    from forum.models import Message, Participant

    trigger = Message.objects.get(pk=act.trigger_message_id)
    return Participant.objects.filter(conversation_id=act.conversation_pk).exclude(pk=trigger.participant_id).order_by("id").first()


def _get_or_create_run(act):
    """The research run of the act (a failed one is reset to pending). Created outside any transaction holding an LLM call."""
    from moderation.models import InterventionAct

    run = ModerationRun.objects.filter(kind="research", source_act_id=act.act_id).first()
    if run is not None:
        if run.status in _RETRYABLE:
            ModerationRun.objects.filter(pk=run.pk).update(status="pending", failure_reason="", error="", finished_at=None)
            run.refresh_from_db()
        return run
    from forum.models import Message

    trigger = Message.objects.get(pk=act.trigger_message_id)
    return ModerationRun.objects.create(
        conversation_id=act.conversation_pk, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="research",
        source_act=InterventionAct.objects.get(pk=act.act_id), requested_by=_requester(act),
    )


def run_cost(run) -> Decimal:
    """What the ledger counts for the run's calls."""
    total = _ZERO
    for call in LLMCall.objects.filter(run_id=run.pk, purpose=RESEARCH_PURPOSE, agent="research"):
        total += call.cost_usd if call.cost_usd is not None else call.reserved_usd
    return total


def run_research_eval(acts, *, max_usd, on_result=None) -> ResearchReport:
    """Run the research step on each act. Stops before any call whose worst case (tokens plus search-fee allowance) would take
    the spend of this run over `max_usd` (equality is allowed). Expected failures are recorded on the run, never raised."""
    max_usd = Decimal(str(max_usd))
    if not max_usd.is_finite() or max_usd < 0:
        raise ValueError("max_usd must be a non-negative number of dollars")
    report = ResearchReport(max_usd=max_usd)
    spent = _ZERO
    try:
        for act in acts:
            if not settings.LLM_ENABLED or not settings.ANTHROPIC_API_KEY:
                raise _Stop("LLM calls are switched off (LLM_ENABLED is False or there is no key); nothing was run")
            worst = estimate_research_usd(act)
            if spent + worst > max_usd:
                raise _Stop(f"the next call's worst case (${worst:.6f}) would pass the limit of ${max_usd:.6f}; "
                            f"counted so far ${spent:.6f}")
            run = _get_or_create_run(act)
            run = run_research(run, purpose=RESEARCH_PURPOSE)
            cost = run_cost(run)
            report.cost_total += cost
            report.search_fee_allowance += int(settings.RESEARCH_MAX_USES) * SEARCH_FEE_USD
            spent += cost + int(settings.RESEARCH_MAX_USES) * SEARCH_FEE_USD
            report.results.append((act, run.status, cost))
            if on_result is not None:
                on_result(act, run, cost)
            if run.status in ("skipped_disabled", "skipped_budget"):
                raise _Stop(f"the run was skipped ({run.status}); stopping")
    except _Stop as stop:
        report.stopped_reason = stop.reason
    return report


# --- Collecting for judging -------------------------------------------------------------------------------------------

def split_note(content):
    """(note_text, n_sources): the posted message without its Sources block, and the number of "- " lines in that block."""
    note, marker, block = content.partition(SOURCES_MARKER)
    n = sum(1 for line in block.splitlines() if line.startswith("- ")) if marker else 0
    return note, n


def collect(experiment_name, assignment="as-is"):
    """(cases, unposted): one ResearchCase per finished research run of the experiment; `unposted` lists the transcript ids of
    research runs without a posted note."""
    from forum.models import Conversation

    base, _ = seeded_judge.collect(experiment_name)
    by_id = {c.conversation_id: c for c in base if c.assignment == assignment}
    conversations = {c.pk: c for c in Conversation.objects.filter(experiment__name=experiment_name)}
    cases, unposted = [], []
    runs = (ModerationRun.objects.filter(kind="research", conversation_id__in=list(conversations))
            .select_related("posted_message", "source_act").order_by("conversation_id", "id"))
    for run in runs:
        key, _, suffix = conversations[run.conversation_id].transcript_id.partition(":")
        if suffix != ("swapped" if assignment == "swapped" else ""):
            continue
        case = by_id.get(key)
        if case is None:
            continue
        if run.status != "done" or run.posted_message is None:
            unposted.append(key)
            continue
        note, n_sources = split_note(run.posted_message.content)
        call = LLMCall.objects.filter(run_id=run.pk, purpose=RESEARCH_PURPOSE, agent="research", status="ok").order_by("-id").first()
        confidence = (call.parsed or {}).get("confidence") if call is not None else None
        cases.append(ResearchCase(
            conversation_id=key, assignment=assignment, fact_id=case.fact_id, arm=case.arm, side=case.side, level=case.level,
            is_error_arm=case.is_error_arm, false_claim=case.false_claim, true_claim=case.true_claim, note_text=note,
            n_sources=n_sources, note_words=len(note.split()), confidence=confidence, run_status=run.status,
            cost_usd=run_cost(run), moderator_act_type=run.source_act.act_type,
        ))
    return cases, unposted


def collect_cases(experiment_name, assignment="as-is") -> list[ResearchCase]:
    return collect(experiment_name, assignment)[0]


# --- One judgment -----------------------------------------------------------------------------------------------------

def _system():
    rubric = RUBRIC_PATH.read_text(encoding="utf-8").strip()
    return (PROMPT_DIR / "judge_research_v1.md").read_text(encoding="utf-8").replace("{{RUBRIC}}", rubric)


def _request(case):
    """(system, messages): the claim as seeded, the true fact and the note; nothing about side, level, arm or fact id."""
    if case.is_error_arm:
        claim = f"A false claim was planted in a participant's message. The seeded false claim:\n{case.false_claim}"
    else:
        claim = f"No error was planted. The participant's message states this claim, which is TRUE:\n{case.true_claim}"
    content = f"{claim}\n\nThe true fact:\n{case.true_claim}\n\nThe research note:\n{case.note_text.strip()}"
    return _system(), [{"role": "user", "content": content}]


def needs_call(case):
    return bool(case.note_text.strip())


def estimate_call_usd(case) -> Decimal:
    if not needs_call(case):
        return _ZERO
    system, messages = _request(case)
    tokens = budget.estimate_input_tokens(system=system, messages=messages, schema=ResearchJudgeOut)
    return budget.reservation_usd(tunables.JUDGE_MODEL_SEEDED, estimated_input_tokens=tokens,
                                  max_tokens=tunables.JUDGE_SEEDED_MAX_TOKENS)


def judge_case(case, *, session=None, attempt=1):
    """(ResearchJudgeOut, LLMResult). Callers must check `needs_call` first (an empty note gets no call and no tag)."""
    system, messages = _request(case)
    result = llm.call(
        purpose=PURPOSE, agent=AGENT, model=tunables.JUDGE_MODEL_SEEDED, system=system, messages=messages,
        output_schema=ResearchJudgeOut, max_tokens=tunables.JUDGE_SEEDED_MAX_TOKENS, prompt_version=PROMPT_VERSION,
        attempt=attempt, temperature=None, session=session,
    )
    return result.parsed, result


# --- Storage ----------------------------------------------------------------------------------------------------------

def judgments_path(experiment, directory=None):
    name = f"research_{experiment}.jsonl"
    return Path(directory) / name if directory else DEFAULT_DIR / name


def _key(row):
    return (row["conversation_id"], row["assignment"], row["prompt_version"])


def load_done(path):
    path = Path(path)
    if not path.exists():
        return set()
    return {_key(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def judgment_row(case, out):
    row = asdict(case)
    row["cost_usd"] = str(case.cost_usd)
    return {**row, "tag": out.tag, "verdict": out.verdict, "rationale": out.rationale, "prompt_version": PROMPT_VERSION}


def append_judgment(path, case, out):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(judgment_row(case, out), ensure_ascii=False) + "\n")


def _drop_existing(path, keys):
    path = Path(path)
    if not path.exists():
        return
    kept = [line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and _key(json.loads(line)) not in keys]
    path.write_text("".join(line + "\n" for line in kept), encoding="utf-8")


# --- Judging ----------------------------------------------------------------------------------------------------------

@dataclass
class JudgeReport(seeded_judge.JudgeReport):
    no_note: list = field(default_factory=list)  # conversation ids with an empty note: no call, no tag


def run_judging(cases, *, max_usd, on_result=None, path=None, overwrite=False, session=None) -> JudgeReport:
    """Judge every case with a note. Stops before any call whose worst case would take this run's judge spend over `max_usd`
    (equality is allowed); one retry on `LLMOutputError`. With `path`, each judgment is appended and cases already there are
    skipped unless `overwrite`."""
    max_usd = Decimal(str(max_usd))
    if not max_usd.is_finite() or max_usd < 0:
        raise ValueError("max_usd must be a non-negative number of dollars")
    session = session or budget.SessionBudget(max_usd)
    report = JudgeReport(max_usd=max_usd)
    baseline = budget.spend(purposes=(PURPOSE,))
    done = set() if path is None else load_done(path)
    if path is not None and overwrite:
        _drop_existing(path, {(c.conversation_id, c.assignment, PROMPT_VERSION) for c in cases})
        done = set()

    def spent():
        return budget.spend(purposes=(PURPOSE,)) - baseline

    try:
        for case in cases:
            if (case.conversation_id, case.assignment, PROMPT_VERSION) in done:
                report.skipped_existing += 1
                continue
            if not needs_call(case):
                report.no_note.append(case.conversation_id)
                continue
            outcome = None
            for attempt in (1, 2):
                if not settings.LLM_ENABLED or not settings.ANTHROPIC_API_KEY:
                    raise _Stop("LLM calls are switched off (LLM_ENABLED is False or there is no key); nothing was judged")
                worst = estimate_call_usd(case)
                if spent() + worst > max_usd:
                    raise _Stop(f"the next call's worst case (${worst:.6f}) would pass the limit of ${max_usd:.6f}; "
                                f"spent ${spent():.6f}")
                try:
                    report.calls += 1
                    outcome = judge_case(case, session=session, attempt=attempt)
                    break
                except LLMDisabled:
                    raise _Stop("LLM calls are switched off (the gateway refused: kill switch or no key)") from None
                except LLMRefused as exc:
                    raise _Stop(f"the guard refused a call ({type(exc).__name__}): {exc}") from None
                except BudgetUnavailable as exc:
                    raise _Stop(f"the budget ledger could not be read: {exc}") from None
                except LLMAPIError as exc:
                    raise _Stop(f"the API call failed (status {exc.status_code}, {exc.error_type or 'no type'})") from None
                except LLMOutputError as exc:
                    if attempt == 2:
                        report.failures.append((case.conversation_id, case.assignment, exc.reason or "unusable output"))
            if outcome is None:
                continue
            out, result = outcome
            report.judgments.append((case, out))
            if path is not None:
                append_judgment(path, case, out)
            if on_result is not None:
                on_result(case, out, result)
    except _Stop as stop:
        report.stopped_reason = stop.reason
    report.cost_total = spent()
    return report
