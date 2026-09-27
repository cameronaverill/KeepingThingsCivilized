"""moderation/llm.py: `_preflight()` in isolation -- the private helper docs/step20b_spike_brief.md says extracts
`call()`'s steps 1-4 (kill switch/API key, circuit breaker, model allow-list, budget reservation + ledger row).
Written from the brief's contract only, without reading llm.py's diff.

Convention note: `_preflight`'s exact keyword list is not fully spelled out in the brief (it gives
`_preflight(*, purpose, agent, model, system, messages, max_tokens, ..., request_extra=None)` with an elided
middle). Since it is a private helper, `preflight_kwargs()` below was built by introspecting the shipped function so
these tests can call it at all; every *assertion* still comes from the brief, not from the implementation.
"""
from decimal import Decimal

import pytest
from moderation_testkit import SONNET, seed_call, utc

D = Decimal

TOOLS = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]


def preflight_kwargs(**overrides):
    kwargs = dict(
        purpose="moderation",
        agent="master",
        model=SONNET,
        system="You are a careful test moderator.",
        messages=[{"role": "user", "content": "Hello there, this is a test message."}],
        max_tokens=500,
        prompt_version="",
        attempt=1,
        temperature=None,
        conversation_id=None,
        run_id=None,
        session=None,
        cache_system=True,
        request_extra={"tools": TOOLS},
        structural_for_estimate=TOOLS,
    )
    kwargs.update(overrides)
    return kwargs


def run_preflight(**overrides):
    from moderation import llm

    return llm._preflight(**preflight_kwargs(**overrides))


def rows():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk"))


def refused_rows():
    return [r for r in rows() if r.status.startswith("refused_")]


@pytest.fixture(autouse=True)
def ready(llm_ready, tune):
    tune(
        BUDGET_PER_CONVERSATION_USD=D("1.25"),
        BUDGET_SITE_USD_PER_DAY=D("1.50"),
        BUDGET_SITE_USD_TOTAL=D("5.00"),
        BUDGET_EVAL_USD_TOTAL=D("10.00"),
    )
    return llm_ready


# --- kill switch and API key ----------------------------------------------------------------------------------------


def test_kill_switch_off_refuses(settings):
    """The brief's own wording says kill switch off "refuses before any ledger row", but call()'s existing
    (unchanged) test suite establishes that a refused_disabled row IS written for this exact case
    (test_kill_switch_off_refuses, tests/moderation/test_moderation_gateway_refusals.py) -- and _preflight is
    required to make call()'s behavior byte-for-byte unchanged. So this test follows the established, still-green
    behavior (a row is written) rather than that one sentence in the brief; flagged as a contract ambiguity for the
    architect to reconcile the brief's wording, not treated as a failure of the code."""
    from moderation.errors import LLMDisabled

    settings.LLM_ENABLED = False
    with pytest.raises(LLMDisabled):
        run_preflight()
    found = rows()
    assert len(found) == 1, found
    assert found[0].status == "refused_disabled"


def test_missing_api_key_refuses_the_same_way(settings):
    from moderation.errors import LLMDisabled

    settings.ANTHROPIC_API_KEY = ""
    with pytest.raises(LLMDisabled):
        run_preflight()
    assert rows()[0].status == "refused_disabled"


# --- breaker ---------------------------------------------------------------------------------------------------------


def test_breaker_open_refuses_and_writes_a_refusal_row():
    from moderation import breaker
    from moderation.errors import BreakerOpen

    breaker.trip("manual", "test")
    with pytest.raises(BreakerOpen):
        run_preflight()
    found = rows()
    assert len(found) == 1, found
    assert found[0].status == "refused_breaker"


# --- model allow-list --------------------------------------------------------------------------------------------


def test_unknown_model_refuses_and_writes_a_refusal_row():
    from moderation.errors import ModelNotAllowed

    with pytest.raises(ModelNotAllowed):
        run_preflight(model="claude-opus-4-1")
    found = rows()
    assert len(found) == 1, found
    assert found[0].status == "refused_model"


# --- budget: over cap, and the reservation must not be double-counted -----------------------------------------------

OLD = utc(2026, 9, 10, 9)


def test_over_budget_refuses_and_does_not_double_count_the_reservation():
    from moderation import budget
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="4.995", created_at=OLD)
    with pytest.raises(BudgetExceeded):
        run_preflight(purpose="spike")
    found = refused_rows()
    assert len(found) == 1, found
    assert found[0].status == "refused_budget"
    # a failed reservation leaves no residue: total spend is exactly what was seeded, not seeded-plus-a-partial-reserve
    assert budget.spend() == D("4.995")


def test_repeated_over_budget_attempts_do_not_pile_up_reservations():
    from moderation import budget
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="4.995", created_at=OLD)
    for _ in range(3):
        with pytest.raises(BudgetExceeded):
            run_preflight(purpose="spike")
    assert len(refused_rows()) == 3
    assert budget.spend() == D("4.995")


# --- success: a pending row with the right identity and an auditable request_extra -----------------------------------


def test_a_successful_preflight_returns_a_pending_row_with_the_right_fields():
    row, probe, reserved = run_preflight(
        purpose="spike", agent="intervenor", conversation_id=7, run_id=9, prompt_version="v3", attempt=2,
    )
    assert row.pk is not None
    assert row.status == "pending"
    assert row.purpose == "spike"
    assert row.agent == "intervenor"
    assert row.model == SONNET
    assert row.conversation_id == 7
    assert row.run_id == 9
    assert row.prompt_version == "v3"
    assert row.attempt == 2
    assert row.request["tools"] == TOOLS  # request_extra is visible in the logged request, for audit
    assert isinstance(probe, bool)
    assert reserved > D("0")
    assert row.reserved_usd == reserved
    assert row.finished_at is None
    assert row.cost_usd is None


def test_request_extra_is_merged_into_the_logged_request_without_the_helper_caring_what_it_means():
    row, _probe, _reserved = run_preflight(
        request_extra={"something_bespoke": {"a": 1}}, structural_for_estimate={"a": 1},
    )
    assert row.request["something_bespoke"] == {"a": 1}


def test_purpose_is_still_validated_against_all_purposes_before_any_ledger_row():
    from moderation.budget import EVAL_PURPOSES, SITE_PURPOSES
    from moderation.llm import ALL_PURPOSES

    assert ALL_PURPOSES == SITE_PURPOSES + EVAL_PURPOSES
    with pytest.raises(ValueError):
        run_preflight(purpose="chitchat")
    assert rows() == []
