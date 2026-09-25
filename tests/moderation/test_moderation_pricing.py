"""moderation/pricing.py: the allowed models, their prices, and compute_cost (hand-computed expectations)."""
import dataclasses
from decimal import Decimal

import pytest
from moderation_testkit import HAIKU, SONNET


def test_exactly_two_models_are_allowed():
    from moderation.pricing import ALLOWED_MODELS

    assert set(ALLOWED_MODELS) == {SONNET, HAIKU}


def test_price_table_has_the_exact_documented_values():
    """The one test that reads the table itself (USD per million tokens)."""
    from moderation.pricing import ALLOWED_MODELS

    s, h = ALLOWED_MODELS[SONNET], ALLOWED_MODELS[HAIKU]
    assert (s.input, s.output, s.cache_write_5m, s.cache_write_1h, s.cache_read) == (
        Decimal("2"),
        Decimal("10"),
        Decimal("2.50"),
        Decimal("4"),
        Decimal("0.20"),
    )
    assert (h.input, h.output, h.cache_write_5m, h.cache_write_1h, h.cache_read) == (
        Decimal("1"),
        Decimal("5"),
        Decimal("1.25"),
        Decimal("2"),
        Decimal("0.10"),
    )
    for price in (s, h):
        for field in dataclasses.fields(price):
            assert isinstance(getattr(price, field.name), Decimal), field.name


def test_model_price_is_a_frozen_dataclass_with_the_five_fields():
    from moderation.pricing import ALLOWED_MODELS, ModelPrice

    assert dataclasses.is_dataclass(ModelPrice)
    assert [f.name for f in dataclasses.fields(ModelPrice)] == ["input", "output", "cache_write_5m", "cache_write_1h", "cache_read"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        ALLOWED_MODELS[SONNET].input = Decimal("0")


def test_price_provenance_constants():
    from moderation import pricing

    assert pricing.PRICES_VERIFIED_ON == "2026-09-25"
    assert pricing.PRICES_SOURCE_URL == "https://platform.claude.com/docs/en/about-claude/pricing"


def test_get_price_returns_the_table_entry():
    from moderation.pricing import ALLOWED_MODELS, get_price

    assert get_price(SONNET) == ALLOWED_MODELS[SONNET]
    assert get_price(HAIKU) == ALLOWED_MODELS[HAIKU]


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-4-1",
        "gpt-4o",
        "",
        "CLAUDE-SONNET-5",  # exact match only: fail closed
        " claude-sonnet-5",
        "claude-haiku-4-5-20251001",  # a dated alias is not on the list either
    ],
)
def test_any_other_model_is_refused(model):
    from moderation.errors import LLMRefused, ModelNotAllowed
    from moderation.pricing import get_price

    with pytest.raises(ModelNotAllowed) as excinfo:
        get_price(model)
    assert isinstance(excinfo.value, LLMRefused)
    assert excinfo.value.status == "refused_model"


def test_compute_cost_with_no_tokens_is_zero():
    from moderation.pricing import compute_cost

    for model in (SONNET, HAIKU):
        cost = compute_cost(model)
        assert isinstance(cost, Decimal)
        assert cost == Decimal("0")


@pytest.mark.parametrize(
    "model, kwargs, expected",
    [
        # One million tokens of each kind costs exactly the per-million price.
        (SONNET, {"input_tokens": 1_000_000}, "2"),
        (SONNET, {"output_tokens": 1_000_000}, "10"),
        (SONNET, {"cache_write_tokens": 1_000_000}, "2.5"),
        (SONNET, {"cache_read_tokens": 1_000_000}, "0.2"),
        (HAIKU, {"input_tokens": 1_000_000}, "1"),
        (HAIKU, {"output_tokens": 1_000_000}, "5"),
        (HAIKU, {"cache_write_tokens": 1_000_000}, "1.25"),
        (HAIKU, {"cache_read_tokens": 1_000_000}, "0.1"),
        # Fractions of a million.
        (SONNET, {"input_tokens": 123_456}, "0.246912"),
        (HAIKU, {"output_tokens": 2_000}, "0.01"),
        # Mixed: 1000*2 + 500*10 + 2000*2.5 + 4000*0.2 = 2000 + 5000 + 5000 + 800 micro-dollars.
        (SONNET, {"input_tokens": 1000, "output_tokens": 500, "cache_write_tokens": 2000, "cache_read_tokens": 4000}, "0.0128"),
        # Haiku: 3000*1 + 1000*5 + 10000*0.1 = 3000 + 5000 + 1000 micro-dollars.
        (HAIKU, {"input_tokens": 3000, "output_tokens": 1000, "cache_read_tokens": 10_000}, "0.009"),
    ],
)
def test_compute_cost_hand_computed(model, kwargs, expected):
    from moderation.pricing import compute_cost

    cost = compute_cost(model, **kwargs)
    assert isinstance(cost, Decimal)
    assert cost == Decimal(expected)


def test_cache_writes_are_priced_at_the_five_minute_rate_not_the_one_hour_rate():
    from moderation.pricing import compute_cost

    # Sonnet 5-minute write is $2.50/M; the 1-hour rate would be $4/M.
    assert compute_cost(SONNET, cache_write_tokens=1_000_000) == Decimal("2.5")


@pytest.mark.parametrize(
    "model, kwargs, expected",
    [
        (HAIKU, {"cache_read_tokens": 1}, "0.000001"),  # 1e-7 rounds UP to one micro-dollar, never down to zero
        (SONNET, {"cache_read_tokens": 1}, "0.000001"),  # 2e-7
        (HAIKU, {"cache_read_tokens": 10}, "0.000001"),  # exactly 1e-6: unchanged
        (HAIKU, {"cache_read_tokens": 11}, "0.000002"),  # 1.1e-6
        (HAIKU, {"cache_write_tokens": 1}, "0.000002"),  # 1.25e-6
        (SONNET, {"cache_write_tokens": 1}, "0.000003"),  # 2.5e-6
    ],
)
def test_cost_is_rounded_up_to_six_decimal_places(model, kwargs, expected):
    from moderation.pricing import compute_cost

    cost = compute_cost(model, **kwargs)
    assert cost == Decimal(expected)
    assert cost == cost.quantize(Decimal("0.000001"))


def test_compute_cost_refuses_an_unknown_model():
    from moderation.errors import ModelNotAllowed
    from moderation.pricing import compute_cost

    with pytest.raises(ModelNotAllowed):
        compute_cost("claude-opus-4-1", input_tokens=10)


def test_compute_cost_arguments_are_keyword_only():
    from moderation.pricing import compute_cost

    with pytest.raises(TypeError):
        compute_cost(SONNET, 100)
