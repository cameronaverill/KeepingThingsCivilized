"""Generate the base conversations of the seeded-error experiment through the LLM gateway (docs/plan.md section 9,
docs/step12_generator_brief.md).

For each ready fact: one left base is written by the model (`generator_v1.md`), then a right base that mirrors it
(`mirror_v1.md`); `seeding.arms` turns the pair into replay transcripts. Every call goes through `moderation.llm.call`
(the only gateway) with purpose `replay`; a failed or invalid base is retried once. Never call inside `transaction.atomic`.
"""
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

from django.conf import settings
from pydantic import BaseModel, ConfigDict

from config import tunables
from moderation import budget, llm
from moderation.errors import BudgetUnavailable, LLMAPIError, LLMDisabled, LLMOutputError, LLMRefused
from seeding.arms import AUTHORS, MARKER, BaseError, build_transcripts, check_pair, validate_base
from seeding.facts import Fact

PROMPT_DIR = Path(__file__).parent / "prompts"
DEFAULT_BASES_DIR = Path("generated") / "bases"
DEFAULT_REJECTED_DIR = Path("generated") / "rejected"
PURPOSE = "replay"
AGENT = "generator"
PROMPT_VERSIONS = {"generate": "gen_v7", "mirror": "mirror_v7"}
AUDIT_AGENT = "generator_audit"
AUDIT_PROMPT_VERSION = "audit_v1"
_PROMPT_FILES = {"generate": "generator_v1.md", "mirror": "mirror_v1.md"}
_ZERO = Decimal("0")


class MessageOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str


class BaseOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    messages: list[MessageOut]


class AuditOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    labels: list[Literal["pro", "con", "mixed"]]


class AuditResult(BaseModel):
    labels: list[str]
    passed: bool
    reason: str | None = None


# --- Prompt -----------------------------------------------------------------------------------------------------------

def _stance_words(side, author):
    pro = (side == "left") == (author == "Participant B")
    return "argues in favor of the proposition" if pro else "argues against the proposition"


def _kind(side, left_base):
    return "mirror" if side == "right" and left_base is not None else "generate"


def _render_left_base(left_base):
    return "\n\n".join(f"{m['author']}: {m['text']}" for m in left_base["messages"])


def _render_lengths(left_base):
    return "\n".join(f"- Message {m['seq']} ({m['author']}): {len(m['text'])} characters" for m in left_base["messages"])


def _request(fact, side, left_base=None, conversation_hint=None):
    """The (system, messages, kind) of one generation call. Nothing here names a variant, an arm or a false claim."""
    if side not in ("left", "right"):
        raise ValueError(f"unknown side {side!r}")
    if not fact.subject:
        raise ValueError(f"fact {fact.id} has no subject")
    kind = _kind(side, left_base)
    text = (PROMPT_DIR / _PROMPT_FILES[kind]).read_text(encoding="utf-8")
    hint = (f"ADDITIONAL NOTE: {conversation_hint.strip()}\n" if conversation_hint and conversation_hint.strip() else "")
    values = {
        "TITLE": tunables.GENERATOR_TOPIC_TITLE,
        "PROPOSITION": tunables.GENERATOR_TOPIC_PROPOSITION,
        "A_STANCE": _stance_words(side, "Participant A"),
        "B_STANCE": _stance_words(side, "Participant B"),
        "SUBJECT": fact.subject,
        "MIN": str(tunables.GENERATOR_MIN_MESSAGES),
        "MAX": str(tunables.GENERATOR_MAX_MESSAGES),
        "MARKER": MARKER,
        "HINT": hint,
        "LENGTHS": _render_lengths(left_base) if kind == "mirror" else "",
    }
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    if kind == "mirror":
        content = "The original transcript to mirror:\n\n" + _render_left_base(left_base)
    else:
        content = "Write the transcript now."
    return text, [{"role": "user", "content": content}], kind


# --- One call ---------------------------------------------------------------------------------------------------------

def _to_base(fact, side, parsed, prompt_version=None):
    messages = [{"seq": i, "author": AUTHORS[(i - 1) % 2], "text": m.text} for i, m in enumerate(parsed.messages, start=1)]
    base = {"side": side, "fact_id": fact.id, "messages": messages}
    if prompt_version:
        base["prompt_version"] = prompt_version
    try:
        validate_base(base)
    except BaseError as err:
        err.base = base  # so the caller can keep it for inspection
        raise
    return base


