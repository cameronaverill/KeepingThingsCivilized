"""The whole provider-error mapping against Anthropic's documented error list (400 invalid_request_error, 401
authentication_error, 402 billing_error, 403 permission_error, 404 not_found_error, 409 conflict_error, 413
request_too_large, 429 rate_limit_error, 500 api_error, 504 timeout_error, 529 overloaded_error): what llm.py
normalizes, what the ledger stores, what is raised, what it costs, and how the breaker classifies it. Real SDK
exception objects throughout."""
from decimal import Decimal

import pytest
from moderation_testkit import (
    SPEND_MSG, SPEND_MSG_WORKSPACE, raiser, reply, run_call, sdk_status_error, sdk_status_error_raw,
)

D = Decimal
CREDIT_MSG = "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."
PREFILL_MSG = "This model does not support assistant message prefill. The conversation must end with a user message."


@pytest.fixture(autouse=True)
def setup(llm_ready, tune):
    tune(BREAKER_MAX_CONSECUTIVE_ERRORS=5, BREAKER_ERROR_WINDOW_SECONDS=300, BREAKER_COOLDOWN_SECONDS=300,
         BREAKER_COOLDOWN_MAX_SECONDS=3600, BREAKER_PROBE_TIMEOUT_SECONDS=180)
    return llm_ready


def guard():
    from moderation.models import GuardState

    return GuardState.load()


def only_row():
    from moderation.models import LLMCall

    return LLMCall.objects.get()


# (class, status, error_type, message, details, expected breaker class, expected hard reason)
TABLE = [
    ("BadRequestError", 400, "invalid_request_error", "max_tokens: must be greater than 0", None, "soft", None),
    ("BadRequestError", 400, "invalid_request_error", PREFILL_MSG, None, "soft", None),
    ("BadRequestError", 400, "invalid_request_error", SPEND_MSG, None, "hard", "spend_limit"),
    ("BadRequestError", 400, "invalid_request_error", SPEND_MSG_WORKSPACE, None, "hard", "spend_limit"),
    ("AuthenticationError", 401, "authentication_error", "invalid x-api-key", None, "hard", "auth_error"),
    ("APIStatusError", 402, "billing_error", CREDIT_MSG, None, "hard", "billing_error"),
    ("PermissionDeniedError", 403, "permission_error", "Your API key does not have permission to use the specified resource.", None, "hard", "auth_error"),
    ("NotFoundError", 404, "not_found_error", "model: claude-nope", None, "soft", None),
    ("ConflictError", 409, "conflict_error", "Conflict", None, "soft", None),
    ("RequestTooLargeError", 413, "request_too_large", "Request exceeds the maximum allowed number of bytes.", None, "soft", None),
    ("RateLimitError", 429, "rate_limit_error", "This request would exceed your rate limit", None, "soft", None),
    ("RateLimitError", 429, "rate_limit_error", "Monthly cap reached", {"error_code": "enforced_spend_limit_reached"}, "hard", "spend_limit"),
    ("InternalServerError", 500, "api_error", "An unexpected error has occurred internal to Anthropic's systems.", None, "soft", None),
    ("DeadlineExceededError", 504, "timeout_error", "Request timed out", None, "soft", None),
    ("OverloadedError", 529, "overloaded_error", "Overloaded", None, "soft", None),
]
IDS = [f"{row[1]}-{row[2]}-{row[5]}" + ("-code" if row[4] else "") for row in TABLE]


@pytest.mark.parametrize("cls, status, error_type, message, details, kind, reason", TABLE, ids=IDS)
def test_normalizer_returns_status_type_code_and_message(cls, status, error_type, message, details, kind, reason):
    from moderation import llm

    exc = sdk_status_error(cls, status, error_type, message, details)
    got_status, got_type, got_code, got_message = llm._normalize_provider_error(exc)[:4]
    assert got_status == status
    assert got_type == error_type
    assert (got_code or "") == ((details or {}).get("error_code") or "")
    assert got_message == message


