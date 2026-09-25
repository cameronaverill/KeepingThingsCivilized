"""The two-tier circuit breaker (brief section 10): hard trips need a human; soft trips cool down and recover through
a single half-open probe with exponential backoff."""
from datetime import timedelta
from decimal import Decimal

import pytest
from moderation_testkit import (
    SONNET, SPEND_MSG, SPEND_MSG_WORKSPACE, raiser, reply, run_call, sdk_status_error, utc,
)

T0 = utc(2026, 9, 25, 12)


@pytest.fixture(autouse=True)
def setup(llm_ready, tune):
    tune(
        BREAKER_MAX_CONSECUTIVE_ERRORS=5, BREAKER_ERROR_WINDOW_SECONDS=300, BREAKER_COOLDOWN_SECONDS=300,
        BREAKER_COOLDOWN_MAX_SECONDS=3600, BREAKER_PROBE_TIMEOUT_SECONDS=180,
    )
    return llm_ready


@pytest.fixture
def clock(llm_ready):
    return llm_ready


def state():
    from moderation.models import GuardState

    return GuardState.objects.get(pk=1)


def err(status_code=500, error_type="api_error", error_code=None, message="upstream trouble"):
    from moderation import breaker

    breaker.record_error(status_code=status_code, error_type=error_type, error_code=error_code, message=message)


def soft_trip(**kwargs):
    for _ in range(5):
        err(**kwargs)
    return state().cooldown_until


def provider_error(status_code=500, error_type="api_error", message="upstream trouble", error_code=None):
    from moderation.fake_llm import FakeProviderError

    return FakeProviderError(status_code=status_code, error_type=error_type, message=message, error_code=error_code)


def statuses():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk").values_list("status", flat=True))


# --- new GuardState fields --------------------------------------------------------------------------------------------


def test_guard_state_has_the_new_fields_with_clean_defaults():
    from moderation.models import GuardState

    row = GuardState.load()
    assert row.trip_kind == ""
    assert row.cooldown_until is None
    assert isinstance(row.cooldown_seconds, int)
    assert row.probe_in_flight is False
    assert row.probe_started_at is None
    assert row.last_alert_at is None
    assert row.last_alert_error == ""


def test_guard_state_is_still_a_single_row_with_the_new_fields():
    from django.db import IntegrityError, transaction

    from moderation.models import GuardState

    GuardState.load()
    with pytest.raises(IntegrityError), transaction.atomic():
        GuardState.objects.bulk_create([GuardState(pk=2, trip_kind="hard")])
    assert GuardState.objects.count() == 1


def test_trip_kind_and_reason_choices():
    from moderation.models import GuardState

    kinds = {v for v, _ in GuardState._meta.get_field("trip_kind").choices}
    reasons = {v for v, _ in GuardState._meta.get_field("trip_reason").choices}
    assert {"hard", "soft"} <= kinds
    assert {"spend_limit", "consecutive_errors", "manual", "auth_error"} <= reasons


def test_the_second_migration_exists_and_the_models_are_in_sync():
    import io
    from pathlib import Path

    from django.apps import apps
    from django.core.management import call_command

    migrations = Path(apps.get_app_config("moderation").path) / "migrations"
    assert list(migrations.glob("0002_*.py"))
    call_command("makemigrations", "moderation", "--check", "--dry-run", stdout=io.StringIO())


# --- hard trips -----------------------------------------------------------------------------------------------------


HARD_CASES = [
    (400, "invalid_request_error", None, SPEND_MSG, "spend_limit"),
    (400, "invalid_request_error", None, SPEND_MSG_WORKSPACE, "spend_limit"),
    (429, "rate_limit_error", "enforced_spend_limit_reached", "Limit reached", "spend_limit"),
    (401, "authentication_error", None, "invalid x-api-key", "auth_error"),
    (403, "permission_error", None, "Your API key does not have permission", "auth_error"),
    (401, "api_error", None, "status alone is enough", "auth_error"),
    (403, "api_error", None, "status alone is enough", "auth_error"),
    (500, "authentication_error", None, "error type alone is enough", "auth_error"),
    (400, "permission_error", None, "error type alone is enough", "auth_error"),
    (None, "authentication_error", None, "no status, auth type", "auth_error"),
]


