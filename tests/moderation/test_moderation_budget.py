"""moderation/budget.py, part 1: the ledger sum (spend), SessionBudget, the reservation formula, the token estimator."""
from decimal import Decimal

import pytest
from moderation_testkit import HAIKU, SONNET, OtherSchema, Verdict, seed_call, utc

# --- constants and SessionBudget ------------------------------------------------------------------------------------


def test_purpose_groups():
    from moderation import budget

    assert budget.SITE_PURPOSES == ("moderation", "spike", "golden")
    assert budget.EVAL_PURPOSES == ("replay", "judge")


def test_session_budget_starts_with_nothing_spent():
    from moderation.budget import SessionBudget

    session = SessionBudget(Decimal("0.50"))
    assert session.limit == Decimal("0.50")
    assert session.spent == Decimal("0")


# --- spend(): what the ledger counts --------------------------------------------------------------------------------


def test_spend_of_an_empty_ledger_is_zero_decimal():
    from moderation import budget

    total = budget.spend()
    assert isinstance(total, Decimal)
    assert total == Decimal("0")


def test_ok_and_error_rows_count_at_cost_usd_not_reserved_usd():
    from moderation import budget

    seed_call("ok", cost="0.30", reserved="5.00")
    seed_call("error", cost="0.10", reserved="1.00")
    assert budget.spend() == Decimal("0.40")


def test_ok_row_that_cost_more_than_reserved_counts_the_real_cost():
    from moderation import budget

    seed_call("ok", cost="0.90", reserved="0.10")
    assert budget.spend() == Decimal("0.90")


def test_pending_rows_count_at_reserved_usd():
    from moderation import budget

    seed_call("pending", reserved="0.40")
    seed_call("ok", cost="0.30", reserved="0.35")
    assert budget.spend() == Decimal("0.70")


@pytest.mark.parametrize("status", ["refused_budget", "refused_breaker", "refused_disabled", "refused_model"])
def test_refused_rows_never_count(status):
    from moderation import budget

    seed_call(status, reserved="9.99")
    assert budget.spend() == Decimal("0")


def test_spend_can_be_limited_to_purposes():
    from moderation import budget

    seed_call("ok", purpose="moderation", cost="1")
    seed_call("ok", purpose="spike", cost="2")
    seed_call("ok", purpose="golden", cost="4")
    seed_call("ok", purpose="replay", cost="8")
    seed_call("ok", purpose="judge", cost="16")
    assert budget.spend() == Decimal("31")  # purposes=None means every purpose
    assert budget.spend(purposes=budget.SITE_PURPOSES) == Decimal("7")
    assert budget.spend(purposes=budget.EVAL_PURPOSES) == Decimal("24")
    assert budget.spend(purposes=("spike",)) == Decimal("2")


def test_spend_can_be_limited_to_a_conversation():
    from moderation import budget

    seed_call("ok", cost="0.10", conversation_id=1)
    seed_call("ok", cost="0.20", conversation_id=2)
    seed_call("pending", reserved="0.05", conversation_id=2)
    seed_call("ok", cost="0.40")  # no conversation
    assert budget.spend(conversation_id=1) == Decimal("0.10")
    assert budget.spend(conversation_id=2) == Decimal("0.25")
    assert budget.spend(conversation_id=99) == Decimal("0")


def test_spend_since_is_inclusive_and_until_is_exclusive():
    """Half-open [since, until): a row exactly at `since` counts, a row exactly at `until` does not."""
    from moderation import budget

    seed_call("ok", cost="1", created_at=utc(2026, 9, 24, 23, 59, 59))
    seed_call("ok", cost="2", created_at=utc(2026, 9, 25, 0, 0, 0))
    seed_call("ok", cost="4", created_at=utc(2026, 9, 25, 23, 59, 59))
    seed_call("ok", cost="8", created_at=utc(2026, 9, 26, 0, 0, 0))
    day = budget.spend(since=utc(2026, 9, 25), until=utc(2026, 9, 26))
    assert day == Decimal("6")
    assert budget.spend(since=utc(2026, 9, 25)) == Decimal("14")
    assert budget.spend(until=utc(2026, 9, 25)) == Decimal("1")


def test_spend_arguments_are_keyword_only():
    from moderation import budget

    with pytest.raises(TypeError):
        budget.spend(("moderation",))