@pytest.mark.parametrize("cls, status, error_type, message, details, kind, reason", TABLE, ids=IDS)
def test_the_whole_path_ledger_exception_cost_and_breaker(install_fake, cls, status, error_type, message, details, kind, reason):
    from moderation import breaker, budget
    from moderation.errors import LLMAPIError

    code = (details or {}).get("error_code") or ""
    install_fake(raiser(sdk_status_error(cls, status, error_type, message, details)))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    err = excinfo.value
    assert (err.status_code, err.error_type, err.message) == (status, error_type, message)
    assert (err.error_code or "") == code
    row = only_row()
    assert err.call_id == row.pk
    assert row.status == "error"
    assert row.error_code == (code or error_type)
    assert message in row.error
    assert budget.spend() == D("0")  # an HTTP status means the request was rejected: not billed
    state = guard()
    if kind == "hard":
        assert breaker.is_open() is True
        assert (state.trip_kind, state.trip_reason, state.cooldown_until) == ("hard", reason, None)
    else:
        assert breaker.is_open() is False  # one counted error
        assert (state.breaker_tripped, state.consecutive_errors) == (False, 1)


@pytest.mark.parametrize("cls, status, error_type, message, details, kind, reason", [r for r in TABLE if r[5] == "soft"], ids=[i for r, i in zip(TABLE, IDS) if r[5] == "soft"])
def test_five_of_each_counted_error_soft_trip(install_fake, cls, status, error_type, message, details, kind, reason):
    from moderation import breaker
    from moderation.errors import BreakerOpen, LLMAPIError

    install_fake(*[raiser(sdk_status_error(cls, status, error_type, message, details)) for _ in range(5)])
    for _ in range(5):
        with pytest.raises(LLMAPIError):
            run_call()
    state = guard()
    assert breaker.is_open() is True
    assert (state.trip_kind, state.trip_reason) == ("soft", "consecutive_errors")
    with pytest.raises(BreakerOpen) as excinfo:
        run_call()
    assert excinfo.value.kind == "soft"


@pytest.mark.parametrize("kind, error_type", [("connection", "connection_error"), ("timeout", "timeout_error")])
def test_status_less_failures_keep_the_reservation_and_are_counted(install_fake, kind, error_type):
    import anthropic
    import httpx2

    from moderation import breaker, budget
    from moderation.errors import LLMAPIError

    request = httpx2.Request("POST", "https://api.example.test/v1/messages")
    exc = anthropic.APIConnectionError(request=request) if kind == "connection" else anthropic.APITimeoutError(request)
    install_fake(raiser(exc))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    err = excinfo.value
    row = only_row()
    assert (err.status_code, err.error_type) == (None, error_type)
    assert row.status == "error" and row.error_code == error_type
    assert budget.spend() == row.reserved_usd > 0
    assert guard().consecutive_errors == 1 and breaker.is_open() is False


# --- billing (new) -----------------------------------------------------------------------------------------------------


BILLING_CASES = [
    ("APIStatusError", 402, "billing_error", CREDIT_MSG),
    ("BadRequestError", 400, "billing_error", "billing problem"),
    ("BadRequestError", 400, "invalid_request_error", CREDIT_MSG),
    ("BadRequestError", 400, "invalid_request_error", "Your CREDIT BALANCE is too low"),
    ("BadRequestError", 400, "invalid_request_error", "credit balance too low, add credits"),
]


@pytest.mark.parametrize("cls, status, error_type, message", BILLING_CASES)
def test_billing_problems_hard_trip_and_refuse_later_calls(install_fake, cls, status, error_type, message):
    from moderation.errors import BreakerOpen, LLMAPIError

    client = install_fake(raiser(sdk_status_error(cls, status, error_type, message)), reply())
    with pytest.raises(LLMAPIError):
        run_call()
    state = guard()
    assert (state.trip_kind, state.trip_reason, state.cooldown_until) == ("hard", "billing_error", None)
    with pytest.raises(BreakerOpen) as excinfo:
        run_call()
    assert excinfo.value.kind == "hard" and excinfo.value.retry_at is None
    assert len(client.calls) == 1


@pytest.mark.parametrize("args", [(402, "billing_error", CREDIT_MSG), (None, "billing_error", "x"), (400, "invalid_request_error", CREDIT_MSG)])
def test_billing_problems_hard_trip_through_record_error_directly(args):
    from moderation import breaker

    status, error_type, message = args
    breaker.record_error(status_code=status, error_type=error_type, error_code=None, message=message)
    assert guard().trip_reason == "billing_error" and guard().trip_kind == "hard"