@pytest.mark.parametrize("status, error_type, code, message, reason", HARD_CASES)
def test_hard_errors_trip_at_once_with_no_cooldown(status, error_type, code, message, reason, clock):
    from moderation import breaker

    err(status, error_type, code, message)
    assert breaker.is_open() is True
    row = state()
    assert row.breaker_tripped is True
    assert row.trip_kind == "hard"
    assert row.trip_reason == reason
    assert row.cooldown_until is None
    assert row.probe_in_flight is False
    assert row.tripped_at == clock.now()
    assert message in row.trip_detail


@pytest.mark.parametrize("status, error_type, code, message, reason", HARD_CASES)
def test_a_hard_trip_stays_tripped_however_long_we_wait_and_only_reset_clears_it(status, error_type, code, message, reason, clock):
    from moderation import breaker

    err(status, error_type, code, message)
    clock.advance(days=30)
    assert breaker.is_open() is True
    for _ in range(3):
        breaker.check()  # never admits a probe for a hard trip
    assert breaker.is_open() is True
    assert state().probe_in_flight is False
    breaker.record_success()
    assert breaker.is_open() is True
    breaker.reset()
    assert breaker.is_open() is False


def test_a_manual_trip_is_hard(clock):
    from moderation import breaker

    breaker.trip("manual", "stopped by hand")
    row = state()
    assert (row.breaker_tripped, row.trip_kind, row.trip_reason, row.cooldown_until) == (True, "hard", "manual", None)
    clock.advance(days=3)
    assert breaker.is_open() is True


def test_check_reports_hard_with_no_retry_time():
    from moderation import breaker

    breaker.trip("manual")
    result = breaker.check()
    assert result.kind == "hard"
    assert result.retry_at is None


@pytest.mark.parametrize(
    "exc",
    [
        sdk_status_error("AuthenticationError", 401, "authentication_error", "invalid x-api-key"),
        sdk_status_error("PermissionDeniedError", 403, "permission_error", "no permission"),
        sdk_status_error("BadRequestError", 400, "invalid_request_error", SPEND_MSG),
        sdk_status_error("BadRequestError", 400, "invalid_request_error", SPEND_MSG_WORKSPACE),
        sdk_status_error(
            "RateLimitError", 429, "rate_limit_error", "Limit reached", details={"error_code": "enforced_spend_limit_reached"}
        ),
    ],
    ids=["401", "403", "400-spend", "400-workspace-spend", "429-enforced"],
)
def test_real_sdk_errors_hard_trip_and_refuse_every_later_call(install_fake, exc, clock):
    from moderation.errors import BreakerOpen, LLMAPIError

    client = install_fake(raiser(exc), reply())
    with pytest.raises(LLMAPIError):
        run_call()
    assert state().trip_kind == "hard"
    clock.advance(days=2)
    with pytest.raises(BreakerOpen) as excinfo:
        run_call()
    assert excinfo.value.kind == "hard"
    assert excinfo.value.retry_at is None
    assert len(client.calls) == 1
    assert statuses() == ["error", "refused_breaker"]


def test_a_hard_error_during_a_soft_cooldown_upgrades_to_hard(clock):
    from moderation import breaker

    soft_trip()
    assert state().trip_kind == "soft"
    err(401, "authentication_error", None, "invalid x-api-key")
    row = state()
    assert (row.trip_kind, row.trip_reason, row.cooldown_until) == ("hard", "auth_error", None)
    clock.advance(days=1)
    assert breaker.is_open() is True


# --- soft trips ---------------------------------------------------------------------------------------------------


COUNTED = [
    (None, "timeout_error", None, "Request timed out"),
    (None, "connection_error", None, "Connection error."),
    (500, "api_error", None, "Internal server error"),
    (502, "api_error", None, "Bad gateway"),
    (529, "overloaded_error", None, "Overloaded"),
    (429, "rate_limit_error", None, "Rate limited"),
    (429, "rate_limit_error", "some_other_code", "Rate limited"),
    (400, "invalid_request_error", None, "max_tokens: must be positive"),
    (404, "not_found_error", None, "model: not found"),
    (413, "request_too_large", None, "Request too large"),
]


