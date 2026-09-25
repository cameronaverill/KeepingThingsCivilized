"""The budget guard: ledger sums, caps, reservations and the worst-case cost of one moderation run.

Every number comes from config/tunables.py (exposed as Django settings). All amounts are Decimal USD.
"""
import json
import math
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Case, DecimalField, F, Sum, Value, When
from django.db.models.functions import Coalesce

from moderation import clock, pricing
from moderation.errors import BudgetExceeded, BudgetUnavailable
from moderation.models import LLMCall

SITE_PURPOSES = ("moderation", "spike", "golden")
EVAL_PURPOSES = ("replay", "judge")

_ZERO = Decimal("0")


class SessionBudget:
    """An in-memory cap for one command run (`--max-usd`). `spent` includes reservations that are still in flight."""

    def __init__(self, limit_usd):
        self.limit = Decimal(str(limit_usd))
        self.spent = _ZERO

    def __repr__(self):
        return f"SessionBudget(limit={self.limit}, spent={self.spent})"


def _text_chars(content):
    """Characters in a message's content: a string, or a list of blocks (dicts with `text`, or plain strings)."""
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    if isinstance(content, (list, tuple)):
        return sum(_text_chars(block) for block in content)
    if isinstance(content, dict):
        if "text" in content:
            return _text_chars(content["text"])
        if "content" in content:
            return _text_chars(content["content"])
        return len(json.dumps(content, sort_keys=True))
    return len(str(content))


def _schema_chars(schema):
    if schema is None:
        return 0
    if isinstance(schema, dict):
        return len(json.dumps(schema, sort_keys=True))
    if hasattr(schema, "model_json_schema"):  # a Pydantic model class
        return len(json.dumps(schema.model_json_schema(), sort_keys=True))
    return len(str(schema))


def estimate_input_tokens(*, system, messages, schema=None):
    """A local, network-free estimate of the request's input tokens that deliberately over-counts:
    ceil(characters / TOKEN_ESTIMATE_CHARS_PER_TOKEN) + TOKEN_ESTIMATE_OVERHEAD_TOKENS.
    `schema` may be a JSON-schema dict or a Pydantic model class; it is counted because the API injects it into the prompt."""
    chars = _text_chars(system)
    chars += sum(_text_chars(message.get("content") if isinstance(message, dict) else message) for message in messages)
    chars += _schema_chars(schema)
    per_token = Decimal(str(settings.TOKEN_ESTIMATE_CHARS_PER_TOKEN))
    return math.ceil(Decimal(chars) / per_token) + int(settings.TOKEN_ESTIMATE_OVERHEAD_TOKENS)


def reservation_usd(model, *, estimated_input_tokens, max_tokens):
    """Worst-case cost of one call: ALL input at the cache-write price (1.25x, the dearest input price), plus
    `max_tokens` of output at the output price. Caching is never counted on."""
    return pricing.compute_cost(model, cache_write_tokens=estimated_input_tokens, output_tokens=max_tokens)


def _counted_amount():
    """SQL expression: what a ledger row counts for. ok/error at cost_usd (reserved_usd if the cost is unknown),
    pending at reserved_usd, refused_* never."""
    field = DecimalField(max_digits=12, decimal_places=6)
    return Case(
        When(status__in=("ok", "error"), then=Coalesce(F("cost_usd"), F("reserved_usd"))),
        When(status="pending", then=F("reserved_usd")),
        default=Value(_ZERO),
        output_field=field,
    )


def spend(*, purposes=None, since=None, until=None, conversation_id=None):
    """Ledger sum in USD. `since` is inclusive and `until` exclusive (on created_at)."""
    rows = LLMCall.objects.all()
    if purposes is not None:
        rows = rows.filter(purpose__in=list(purposes))
    if since is not None:
        rows = rows.filter(created_at__gte=since)
    if until is not None:
        rows = rows.filter(created_at__lt=until)
    if conversation_id is not None:
        rows = rows.filter(conversation_id=conversation_id)
    total = rows.aggregate(total=Sum(_counted_amount()))["total"]
    return Decimal(total) if total is not None else _ZERO


def utc_day_bounds(moment=None):
    """(start, end) of the UTC day containing `moment` (default: now). `end` is the start of the next day."""
    moment = moment or clock.now()
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _check(cap_name, limit, spent, amount):
    if spent + amount > limit:
        raise BudgetExceeded(cap_name=cap_name, limit=limit, spent=spent, requested=amount)


def check_caps(*, purpose, conversation_id, amount, session=None):
    """Raise BudgetExceeded if adding `amount` would pass any cap that applies. Reaching a cap exactly is allowed.
    Cap names: site_total, site_day, spike_total, conversation, eval_total, session."""
    amount = Decimal(amount)
    try:
        if purpose in SITE_PURPOSES:
            _check("site_total", settings.BUDGET_SITE_USD_TOTAL, spend(purposes=SITE_PURPOSES), amount)
            start, end = utc_day_bounds()
            _check(
                "site_day",
                settings.BUDGET_SITE_USD_PER_DAY,
                spend(purposes=SITE_PURPOSES, since=start, until=end),
                amount,
            )
            if purpose == "spike":
                _check("spike_total", settings.BUDGET_SPIKE_USD_TOTAL, spend(purposes=("spike",)), amount)
            if purpose == "moderation" and conversation_id is not None:
                _check(
                    "conversation",
                    settings.BUDGET_PER_CONVERSATION_USD,
                    spend(purposes=("moderation",), conversation_id=conversation_id),
                    amount,
                )
        elif purpose in EVAL_PURPOSES:
            _check("eval_total", settings.BUDGET_EVAL_USD_TOTAL, spend(purposes=EVAL_PURPOSES), amount)
        else:
            raise ValueError(f"unknown purpose {purpose!r}")
    except DatabaseError as exc:
        raise BudgetUnavailable(f"ledger could not be read: {exc}") from exc
    if session is not None:
        _check("session", session.limit, session.spent, amount)


def worst_case_run_cost():
    """Reservation-basis cost of one Master call and one Intervenor call for each model in use (MASTER_MODEL,
    INTERVENOR_MODEL, SPIKE_MODEL), with TRANSCRIPT_MAX_MESSAGES messages of MAX_MESSAGE_CHARS characters each plus
    SYSTEM_PROMPT_TOKENS_ESTIMATE. Returns {model: {"master": Decimal, "intervenor": Decimal, "total": Decimal}}."""
    chars = int(settings.TRANSCRIPT_MAX_MESSAGES) * int(settings.MAX_MESSAGE_CHARS)
    per_token = Decimal(str(settings.TOKEN_ESTIMATE_CHARS_PER_TOKEN))
    input_tokens = (
        math.ceil(Decimal(chars) / per_token)
        + int(settings.TOKEN_ESTIMATE_OVERHEAD_TOKENS)
        + int(settings.SYSTEM_PROMPT_TOKENS_ESTIMATE)
    )
    models = []
    for model in (settings.MASTER_MODEL, settings.INTERVENOR_MODEL, settings.SPIKE_MODEL):
        if model not in models:
            models.append(model)
    result = {}
    for model in models:
        master = reservation_usd(model, estimated_input_tokens=input_tokens, max_tokens=settings.MASTER_MAX_TOKENS)
        intervenor = reservation_usd(
            model, estimated_input_tokens=input_tokens, max_tokens=settings.INTERVENOR_MAX_TOKENS
        )
        result[model] = {"master": master, "intervenor": intervenor, "total": master + intervenor}
    return result
