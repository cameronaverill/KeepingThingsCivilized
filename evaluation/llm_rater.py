"""The LLM rater runner (plan section 9, docs/step14_brief.md, section 14a).

An LLM rater reads ONE message through `evaluation.blinding.blinded_view` (the proposition, the message and a few
earlier messages, nothing else), makes one guarded call through `moderation.llm.call` (`purpose="judge"`) and gets back
phrase-level findings. This module turns them into a `Rating` with `Finding` rows, and `run_panel` does that for every
active LLM rater of a panel over many targets under a spending cap, then builds the consensus.

This is the ONLY module of the `evaluation` app that may import `moderation.llm`, `moderation.errors` and
`moderation.quotes` (a test pins that), and no site app imports `evaluation`.

Public API
    load_rater_prompt(dimensions, *, rubrics_dir=None) -> RaterPrompt(name, text, sha256, rubrics)
    rate_target(rater, target, *, dimensions=None, replicate=1, session=None) -> RateResult
    run_panel(panel, targets, *, replicates=1, max_usd, dimensions=None, on_result=None) -> RunReport
    plan_panel(panel, targets, *, replicates=1, dimensions=None) -> PanelPlan          (no write, no call)
    estimate_call_usd(model, target, dimensions=None) -> Decimal                      (worst case of one call)
    build_panel_consensus(panel, target) -> list[ConsensusFinding]                    (saved; idempotent)

Choices made where the brief is silent (all reported to the architect)
- Rubrics come from `settings.RATER_RUBRICS_DIR` (a tunable; None = the repository's `rubrics/`) or the `rubrics_dir`
  argument of the loader; `rubrics/<dimension>_v<N>.md`, the highest N present. The loader lives here, not in a separate module.
- Reasons a finding is dropped (`RejectedFinding.reason`): `duplicate_local_id`, `invalid_local_id`,
  `dimension_not_requested`, `missing_reason` (no intensity and no reason), `intensity_and_reason`, `intensity_out_of_range`,
  `quote_not_found`, `invalid_finding` (anything `Finding.save()` still refused). A reason never contains message text.
  A confidence outside 0..1 is not a reason to drop the finding: it is stored as null.
- The stored `quote` is the message's own slice at the located offsets (so `quote == text[start:end]`), not the model's copy.
- `rate_target` rates unconditionally (it never looks for an earlier rating); only `run_panel` resumes.
- `run_panel` skips a (rater, target, replicate) that has ANY `done` rating, whatever dimensions it covered.
- Consensus: built from each panel member's `done` rating with the smallest replicate number (replicates exist to measure
  noise, not to change the ground truth), once every active LLM rater of the panel has one; human raters count only when
  they are panel members and have a done rating; nothing is built when a consensus for (panel, target) already exists.
- With `LLM_ENABLED` False or no key (or when a call raises `LLMDisabled`), no call is made and NOTHING is written: the run
  stops and every remaining (rater, target, replicate) is reported as `not_run` with reason `llm_disabled` (architect
  ruling; a `failed` rating is stored only when a call was really made and its output was unusable twice).
- A guard refusal other than the kill switch (budget, breaker, model), `BudgetUnavailable` and `LLMAPIError` end the run
  cleanly: the rest is `not_run` and `stopped_reason` says why. Nothing is retried but a structural failure, once.
"""
import hashlib
import logging
import math
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from moderation import budget, clock, pricing, taxonomy
from moderation import llm
from moderation.errors import BudgetUnavailable, LLMAPIError, LLMDisabled, LLMOutputError, LLMRefused
from moderation.models import InterventionAct, LLMCall
from moderation.quotes import NOT_FOUND, locate_quote
from forum.models import Message