@pytest.mark.parametrize("status, error_type, code, message", COUNTED)
def test_five_counted_errors_soft_trip_with_a_cooldown(status, error_type, code, message, clock):
    from moderation import breaker

    for _ in range(4):
        err(status, error_type, code, message)
        assert breaker.is_open() is False
        assert state().trip_kind == ""
    err(status, error_type, code, message)
    row = state()
    assert breaker.is_open() is True
    assert row.breaker_tripped is True
    assert (row.trip_kind, row.trip_reason) == ("soft", "consecutive_errors")
    assert row.cooldown_until == clock.now() + timedelta(seconds=300)
    assert row.cooldown_seconds == 300
    assert row.tripped_at == clock.now()
    assert row.probe_in_flight is False


def test_counted_errors_of_different_kinds_add_up():
    from moderation import breaker

    for args in [(None, "timeout_error"), (500, "api_error"), (529, "overloaded_error"), (429, "rate_limit_error"), (404, "not_found_error")]:
        err(*args)
    assert breaker.is_open() is True
    assert state().trip_kind == "soft"


def test_the_soft_threshold_and_window_are_the_tunables(tune, clock):
    from moderation import breaker

    tune(BREAKER_MAX_CONSECUTIVE_ERRORS=2, BREAKER_ERROR_WINDOW_SECONDS=10)
    err()
    clock.advance(seconds=11)  # a gap longer than the window restarts the count
    err()
    assert breaker.is_open() is False
    err()
    assert breaker.is_open() is True


def test_a_success_resets_the_soft_count():
    from moderation import breaker

    for _ in range(4):
        err()
    breaker.record_success()
    for _ in range(4):
        err()
    assert breaker.is_open() is False


def test_the_first_cooldown_is_the_tunable(tune, clock):
    tune(BREAKER_COOLDOWN_SECONDS=42)
    soft_trip()
    assert state().cooldown_until == clock.now() + timedelta(seconds=42)


def test_is_open_during_the_cooldown_and_check_reports_soft_with_the_retry_time(clock):
    from moderation import breaker

    until = soft_trip()
    clock.advance(seconds=299)
    assert breaker.is_open() is True
    result = breaker.check()
    assert result.kind == "soft"
    assert result.retry_at == until
    assert state().probe_in_flight is False  # nothing is admitted during the cooldown


def test_the_gateway_refuses_during_the_cooldown_and_never_calls_the_client(install_fake, clock):
    from moderation.errors import BreakerOpen, LLMAPIError

    client = install_fake(*[provider_error() for _ in range(5)], reply())
    for _ in range(5):
        with pytest.raises(LLMAPIError):
            run_call()
    until = state().cooldown_until
    clock.advance(seconds=299)
    for _ in range(3):
        with pytest.raises(BreakerOpen) as excinfo:
            run_call()
        assert excinfo.value.kind == "soft"
        assert excinfo.value.retry_at == until
    assert len(client.calls) == 5
    assert statuses() == ["error"] * 5 + ["refused_breaker"] * 3


def test_the_two_kinds_have_different_messages(install_fake, clock):
    from moderation import breaker
    from moderation.errors import BreakerOpen

    install_fake(reply())
    soft_trip()
    with pytest.raises(BreakerOpen) as soft:
        run_call()
    breaker.reset()
    breaker.trip("manual")
    with pytest.raises(BreakerOpen) as hard:
        run_call()
    assert str(soft.value) != str(hard.value)
    assert "temporarily unavailable" in str(soft.value).lower()
    assert "administrator" in str(hard.value).lower()
    assert (soft.value.kind, hard.value.kind) == ("soft", "hard")
    assert soft.value.status == hard.value.status == "refused_breaker"


# --- half-open probe ---------------------------------------------------------------------------------------------


