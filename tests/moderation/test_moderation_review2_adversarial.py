"""Independent review pass: adversarial inputs and failure modes of the gateway and budget code."""
import time
from decimal import Decimal

import pytest
from moderation_testkit import SONNET, Verdict, call_kwargs, make_msg, reply, run_call, seed_call

D = Decimal


def rows():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk"))


@pytest.fixture(autouse=True)
def ready(llm_ready, tune):
    tune(
        BUDGET_PER_CONVERSATION_USD=D("1.25"),
        BUDGET_SITE_USD_PER_DAY=D("1.50"),
        BUDGET_SITE_USD_TOTAL=D("5.00"),
        BUDGET_EVAL_USD_TOTAL=D("10.00"),
    )
    return llm_ready


# --- max_tokens -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [-(10**9), 0, 2.0, 1e6, float("nan"), float("inf"), "1000", b"1", [1], D("100")])
def test_max_tokens_of_the_wrong_kind_never_reaches_the_ledger_or_client(install_fake, bad):
    client = install_fake(reply())
    with pytest.raises(ValueError):
        run_call(max_tokens=bad)
    assert client.calls == [] and rows() == []


@pytest.mark.parametrize("huge", [10**7, 10**12, 10**30])
def test_a_huge_max_tokens_is_refused_cleanly_not_by_a_decimal_overflow(install_fake, huge):
    """Either the budget refuses it (BudgetExceeded, with a refusal row) or validation does (ValueError, no row).
    A decimal.InvalidOperation leaking out of the price arithmetic is neither."""
    from moderation.errors import BudgetExceeded

    client = install_fake(reply())
    with pytest.raises((BudgetExceeded, ValueError)) as excinfo:
        run_call(max_tokens=huge)
    assert client.calls == []
    if isinstance(excinfo.value, BudgetExceeded):
        assert [r.status for r in rows()] == ["refused_budget"]
    else:
        assert rows() == []


def test_max_tokens_one_is_accepted(install_fake):
    install_fake(reply(input_tokens=10, output_tokens=1))
    assert run_call(max_tokens=1).call_id


# --- text shapes --------------------------------------------------------------------------------------------------


def test_an_empty_system_prompt_is_priced_not_crashed(install_fake):
    """(An empty message LIST is now a ValueError; see test_moderation_review3_guards.py.)"""
    client = install_fake(reply())
    run_call(messages=[{"role": "user", "content": "x"}], system="")
    assert len(client.calls) == 1
    assert rows()[0].reserved_usd > 0


def test_unicode_and_emoji_text_round_trips_through_the_ledger(install_fake):
    text = "日本語 \U0001f600 عربى \u0000 é" * 50
    install_fake(reply())
    run_call(system=text, messages=[{"role": "user", "content": text}])
    row = rows()[0]
    assert row.request["system"] == text
    assert row.request["messages"][0]["content"] == text


def test_a_very_long_prompt_reserves_more_and_is_estimated_quickly(install_fake):
    from moderation import budget

    text = "x" * 5_000_000
    started = time.monotonic()
    estimate = budget.estimate_input_tokens(system=text, messages=[{"role": "user", "content": text}])
    assert time.monotonic() - started < 5
    assert estimate >= 2 * 5_000_000 / 2.5


def test_a_prompt_beyond_the_input_ceiling_is_a_value_error_before_anything_happens(install_fake):
    """4M characters is about 1.6M estimated tokens, far over LLM_MAX_INPUT_TOKENS: ValueError, no row, no call."""
    client = install_fake(reply())
    with pytest.raises(ValueError):
        run_call(system="x" * 4_000_000)
    assert client.calls == []
    assert rows() == []


def test_input_under_the_ceiling_but_over_a_small_cap_is_still_a_budget_refusal(install_fake, tune, settings):
    """About 24,000 input tokens (under the 50,000 ceiling) reserve about $0.06 on Sonnet; a 0.05 conversation cap
    refuses it as BudgetExceeded and logs a refused_budget row."""
    from moderation import budget
    from moderation.errors import BudgetExceeded

    system = "x" * 60_000
    assert budget.estimate_input_tokens(system=system, messages=call_kwargs()["messages"]) < settings.LLM_MAX_INPUT_TOKENS
    tune(BUDGET_PER_CONVERSATION_USD=D("0.05"))
    client = install_fake(reply())
    with pytest.raises(BudgetExceeded) as excinfo:
        run_call(system=system, conversation_id=3)
    assert excinfo.value.cap_name == "conversation"
    assert client.calls == []
    assert [r.status for r in rows()] == ["refused_budget"]


def test_input_under_the_ceiling_but_over_a_session_budget_is_a_budget_refusal(install_fake, settings):
    from moderation.budget import SessionBudget
    from moderation.errors import BudgetExceeded

    client = install_fake(reply())
    session = SessionBudget(D("0.05"))
    with pytest.raises(BudgetExceeded) as excinfo:
        run_call(system="x" * 60_000, session=session)
    assert excinfo.value.cap_name == "session"
    assert client.calls == []
    assert [r.status for r in rows()] == ["refused_budget"]
    assert session.spent == D("0")


