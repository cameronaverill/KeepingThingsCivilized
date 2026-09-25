"""A call that claims the half-open probe but is then refused (or fails without telling us anything about the API) must
give the probe back: otherwise recovery would be blocked for BREAKER_PROBE_TIMEOUT_SECONDS by a call that never went out."""
from decimal import Decimal

import pytest
from moderation_testkit import SONNET, reply, run_call, seed_call, utc

D = Decimal


@pytest.fixture(autouse=True)
def setup(llm_ready, tune):
    tune(
        BREAKER_MAX_CONSECUTIVE_ERRORS=5, BREAKER_ERROR_WINDOW_SECONDS=300, BREAKER_COOLDOWN_SECONDS=300,
        BREAKER_COOLDOWN_MAX_SECONDS=3600, BREAKER_PROBE_TIMEOUT_SECONDS=180,
        BUDGET_PER_CONVERSATION_USD=D("1.25"), BUDGET_SITE_USD_PER_DAY=D("1.50"), BUDGET_SITE_USD_TOTAL=D("5.00"),
        BUDGET_EVAL_USD_TOTAL=D("10.00"),
    )
    return llm_ready


@pytest.fixture
def cooled_down(llm_ready):
    """A soft-tripped breaker whose cooldown has just ended."""
    from moderation import breaker

    for _ in range(5):
        breaker.record_error(status_code=500, error_type="api_error", error_code=None, message="down")
    llm_ready.advance(seconds=300)
    return llm_ready


def probe_state():
    from moderation.models import GuardState

    row = GuardState.objects.get(pk=1)
    return row.probe_in_flight, row.probe_started_at, row.breaker_tripped


def assert_probe_given_back():
    from moderation import breaker

    in_flight, started, tripped = probe_state()
    assert (in_flight, started) == (False, None)
    assert tripped is True  # still soft-tripped: only a successful call closes it
    assert breaker.is_open() is False  # the next caller may probe at once, no 180 s wait


def test_a_budget_refusal_gives_the_probe_back(install_fake, cooled_down):
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="1.499", created_at=utc(2026, 9, 25, 2))  # the day cap has no room
    client = install_fake(reply())
    with pytest.raises(BudgetExceeded):
        run_call()
    assert client.calls == []
    assert_probe_given_back()


def test_a_disallowed_model_gives_the_probe_back(install_fake, cooled_down):
    from moderation.errors import ModelNotAllowed

    install_fake(reply())
    with pytest.raises(ModelNotAllowed):
        run_call(model="claude-opus-4-1")
    assert_probe_given_back()


def test_an_unreadable_ledger_gives_the_probe_back(install_fake, cooled_down, monkeypatch):
    import django.db.backends.utils as dbu
    from django.db import OperationalError

    from moderation.errors import BudgetUnavailable

    original = dbu.CursorWrapper.execute

    def failing(self, sql, params=None):
        if "moderation_llmcall" in str(sql) and str(sql).lstrip().upper().startswith("SELECT"):
            raise OperationalError("simulated: ledger unreadable")
        return original(self, sql, params)

    install_fake(reply())
    monkeypatch.setattr(dbu.CursorWrapper, "execute", failing)
    with pytest.raises(BudgetUnavailable):
        run_call()
    monkeypatch.undo()
    assert_probe_given_back()


def test_an_unexpected_client_exception_gives_the_probe_back(install_fake, cooled_down):
    """Not an API answer, so neither success nor failure for the breaker; the next caller may probe at once."""

    def broken(kwargs):
        raise RuntimeError("bug in the client")

    install_fake(broken)
    with pytest.raises(RuntimeError):
        run_call()
    assert_probe_given_back()


def test_a_missing_client_gives_the_probe_back(cooled_down):
    from moderation import llm

    llm.reset_client()  # the autouse guard makes get_client() raise AssertionError
    with pytest.raises(AssertionError):
        run_call()
    assert_probe_given_back()


def test_after_a_refused_probe_the_next_caller_can_probe_and_close_the_breaker(install_fake, cooled_down):
    from moderation import breaker
    from moderation.errors import ModelNotAllowed

    client = install_fake(reply())
    with pytest.raises(ModelNotAllowed):
        run_call(model="claude-opus-4-1")
    run_call()
    assert len(client.calls) == 1
    assert breaker.is_open() is False
    assert probe_state() == (False, None, False)


def test_a_refused_call_that_is_not_the_probe_leaves_the_probe_alone(install_fake, cooled_down):
    """While another caller's probe is in flight, a refused caller must not release it."""
    from moderation import breaker
    from moderation.errors import BreakerOpen

    breaker.check()  # someone else's probe
    started = probe_state()[1]
    install_fake(reply())
    with pytest.raises(BreakerOpen):
        run_call()
    assert probe_state() == (True, started, True)


@pytest.mark.django_db(transaction=True)
def test_the_alert_email_is_scrubbed_of_the_api_key_even_if_a_provider_message_echoes_it(install_fake, settings, mailoutbox):
    from moderation.errors import LLMAPIError
    from moderation.fake_llm import FakeProviderError
    from moderation_testkit import FAKE_KEY

    canary = FAKE_KEY + "-echo-canary-5150"
    settings.ANTHROPIC_API_KEY = canary
    settings.ALERT_EMAIL = "alerts@example.test"
    install_fake(FakeProviderError(401, "authentication_error", f"invalid x-api-key: {canary}"))
    with pytest.raises(LLMAPIError):
        run_call()
    assert len(mailoutbox) == 1
    assert canary not in mailoutbox[0].subject + mailoutbox[0].body


def test_a_provider_message_that_echoes_the_api_key_is_never_stored(install_fake, settings):
    """Defence in depth (real providers do not echo keys): the ledger row, the breaker detail (printed by
    manage.py budget) and the raised exception must not carry the key either."""
    from moderation.errors import LLMAPIError
    from moderation.fake_llm import FakeProviderError
    from moderation.models import GuardState, LLMCall
    from moderation_testkit import FAKE_KEY

    canary = FAKE_KEY + "-echo-canary-5150"
    settings.ANTHROPIC_API_KEY = canary
    install_fake(FakeProviderError(401, "authentication_error", f"invalid x-api-key: {canary}"))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    assert canary not in str(excinfo.value) and canary not in excinfo.value.message
    assert canary not in GuardState.objects.get(pk=1).trip_detail
    assert all(canary not in (c.error + c.error_code) for c in LLMCall.objects.all())
