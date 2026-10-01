"""The seeded-error judge (docs/step14_judge_brief.md, docs/plan.md section 9 items 5-6).

`collect_cases` reads what a replay left in the database (one `Case` per conversation of an experiment); `judge_case` grades the
moderator's response with the factual-tag rubric through the LLM gateway (`purpose="judge"`); `run_judging` does that for many
cases under a spending cap and appends one JSON line per case to `generated/judgments/<experiment>.jsonl`.

The judge is never told the side, level, direction, fact id, arm or stances: `_request` builds its prompt from the claim, the true
fact and the response text only. When the moderator posted nothing no call is made (tag "0" for an error arm, "N/A" for a true arm).
Never call inside `transaction.atomic`.
"""
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

from django.conf import settings
from pydantic import BaseModel, ConfigDict

from config import tunables
from moderation import budget, llm
from moderation.errors import BudgetUnavailable, LLMAPIError, LLMDisabled, LLMOutputError, LLMRefused
from seeding.facts import load_facts

PROMPT_DIR = Path(__file__).parent / "prompts"
RUBRIC_PATH = Path(__file__).resolve().parent.parent / "rubrics" / "factual_tag_v1.md"
DEFAULT_DIR = Path("generated") / "judgments"
PURPOSE = "judge"
AGENT = "seeded_judge"
PROMPT_VERSION = "sj_v1"
ARMS = ("true", "l1", "l2", "l3", "err")
NO_RESPONSE = "The moderator posted nothing."
_ZERO = Decimal("0")


class JudgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tag: Literal["0", "1", "2", "3", "N/A"]
    unseeded_flagged: int
    rationale: str


@dataclass
class Case:
    conversation_id: str  # the transcript id, e.g. "<fact>_<side>_<arm>"
    assignment: str  # "as-is" | "swapped"
    fact_id: str
    arm: str
    side: str
    level: int | None
    is_error_arm: bool
    false_claim: str | None
    true_claim: str
    run_status: str
    intervened: bool
    n_issues: int
    n_acts: int
    response_text: str
    response_words: int
    act_types: list = field(default_factory=list)
    issue_quotes: list = field(default_factory=list)


# --- Collecting -------------------------------------------------------------------------------------------------------

def parse_transcript_id(transcript_id):
    """(fact_id, side, arm, assignment) from '<fact>_<left|right>_<arm>' with an optional ':swapped' suffix."""
    base, _, suffix = transcript_id.partition(":")
    assignment = suffix or "as-is"
    parts = base.rsplit("_", 2)
    if len(parts) != 3 or parts[1] not in ("left", "right") or parts[2] not in ARMS:
        raise ValueError(f"cannot read fact/side/arm from transcript id {transcript_id!r}")
    return parts[0], parts[1], parts[2], assignment


def collect(experiment_name):
    """(cases, skipped): one case per conversation with a finished (done) replay run; `skipped` lists the transcript ids
    of the conversations without one."""
    from forum.models import Experiment, Message

    experiment = Experiment.objects.filter(name=experiment_name).first()
    if experiment is None:
        raise ValueError(f"no experiment named {experiment_name!r}")
    facts = {f.id: f for f in load_facts()}
    cases, skipped = [], []
    for conversation in experiment.conversations.order_by("transcript_id"):
        key = conversation.transcript_id
        run = (conversation.moderation_runs.filter(kind="replay", status="done").order_by("replicate", "id").first())
        if run is None:
            skipped.append(key)
            continue
        fact_id, side, arm, assignment = parse_transcript_id(key)
        planted = []
        for message in Message.objects.filter(conversation=conversation).order_by("seq_no"):
            planted.extend(message.planted or [])
        is_error = arm != "true"
        false_claim = true_claim = None
        if is_error and planted:
            false_claim, true_claim = planted[0].get("phrase"), planted[0].get("correction")
        if not true_claim:
            fact = facts.get(fact_id)
            true_claim = fact.claim_true if fact else ""
        level = int(arm[1]) if arm in ("l1", "l2", "l3") else None
        acts = list(run.acts.filter(validity="valid").order_by("order"))
        text = "\n\n".join(a.text for a in acts)
        issues = list(run.issues.order_by("id"))
        cases.append(Case(
            conversation_id=key.partition(":")[0], assignment=assignment, fact_id=fact_id, arm=arm, side=side, level=level,
            is_error_arm=is_error, false_claim=false_claim, true_claim=true_claim, run_status=run.status,
            intervened=bool(acts), n_issues=len(issues), n_acts=len(acts), response_text=text,
            response_words=len(text.split()), act_types=[a.act_type for a in acts],
            issue_quotes=[i.quote for i in issues if i.quote],
        ))
    return cases, skipped


def collect_cases(experiment_name) -> list[Case]:
    return collect(experiment_name)[0]


# --- One judgment -----------------------------------------------------------------------------------------------------

def _system():
    rubric = RUBRIC_PATH.read_text(encoding="utf-8").strip()
    return (PROMPT_DIR / "judge_v1.md").read_text(encoding="utf-8").replace("{{RUBRIC}}", rubric)


