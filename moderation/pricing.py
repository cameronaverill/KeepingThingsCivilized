"""Price table for the two models the guard allows. Unknown model = no call (fail closed).

Verified against Anthropic's model pages (platform.claude.com/docs/en/models/sonnet-5/overview and
.../haiku-4-5/overview) on 2026-09-25. Cache writes are priced at the 5-minute rate (we never ask for the 1-hour cache).
Sonnet 5 uses a new tokenizer (about 30% more tokens for the same text than Sonnet 4.6); usage numbers already reflect it.
"""
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from moderation.errors import ModelNotAllowed

PRICES_VERIFIED_ON = "2026-09-25"
PRICES_SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"

_PER_MILLION = Decimal(1_000_000)
_MICRO = Decimal("0.000001")


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input: Decimal
    output: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal
    cache_read: Decimal


# Before adding a model here, verify in Anthropic's docs whether it accepts a `temperature` and whether it accepts
# thinking={"type": "disabled"} (Fable 5.x, Mythos and Opus 5.5 always think and reject it), and declare both below.
ALLOWED_MODELS = {
    "claude-sonnet-5": ModelPrice(
        input=Decimal("2"),
        output=Decimal("10"),
        cache_write_5m=Decimal("2.50"),
        cache_write_1h=Decimal("4"),
        cache_read=Decimal("0.20"),
    ),
    "claude-haiku-4-5": ModelPrice(
        input=Decimal("1"),
        output=Decimal("5"),
        cache_write_5m=Decimal("1.25"),
        cache_write_1h=Decimal("2"),
        cache_read=Decimal("0.10"),
    ),
}


# Whether the model accepts a `temperature` at all. claude-sonnet-5 returns HTTP 400 for any non-default sampling
# parameter (docs models/sonnet-5/whats-new-sonnet-5, "Sampling parameters not accepted"), so we refuse locally.
ACCEPTS_TEMPERATURE = {"claude-sonnet-5": False, "claude-haiku-4-5": True}

# Whether the model accepts thinking={"type": "disabled"}, which llm.call sends on every call (docs
# build-with-claude/thinking-troubleshooting). A model that rejects it would fail every call, so llm.call refuses it.
ACCEPTS_THINKING_DISABLED = {"claude-sonnet-5": True, "claude-haiku-4-5": True}


def get_price(model):
    try:
        return ALLOWED_MODELS[model]
    except (KeyError, TypeError):
        raise ModelNotAllowed(f"model {model!r} is not allowed; allowed: {', '.join(ALLOWED_MODELS)}") from None


def compute_cost(model, *, input_tokens=0, output_tokens=0, cache_write_tokens=0, cache_read_tokens=0):
    """Cost in USD, rounded UP to 6 decimal places. Cache writes are priced at the 5-minute rate."""
    price = get_price(model)
    total = (
        Decimal(input_tokens) * price.input
        + Decimal(output_tokens) * price.output
        + Decimal(cache_write_tokens) * price.cache_write_5m
        + Decimal(cache_read_tokens) * price.cache_read
    ) / _PER_MILLION
    return total.quantize(_MICRO, rounding=ROUND_CEILING)
