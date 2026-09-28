"""moderation/llm.py after the request went out: provider errors, unusable output, breaker feed, concurrent-looking calls."""
import logging
from decimal import Decimal

import pytest
from moderation_testkit import SONNET, Verdict, make_msg, reply, run_call

D = Decimal
SPEND_MSG = "You have reached your specified API usage limits. You will regain access on 2026-10-01 at 00:00 UTC."


def rows():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk"))


def only_row():
    found = rows()
    assert len(found) == 1, found
    return found[0]


def guard():
    from moderation.models import GuardState

    return GuardState.load()


@pytest.fixture(autouse=True)
def ready(llm_ready, tune):
    tune(BREAKER_MAX_CONSECUTIVE_ERRORS=5, BREAKER_ERROR_WINDOW_SECONDS=300)
    return llm_ready


def provider_error(status_code=500, error_type="api_error", message="upstream exploded", error_code=None):
    from moderation.fake_llm import FakeProviderError

    return FakeProviderError(status_code=status_code, error_type=error_type, message=message, error_code=error_code)


# --- provider errors (FakeProviderError) ---------------------------------------------------------------------------


def test_a_provider_error_raises_llm_api_error_with_all_attributes(install_fake):
    from moderation.errors import LLMAPIError

    install_fake(provider_error(529, "overloaded_error", "Overloaded", error_code="busy"))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    err = excinfo.value
    row = only_row()
    assert err.status_code == 529
    assert err.error_type == "overloaded_error"
    assert err.error_code == "busy"
    assert err.message == "Overloaded"
    assert err.call_id == row.pk


def test_a_provider_error_is_logged_as_error_with_its_code(install_fake, llm_ready):
    from moderation.errors import LLMAPIError

    install_fake(provider_error(529, "overloaded_error", "Overloaded", error_code="busy"))
    with pytest.raises(LLMAPIError):
        run_call(conversation_id=3, run_id=4)
    row = only_row()
    assert row.status == "error"
    assert row.error_code == "busy"
    assert "Overloaded" in row.error
    assert (row.cost_usd or 0) == 0
    assert row.finished_at is not None
    assert (row.purpose, row.agent, row.model, row.conversation_id, row.run_id) == ("moderation", "master", SONNET, 3, 4)
    assert row.reserved_usd > 0  # the reservation is kept for the record


def test_an_errored_call_is_logged_at_warning_with_the_status_but_not_the_provider_message(install_fake, llm_ready, caplog):
    from moderation.errors import LLMAPIError

    install_fake(provider_error(500, "api_error", "a sensitive upstream detail"))
    with caplog.at_level(logging.WARNING, logger="moderation.llm"):
        with pytest.raises(LLMAPIError):
            run_call()
    [record] = [r for r in caplog.records if r.levelname == "WARNING"]
    message = record.getMessage()
    assert "status=error" in message
    assert "a sensitive upstream detail" not in message


def test_a_successful_call_is_logged_at_info_without_the_request_or_response_text(install_fake, caplog):
    install_fake(reply())
    with caplog.at_level(logging.INFO, logger="moderation.llm"):
        run_call()
    [record] = [r for r in caplog.records if "finished" in r.getMessage()]
    message = record.getMessage()
    assert "status=ok" in message
    assert "Hello there, this is a test message." not in message


def test_a_provider_error_without_a_code(install_fake):
    from moderation.errors import LLMAPIError

    install_fake(provider_error(500, "api_error", "boom"))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    assert not excinfo.value.error_code  # None or empty: the contract does not say which
    assert only_row().error_code in ("", None, "api_error")  # blank, or falls back to the error type


def test_a_failed_call_releases_its_reservation_from_the_ledger_and_the_session(install_fake):
    from moderation import budget
    from moderation.budget import SessionBudget
    from moderation.errors import LLMAPIError

    session = SessionBudget(D("1"))
    install_fake(provider_error())
    with pytest.raises(LLMAPIError):
        run_call(session=session)
    assert only_row().status == "error"
    assert budget.spend() == D("0")
    assert session.spent == D("0")


def test_a_provider_error_is_fed_to_the_breaker(install_fake, llm_ready):
    from moderation.errors import LLMAPIError

    install_fake(provider_error())
    with pytest.raises(LLMAPIError):
        run_call()
    state = guard()
    assert state.consecutive_errors == 1
    assert state.last_error_at == llm_ready.now()
    assert state.breaker_tripped is False


