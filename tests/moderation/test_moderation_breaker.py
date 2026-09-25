"""moderation/breaker.py: the circuit breaker and its persistent state (GuardState)."""
import pytest
from moderation_testkit import utc

SPEND_MSG = "You have reached your specified API usage limits. You will regain access on 2026-10-01 at 00:00 UTC."


@pytest.fixture(autouse=True)
def clock(frozen_clock, tune):
    tune(BREAKER_MAX_CONSECUTIVE_ERRORS=5, BREAKER_ERROR_WINDOW_SECONDS=300)
    return frozen_clock


def err(status_code=500, error_type="api_error", error_code=None, message="upstream trouble"):
    from moderation import breaker

    breaker.record_error(status_code=status_code, error_type=error_type, error_code=error_code, message=message)


def state():
    from moderation.models import GuardState

    return GuardState.objects.get(pk=1)


# --- initial state --------------------------------------------------------------------------------------------------


def test_breaker_starts_closed():
    from moderation import breaker

    assert breaker.is_open() is False


def test_load_creates_the_single_row_with_clean_defaults():
    from moderation.models import GuardState

    row = GuardState.load()
    assert row.pk == 1
    assert row.breaker_tripped is False
    assert row.consecutive_errors == 0
    assert row.tripped_at is None
    assert row.last_error_at is None
    assert not row.trip_reason
    assert not row.trip_detail


def test_load_is_idempotent_and_never_makes_a_second_row():
    from moderation.models import GuardState

    first, second = GuardState.load(), GuardState.load()
    assert first.pk == second.pk == 1
    assert GuardState.objects.count() == 1


def test_a_second_guard_state_row_cannot_be_created_with_save():
    from django.db import transaction

    from moderation.models import GuardState

    GuardState.load()
    try:
        with transaction.atomic():
            GuardState(pk=2).save()
    except Exception:
        pass  # rejected outright: fine
    assert list(GuardState.objects.values_list("pk", flat=True)) == [1]  # or silently forced onto pk=1: also fine


def test_a_second_guard_state_row_cannot_bypass_save_either():
    """bulk_create skips save(); only a database-level rule stops it (a CHECK pk = 1 constraint or similar)."""
    from django.db import IntegrityError, transaction

    from moderation.models import GuardState

    GuardState.load()
    with pytest.raises(IntegrityError), transaction.atomic():
        GuardState.objects.bulk_create([GuardState(pk=2)])
    assert GuardState.objects.count() == 1


# --- immediate trips (spend-limit errors) ---------------------------------------------------------------------------


def test_spend_limit_400_trips_immediately(clock):
    from moderation import breaker

    err(status_code=400, error_type="invalid_request_error", message=SPEND_MSG)
    assert breaker.is_open() is True
    row = state()
    assert row.breaker_tripped is True
    assert row.trip_reason == "spend_limit"
    assert SPEND_MSG in row.trip_detail
    assert row.tripped_at == clock.now()


def test_spend_limit_429_with_the_enforced_code_trips_immediately():
    from moderation import breaker

    err(status_code=429, error_type="rate_limit_error", error_code="enforced_spend_limit_reached", message="Limit reached")
    assert breaker.is_open() is True
    assert state().trip_reason == "spend_limit"


@pytest.mark.parametrize(
    "kwargs",
    [
        # 400 invalid_request_error, but a different message (an ordinary bad request)
        dict(status_code=400, error_type="invalid_request_error", message="max_tokens: must be positive"),
        # The words appear, but the message does not START with them
        dict(status_code=400, error_type="invalid_request_error", message="Error: You have reached your specified limit"),
        # Right message, wrong status
        dict(status_code=500, error_type="invalid_request_error", message=SPEND_MSG),
        # Right message and status, wrong error type
        dict(status_code=400, error_type="api_error", message=SPEND_MSG),
        # 429 without the enforced code is an ordinary rate limit
        dict(status_code=429, error_type="rate_limit_error", error_code=None, message="Rate limited"),
        dict(status_code=429, error_type="rate_limit_error", error_code="some_other_code", message="Rate limited"),
        # The code on a non-429 status
        dict(status_code=500, error_type="api_error", error_code="enforced_spend_limit_reached", message="odd"),
    ],
)
def test_ordinary_errors_do_not_trip_immediately(kwargs):
    from moderation import breaker

    err(**kwargs)
    assert breaker.is_open() is False
    assert state().consecutive_errors == 1


# --- consecutive errors ---------------------------------------------------------------------------------------------


def test_five_errors_in_a_row_trip_the_breaker(clock):
    from moderation import breaker

    for _ in range(4):
        err(message="boom")
        assert breaker.is_open() is False
    assert state().consecutive_errors == 4
    err(message="the fifth")
    assert breaker.is_open() is True
    row = state()
    assert row.trip_reason == "consecutive_errors"
    assert row.trip_kind == "soft"  # since the two-tier breaker: a cooldown, not a permanent trip (see test_moderation_breaker_tiers.py)
    assert row.cooldown_until == clock.now() + __import__("datetime").timedelta(seconds=300)
    assert "the fifth" in row.trip_detail or "5" in row.trip_detail
    assert row.tripped_at == clock.now()