# --- reservation_usd: the worst-case formula ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model, est_in, max_tokens, expected",
    [
        # Input at the 5-minute cache-WRITE price, output at the output price:
        # Sonnet: 1000 * 2.5 + 500 * 10 = 2500 + 5000 micro-dollars.
        (SONNET, 1000, 500, "0.0075"),
        # Haiku: 10000 * 1.25 + 1000 * 5 = 12500 + 5000.
        (HAIKU, 10_000, 1000, "0.0175"),
        # Sonnet: 31000 * 2.5 + 1500 * 10 = 77500 + 15000. (At the base input price it would be 0.0770.)
        (SONNET, 31_000, 1500, "0.0925"),
        # Haiku: 31000 * 1.25 + 1500 * 5 = 38750 + 7500. (At the base input price it would be 0.0385.)
        (HAIKU, 31_000, 1500, "0.04625"),
        # Rounded UP to 6 places: 1*2.5 + 1*10 = 12.5 micro-dollars -> 13.
        (SONNET, 1, 1, "0.000013"),
    ],
)
def test_reservation_formula(model, est_in, max_tokens, expected):
    from moderation import budget

    result = budget.reservation_usd(model, estimated_input_tokens=est_in, max_tokens=max_tokens)
    assert isinstance(result, Decimal)
    assert result == Decimal(expected)


def test_reservation_refuses_an_unknown_model():
    from moderation import budget
    from moderation.errors import ModelNotAllowed

    with pytest.raises(ModelNotAllowed):
        budget.reservation_usd("claude-opus-4-1", estimated_input_tokens=10, max_tokens=10)


# --- estimate_input_tokens: local, deliberately over-counting -----------------------------------------------------


def test_estimator_adds_the_fixed_overhead_even_for_nothing(tune):
    from moderation import budget

    tune(TOKEN_ESTIMATE_CHARS_PER_TOKEN=2.5, TOKEN_ESTIMATE_OVERHEAD_TOKENS=1000)
    estimate = budget.estimate_input_tokens(system="", messages=[])
    assert isinstance(estimate, int)
    assert 1000 <= estimate <= 1010  # overhead, plus at most a few tokens for any empty-structure characters


def test_estimator_counts_a_character_as_at_least_a_2_5th_of_a_token(tune):
    """2500 characters of system prompt is at least ceil(2500 / 2.5) + 1000 = 2000 tokens."""
    from moderation import budget

    tune(TOKEN_ESTIMATE_CHARS_PER_TOKEN=2.5, TOKEN_ESTIMATE_OVERHEAD_TOKENS=1000)
    estimate = budget.estimate_input_tokens(system="a" * 2500, messages=[])
    assert 2000 <= estimate <= 2050


def test_estimator_counts_message_text(tune):
    from moderation import budget

    tune(TOKEN_ESTIMATE_CHARS_PER_TOKEN=2.5, TOKEN_ESTIMATE_OVERHEAD_TOKENS=1000)
    one = budget.estimate_input_tokens(system="", messages=[{"role": "user", "content": "b" * 5000}])
    assert 3000 <= one <= 3060  # ceil(5000 / 2.5) + 1000 = 3000
    two = budget.estimate_input_tokens(
        system="",
        messages=[{"role": "user", "content": "b" * 2500}, {"role": "assistant", "content": "c" * 2500}],
    )
    assert 3000 <= two <= 3100


def test_estimator_over_counts_unicode_by_characters_not_bytes(tune):
    """Non-ASCII text is estimated per character, never fewer tokens than the character formula says."""
    from moderation import budget

    tune(TOKEN_ESTIMATE_CHARS_PER_TOKEN=2.5, TOKEN_ESTIMATE_OVERHEAD_TOKENS=0)
    text = "日本語のテキスト" * 250  # 2000 characters
    estimate = budget.estimate_input_tokens(system=text, messages=[])
    assert 800 <= estimate <= 850


def test_estimator_grows_with_more_text_and_with_a_schema(tune):
    from moderation import budget

    tune(TOKEN_ESTIMATE_CHARS_PER_TOKEN=2.5, TOKEN_ESTIMATE_OVERHEAD_TOKENS=1000)
    messages = [{"role": "user", "content": "hello"}]
    short = budget.estimate_input_tokens(system="short", messages=messages)
    longer = budget.estimate_input_tokens(system="long " * 400, messages=messages)
    with_schema = budget.estimate_input_tokens(system="short", messages=messages, schema=Verdict)
    assert longer > short
    assert with_schema > short