def test_a_spend_limit_error_trips_the_breaker_at_once_and_blocks_the_next_call(install_fake):
    from moderation import breaker
    from moderation.errors import BreakerOpen, LLMAPIError

    client = install_fake(provider_error(400, "invalid_request_error", SPEND_MSG), reply())
    with pytest.raises(LLMAPIError):
        run_call()
    assert breaker.is_open() is True
    assert guard().trip_reason == "spend_limit"
    with pytest.raises(BreakerOpen):
        run_call()
    assert len(client.calls) == 1  # the second call never left
    assert [r.status for r in rows()] == ["error", "refused_breaker"]


def test_the_enforced_429_limit_also_trips_the_breaker(install_fake):
    from moderation import breaker
    from moderation.errors import LLMAPIError

    install_fake(provider_error(429, "rate_limit_error", "Spend limit hit", error_code="enforced_spend_limit_reached"))
    with pytest.raises(LLMAPIError):
        run_call()
    assert breaker.is_open() is True


def test_five_provider_errors_in_a_row_trip_the_breaker(install_fake):
    from moderation import breaker
    from moderation.errors import BreakerOpen, LLMAPIError

    client = install_fake(*[provider_error() for _ in range(5)], reply())
    for _ in range(5):
        with pytest.raises(LLMAPIError):
            run_call()
    assert breaker.is_open() is True
    assert guard().trip_reason == "consecutive_errors"
    with pytest.raises(BreakerOpen):
        run_call()
    assert len(client.calls) == 5
    assert [r.status for r in rows()] == ["error"] * 5 + ["refused_breaker"]


def test_a_success_between_errors_keeps_the_breaker_closed(install_fake):
    from moderation import breaker
    from moderation.errors import LLMAPIError

    install_fake(*[provider_error() for _ in range(4)], reply(), *[provider_error() for _ in range(4)])
    for _ in range(4):
        with pytest.raises(LLMAPIError):
            run_call()
    run_call()
    assert guard().consecutive_errors == 0
    for _ in range(4):
        with pytest.raises(LLMAPIError):
            run_call()
    assert breaker.is_open() is False


def test_an_unexpected_exception_from_parse_bills_the_reservation_reraises_and_does_not_feed_the_breaker(install_fake):
    """It is not an API error, a ValidationError or a fake provider error, so the request may have been sent: the
    row is error/client_error, costed at its reservation (never under-count), and the breaker count does not move."""
    from moderation import budget

    def broken(kwargs):
        raise RuntimeError("a bug in the client")

    install_fake(broken)
    with pytest.raises(RuntimeError, match="a bug in the client"):
        run_call()
    row = only_row()
    assert row.status == "error"
    assert row.error_code == "client_error"
    assert "a bug in the client" in row.error
    assert row.reserved_usd > 0
    assert row.cost_usd == row.reserved_usd
    assert budget.spend() == row.reserved_usd
    assert row.finished_at is not None
    assert guard().consecutive_errors == 0
    assert guard().breaker_tripped is False


def test_an_unexpected_exception_from_parse_keeps_the_session_reservation(install_fake):
    from moderation.budget import SessionBudget

    def broken(kwargs):
        raise RuntimeError("boom")

    session = SessionBudget(D("1"))
    install_fake(broken)
    with pytest.raises(RuntimeError):
        run_call(session=session)
    assert session.spent == only_row().reserved_usd > 0


def test_an_exception_from_get_client_sent_nothing_so_it_costs_zero(llm_ready):
    from moderation import budget, llm

    llm.reset_client()  # the autouse guard makes get_client() raise AssertionError
    with pytest.raises(AssertionError):
        run_call()
    row = only_row()
    assert row.status == "error"
    assert row.error_code == "client_error"
    assert (row.cost_usd or 0) == 0
    assert budget.spend() == D("0")
    assert guard().consecutive_errors == 0


def test_an_http_error_costs_zero_but_a_status_less_failure_keeps_its_reservation_counted(install_fake):
    """A connection or timeout failure may have reached the server and been billed, so the reservation stays."""
    from moderation import budget
    from moderation.errors import LLMAPIError

    install_fake(provider_error(500, "api_error", "http failure"))
    with pytest.raises(LLMAPIError):
        run_call()
    assert budget.spend() == D("0")

    install_fake(provider_error(None, "connection_error", "socket closed"))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    assert excinfo.value.status_code is None
    reserved = rows()[-1].reserved_usd
    assert reserved > 0
    assert budget.spend() == reserved