def test_trip_reason_accepts_billing_error_and_the_migrations_are_current():
    import io

    from django.core.management import call_command

    from moderation.models import GuardState

    assert "billing_error" in {v for v, _ in GuardState._meta.get_field("trip_reason").choices}
    call_command("makemigrations", "moderation", "--check", "--dry-run", stdout=io.StringIO())
    row = GuardState.load()
    row.trip_reason = "billing_error"
    row.full_clean(exclude=["tripped_at", "last_error_at", "cooldown_until", "probe_started_at", "last_alert_at"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("error_type, message", [("billing_error", CREDIT_MSG), ("invalid_request_error", "Your Credit Balance is too low")])
def test_billing_alert_email(install_fake, settings, mailoutbox, error_type, message):
    from moderation.errors import LLMAPIError

    settings.ALERT_EMAIL = "alerts@example.test"
    status = 402 if error_type == "billing_error" else 400
    install_fake(raiser(sdk_status_error("APIStatusError" if status == 402 else "BadRequestError", status, error_type, message)))
    with pytest.raises(LLMAPIError):
        run_call()
    assert len(mailoutbox) == 1
    mail = mailoutbox[0]
    assert mail.subject == "[Forum] AI moderator paused: billing problem"
    lowered = mail.body.lower()
    assert "billing" in lowered and "credit balance" in lowered
    assert "reset_breaker" in mail.body
    assert mail.to == ["alerts@example.test"]


# --- spend-limit fallback (new) ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "Workspace spend limit reached, contact your administrator.",
        "Your API USAGE LIMIT has been hit for this month",
        "spend limit exceeded",
        "You have hit the Usage Limit set for this workspace",
    ],
)
def test_a_400_mentioning_a_usage_or_spend_limit_is_a_hard_spend_limit_trip_even_without_the_documented_prefix(install_fake, message):
    from moderation import breaker
    from moderation.errors import LLMAPIError

    assert not message.startswith("You have reached your specified")
    install_fake(raiser(sdk_status_error("BadRequestError", 400, "invalid_request_error", message)))
    with pytest.raises(LLMAPIError):
        run_call()
    state = guard()
    assert breaker.is_open() is True
    assert (state.trip_kind, state.trip_reason) == ("hard", "spend_limit")


@pytest.mark.parametrize(
    "message",
    [
        PREFILL_MSG,
        "max_tokens: must be greater than 0",
        "messages: at least one message is required",
        "Error: You have reached your specified limit",  # the old test's near-miss: not a usage/spend limit phrase
        "rate limit exceeded",
    ],
)
def test_an_unrelated_400_is_only_soft_counted(install_fake, message):
    from moderation import breaker
    from moderation.errors import LLMAPIError

    install_fake(raiser(sdk_status_error("BadRequestError", 400, "invalid_request_error", message)))
    with pytest.raises(LLMAPIError):
        run_call()
    assert breaker.is_open() is False
    assert (guard().breaker_tripped, guard().consecutive_errors) == (False, 1)


# --- empty labels (new) ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("cls, status", [("InternalServerError", 502), ("ServiceUnavailableError", 503), ("InternalServerError", 504)])
@pytest.mark.parametrize("body", ["<html>Bad gateway</html>", None, "", {"unexpected": "shape"}], ids=["html", "none", "empty", "odd-json"])
def test_an_error_with_a_status_but_no_usable_type_is_labelled_http_status(install_fake, cls, status, body):
    from moderation.errors import LLMAPIError

    install_fake(raiser(sdk_status_error_raw(cls, status, body, message="Bad gateway")))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    assert excinfo.value.status_code == status
    assert excinfo.value.error_type == f"http_{status}"
    row = only_row()
    assert row.error_code == f"http_{status}"
    assert row.status == "error"
    assert guard().consecutive_errors == 1  # still counted, not a hard error


def test_the_fake_provider_error_with_an_empty_type_gets_the_same_label(install_fake):
    from moderation.errors import LLMAPIError
    from moderation.fake_llm import FakeProviderError

    install_fake(FakeProviderError(502, "", "Bad gateway"))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    assert excinfo.value.error_type == "http_502"
    assert only_row().error_code