def test_estimator_reads_the_tunables(tune):
    from moderation import budget

    tune(TOKEN_ESTIMATE_CHARS_PER_TOKEN=1.0, TOKEN_ESTIMATE_OVERHEAD_TOKENS=0)
    assert 100 <= budget.estimate_input_tokens(system="a" * 100, messages=[]) <= 110
    tune(TOKEN_ESTIMATE_CHARS_PER_TOKEN=1.0, TOKEN_ESTIMATE_OVERHEAD_TOKENS=500)
    assert 600 <= budget.estimate_input_tokens(system="a" * 100, messages=[]) <= 610


def test_estimator_arguments_are_keyword_only():
    from moderation import budget

    with pytest.raises(TypeError):
        budget.estimate_input_tokens("system", [])


# --- worst_case_run_cost --------------------------------------------------------------------------------------------


def _tune_run(tune, model, overhead):
    tune(
        MASTER_MODEL=model,
        INTERVENOR_MODEL=model,
        SPIKE_MODEL=model,
        TRANSCRIPT_MAX_MESSAGES=4,
        MAX_MESSAGE_CHARS=1000,
        SYSTEM_PROMPT_TOKENS_ESTIMATE=2000,
        TOKEN_ESTIMATE_CHARS_PER_TOKEN=2.5,
        TOKEN_ESTIMATE_OVERHEAD_TOKENS=overhead,
        MASTER_MAX_TOKENS=1500,
        INTERVENOR_MAX_TOKENS=1000,
    )


def test_worst_case_run_cost_sonnet_without_overhead(tune):
    """Input = 4 messages * 1000 chars / 2.5 = 1600 tokens + 2000 system = 3600 tokens, all at the cache-write price.
    Master: 3600 * 2.5 + 1500 * 10 = 9000 + 15000 = 0.024. Intervenor: 9000 + 1000 * 10 = 0.019. Sum 0.043."""
    from moderation import budget

    _tune_run(tune, SONNET, overhead=0)
    result = budget.worst_case_run_cost()
    assert isinstance(result, dict)
    entry = result[SONNET]
    assert entry["master"] == Decimal("0.024")
    assert entry["intervenor"] == Decimal("0.019")
    assert entry["total"] == Decimal("0.043")


def test_worst_case_run_cost_haiku_without_overhead(tune):
    """Master: 3600 * 1.25 + 1500 * 5 = 4500 + 7500 = 0.012. Intervenor: 4500 + 5000 = 0.0095. Sum 0.0215."""
    from moderation import budget

    _tune_run(tune, HAIKU, overhead=0)
    entry = budget.worst_case_run_cost()[HAIKU]
    assert entry["master"] == Decimal("0.012")
    assert entry["intervenor"] == Decimal("0.0095")
    assert entry["total"] == Decimal("0.0215")


def test_worst_case_run_cost_includes_the_estimator_overhead(tune):
    """With the 1000-token overhead the input is 4600 tokens: Sonnet master 4600 * 2.5 + 15000 = 0.0265,
    intervenor 11500 + 10000 = 0.0215, sum 0.048."""
    from moderation import budget

    _tune_run(tune, SONNET, overhead=1000)
    entry = budget.worst_case_run_cost()[SONNET]
    assert entry["master"] == Decimal("0.0265")
    assert entry["intervenor"] == Decimal("0.0215")
    assert entry["total"] == Decimal("0.048")


def test_worst_case_run_cost_follows_the_tunables(tune):
    from moderation import budget

    _tune_run(tune, SONNET, overhead=0)
    tune(TRANSCRIPT_MAX_MESSAGES=8)  # 8000 chars -> 3200 + 2000 = 5200 tokens: master 13000 + 15000 = 0.028
    assert budget.worst_case_run_cost()[SONNET]["master"] == Decimal("0.028")
    tune(MASTER_MAX_TOKENS=2500)  # output part 25000
    assert budget.worst_case_run_cost()[SONNET]["master"] == Decimal("0.038")


def test_worst_case_run_cost_lists_the_master_model_by_default():
    from moderation import budget
    from django.conf import settings

    result = budget.worst_case_run_cost()
    assert settings.MASTER_MODEL in result
    assert settings.INTERVENOR_MODEL in result


def test_worst_case_of_one_run_fits_under_the_per_conversation_cap():
    """Cap invariant: with the shipped tunables, one worst-case run of every model in use must be reservable."""
    from django.conf import settings

    from moderation import budget

    for model, entry in budget.worst_case_run_cost().items():
        assert entry["total"] <= settings.BUDGET_PER_CONVERSATION_USD, model
        assert entry["total"] > 0