def test_a_status_less_failure_keeps_the_session_reservation_too(install_fake):
    from moderation.budget import SessionBudget
    from moderation.errors import LLMAPIError

    session = SessionBudget(D("1"))
    install_fake(provider_error(None, "connection_error", "socket closed"))
    with pytest.raises(LLMAPIError):
        run_call(session=session)
    assert session.spent == only_row().reserved_usd > 0


# --- real SDK exception objects, normalized inside llm.py -------------------------------------------------------


def _sdk_status_error(cls, status, body, message, headers=None):
    import httpx2

    request = httpx2.Request("POST", "https://api.example.test/v1/messages")
    response = httpx2.Response(status, request=request, headers=headers or {"request-id": "req_test_123"})
    return cls(message, response=response, body=body)


def _raiser(exc):
    def item(kwargs):
        raise exc

    return item


SPEND_MSG_WORKSPACE = "You have reached your specified workspace API usage limits. You will regain access on 2026-10-01 at 00:00 UTC."


def _bad_request(message):
    import anthropic

    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    return _sdk_status_error(anthropic.BadRequestError, 400, body, message)


def _rate_limit(code=None, message="Rate limit reached"):
    """A 429 the way the API sends it: the code, when present, is at error.details.error_code."""
    import anthropic

    error = {"type": "rate_limit_error", "message": message}
    if code is not None:
        error["details"] = {"error_code": code}
    return _sdk_status_error(anthropic.RateLimitError, 429, {"type": "error", "error": error}, message)


@pytest.mark.parametrize("message", [SPEND_MSG, SPEND_MSG_WORKSPACE])
def test_sdk_400_spend_limit_messages_trip_the_breaker_at_once(install_fake, message):
    from moderation import breaker
    from moderation.errors import BreakerOpen, LLMAPIError

    client = install_fake(_raiser(_bad_request(message)), reply())
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    err = excinfo.value
    assert err.status_code == 400
    assert err.error_type == "invalid_request_error"
    assert err.message.startswith("You have reached your specified")
    assert only_row().status == "error"
    assert breaker.is_open() is True
    assert guard().trip_reason == "spend_limit"
    with pytest.raises(BreakerOpen):
        run_call()
    assert len(client.calls) == 1


def test_sdk_429_with_the_enforced_spend_limit_code_trips_the_breaker_at_once(install_fake):
    """The cost circuit breaker, on the real SDK shape: the code lives at body.error.details.error_code."""
    from moderation import breaker
    from moderation.errors import BreakerOpen, LLMAPIError

    client = install_fake(_raiser(_rate_limit("enforced_spend_limit_reached", "Spend limit reached")), reply())
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    err = excinfo.value
    assert err.status_code == 429
    assert err.error_type == "rate_limit_error"
    assert err.error_code == "enforced_spend_limit_reached"
    row = only_row()
    assert row.status == "error"
    assert row.error_code == "enforced_spend_limit_reached"
    assert breaker.is_open() is True
    assert guard().trip_reason == "spend_limit"
    with pytest.raises(BreakerOpen):
        run_call()
    assert len(client.calls) == 1


def test_sdk_ordinary_400_does_not_trip_the_breaker_at_once(install_fake):
    from moderation import breaker
    from moderation.errors import LLMAPIError

    install_fake(_raiser(_bad_request("max_tokens: must be greater than 0")))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    assert excinfo.value.status_code == 400
    assert breaker.is_open() is False
    assert guard().consecutive_errors == 1


@pytest.mark.parametrize("code", [None, "some_other_code"])
def test_sdk_ordinary_429_does_not_trip_the_breaker_at_once(install_fake, code):
    from moderation import breaker
    from moderation.errors import LLMAPIError

    install_fake(_raiser(_rate_limit(code, "Slow down")))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    assert excinfo.value.status_code == 429
    assert excinfo.value.error_type == "rate_limit_error"
    assert excinfo.value.error_code != "enforced_spend_limit_reached"
    assert breaker.is_open() is False
    assert guard().consecutive_errors == 1