def test_the_error_count_threshold_is_the_tunable(tune):
    from moderation import breaker

    tune(BREAKER_MAX_CONSECUTIVE_ERRORS=2)
    err()
    assert breaker.is_open() is False
    err()
    assert breaker.is_open() is True


def test_errors_are_recorded_with_time(clock):
    err()
    assert state().last_error_at == clock.now()


def test_errors_spaced_beyond_the_window_never_trip(clock):
    from moderation import breaker

    for _ in range(12):
        err()
        clock.advance(seconds=301)
        assert breaker.is_open() is False
    assert state().consecutive_errors == 1


def test_an_error_after_a_long_gap_starts_the_count_again(clock):
    err()
    err()
    err()
    assert state().consecutive_errors == 3
    clock.advance(seconds=301)
    err()
    assert state().consecutive_errors == 1


def test_a_gap_of_exactly_the_window_still_counts_as_in_a_row(clock):
    """"More than" the window resets the count; exactly the window does not."""
    err()
    clock.advance(seconds=300)
    err()
    assert state().consecutive_errors == 2


def test_the_window_runs_from_the_previous_error_not_from_the_first(clock):
    """Five errors, each 200 s after the last (800 s in total) still trip: no gap is longer than 300 s."""
    from moderation import breaker

    for i in range(5):
        err()
        if i < 4:
            clock.advance(seconds=200)
    assert breaker.is_open() is True


def test_the_window_is_the_tunable(tune, clock):
    tune(BREAKER_ERROR_WINDOW_SECONDS=10)
    err()
    clock.advance(seconds=11)
    err()
    assert state().consecutive_errors == 1


def test_a_success_resets_the_count():
    from moderation import breaker

    for _ in range(4):
        err()
    breaker.record_success()
    assert state().consecutive_errors == 0
    for _ in range(4):
        err()
    assert breaker.is_open() is False
    err()
    assert breaker.is_open() is True


def test_a_success_never_closes_a_tripped_breaker():
    from moderation import breaker

    breaker.trip("manual", "stopped by hand")
    breaker.record_success()
    assert breaker.is_open() is True


# --- trip() and reset() ---------------------------------------------------------------------------------------------


def test_manual_trip_records_reason_detail_and_time(clock):
    from moderation import breaker

    breaker.trip("manual", "operator pressed the button")
    assert breaker.is_open() is True
    row = state()
    assert (row.breaker_tripped, row.trip_reason, row.trip_detail) == (True, "manual", "operator pressed the button")
    assert row.tripped_at == clock.now()


def test_trip_detail_defaults_to_empty():
    from moderation import breaker

    breaker.trip("manual")
    assert state().trip_detail == ""


def test_reset_closes_the_breaker_and_clears_the_count():
    from moderation import breaker

    for _ in range(5):
        err()
    assert breaker.is_open() is True
    breaker.reset()
    assert breaker.is_open() is False
    row = state()
    assert row.breaker_tripped is False
    assert row.consecutive_errors == 0


def test_after_a_reset_the_breaker_can_trip_again():
    from moderation import breaker

    breaker.trip("manual")
    breaker.reset()
    for _ in range(5):
        err()
    assert breaker.is_open() is True
    assert state().trip_reason == "consecutive_errors"


def test_reset_on_a_closed_breaker_is_harmless_and_clears_the_count():
    from moderation import breaker

    err()
    err()
    breaker.reset()
    assert breaker.is_open() is False
    assert state().consecutive_errors == 0


# --- persistence: nothing is cached in memory -----------------------------------------------------------------------


def test_the_breaker_state_lives_in_the_database_not_in_memory():
    """Simulated restart: state written straight to the table (as another process would) is seen immediately."""
    from moderation import breaker
    from moderation.models import GuardState

    GuardState.load()
    GuardState.objects.filter(pk=1).update(breaker_tripped=True, trip_reason="spend_limit")
    assert breaker.is_open() is True
    GuardState.objects.filter(pk=1).update(breaker_tripped=False)
    assert breaker.is_open() is False


def test_the_error_count_survives_a_restart():
    from moderation import breaker
    from moderation.models import GuardState

    err()
    err()
    err()
    GuardState.objects.filter(pk=1).update(consecutive_errors=4)  # what a fresh process would load
    err()
    assert breaker.is_open() is True


def test_a_tripped_breaker_stays_tripped_when_reloaded_from_the_database():
    from moderation import breaker
    from moderation.models import GuardState

    breaker.trip("spend_limit", "limit reached")
    fresh = GuardState.objects.get(pk=1)
    assert fresh.breaker_tripped is True
    assert (fresh.trip_reason, fresh.trip_detail) == ("spend_limit", "limit reached")
    assert GuardState.load().breaker_tripped is True


def test_record_error_arguments_are_keyword_only():
    from moderation import breaker

    with pytest.raises(TypeError):
        breaker.record_error(500, "api_error", None, "x")