def _audit_max_tokens():
    return int(getattr(tunables, "GENERATOR_AUDIT_MAX_TOKENS", 300))


def _intended(side):
    return {"Participant A": "con" if side == "left" else "pro", "Participant B": "pro" if side == "left" else "con"}


def _audit_request(base):
    system = (PROMPT_DIR / "audit_v1.md").read_text(encoding="utf-8")
    system = system.replace("{{TITLE}}", tunables.GENERATOR_TOPIC_TITLE).replace("{{PROPOSITION}}", tunables.GENERATOR_TOPIC_PROPOSITION)
    body = "\n\n".join(
        f"Message {m['seq']}: " + m["text"].replace(MARKER, "[factual sentence]") for m in base["messages"])
    return system, [{"role": "user", "content": body}]


def audit_stances(base, side, *, session=None, attempt=1):
    """Have the model label each message pro/con/mixed; the base passes only if every message has its author's intended
    stance for this side. Returns (AuditResult, LLMResult)."""
    system, messages = _audit_request(base)
    result = llm.call(
        purpose=PURPOSE, agent=AUDIT_AGENT, model=tunables.GENERATOR_MODEL, system=system, messages=messages,
        output_schema=AuditOut, max_tokens=_audit_max_tokens(), prompt_version=AUDIT_PROMPT_VERSION,
        attempt=attempt, temperature=None, session=session,
    )
    labels = list(result.parsed.labels)
    intended = _intended(side)
    reason = None
    if len(labels) != len(base["messages"]):
        reason = f"the stance audit returned {len(labels)} labels for {len(base['messages'])} messages"
    else:
        for message, label in zip(base["messages"], labels):
            if label != intended[message["author"]]:
                reason = (f"message {message['seq']} by {message['author']} argues '{label}' but must argue "
                          f"'{intended[message['author']]}' in every message; labels: {', '.join(labels)}")
                break
    return AuditResult(labels=labels, passed=reason is None, reason=reason), result


def generate_base(fact, side, *, left_base=None, session=None, conversation_hint=None, attempt=1):
    """One generation call. Returns (base dict, LLMResult). Raises BaseError if the reply is not a valid base, and whatever
    `moderation.llm.call` raises (LLMRefused, LLMOutputError, LLMAPIError, BudgetUnavailable)."""
    system, messages, kind = _request(fact, side, left_base, conversation_hint)
    result = llm.call(
        purpose=PURPOSE, agent=AGENT, model=tunables.GENERATOR_MODEL, system=system, messages=messages,
        output_schema=BaseOut, max_tokens=tunables.GENERATOR_MAX_TOKENS, prompt_version=PROMPT_VERSIONS[kind],
        attempt=attempt, temperature=None, session=session,
    )
    base = _to_base(fact, side, result.parsed, PROMPT_VERSIONS[kind])
    try:
        audit, _ = audit_stances(base, side, session=session, attempt=attempt)
    except LLMOutputError as err:
        err.base = base
        raise
    if not audit.passed:
        err = BaseError(f"stance audit failed: {audit.reason}")
        err.base = base
        raise err
    return base, result


def _stub_left_base(fact):
    """A worst-case-sized left base (the longest a reply can be) to price a mirror call before the left base exists."""
    n = tunables.GENERATOR_MAX_MESSAGES
    chars = int(tunables.GENERATOR_MAX_TOKENS * float(settings.TOKEN_ESTIMATE_CHARS_PER_TOKEN)) // n
    messages = [{"seq": i, "author": "Participant A" if i % 2 else "Participant B", "text": "x" * chars} for i in range(1, n + 1)]
    return {"side": "left", "fact_id": fact.id, "messages": messages}


def estimate_call_usd(fact, side, left_base=None) -> Decimal:
    """Worst-case cost of one generation call, exactly what `llm.call` reserves."""
    if side == "right" and left_base is None:
        left_base = _stub_left_base(fact)
    system, messages, _ = _request(fact, side, left_base)
    tokens = budget.estimate_input_tokens(system=system, messages=messages, schema=BaseOut)
    cost = budget.reservation_usd(tunables.GENERATOR_MODEL, estimated_input_tokens=tokens, max_tokens=tunables.GENERATOR_MAX_TOKENS)
    return cost + _audit_estimate_usd(fact, side)