from .blinding import blinded_view
from .consensus import build_consensus
from .models import ConsensusFinding, Finding, Rating
from .schemas import RaterOutput
from .targets import target_ref, target_text

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
DEFAULT_RUBRICS_DIR = Path(__file__).resolve().parent.parent / "rubrics"
RATER_PROMPT = "rater_v1"
JUDGE_PURPOSE = "judge"
_ZERO = Decimal("0")

_COVERAGE_TEXT = {
    "all_claims": (
        "report every checkable claim in the message, including the ones that are accurate (intensity 0)."
    ),
    "flagged_only": (
        "report only phrases with an intensity of 1 or higher; do not report phrases that would be 0. If there is "
        "none, list this dimension in no_issues_in."
    ),
}


# --- Prompt and rubrics ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RaterPrompt:
    name: str  # the instruction file's stem, for example "rater_v1"
    text: str  # the system prompt: the fixed instructions, then each requested dimension's coverage rule and rubric
    sha256: str  # sha256 of `text` (UTF-8), as hex
    rubrics: dict  # {dimension: {"rubric": "factual_accuracy_v1", "sha256": <of the rubric file's bytes>}}

    @property
    def guideline_version(self):
        """What a `Rating` records: the prompt name and the rubric names, for example "rater_v1+abusiveness_v1"."""
        return "+".join([self.name] + [entry["rubric"] for entry in self.rubrics.values()])


def default_rubrics_dir():
    configured = getattr(settings, "RATER_RUBRICS_DIR", None)
    return Path(configured) if configured else DEFAULT_RUBRICS_DIR


def _rubric_path(dimension, directory):
    """The highest-version `<dimension>_v<N>.md` in `directory`; FileNotFoundError if there is none."""
    candidates = []
    for path in Path(directory).glob(f"{dimension}_v*.md"):
        suffix = path.stem[len(dimension) + 2 :]
        if suffix.isdigit():
            candidates.append((int(suffix), path))
    if not candidates:
        raise FileNotFoundError(f"no rubric file {dimension}_v<N>.md in {directory}")
    return max(candidates)[1]


def available_dimensions(directory=None):
    """The taxonomy's dimensions that have a rubric file, in taxonomy order."""
    directory = Path(directory) if directory else default_rubrics_dir()
    found = []
    for dimension in taxonomy.DIMENSIONS:
        try:
            _rubric_path(dimension, directory)
        except FileNotFoundError:
            continue
        found.append(dimension)
    return found


def _clean_dimensions(dimensions, directory=None):
    """The requested dimensions, validated, without duplicates, in taxonomy order. None = every dimension with a rubric."""
    if dimensions is None:
        dimensions = available_dimensions(directory)
    if isinstance(dimensions, str):
        dimensions = [dimensions]
    dimensions = list(dimensions)
    unknown = [d for d in dimensions if d not in taxonomy.DIMENSIONS]
    if unknown:
        raise ValueError(f"unknown dimension(s) {unknown}; use {list(taxonomy.DIMENSIONS)}")
    if not dimensions:
        raise ValueError("at least one dimension is needed")
    return [d for d in taxonomy.DIMENSIONS if d in dimensions]


def load_rater_prompt(dimensions, *, rubrics_dir=None):
    """The fixed instructions (`evaluation/prompts/rater_v1.md`) plus the rubric of each requested dimension. Nothing in
    the text varies per call, so it can be cached as one block."""
    directory = Path(rubrics_dir) if rubrics_dir else default_rubrics_dir()
    dimensions = _clean_dimensions(dimensions, directory)
    instructions = (PROMPTS_DIR / f"{RATER_PROMPT}.md").read_text(encoding="utf-8").rstrip()
    sections = []
    refs = {}
    for dimension in dimensions:
        path = _rubric_path(dimension, directory)
        data = path.read_bytes()
        refs[dimension] = {"rubric": path.stem, "sha256": hashlib.sha256(data).hexdigest()}
        coverage = _COVERAGE_TEXT[taxonomy.DIMENSIONS[dimension]["coverage"]]
        sections.append(
            f"=== Dimension: {dimension} (rubric {path.stem}) ===\nCoverage: {coverage}\n\n{data.decode('utf-8').strip()}"
        )
    text = instructions + "\n\nREQUESTED DIMENSIONS AND RUBRICS\n\n" + "\n\n".join(sections) + "\n"
    return RaterPrompt(RATER_PROMPT, text, hashlib.sha256(text.encode("utf-8")).hexdigest(), refs)


