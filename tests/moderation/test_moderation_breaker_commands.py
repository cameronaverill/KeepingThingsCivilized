"""manage.py budget and reset_breaker with the two-tier breaker and the alert settings."""
import re
from io import StringIO

import pytest
from django.core.management import call_command
from moderation_testkit import FAKE_KEY, SPEND_MSG, utc

ADDRESS = "alerts@example.test"


@pytest.fixture(autouse=True)
def setup(llm_ready, tune, settings):
    tune(BREAKER_MAX_CONSECUTIVE_ERRORS=5, BREAKER_ERROR_WINDOW_SECONDS=300, BREAKER_COOLDOWN_SECONDS=300,
         BREAKER_COOLDOWN_MAX_SECONDS=3600, BREAKER_PROBE_TIMEOUT_SECONDS=180, ALERT_MIN_SECONDS_BETWEEN_EMAILS=3600)
    settings.ALERT_EMAIL = ""
    return llm_ready


@pytest.fixture
def clock(llm_ready):
    return llm_ready


def run(name, *args):
    out = StringIO()
    call_command(name, *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def state():
    from moderation.models import GuardState

    return GuardState.objects.get(pk=1)


def err(status_code=500, error_type="api_error", error_code=None, message="upstream trouble"):
    from moderation import breaker

    breaker.record_error(status_code=status_code, error_type=error_type, error_code=error_code, message=message)


def soft_trip():
    for _ in range(5):
        err()


def lines_with(text, needle):
    found = [l for l in text.splitlines() if needle.lower() in l.lower()]
    assert found, f"nothing mentions {needle!r} in:\n{text}"
    return "\n".join(found)


# --- budget ---------------------------------------------------------------------------------------------------------


def test_budget_shows_a_soft_trip_with_reason_and_cooldown():
    soft_trip()
    text = run("budget")
    breaker_text = lines_with(text, "breaker")
    assert "soft" in text.lower()
    assert "consecutive_errors" in text
    assert "12:05" in text  # cooldown_until, 12:00 + 300 s
    assert "closed" not in breaker_text.lower()


def test_budget_shows_a_hard_trip_and_no_cooldown():
    from moderation import breaker

    breaker.trip("spend_limit", "detail-marker-xyz")
    text = run("budget")
    assert "hard" in text.lower()
    assert "spend_limit" in text and "detail-marker-xyz" in text
    assert "12:05" not in text


def test_budget_shows_the_probe_state(clock):
    from moderation import breaker

    soft_trip()
    before = run("budget")
    clock.advance(seconds=300)
    breaker.check()
    after = run("budget")
    assert before != after
    probe_after = lines_with(after, "probe").lower()
    probe_before = lines_with(before, "probe").lower()
    assert probe_after != probe_before
    assert "in flight" in probe_after or "in progress" in probe_after or "yes" in probe_after or "running" in probe_after


def test_budget_still_says_closed_when_nothing_is_wrong():
    assert "closed" in lines_with(run("budget"), "breaker").lower()


def test_budget_says_whether_the_alert_email_is_configured_and_never_prints_it(settings):
    settings.ALERT_EMAIL = ""
    line = lines_with(run("budget"), "alert").lower()
    assert "not configured" in line
    settings.ALERT_EMAIL = ADDRESS
    text = run("budget")
    line = lines_with(text, "alert").lower()
    assert "configured" in line and "not configured" not in line
    assert ADDRESS not in text
    assert "example.test" not in text
    assert "alerts@" not in text


def test_budget_shows_the_last_alert_time_and_error():
    from moderation.models import GuardState

    row = GuardState.load()
    row.last_alert_at = utc(2026, 9, 25, 10, 47, 13)
    row.save()
    assert "10:47" in run("budget")
    row.last_alert_error = "ConnectionRefusedError: smtp exploded"
    row.save()
    assert "smtp exploded" in run("budget")


def test_budget_does_not_print_the_secret_settings(settings):
    settings.ANTHROPIC_API_KEY = FAKE_KEY + "-canary-1234"
    settings.ALERT_EMAIL = ADDRESS
    assert "canary-1234" not in run("budget")


def test_budget_stays_read_only_with_a_tripped_breaker(clock):
    from moderation import breaker

    soft_trip()
    clock.advance(seconds=300)
    before = state()
    run("budget")
    run("budget")
    after = state()
    assert (after.probe_in_flight, after.cooldown_until, after.breaker_tripped, after.consecutive_errors) == (
        before.probe_in_flight, before.cooldown_until, before.breaker_tripped, before.consecutive_errors)
    assert breaker.is_open() is False  # the cooldown is over and no probe was claimed by the report


# --- reset_breaker ----------------------------------------------------------------------------------------------------


def test_reset_breaker_clears_a_soft_trip_with_its_cooldown_and_probe(clock):
    from moderation import breaker

    soft_trip()
    clock.advance(seconds=300)
    breaker.check()
    text = run("reset_breaker")
    row = state()
    assert row.breaker_tripped is False
    assert row.trip_kind == "" and row.trip_reason == ""
    assert row.cooldown_until is None
    assert row.probe_in_flight is False and row.probe_started_at is None
    assert row.consecutive_errors == 0
    assert "consecutive_errors" in text
    assert "soft" in text.lower()


def test_reset_breaker_clears_a_hard_trip_and_names_the_kind():
    err(401, "authentication_error", None, "invalid x-api-key")
    text = run("reset_breaker")
    assert state().breaker_tripped is False
    assert "hard" in text.lower() and "auth_error" in text


def test_reset_breaker_still_warns_about_the_console_only_for_spend_limits():
    from moderation import breaker

    err(400, "invalid_request_error", None, SPEND_MSG)
    assert "console" in run("reset_breaker").lower()
    err(401, "authentication_error", None, "invalid x-api-key")
    assert "console" not in run("reset_breaker").lower()
    breaker.trip("manual")
    assert "console" not in run("reset_breaker").lower()


def test_reset_breaker_on_a_closed_breaker_is_harmless():
    text = run("reset_breaker")
    assert text.strip()
    assert state().breaker_tripped is False