def _audit_estimate_usd(fact, side):
    n = tunables.GENERATOR_MAX_MESSAGES
    chars = int(tunables.GENERATOR_MAX_TOKENS * float(settings.TOKEN_ESTIMATE_CHARS_PER_TOKEN)) // n
    stub = {"side": side, "fact_id": fact.id,
            "messages": [{"seq": i, "author": AUTHORS[(i - 1) % 2], "text": "x" * chars} for i in range(1, n + 1)]}
    system, messages = _audit_request(stub)
    tokens = budget.estimate_input_tokens(system=system, messages=messages, schema=AuditOut)
    return budget.reservation_usd(tunables.GENERATOR_MODEL, estimated_input_tokens=tokens, max_tokens=_audit_max_tokens())


# --- Plan and run -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    fact_id: str
    side: str
    kind: str  # "generate" (left base) or "mirror" (right base)
    worst_case_usd: Decimal


def _attempts():
    return max(1, int(tunables.GENERATOR_MAX_ATTEMPTS))


def usable(fact) -> bool:
    """A fact can be generated for when it is ready and has a subject (the only thing the prompts may say about the claim)."""
    return fact.ready() and bool(fact.subject and fact.subject.strip())


def plan_generation(facts) -> list[Item]:
    items = []
    for fact in facts:
        if not usable(fact):
            continue
        items.append(Item(fact.id, "left", "generate", estimate_call_usd(fact, "left") * _attempts()))
        items.append(Item(fact.id, "right", "mirror", estimate_call_usd(fact, "right") * _attempts()))
    return items


@dataclass
class Report:
    max_usd: Decimal
    planned_facts: int = 0
    calls: int = 0
    bases_generated: int = 0
    bases_reused: int = 0
    facts_done: int = 0
    skipped_not_ready: list = field(default_factory=list)
    failures: list = field(default_factory=list)  # (fact_id, reason)
    transcripts: list = field(default_factory=list)
    cost_total: Decimal = _ZERO
    stopped_reason: str | None = None

    def summary(self):
        lines = [
            f"Facts planned: {self.planned_facts}, done: {self.facts_done}, failed: {len({f for f, _ in self.failures})}, "
            f"not ready (skipped): {len(self.skipped_not_ready)}.",
            f"Calls made: {self.calls}. Bases generated: {self.bases_generated}, reused from disk: {self.bases_reused}. "
            f"Transcripts built: {len(self.transcripts)}.",
            f"Cost: ${self.cost_total:.6f} of a ${self.max_usd:.6f} limit.",
        ]
        for fact_id, reason in self.failures:
            lines.append(f"Failed {fact_id}: {reason}.")
        if self.skipped_not_ready:
            lines.append("Not ready: " + ", ".join(self.skipped_not_ready) + ".")
        if self.stopped_reason:
            lines.append(f"Stopped early: {self.stopped_reason}.")
        return "\n".join(lines)

    __str__ = summary