def test_sdk_server_error_is_normalized_and_counts_toward_the_breaker(install_fake):
    import anthropic
    from moderation import breaker, budget
    from moderation.errors import LLMAPIError

    body = {"type": "error", "error": {"type": "api_error", "message": "Internal error"}}
    install_fake(_raiser(_sdk_status_error(anthropic.InternalServerError, 500, body, "Internal error")))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    assert excinfo.value.status_code == 500
    assert excinfo.value.error_type == "api_error"
    assert breaker.is_open() is False
    assert guard().consecutive_errors == 1
    assert only_row().status == "error"
    assert budget.spend() == D("0")  # an HTTP error is costed at 0


@pytest.mark.parametrize("kind, error_type", [("connection", "connection_error"), ("timeout", "timeout_error")])
def test_sdk_connection_failures_normalize_with_no_status_and_keep_the_reservation(install_fake, kind, error_type):
    import anthropic
    import httpx2
    from moderation import budget
    from moderation.errors import LLMAPIError

    request = httpx2.Request("POST", "https://api.example.test/v1/messages")
    exc = anthropic.APIConnectionError(request=request) if kind == "connection" else anthropic.APITimeoutError(request)
    install_fake(_raiser(exc))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    err = excinfo.value
    assert err.status_code is None
    assert err.error_type == error_type
    row = only_row()
    assert row.status == "error"
    assert guard().consecutive_errors == 1
    assert budget.spend() == row.reserved_usd > 0  # it may have reached the server: the reservation stays counted


def test_callers_never_see_sdk_exception_types(install_fake):
    import anthropic
    from moderation.errors import LLMAPIError

    body = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    install_fake(_raiser(_sdk_status_error(anthropic.InternalServerError, 529, body, "Overloaded")))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    assert not isinstance(excinfo.value, anthropic.AnthropicError)


# --- output the gateway cannot use: billed, logged ok, LLMOutputError ------------------------------------------------


def test_truncation_at_max_tokens_is_an_output_error_but_the_call_is_billed(install_fake):
    """1000 in * 2 + 500 out * 10 = 0.002 + 0.005 = 0.007."""
    from moderation import budget
    from moderation.errors import LLMOutputError

    install_fake(reply(parsed=None, stop_reason="max_tokens", input_tokens=1000, output_tokens=500))
    with pytest.raises(LLMOutputError) as excinfo:
        run_call()
    err = excinfo.value
    row = only_row()
    assert isinstance(err.reason, str) and err.reason
    assert err.stop_reason == "max_tokens"
    assert err.call_id == row.pk
    assert row.status == "ok"
    assert row.stop_reason == "max_tokens"
    assert row.cost_usd == D("0.007")
    assert (row.tokens_in, row.tokens_out) == (1000, 500)
    assert row.parsed is None
    assert budget.spend() == D("0.007")


def test_a_model_refusal_is_an_output_error_but_the_call_is_billed(install_fake):
    """1000 in * 2 + 20 out * 10 = 0.002 + 0.0002 = 0.0022."""
    from moderation.errors import LLMOutputError

    install_fake(reply(parsed=None, stop_reason="refusal", input_tokens=1000, output_tokens=20))
    with pytest.raises(LLMOutputError) as excinfo:
        run_call()
    row = only_row()
    assert excinfo.value.stop_reason == "refusal"
    assert excinfo.value.call_id == row.pk
    assert row.status == "ok"
    assert row.stop_reason == "refusal"
    assert row.cost_usd == D("0.0022")


def test_invalid_output_is_an_output_error_but_the_call_is_billed(install_fake):
    """No parsed output although the model stopped normally (bad JSON, or it fails the schema)."""
    from moderation.errors import LLMOutputError

    install_fake(reply(parsed=None, stop_reason="end_turn", input_tokens=1000, output_tokens=100))
    with pytest.raises(LLMOutputError) as excinfo:
        run_call()
    row = only_row()
    assert excinfo.value.stop_reason == "end_turn"
    assert excinfo.value.call_id == row.pk
    assert row.status == "ok"
    assert row.cost_usd == D("0.003")  # 0.002 + 100 * 10 micro-dollars
    assert row.parsed is None