def test_after_the_cooldown_exactly_one_probe_is_admitted(clock):
    from moderation import breaker

    soft_trip()
    clock.advance(seconds=300)  # now == cooldown_until: admitted
    assert breaker.is_open() is False  # a call would be let through
    breaker.check()  # claims the probe
    row = state()
    assert row.probe_in_flight is True
    assert row.probe_started_at == clock.now()
    assert breaker.is_open() is True  # everyone else is refused while it is in flight


def test_a_call_one_second_before_the_cooldown_ends_is_not_a_probe(clock):
    from moderation import breaker

    soft_trip()
    clock.advance(seconds=299)
    breaker.check()
    assert state().probe_in_flight is False
    assert breaker.is_open() is True


def test_while_a_probe_is_in_flight_other_callers_are_refused_soft(install_fake, clock):
    from moderation.errors import BreakerOpen
    from moderation_testkit import make_msg

    soft_trip()
    clock.advance(seconds=300)
    seen = {}

    def probe(kwargs):
        try:
            run_call()
        except BreakerOpen as err_:
            seen["error"] = err_
        seen["client_calls_so_far"] = len(client.calls)
        return make_msg()

    client = install_fake(probe, reply())
    run_call()
    assert seen["error"].kind == "soft"
    assert seen["client_calls_so_far"] == 1  # the refused caller never reached the client
    assert statuses() == ["refused_breaker", "ok"] or statuses() == ["ok", "refused_breaker"]


def test_probe_success_closes_the_breaker_and_resets_counts_and_cooldown(install_fake, clock):
    from moderation import breaker

    client = install_fake(reply())
    soft_trip()
    clock.advance(seconds=300)
    run_call()
    row = state()
    assert breaker.is_open() is False
    assert row.breaker_tripped is False
    assert row.trip_kind == ""
    assert row.consecutive_errors == 0
    assert row.cooldown_until is None
    assert row.probe_in_flight is False
    assert len(client.calls) == 1
    # Back to the base cooldown: the next soft trip is not doubled.
    clock.advance(seconds=1)
    soft_trip()
    assert state().cooldown_until == clock.now() + timedelta(seconds=300)


def test_probe_success_after_earlier_doublings_resets_the_backoff(clock):
    from moderation import breaker

    soft_trip()
    for _ in range(3):  # fail probes: 600, 1200, 2400
        clock.set(state().cooldown_until)
        breaker.check()
        err()
    assert state().cooldown_seconds == 2400
    clock.set(state().cooldown_until)
    breaker.check()
    breaker.record_success()
    assert breaker.is_open() is False
    clock.advance(seconds=1)
    soft_trip()
    assert state().cooldown_seconds == 300


def test_an_output_error_from_the_probe_counts_as_a_success(install_fake, clock):
    from moderation import breaker
    from moderation.errors import LLMOutputError

    install_fake(reply(parsed=None, stop_reason="max_tokens"))
    soft_trip()
    clock.advance(seconds=300)
    with pytest.raises(LLMOutputError):
        run_call()
    assert breaker.is_open() is False
    assert state().consecutive_errors == 0


def test_probe_failure_reopens_soft_with_a_doubled_cooldown(install_fake, clock):
    from moderation import breaker
    from moderation.errors import LLMAPIError

    client = install_fake(provider_error(503, "api_error", "still down"), reply())
    soft_trip()
    clock.advance(seconds=300)
    with pytest.raises(LLMAPIError):
        run_call()
    row = state()
    assert breaker.is_open() is True
    assert (row.trip_kind, row.trip_reason) == ("soft", "consecutive_errors")
    assert row.cooldown_seconds == 600
    assert row.cooldown_until == clock.now() + timedelta(seconds=600)
    assert row.probe_in_flight is False
    assert len(client.calls) == 1


def test_the_backoff_doubles_each_time_and_is_capped(clock):
    from moderation import breaker

    soft_trip()
    seen = [state().cooldown_seconds]
    for _ in range(5):
        clock.set(state().cooldown_until)
        breaker.check()
        err()
        seen.append(state().cooldown_seconds)
        assert state().cooldown_until == clock.now() + timedelta(seconds=state().cooldown_seconds)
    assert seen == [300, 600, 1200, 2400, 3600, 3600]