class _Stop(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _Runner:
    def __init__(self, max_usd, session, bases_dir, overwrite, on_result):
        self.report = Report(max_usd=max_usd)
        self.max_usd = max_usd
        self.session = session
        self.bases_dir = Path(bases_dir) if bases_dir is not None else DEFAULT_BASES_DIR
        self.rejected_dir = (self.bases_dir.parent / "rejected") if bases_dir is not None else DEFAULT_REJECTED_DIR
        self.overwrite = overwrite
        self.on_result = on_result
        self.baseline = budget.spend(purposes=(PURPOSE,))

    def spent(self):
        return budget.spend(purposes=(PURPOSE,)) - self.baseline

    def path(self, fact, side):
        return self.bases_dir / f"{fact.id}_{side}.json"

    def load(self, fact, side):
        path = self.path(fact, side)
        if self.overwrite or not path.exists():
            return None
        base = json.loads(path.read_text(encoding="utf-8"))
        validate_base(base)
        if base.get("prompt_version") != PROMPT_VERSIONS["generate" if side == "left" else "mirror"]:
            return None  # written by an older prompt: regenerate
        if base["fact_id"] != fact.id or base["side"] != side:
            raise BaseError(f"{path.name} is not the {side} base of {fact.id}")
        self.report.bases_reused += 1
        return base

    def save(self, fact, base):
        self.bases_dir.mkdir(parents=True, exist_ok=True)
        self.path(fact, base["side"]).write_text(json.dumps(base, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def reject(self, fact, side, base):
        """Keep a base that failed validation or the pair check for inspection; it is never reused."""
        if base is None:
            return
        directory = self.rejected_dir
        directory.mkdir(parents=True, exist_ok=True)
        n = 1
        while (directory / f"{fact.id}_{side}_{n}.json").exists():
            n += 1
        (directory / f"{fact.id}_{side}_{n}.json").write_text(
            json.dumps(base, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def call_once(self, fact, side, left_base, attempt, hint):
        if not settings.LLM_ENABLED or not settings.ANTHROPIC_API_KEY:
            raise _Stop("LLM calls are switched off (LLM_ENABLED is False or there is no key); nothing was generated")
        worst = estimate_call_usd(fact, side, left_base)
        spent = self.spent()
        if spent + worst > self.max_usd:
            raise _Stop(f"the next call's worst case (${worst:.6f}) would pass the limit of ${self.max_usd:.6f}; spent ${spent:.6f}")
        try:
            self.report.calls += 1
            base, result = generate_base(
                fact, side, left_base=left_base, session=self.session, conversation_hint=hint, attempt=attempt
            )
        except LLMDisabled:
            raise _Stop("LLM calls are switched off (the gateway refused: kill switch or no key)") from None
        except LLMRefused as exc:
            raise _Stop(f"the guard refused a call ({type(exc).__name__}): {exc}") from None
        except BudgetUnavailable as exc:
            raise _Stop(f"the budget ledger could not be read: {exc}") from None
        except LLMAPIError as exc:
            raise _Stop(f"the API call failed (status {exc.status_code}, {exc.error_type or 'no type'})") from None
        self.report.bases_generated += 1
        if self.on_result is not None:
            self.on_result(fact, side, result)
        return base

    def with_retry(self, fact, side, left_base=None, hint=None):
        """Call up to GENERATOR_MAX_ATTEMPTS times; after an unusable reply the next call carries the specific refusal reason
        as its hint. Raises the last BaseError/LLMOutputError if every attempt fails."""
        attempts = max(1, int(tunables.GENERATOR_MAX_ATTEMPTS))
        for attempt in range(1, attempts + 1):
            try:
                return self.call_once(fact, side, left_base, attempt, hint)
            except (BaseError, LLMOutputError) as err:
                self.reject(fact, side, getattr(err, "base", None))
                if attempt == attempts:
                    raise
                reason = str(err) if isinstance(err, BaseError) else (err.reason or "the previous reply was unusable")
                hint = f"The previous attempt was refused: {reason}."

    def do_fact(self, fact):
        left = self.load(fact, "left")
        if left is None:
            left = self.with_retry(fact, "left")
            self.save(fact, left)
        right = self.load(fact, "right")
        if right is not None:
            try:
                check_pair(left, right)
            except BaseError:
                right = None  # a saved right base that no longer matches the left one is regenerated, not reused
        if right is None:
            right = self.with_retry(fact, "right", left)
            try:
                check_pair(left, right)
            except BaseError as err:
                self.reject(fact, "right", right)
                right = self.with_retry(fact, "right", left, hint=f"The mirrored version did not match the original: {err}.")
                try:
                    check_pair(left, right)
                except BaseError:
                    self.reject(fact, "right", right)
                    raise
            self.save(fact, right)
        return build_transcripts(fact, left, right)


def run_generation(facts, *, max_usd, session=None, bases_dir=None, overwrite=False, on_result=None) -> Report:
    """Generate (or reuse) both bases of every ready fact and build their transcripts. Stops before any call whose worst case
    would take this run's spend over `max_usd` (equality is allowed). Returns a `Report`; the transcripts are in
    `report.transcripts` and nothing but the bases is written here."""
    max_usd = Decimal(str(max_usd))
    if not max_usd.is_finite() or max_usd < 0:
        raise ValueError("max_usd must be a non-negative number of dollars")
    session = session or budget.SessionBudget(max_usd)
    runner = _Runner(max_usd, session, bases_dir, overwrite, on_result)
    report = runner.report
    ready = []
    for fact in facts:
        if usable(fact):
            ready.append(fact)
        else:
            report.skipped_not_ready.append(fact.id)
    report.planned_facts = len(ready)
    try:
        for fact in ready:
            try:
                report.transcripts.extend(runner.do_fact(fact))
                report.facts_done += 1
            except (BaseError, LLMOutputError, ValueError) as err:
                reason = err.reason if isinstance(err, LLMOutputError) and err.reason else type(err).__name__
                report.failures.append((fact.id, reason if not isinstance(err, BaseError) else f"invalid base: {err}"))
    except _Stop as stop:
        report.stopped_reason = stop.reason
    report.cost_total = runner.spent()
    return report