def test_non_text_content_blocks_are_counted_not_ignored(install_fake):
    from moderation import budget

    plain = budget.estimate_input_tokens(system="s", messages=[{"role": "user", "content": "hi"}])
    blocks = budget.estimate_input_tokens(
        system="s",
        messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image", "source": {"data": "A" * 10_000}}]}],
    )
    assert blocks > plain + 1000


def test_estimator_accepts_a_schema_as_dict_or_class():
    from moderation import budget

    as_class = budget.estimate_input_tokens(system="s", messages=[], schema=Verdict)
    as_dict = budget.estimate_input_tokens(system="s", messages=[], schema=Verdict.model_json_schema())
    assert as_class > budget.estimate_input_tokens(system="s", messages=[])
    assert as_dict > budget.estimate_input_tokens(system="s", messages=[])


def test_the_estimator_never_undercounts_the_documented_formula_for_plain_text(tune):
    from moderation import budget

    tune(TOKEN_ESTIMATE_CHARS_PER_TOKEN=2.5, TOKEN_ESTIMATE_OVERHEAD_TOKENS=1000)
    for n in (0, 1, 2, 3, 4, 5, 999, 1000, 1001):
        assert budget.estimate_input_tokens(system="a" * n, messages=[]) >= -(-n * 2 // 5) + 1000


# --- more ledger failure modes ------------------------------------------------------------------------------------


def _fail_sql(monkeypatch, table, verb):
    import django.db.backends.utils as dbu
    from django.db import OperationalError

    original = dbu.CursorWrapper.execute

    def failing(self, sql, params=None):
        text = str(sql)
        if table in text and text.lstrip().upper().startswith(verb):
            raise OperationalError(f"simulated: {verb} {table} failed")
        return original(self, sql, params)

    monkeypatch.setattr(dbu.CursorWrapper, "execute", failing)


def test_an_unwritable_ledger_makes_no_call(install_fake, monkeypatch):
    """Reservation INSERT fails: fail closed with BudgetUnavailable, no client call."""
    from moderation.errors import BudgetUnavailable

    client = install_fake(reply())
    _fail_sql(monkeypatch, "moderation_llmcall", "INSERT")
    with pytest.raises(BudgetUnavailable):
        run_call()
    assert client.calls == []


def test_an_unreadable_breaker_state_makes_no_call(install_fake, monkeypatch):
    from moderation.errors import BudgetUnavailable

    client = install_fake(reply())
    _fail_sql(monkeypatch, "moderation_guardstate", "SELECT")
    with pytest.raises(BudgetUnavailable):
        run_call()
    assert client.calls == []


def test_every_ledger_read_failure_is_budget_unavailable_for_every_cap_path(install_fake, monkeypatch):
    """Whichever cap query fails first, the result is BudgetUnavailable and no call (check_caps directly)."""
    from moderation import budget
    from moderation.errors import BudgetUnavailable

    _fail_sql(monkeypatch, "moderation_llmcall", "SELECT")
    for purpose, conv in [("moderation", 1), ("moderation", None), ("spike", None), ("golden", None), ("replay", None), ("judge", None)]:
        with pytest.raises(BudgetUnavailable):
            budget.check_caps(purpose=purpose, conversation_id=conv, amount=D("0.01"))


# --- a caller's transaction ---------------------------------------------------------------------------------------


def test_a_caller_transaction_that_rolls_back_erases_the_ledger_rows_but_not_the_client_call(install_fake, settings):
    """DOCUMENTED HAZARD (brief section 9, item 9): call() must never run inside transaction.atomic(). If the caller
    rolls back afterwards, the cost record disappears although the request was sent (and billed). This test pins the
    behaviour so that nobody relies on the opposite."""
    from moderation import budget

    # Production forbids this (LLM_FORBID_ATOMIC_CALLS is True there); the guard is switched off explicitly so the
    # hazard itself can be demonstrated.
    settings.LLM_FORBID_ATOMIC_CALLS = False
    client = install_fake(reply(input_tokens=1000, output_tokens=500))

    class Boom(Exception):
        pass

    from django.db import transaction

    with pytest.raises(Boom):
        with transaction.atomic():
            run_call()
            assert budget.spend() > 0
            raise Boom
    assert len(client.calls) == 1  # the request was sent
    assert rows() == []  # ...but nothing was recorded
    assert budget.spend() == D("0")


def test_the_breaker_state_also_rolls_back_with_a_caller_transaction(install_fake, settings):
    """Same hazard for the circuit breaker: a spend-limit trip inside a rolled-back transaction is forgotten."""
    from django.db import transaction

    from moderation import breaker
    from moderation.errors import LLMAPIError
    from moderation.fake_llm import FakeProviderError

    settings.LLM_FORBID_ATOMIC_CALLS = False  # production forbids calling inside a transaction; off to show the hazard
    install_fake(FakeProviderError(400, "invalid_request_error", "You have reached your specified API usage limits."))

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with transaction.atomic():
            with pytest.raises(LLMAPIError):
                run_call()
            assert breaker.is_open() is True
            raise Boom
    assert breaker.is_open() is False