def _request(case):
    """(system, messages). Names only the claim, the true fact and the response; nothing about side, level, arm or fact id."""
    if case.is_error_arm:
        claim = (f"A false claim was planted in a participant's message. The seeded false claim:\n{case.false_claim}")
    else:
        claim = (f"No error was planted. The participant's message states this claim, which is TRUE:\n{case.true_claim}")
    response = case.response_text.strip() or NO_RESPONSE
    content = (f"{claim}\n\nThe true fact:\n{case.true_claim}\n\nThe moderator's response:\n{response}")
    return _system(), [{"role": "user", "content": content}]


def _no_response(case):
    out = JudgeOut(tag="0" if case.is_error_arm else "N/A", unseeded_flagged=0, rationale="no moderator response")
    return out, llm.LLMResult(parsed=out, call_id=0, stop_reason="not_called", input_tokens=0, output_tokens=0,
                              cache_write_tokens=0, cache_read_tokens=0, cost_usd=_ZERO, latency_ms=0)


def needs_call(case):
    return bool(case.response_text.strip())


def estimate_call_usd(case) -> Decimal:
    if not needs_call(case):
        return _ZERO
    system, messages = _request(case)
    tokens = budget.estimate_input_tokens(system=system, messages=messages, schema=JudgeOut)
    return budget.reservation_usd(tunables.JUDGE_MODEL_SEEDED, estimated_input_tokens=tokens,
                                  max_tokens=tunables.JUDGE_SEEDED_MAX_TOKENS)


def judge_case(case, *, session=None, attempt=1):
    """Returns (JudgeOut, LLMResult). No LLM call (and a zero-cost result with call_id 0) when nothing was posted."""
    if not needs_call(case):
        return _no_response(case)
    system, messages = _request(case)
    result = llm.call(
        purpose=PURPOSE, agent=AGENT, model=tunables.JUDGE_MODEL_SEEDED, system=system, messages=messages,
        output_schema=JudgeOut, max_tokens=tunables.JUDGE_SEEDED_MAX_TOKENS, prompt_version=PROMPT_VERSION,
        attempt=attempt, temperature=None, session=session,
    )
    return result.parsed, result


# --- Storage ----------------------------------------------------------------------------------------------------------

def judgments_path(experiment, directory=None):
    return Path(directory) / f"{experiment}.jsonl" if directory else DEFAULT_DIR / f"{experiment}.jsonl"


def _key(row):
    return (row["conversation_id"], row["assignment"], row["prompt_version"])


def load_done(path):
    path = Path(path)
    if not path.exists():
        return set()
    return {_key(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def judgment_row(case, out):
    return {**asdict(case), "tag": out.tag, "unseeded_flagged": out.unseeded_flagged, "rationale": out.rationale,
            "prompt_version": PROMPT_VERSION}


def append_judgment(path, case, out):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(judgment_row(case, out), ensure_ascii=False) + "\n")


def _drop_existing(path, keys):
    """Remove rows whose key is in `keys` (an overwrite)."""
    path = Path(path)
    if not path.exists():
        return
    kept = [line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and _key(json.loads(line)) not in keys]
    path.write_text("".join(line + "\n" for line in kept), encoding="utf-8")


# --- Running ----------------------------------------------------------------------------------------------------------

@dataclass
class JudgeReport:
    max_usd: Decimal
    judgments: list = field(default_factory=list)  # (case, JudgeOut)
    calls: int = 0
    skipped_existing: int = 0
    cost_total: Decimal = _ZERO
    failures: list = field(default_factory=list)  # (conversation_id, assignment, reason)
    stopped_reason: str | None = None

    def summary(self):
        lines = [
            f"Judged: {len(self.judgments)}, skipped (already judged): {self.skipped_existing}, failed: {len(self.failures)}.",
            f"Calls made: {self.calls}. Cost: ${self.cost_total:.6f} of a ${self.max_usd:.6f} limit.",
        ]
        for cid, assignment, reason in self.failures:
            lines.append(f"Failed {cid} ({assignment}): {reason}.")
        if self.stopped_reason:
            lines.append(f"Stopped early: {self.stopped_reason}.")
        return "\n".join(lines)

    __str__ = summary


class _Stop(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def run_judging(cases, *, max_usd, on_result=None, path=None, overwrite=False, session=None) -> JudgeReport:
    """Judge every case (cases needing no call cost nothing). Stops before any call whose worst case would take this run's judge
    spend over `max_usd` (equality is allowed); one retry on `LLMOutputError`. With `path`, each judgment is appended to that
    JSON Lines file and cases already there (same conversation, assignment, prompt version) are skipped unless `overwrite`."""
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
            outcome = None
            for attempt in (1, 2):
                if needs_call(case):
                    if not settings.LLM_ENABLED or not settings.ANTHROPIC_API_KEY:
                        raise _Stop("LLM calls are switched off (LLM_ENABLED is False or there is no key); nothing was judged")
                    worst = estimate_call_usd(case)
                    if spent() + worst > max_usd:
                        raise _Stop(f"the next call's worst case (${worst:.6f}) would pass the limit of ${max_usd:.6f}; "
                                    f"spent ${spent():.6f}")
                try:
                    if needs_call(case):
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
            out, _result = outcome
            report.judgments.append((case, out))
            if path is not None:
                append_judgment(path, case, out)
            if on_result is not None:
                on_result(case, out, _result)
    except _Stop as stop:
        report.stopped_reason = stop.reason
    report.cost_total = spent()
    return report