def test_the_cap_is_the_tunable(tune, clock):
    from moderation import breaker

    tune(BREAKER_COOLDOWN_MAX_SECONDS=1000)
    soft_trip()
    for expected in (600, 1000, 1000):
        clock.set(state().cooldown_until)
        breaker.check()
        err()
        assert state().cooldown_seconds == expected


@pytest.mark.parametrize(
    "status, error_type, code, message, reason",
    [(401, "authentication_error", None, "invalid x-api-key", "auth_error"), (400, "invalid_request_error", None, SPEND_MSG, "spend_limit")],
)
def test_a_hard_error_during_the_probe_hard_trips(status, error_type, code, message, reason, clock):
    from moderation import breaker

    soft_trip()
    clock.advance(seconds=300)
    breaker.check()
    err(status, error_type, code, message)
    row = state()
    assert (row.trip_kind, row.trip_reason, row.cooldown_until, row.probe_in_flight) == ("hard", reason, None, False)
    clock.advance(days=5)
    assert breaker.is_open() is True


def test_a_probe_left_in_flight_is_abandoned_after_the_timeout(clock):
    from moderation import breaker

    soft_trip()
    clock.advance(seconds=300)
    breaker.check()
    first_claim = clock.now()
    clock.advance(seconds=179)
    assert breaker.is_open() is True  # still in flight
    breaker.check()
    assert state().probe_started_at == first_claim  # not re-claimed yet
    clock.advance(seconds=2)  # 181 s after the claim
    assert breaker.is_open() is False  # a new caller may claim it
    breaker.check()
    assert state().probe_started_at == clock.now()
    assert state().probe_in_flight is True


def test_the_probe_timeout_is_the_tunable(tune, clock):
    from moderation import breaker

    tune(BREAKER_PROBE_TIMEOUT_SECONDS=10)
    soft_trip()
    clock.advance(seconds=300)
    breaker.check()
    clock.advance(seconds=11)
    assert breaker.is_open() is False


def test_an_abandoned_probe_can_be_reclaimed_and_then_close_the_breaker(install_fake, clock):
    from moderation import breaker

    client = install_fake(reply())
    soft_trip()
    clock.advance(seconds=300)
    breaker.check()  # the "process" that claimed it died
    clock.advance(seconds=181)
    run_call()
    assert breaker.is_open() is False
    assert len(client.calls) == 1


def test_reset_clears_every_part_of_the_state(clock):
    from moderation import breaker

    soft_trip()
    clock.advance(seconds=300)
    breaker.check()
    breaker.reset()
    row = state()
    assert row.breaker_tripped is False
    assert row.trip_kind == "" and row.trip_reason == ""
    assert row.cooldown_until is None
    assert row.probe_in_flight is False and row.probe_started_at is None
    assert row.consecutive_errors == 0
    assert breaker.is_open() is False


def test_reset_of_a_hard_trip_lets_calls_through_again(install_fake):
    from moderation import breaker

    client = install_fake(reply())
    breaker.trip("manual")
    breaker.reset()
    run_call()
    assert len(client.calls) == 1


# --- review additions -------------------------------------------------------------------------------------------------


def test_a_probe_in_flight_for_exactly_the_timeout_is_not_yet_abandoned(clock):
    """"Longer than BREAKER_PROBE_TIMEOUT_SECONDS" means abandoned only after 180 s; at exactly 180 s it is still in flight."""
    from moderation import breaker

    soft_trip()
    clock.advance(seconds=300)
    breaker.check()
    clock.advance(seconds=180)
    assert breaker.is_open() is True
    clock.advance(seconds=1)
    assert breaker.is_open() is False


def test_an_error_from_a_call_already_in_flight_changes_nothing_while_soft_tripped_with_no_probe(clock):
    """Calls that started before the trip may still fail during the cooldown; that must not extend the cooldown."""
    until = soft_trip()
    seconds = state().cooldown_seconds
    clock.advance(seconds=100)
    for _ in range(3):
        err()
    row = state()
    assert row.cooldown_until == until
    assert row.cooldown_seconds == seconds == 300
    assert row.trip_kind == "soft"