def test_the_three_output_failures_give_three_different_reasons(install_fake):
    from moderation.errors import LLMOutputError

    install_fake(
        reply(parsed=None, stop_reason="max_tokens"),
        reply(parsed=None, stop_reason="refusal"),
        reply(parsed=None, stop_reason="end_turn"),
    )
    reasons = []
    for _ in range(3):
        with pytest.raises(LLMOutputError) as excinfo:
            run_call()
        reasons.append(excinfo.value.reason)
    assert len(set(reasons)) == 3, reasons


def test_an_output_error_charges_the_session(install_fake):
    from moderation.budget import SessionBudget
    from moderation.errors import LLMOutputError

    session = SessionBudget(D("1"))
    install_fake(reply(parsed=None, stop_reason="max_tokens", input_tokens=1000, output_tokens=500))
    with pytest.raises(LLMOutputError):
        run_call(session=session)
    assert session.spent == D("0.007")


def test_an_output_error_is_not_a_provider_error_for_the_breaker(install_fake):
    from moderation import breaker
    from moderation.errors import LLMOutputError

    install_fake(*[reply(parsed=None, stop_reason="max_tokens") for _ in range(6)])
    for _ in range(6):
        with pytest.raises(LLMOutputError):
            run_call()
    assert breaker.is_open() is False
    assert guard().consecutive_errors == 0


def _validation_error():
    from pydantic import ValidationError

    try:
        Verdict.model_validate_json('{"label": "cut off mid')
    except ValidationError as err:
        return err
    raise AssertionError("expected a ValidationError")


def test_a_validation_error_from_parse_is_an_invalid_output_error_billed_at_the_full_reservation(install_fake):
    """The real SDK raises pydantic.ValidationError from parse() for truncated or invalid JSON, and the usage is lost.
    The call is logged ok with error_code invalid_output and billed at its reservation (a conservative over-count)."""
    from moderation import budget
    from moderation.budget import SessionBudget
    from moderation.errors import LLMOutputError

    def raising(kwargs):
        raise _validation_error()

    session = SessionBudget(D("1"))
    install_fake(raising)
    with pytest.raises(LLMOutputError) as excinfo:
        run_call(session=session)
    row = only_row()
    assert excinfo.value.reason == "invalid_output"
    assert excinfo.value.call_id == row.pk
    assert row.status == "ok"
    assert row.error_code == "invalid_output"
    assert row.reserved_usd > 0
    assert row.cost_usd == row.reserved_usd
    assert budget.spend() == row.reserved_usd
    assert session.spent == row.reserved_usd
    assert guard().consecutive_errors == 0  # not a provider failure


def test_a_dict_that_does_not_fit_the_schema_is_an_output_error_too(install_fake):
    """The fake either raises ValidationError or returns parsed_output None for it; both end as LLMOutputError."""
    from moderation.errors import LLMOutputError

    install_fake({"label": "no score field"})
    with pytest.raises(LLMOutputError) as excinfo:
        run_call()
    assert excinfo.value.call_id == only_row().pk
    assert only_row().status == "ok"


# --- concurrent-looking reservations ----------------------------------------------------------------------------------


def test_a_second_call_started_while_the_first_is_in_flight_sees_the_first_reservation(install_fake, tune):
    """The day cap fits one worst-case call (about 0.008) but not two. The nested call runs while the outer one is
    pending, so only the reservation can stop it. Afterwards the outer call settles at its real, smaller cost and a
    new call fits again."""
    from moderation.errors import BudgetExceeded

    tune(BUDGET_SITE_USD_PER_DAY=D("0.010"), BUDGET_PER_CONVERSATION_USD=D("0.010"))
    nested = {}

    def outer(kwargs):
        try:
            run_call()
        except BudgetExceeded as err:
            nested["error"] = err
        return make_msg(input_tokens=100, output_tokens=50)  # real cost 0.0007

    client = install_fake(outer, reply(input_tokens=100, output_tokens=50))
    run_call()
    assert isinstance(nested.get("error"), BudgetExceeded)
    assert nested["error"].limit == D("0.010")
    assert nested["error"].spent > 0  # it saw the pending reservation
    run_call()  # the reservation has been replaced by the real cost, so there is room
    assert len(client.calls) == 2
    assert [r.status for r in rows()] == ["ok", "refused_budget", "ok"] or [r.status for r in rows()] == ["refused_budget", "ok", "ok"]