def test_a_real_type_is_never_replaced_by_the_fallback(install_fake):
    from moderation.errors import LLMAPIError

    install_fake(raiser(sdk_status_error("InternalServerError", 500, "api_error", "boom")))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    assert excinfo.value.error_type == "api_error"
    assert only_row().error_code == "api_error"


# --- capability tables (new) ---------------------------------------------------------------------------------------------


def test_capability_tables_cover_exactly_the_allowed_models():
    from moderation import pricing

    assert set(pricing.ACCEPTS_THINKING_DISABLED) == set(pricing.ALLOWED_MODELS)
    assert set(pricing.ACCEPTS_TEMPERATURE) == set(pricing.ALLOWED_MODELS)
    for table in (pricing.ACCEPTS_THINKING_DISABLED, pricing.ACCEPTS_TEMPERATURE):
        assert all(isinstance(v, bool) for v in table.values())
    assert all(pricing.ACCEPTS_THINKING_DISABLED.values())  # both allowed models accept thinking: disabled


@pytest.fixture
def fake_model(monkeypatch):
    """A model added to the allow-list for one test, priced here, with capabilities the test controls."""
    from moderation import pricing

    name = "claude-test-model"
    price = pricing.ModelPrice(input=D("1"), output=D("5"), cache_write_5m=D("1.25"), cache_write_1h=D("2"), cache_read=D("0.10"))
    monkeypatch.setitem(pricing.ALLOWED_MODELS, name, price)
    monkeypatch.setitem(pricing.ACCEPTS_TEMPERATURE, name, True)
    monkeypatch.setitem(pricing.ACCEPTS_THINKING_DISABLED, name, True)
    return name


def test_a_model_that_rejects_thinking_disabled_is_a_value_error_before_any_row(install_fake, fake_model, monkeypatch):
    from moderation import pricing
    from moderation.models import LLMCall

    monkeypatch.setitem(pricing.ACCEPTS_THINKING_DISABLED, fake_model, False)
    client = install_fake(reply())
    with pytest.raises(ValueError):
        run_call(model=fake_model)
    assert client.calls == [] and LLMCall.objects.count() == 0


def test_the_thinking_check_comes_before_the_kill_switch(install_fake, fake_model, monkeypatch, settings):
    from moderation import pricing
    from moderation.models import LLMCall

    settings.LLM_ENABLED = False
    monkeypatch.setitem(pricing.ACCEPTS_THINKING_DISABLED, fake_model, False)
    install_fake(reply())
    with pytest.raises(ValueError):
        run_call(model=fake_model)
    assert LLMCall.objects.count() == 0


def test_a_model_that_accepts_everything_works_and_sends_thinking_disabled(install_fake, fake_model):
    client = install_fake(reply())
    run_call(model=fake_model, temperature=0.2)
    assert client.calls[0]["thinking"] == {"type": "disabled"}
    assert client.calls[0]["extra_body"]["temperature"] == 0.2


def test_the_temperature_table_entry_false_still_raises_value_error(install_fake, fake_model, monkeypatch):
    from moderation import pricing

    monkeypatch.setitem(pricing.ACCEPTS_TEMPERATURE, fake_model, False)
    install_fake(reply())
    with pytest.raises(ValueError):
        run_call(model=fake_model, temperature=0.2)
    run_call(model=fake_model)  # without a temperature it is fine


# --- output failures: recorded ok, breaker SUCCESS -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, code",
    [
        (dict(parsed=None, stop_reason="max_tokens"), "truncated"),
        (dict(parsed=None, stop_reason="refusal"), "refusal"),
        (dict(parsed=None, stop_reason="end_turn"), "invalid_output"),
    ],
    ids=["truncated", "refusal", "invalid"],
)
def test_unusable_output_is_recorded_ok_and_counts_as_a_breaker_success(install_fake, kwargs, code):
    from moderation import breaker
    from moderation.errors import LLMOutputError

    for _ in range(3):
        breaker.record_error(status_code=500, error_type="api_error", error_code=None, message="x")
    assert guard().consecutive_errors == 3
    install_fake(reply(**kwargs))
    with pytest.raises(LLMOutputError):
        run_call()
    row = only_row()
    assert (row.status, row.error_code) == ("ok", code)
    assert guard().consecutive_errors == 0  # a success for the breaker