# --- The input ------------------------------------------------------------------------------------------------------

def _escape(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_input(view, dimensions):
    """The user turn, from a `blinded_view` dict and nothing else. Everything a participant wrote is escaped DATA in
    delimited blocks, so it can never close a block or forge another one."""
    parts = []
    if view["proposition"]:
        parts.append(f"<proposition>{_escape(view['proposition'])}</proposition>")
    if view["context"]:
        blocks = "\n".join(f"<earlier_message>\n{_escape(item['text'])}\n</earlier_message>" for item in view["context"])
        parts.append(f"<context>\n{blocks}\n</context>")
    message = view["message"]
    attribute = ' automated_reply="true"' if message["automated_reply"] else ""
    parts.append(f"<message_to_rate{attribute}>\n{_escape(message['text'])}\n</message_to_rate>")
    parts.append(
        f"Requested dimensions: {', '.join(dimensions)}. Rate only the message in <message_to_rate>, following your "
        "instructions."
    )
    return "\n\n".join(parts)


def _check_target(target):
    if isinstance(target, Message):
        if target.author_type != "user":
            raise ValueError("only a user message (or a moderator's intervention act) can be rated, not a moderator message")
    elif not isinstance(target, InterventionAct):
        raise ValueError(f"a target must be a Message or an InterventionAct, not {type(target).__name__}")


def _conversation_id(target):
    if isinstance(target, Message):
        return target.conversation_id
    return target.run.conversation_id


def _request(target, dimensions, prompt):
    _check_target(target)
    view = blinded_view(target)
    return prompt.text, [{"role": "user", "content": render_input(view, dimensions)}]


def estimate_call_usd(model, target, dimensions=None, *, prompt=None):
    """The worst-case cost of one rating call, exactly what `llm.call` will reserve for it (all input at the dearest input
    price plus `RATER_MAX_TOKENS` of output). Raises ModelNotAllowed for a model that is not in the price table."""
    dimensions = _clean_dimensions(dimensions)
    prompt = prompt or load_rater_prompt(dimensions)
    system, messages = _request(target, dimensions, prompt)
    tokens = budget.estimate_input_tokens(system=system, messages=messages, schema=RaterOutput)
    return budget.reservation_usd(model, estimated_input_tokens=tokens, max_tokens=settings.RATER_MAX_TOKENS)


# --- Rating one target ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RejectedFinding:
    local_id: str
    reason: str  # a short code (see the module docstring); never message text


@dataclass
class RateResult:
    rating: Rating
    findings: list  # the stored Finding rows
    rejected: list  # RejectedFinding
    call_ids: list  # the ledger rows of every attempt, in order
    cost_usd: Decimal
    no_issues_in: list = field(default_factory=list)
    failure_reason: str = ""  # "" when the rating is done

    @property
    def status(self):
        return self.rating.status


def _valid_confidence(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0.0 <= value <= 1.0 else None


def _intensity_problem(finding):
    """A reason code when the finding breaks the intensity/reason rule, else None."""
    if finding.intensity is None:
        return None if finding.not_scorable_reason in taxonomy.NOT_SCORABLE_REASONS else "missing_reason"
    if isinstance(finding.intensity, bool) or not isinstance(finding.intensity, int):
        return "intensity_out_of_range"
    low, high = taxonomy.INTENSITY_RANGE
    if not low <= finding.intensity <= high:
        return "intensity_out_of_range"
    return "intensity_and_reason" if finding.not_scorable_reason else None


def _store_findings(rating, text, dimensions, output):
    """Store the usable findings of `output` under `rating`; return (stored, rejected)."""
    stored, rejected, seen = [], [], set()
    for item in output.findings:
        local_id = item.local_id
        reason = None
        if not local_id or len(local_id) > Finding._meta.get_field("local_id").max_length:
            reason = "invalid_local_id"
        elif local_id in seen:
            reason = "duplicate_local_id"
        elif item.dimension not in dimensions:
            reason = "dimension_not_requested"
        else:
            reason = _intensity_problem(item)
        match = None
        if reason is None:
            match = locate_quote(text, item.quote)
            if match.match == NOT_FOUND:
                reason = "quote_not_found"
        if reason is None:
            row = Finding(
                rating=rating,
                local_id=local_id,
                dimension=item.dimension,
                start=match.start,
                end=match.end,
                quote=text[match.start : match.end],
                intensity=item.intensity,
                not_scorable_reason=item.not_scorable_reason or "",
                confidence=_valid_confidence(item.confidence),
                detail=item.detail if isinstance(item.detail, dict) else {},
            )
            try:
                with transaction.atomic():
                    row.save()
            except (ValidationError, IntegrityError):
                reason = "invalid_finding"
            else:
                stored.append(row)
                seen.add(local_id)
                continue
        rejected.append(RejectedFinding(local_id=local_id, reason=reason))
        seen.add(local_id)
    return stored, rejected


def _ledger_cost(call_ids):
    total = _ZERO
    for row in LLMCall.objects.filter(pk__in=[c for c in call_ids if c is not None]):
        total += row.cost_usd if row.cost_usd is not None else row.reserved_usd
    return total


def _store_rating(rater, target, dimensions, replicate, prompt, *, status, started, llm_call_id):
    target_type, target_id = target_ref(target)
    return Rating.objects.create(
        rater=rater,
        target_type=target_type,
        target_id=target_id,
        dimensions=list(dimensions),
        replicate=replicate,
        llm_call_id=llm_call_id,
        guideline_version=prompt.guideline_version,
        status=status,
        started_at=started,
        finished_at=clock.now(),
    )


def _check_rater(rater):
    if getattr(rater, "kind", None) != "llm" or not rater.active:
        raise ValueError("the rater must be an active LLM rater")


def _check_replicate(replicate):
    if isinstance(replicate, bool) or not isinstance(replicate, int) or replicate < 1:
        raise ValueError(f"replicate must be a whole number of at least 1, not {replicate!r}")


def _rate(rater, target, dimensions, replicate, prompt, session):
    system, messages = _request(target, dimensions, prompt)
    text = target_text(target)
    started = clock.now()
    call_ids, last_error, result = [], None, None
    for attempt in (1, 2):
        kwargs = dict(
            purpose=JUDGE_PURPOSE,
            agent="rater",
            model=rater.model,
            system=system,
            messages=messages,
            output_schema=RaterOutput,
            max_tokens=settings.RATER_MAX_TOKENS,
            prompt_version=prompt.name,
            attempt=attempt,
            run_id=None,
            conversation_id=_conversation_id(target),
            cache_system=True,
        )
        if session is not None:
            kwargs["session"] = session
        try:
            result = llm.call(**kwargs)
        except LLMOutputError as err:
            last_error = err
            if err.call_id is not None:
                call_ids.append(err.call_id)
            continue
        call_ids.append(result.call_id)
        break

    with transaction.atomic():
        if result is None:
            rating = _store_rating(
                rater, target, dimensions, replicate, prompt,
                status="failed", started=started, llm_call_id=call_ids[-1] if call_ids else None,
            )
            stored, rejected, no_issues = [], [], []
        else:
            rating = _store_rating(
                rater, target, dimensions, replicate, prompt,
                status="done", started=started, llm_call_id=result.call_id,
            )
            stored, rejected = _store_findings(rating, text, dimensions, result.parsed)
            no_issues = [d for d in result.parsed.no_issues_in if d in dimensions]
    reason = "" if result is not None else (last_error.reason or "invalid_output")
    logger.info(
        "rating %s (%s) of %s %s replicate %s: %s, %d finding(s) stored, %d rejected",
        rating.pk, rater.name, rating.target_type, rating.target_id, replicate, rating.status, len(stored), len(rejected),
    )
    return RateResult(
        rating=rating,
        findings=stored,
        rejected=rejected,
        call_ids=call_ids,
        cost_usd=_ledger_cost(call_ids),
        no_issues_in=no_issues,
        failure_reason=reason,
    )


def rate_target(rater, target, *, dimensions=None, replicate=1, session=None):
    """One rater rates one target once. Raises ValueError for a rater that is not an active LLM rater, a target that
    cannot be rated (a moderator message), an unknown dimension or a bad replicate number. `LLMRefused`,
    `BudgetUnavailable` and `LLMAPIError` propagate (no rating is stored); an unusable output is retried once with
    `attempt=2` and then stored as a `failed` rating with no findings. `session` is an optional `SessionBudget`."""
    _check_rater(rater)
    _check_replicate(replicate)
    _check_target(target)
    dimensions = _clean_dimensions(dimensions)
    prompt = load_rater_prompt(dimensions)
    return _rate(rater, target, dimensions, replicate, prompt, session)


# --- Consensus ------------------------------------------------------------------------------------------------------

def build_panel_consensus(panel, target):
    """Build and save the consensus findings of `target` once every active LLM rater of the panel has a `done` rating for
    it (human panel members' done ratings are included when present). Idempotent: returns [] when a consensus for this
    panel and target already exists or a rater is still missing."""
    target_type, target_id = target_ref(target)
    llm_raters = list(panel.raters.filter(kind="llm", active=True))
    if not llm_raters:
        return []
    if ConsensusFinding.objects.filter(panel=panel, target_type=target_type, target_id=target_id).exists():
        return []
    findings_by_rater = {}
    llm_ids = {rater.pk for rater in llm_raters}
    for rater in panel.raters.filter(active=True).order_by("name"):
        rating = (
            Rating.objects.filter(rater=rater, target_type=target_type, target_id=target_id, status="done")
            .order_by("replicate", "pk")
            .first()
        )
        if rating is None:
            if rater.pk in llm_ids:
                return []
            continue
        findings_by_rater[rater] = list(rating.findings.all())
    return build_consensus(panel, target, findings_by_rater, save=True)


# --- The panel run --------------------------------------------------------------------------------------------------

@dataclass
class PlannedRating:
    rater: object
    target: object
    replicate: int
    done: bool  # a done rating exists already: skipped at no cost
    worst_case_usd: Decimal  # zero when done


@dataclass
class PanelPlan:
    dimensions: list
    prompt: RaterPrompt
    items: list  # PlannedRating, in run order (replicate-major, then target order, then rater name)

    @property
    def to_do(self):
        return [item for item in self.items if not item.done]

    @property
    def worst_case_usd(self):
        return sum((item.worst_case_usd for item in self.items), _ZERO)


def _panel_raters(panel):
    return list(panel.raters.filter(kind="llm", active=True).order_by("name"))


def _panel_dimensions(panel, dimensions):
    if dimensions is None and panel.dimensions:
        dimensions = list(panel.dimensions)
    return _clean_dimensions(dimensions)


def _is_done(rater, target, replicate):
    target_type, target_id = target_ref(target)
    return Rating.objects.filter(
        rater=rater, target_type=target_type, target_id=target_id, replicate=replicate, status="done"
    ).exists()


def plan_panel(panel, targets, *, replicates=1, dimensions=None):
    """What `run_panel` would do, in its order, with each call's worst-case cost. Writes nothing and calls nothing."""
    _check_replicate(replicates)
    targets = list(targets)
    for target in targets:
        _check_target(target)
    dimensions = _panel_dimensions(panel, dimensions)
    prompt = load_rater_prompt(dimensions)
    raters = _panel_raters(panel)
    for rater in raters:
        pricing.get_price(rater.model)  # ModelNotAllowed before anything is done
    estimates = {}
    items = []
    for replicate in range(1, replicates + 1):
        for target in targets:
            for rater in raters:
                done = _is_done(rater, target, replicate)
                cost = _ZERO
                if not done:
                    key = (rater.model, target_ref(target))
                    if key not in estimates:
                        estimates[key] = estimate_call_usd(rater.model, target, dimensions, prompt=prompt)
                    cost = estimates[key]
                items.append(PlannedRating(rater, target, replicate, done, cost))
    return PanelPlan(dimensions, prompt, items)


@dataclass
class RunReport:
    max_usd: Decimal
    planned: int = 0  # (rater, target, replicate) triples considered
    done: int = 0
    failed: int = 0
    skipped_existing: int = 0
    not_run: list = field(default_factory=list)  # {"rater", "target_type", "target_id", "replicate", "reason"}
    cost_total: Decimal = _ZERO
    cost_by_rater: dict = field(default_factory=dict)
    rejected_total: int = 0
    rejected_by_reason: dict = field(default_factory=dict)
    rejected_by_rater: dict = field(default_factory=dict)
    failure_reasons: dict = field(default_factory=dict)  # reason -> count of failed ratings (also when nothing is stored)
    consensus_targets: int = 0  # targets for which a consensus was built in this call
    stopped_reason: str = ""
    results: list = field(default_factory=list)  # RateResult, in run order

    @property
    def counts(self):
        return {
            "done": self.done,
            "failed": self.failed,
            "skipped_existing": self.skipped_existing,
            "not_run": len(self.not_run),
        }

    def as_dict(self):
        return {
            "max_usd": str(self.max_usd),
            "planned": self.planned,
            "counts": self.counts,
            "cost_total": str(self.cost_total),
            "cost_by_rater": {name: str(value) for name, value in self.cost_by_rater.items()},
            "rejected_total": self.rejected_total,
            "rejected_by_reason": dict(self.rejected_by_reason),
            "rejected_by_rater": dict(self.rejected_by_rater),
            "failure_reasons": dict(self.failure_reasons),
            "consensus_targets": self.consensus_targets,
            "not_run": list(self.not_run),
            "stopped_reason": self.stopped_reason,
        }

    def summary(self):
        lines = [
            f"Ratings: {self.done} done, {self.failed} failed, {self.skipped_existing} already done (skipped), "
            f"{len(self.not_run)} not run (of {self.planned}).",
            f"Cost: ${self.cost_total:.6f} of a ${self.max_usd:.6f} limit.",
        ]
        if self.cost_by_rater:
            lines.append("Cost by rater: " + ", ".join(f"{n} ${v:.6f}" for n, v in sorted(self.cost_by_rater.items())) + ".")
        if self.rejected_total:
            reasons = ", ".join(f"{k} {v}" for k, v in sorted(self.rejected_by_reason.items()))
            lines.append(f"Findings dropped: {self.rejected_total} ({reasons}).")
        if self.failure_reasons:
            lines.append("Failed ratings by reason: " + ", ".join(f"{k} {v}" for k, v in sorted(self.failure_reasons.items())) + ".")
        lines.append(f"Consensus built for {self.consensus_targets} target(s).")
        if self.stopped_reason:
            lines.append(f"Stopped early: {self.stopped_reason}.")
        return "\n".join(lines)

    def __str__(self):
        return self.summary()


def _not_run_entry(item):
    target_type, target_id = target_ref(item.target)
    return {"rater": item.rater.name, "target_type": target_type, "target_id": target_id, "replicate": item.replicate}


def _record(report, item, result):
    report.results.append(result)
    report.cost_total += result.cost_usd
    name = item.rater.name
    report.cost_by_rater[name] = report.cost_by_rater.get(name, _ZERO) + result.cost_usd
    if result.status == "done":
        report.done += 1
    else:
        report.failed += 1
        reason = result.failure_reason or "failed"
        report.failure_reasons[reason] = report.failure_reasons.get(reason, 0) + 1
    for rejected in result.rejected:
        report.rejected_total += 1
        report.rejected_by_reason[rejected.reason] = report.rejected_by_reason.get(rejected.reason, 0) + 1
        report.rejected_by_rater[name] = report.rejected_by_rater.get(name, 0) + 1


def run_panel(panel, targets, *, replicates=1, max_usd, dimensions=None, on_result=None):
    """Rate every target with every active LLM rater of the panel, replicate-major, then in target order, then by rater
    name; skip what already has a `done` rating; stop before a call whose worst case would take the spend of this call (from
    the `judge` ledger) over `max_usd` (equality is allowed) and report the rest as `not_run`; then build the consensus of
    each target as soon as all its raters are done. Returns a `RunReport`. See the module docstring for the rest."""
    max_usd = Decimal(str(max_usd))
    if not max_usd.is_finite() or max_usd < 0:
        raise ValueError("max_usd must be a non-negative number of dollars")
    plan = plan_panel(panel, targets, replicates=replicates, dimensions=dimensions)
    report = RunReport(max_usd=max_usd, planned=len(plan.items))
    session = budget.SessionBudget(max_usd)
    baseline = budget.spend(purposes=(JUDGE_PURPOSE,))
    for index, item in enumerate(plan.items):
        if _is_done(item.rater, item.target, item.replicate):
            report.skipped_existing += 1
            _consensus(report, panel, item.target)
            continue
        if not settings.LLM_ENABLED or not settings.ANTHROPIC_API_KEY:
            report.stopped_reason = "LLM calls are switched off (LLM_ENABLED is False or there is no key); nothing was written"
            _stop(report, plan.items[index:], "llm_disabled")
            break
        spent = budget.spend(purposes=(JUDGE_PURPOSE,)) - baseline
        if spent + item.worst_case_usd > max_usd:
            report.stopped_reason = (
                f"the next call's worst case (${item.worst_case_usd:.6f}) would pass the limit of "
                f"${max_usd:.6f}; spent ${spent:.6f}"
            )
            _stop(report, plan.items[index:], "budget")
            break
        try:
            result = _rate(item.rater, item.target, plan.dimensions, item.replicate, plan.prompt, session)
        except LLMDisabled:
            report.stopped_reason = "LLM calls are switched off (the gateway refused: kill switch or no key); nothing was written"
            _stop(report, plan.items[index:], "llm_disabled")
            break
        except LLMRefused as exc:
            report.stopped_reason = f"the guard refused a call ({type(exc).__name__}): {exc}"
            _stop(report, plan.items[index:], "guard_refused")
            break
        except BudgetUnavailable as exc:
            report.stopped_reason = f"the budget ledger could not be read: {exc}"
            _stop(report, plan.items[index:], "budget_unavailable")
            break
        except LLMAPIError as exc:
            report.stopped_reason = f"the API call failed (status {exc.status_code}, {exc.error_type or 'no type'})"
            _stop(report, plan.items[index:], "api_error")
            break
        _record(report, item, result)
        if on_result is not None:
            on_result(result)
        _consensus(report, panel, item.target)
    return report


def _stop(report, remaining, reason):
    report.not_run = [
        {**_not_run_entry(item), "reason": reason}
        for item in remaining
        if not _is_done(item.rater, item.target, item.replicate)
    ]


def _consensus(report, panel, target):
    if build_panel_consensus(panel, target):
        report.consensus_targets += 1
