"""Email alerts when the breaker opens (brief section 10). Django's locmem outbox (pytest-django's mailoutbox) is the
only mailer; the address is a fake. Transactional tests, so anything sent after the trip's transaction commits is sent."""
import logging
from datetime import timedelta

import pytest
from moderation_testkit import SPEND_MSG, FAKE_KEY, raiser, reply, run_call, sdk_status_error, utc

pytestmark = pytest.mark.django_db(transaction=True)

ADDRESS = "alerts@example.test"
SUBJECTS = {
    "spend_limit": "[Forum] AI moderator paused: spend limit reached",
    "auth_error": "[Forum] AI moderator paused: API key rejected",
    "consecutive_errors": "[Forum] AI moderator paused: repeated API errors",
    "manual": "[Forum] AI moderator paused: manually tripped",
}


@pytest.fixture(autouse=True)
def setup(llm_ready, tune, settings):
    tune(
        BREAKER_MAX_CONSECUTIVE_ERRORS=5, BREAKER_ERROR_WINDOW_SECONDS=300, BREAKER_COOLDOWN_SECONDS=300,
        BREAKER_COOLDOWN_MAX_SECONDS=3600, BREAKER_PROBE_TIMEOUT_SECONDS=180, ALERT_MIN_SECONDS_BETWEEN_EMAILS=3600,
    )
    settings.ALERT_EMAIL = ADDRESS
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


def hard_spend(message=SPEND_MSG):
    err(400, "invalid_request_error", None, message)


def soft_trip(message="upstream trouble", status=500, error_type="api_error"):
    for _ in range(5):
        err(status, error_type, None, message)


def reopen_soft_after_failed_probe(clock):
    from moderation import breaker

    clock.set(state().cooldown_until)
    breaker.check()
    err()


# --- when an email is sent ------------------------------------------------------------------------------------------


def test_a_hard_trip_sends_one_email_to_the_alert_address(mailoutbox, settings):
    hard_spend()
    assert len(mailoutbox) == 1
    mail = mailoutbox[0]
    assert mail.to == [ADDRESS]
    assert mail.from_email == settings.DEFAULT_FROM_EMAIL
    assert mail.subject == SUBJECTS["spend_limit"]


def test_an_auth_error_email(mailoutbox):
    err(401, "authentication_error", None, "invalid x-api-key")
    assert [m.subject for m in mailoutbox] == [SUBJECTS["auth_error"]]


def test_a_manual_trip_email(mailoutbox):
    from moderation import breaker

    breaker.trip("manual", "stopped by hand")
    assert [m.subject for m in mailoutbox] == [SUBJECTS["manual"]]


def test_a_soft_trip_sends_one_email(mailoutbox):
    soft_trip()
    assert [m.subject for m in mailoutbox] == [SUBJECTS["consecutive_errors"]]


def test_no_email_before_the_trip(mailoutbox):
    for _ in range(4):
        err()
    assert mailoutbox == []


def test_one_email_per_closed_to_open_transition_not_one_per_error(mailoutbox):
    soft_trip()
    for _ in range(5):  # more errors from calls that were already in flight
        err()
    assert len(mailoutbox) == 1


def test_a_second_hard_error_while_already_hard_tripped_sends_nothing(mailoutbox):
    hard_spend()
    hard_spend()
    err(401, "authentication_error", None, "invalid x-api-key")  # upgrades nothing: already hard
    assert len(mailoutbox) == 1


def test_a_soft_reopen_after_a_failed_probe_sends_an_email_when_the_rate_limit_allows(mailoutbox, clock):
    from moderation import breaker

    soft_trip()
    assert len(mailoutbox) == 1
    clock.set(clock.now() + timedelta(seconds=3700))  # long past the cooldown and the rate limit
    breaker.check()
    err()
    assert state().trip_kind == "soft"
    assert len(mailoutbox) == 2
    assert mailoutbox[1].subject == SUBJECTS["consecutive_errors"]


def test_a_hard_trip_during_a_probe_sends_a_hard_email(mailoutbox, clock):
    from moderation import breaker

    soft_trip()
    clock.advance(seconds=300)
    breaker.check()
    hard_spend()
    assert [m.subject for m in mailoutbox] == [SUBJECTS["consecutive_errors"], SUBJECTS["spend_limit"]]


def test_real_sdk_errors_send_the_alerts_too(install_fake, mailoutbox):
    from moderation.errors import LLMAPIError

    install_fake(raiser(sdk_status_error("AuthenticationError", 401, "authentication_error", "invalid x-api-key")))
    with pytest.raises(LLMAPIError):
        run_call()
    assert [m.subject for m in mailoutbox] == [SUBJECTS["auth_error"]]


def test_no_email_when_alert_email_is_empty(mailoutbox, settings):
    settings.ALERT_EMAIL = ""
    hard_spend()
    soft_trip()
    assert mailoutbox == []
    row = state()
    assert row.breaker_tripped is True  # the trip itself is unaffected
    assert row.last_alert_at is None
    assert row.last_alert_error == ""


def test_no_email_when_alert_email_is_unset(mailoutbox, settings):
    del settings.ALERT_EMAIL
    hard_spend()
    assert mailoutbox == []
    assert state().breaker_tripped is True


# --- rate limiting --------------------------------------------------------------------------------------------------


def test_a_sent_alert_records_last_alert_at(mailoutbox, clock):
    hard_spend()
    assert state().last_alert_at == clock.now()
    assert state().last_alert_error == ""


def test_soft_alerts_are_rate_limited_by_the_tunable(mailoutbox, clock):
    soft_trip()
    assert len(mailoutbox) == 1
    reopen_soft_after_failed_probe(clock)  # about 5 minutes later: inside the hour
    assert state().trip_kind == "soft"
    assert len(mailoutbox) == 1


@pytest.mark.parametrize("gap, expect_email", [(3599, False), (3600, True), (3601, True)])
def test_the_soft_rate_limit_boundary_is_at_least_the_tunable_seconds(mailoutbox, clock, gap, expect_email):
    from moderation import breaker

    soft_trip()
    clock.set(utc(2026, 9, 25, 12) + timedelta(seconds=gap))
    breaker.check()
    err()
    assert len(mailoutbox) == (2 if expect_email else 1)


def test_the_soft_rate_limit_is_read_from_the_tunable(mailoutbox, clock, tune):
    from moderation import breaker

    tune(ALERT_MIN_SECONDS_BETWEEN_EMAILS=100)
    soft_trip()
    clock.advance(seconds=400)  # past the 300 s cooldown and the 100 s limit
    breaker.check()
    err()
    assert len(mailoutbox) == 2


def test_a_hard_trip_always_sends_even_right_after_a_soft_alert(mailoutbox):
    soft_trip()
    hard_spend()
    assert [m.subject for m in mailoutbox] == [SUBJECTS["consecutive_errors"], SUBJECTS["spend_limit"]]


def test_a_hard_trip_always_sends_even_right_after_another_hard_alert(mailoutbox):
    from moderation import breaker

    hard_spend()
    breaker.reset()
    err(401, "authentication_error", None, "invalid x-api-key")
    assert len(mailoutbox) == 2


def test_a_soft_trip_right_after_a_hard_alert_is_rate_limited(mailoutbox):
    """last_alert_at is shared by both kinds."""
    from moderation import breaker

    hard_spend()
    breaker.reset()
    soft_trip()
    assert len(mailoutbox) == 1


# --- content ----------------------------------------------------------------------------------------------------------


def body_of(mailoutbox):
    assert len(mailoutbox) == 1
    return mailoutbox[0].subject + "\n" + mailoutbox[0].body


def test_spend_limit_email_says_what_happened_what_it_means_and_what_to_do(mailoutbox):
    hard_spend()
    text = body_of(mailoutbox)
    lowered = text.lower()
    assert "spend limit" in lowered
    assert "400" in text and "invalid_request_error" in text
    assert SPEND_MSG in text  # the provider's own message
    assert "2026-09-25" in text and "12:00" in text and "UTC" in text
    assert "posting" in lowered  # moderation is paused; posting still works
    assert "console" in lowered  # check the Console limit
    assert "reset_breaker" in text


def test_auth_error_email(mailoutbox):
    err(401, "authentication_error", None, "invalid x-api-key")
    text = body_of(mailoutbox)
    assert "401" in text and "authentication_error" in text and "invalid x-api-key" in text
    assert ".env" in text and "api key" in text.lower()
    assert "reset_breaker" in text
    assert "12:00" in text and "UTC" in text


def test_manual_email(mailoutbox):
    from moderation import breaker

    breaker.trip("manual", "stopped by hand")
    text = body_of(mailoutbox)
    assert "manual" in text.lower()
    assert "reset_breaker" in text
    assert "12:00" in text and "UTC" in text


def test_soft_email_says_it_retries_by_itself_and_when(mailoutbox):
    soft_trip(message="Overloaded, try later", status=529, error_type="overloaded_error")
    text = body_of(mailoutbox)
    lowered = text.lower()
    assert "529" in text and "overloaded_error" in text and "Overloaded, try later" in text
    assert "retr" in lowered  # it retries by itself
    assert "12:05" in text  # the next attempt, cooldown_until, in UTC
    assert "12:00" in text and "UTC" in text
    assert "posting" in lowered
    assert "reset_breaker" not in text  # nothing to do by hand


def test_emails_are_plain_text(mailoutbox):
    hard_spend()
    assert not getattr(mailoutbox[0], "alternatives", [])


# --- never any secret or user data ---------------------------------------------------------------------------------


CANARY_SYSTEM = "SYSTEM-PROMPT-CANARY-4412"
CANARY_MESSAGE = "USER-MESSAGE-CANARY-9083"
CANARY_KEY = FAKE_KEY + "-canary-7781"


def assert_clean(mailoutbox):
    assert mailoutbox, "an email was expected"
    for mail in mailoutbox:
        everything = "\n".join([mail.subject, mail.body, *mail.to, mail.from_email, str(mail.extra_headers)])
        for secret in (CANARY_SYSTEM, CANARY_MESSAGE, CANARY_KEY):
            assert secret not in everything, secret


@pytest.mark.parametrize(
    "script",
    [
        lambda: [raiser(sdk_status_error("BadRequestError", 400, "invalid_request_error", SPEND_MSG))],
        lambda: [raiser(sdk_status_error("AuthenticationError", 401, "authentication_error", "invalid x-api-key"))],
        lambda: [raiser(sdk_status_error("InternalServerError", 500, "api_error", "Internal error")) for _ in range(5)],
    ],
    ids=["spend-limit", "auth", "soft"],
)
def test_the_email_never_contains_the_key_the_prompt_or_the_messages(install_fake, mailoutbox, settings, script):
    from moderation.errors import BreakerOpen, LLMAPIError

    settings.ANTHROPIC_API_KEY = CANARY_KEY
    items = script()
    install_fake(*items)
    for _ in items:
        with pytest.raises((LLMAPIError, BreakerOpen)):
            run_call(system=CANARY_SYSTEM, messages=[{"role": "user", "content": CANARY_MESSAGE}], conversation_id=987, run_id=654)
    assert_clean(mailoutbox)
    row = state()
    assert CANARY_MESSAGE not in row.trip_detail and CANARY_SYSTEM not in row.trip_detail and CANARY_KEY not in row.trip_detail


def test_the_email_does_not_contain_conversation_or_user_identifiers(install_fake, mailoutbox):
    from moderation.errors import LLMAPIError

    install_fake(raiser(sdk_status_error("AuthenticationError", 401, "authentication_error", "invalid x-api-key")))
    with pytest.raises(LLMAPIError):
        run_call(conversation_id=987123, run_id=654321)
    text = body_of(mailoutbox)
    assert "987123" not in text and "654321" not in text


# --- sent after the trip is committed ----------------------------------------------------------------------------------


@pytest.fixture
def send_spy(monkeypatch):
    """Wraps the locmem backend's send_messages, recording the database state at the moment of sending."""
    from django.core.mail.backends import locmem
    from django.db import connection

    from moderation.models import GuardState

    seen = []
    original = locmem.EmailBackend.send_messages

    def spy(self, messages):
        row = GuardState.objects.get(pk=1)
        seen.append(
            dict(in_atomic=connection.in_atomic_block, tripped=row.breaker_tripped, kind=row.trip_kind, reason=row.trip_reason)
        )
        return original(self, messages)

    monkeypatch.setattr(locmem.EmailBackend, "send_messages", spy)
    return seen


def test_a_hard_alert_is_sent_after_the_trip_is_committed(send_spy):
    hard_spend()
    assert send_spy == [dict(in_atomic=False, tripped=True, kind="hard", reason="spend_limit")]


def test_a_soft_alert_is_sent_after_the_trip_is_committed(send_spy):
    soft_trip()
    assert send_spy == [dict(in_atomic=False, tripped=True, kind="soft", reason="consecutive_errors")]


def test_a_manual_alert_is_sent_after_the_trip_is_committed(send_spy):
    from moderation import breaker

    breaker.trip("manual")
    assert send_spy == [dict(in_atomic=False, tripped=True, kind="hard", reason="manual")]


# --- a failing mailer never hurts the caller ------------------------------------------------------------------------


@pytest.fixture
def broken_mailer(monkeypatch):
    from django.core.mail.backends import locmem

    original = locmem.EmailBackend.send_messages
    state_ = {"broken": True, "attempts": 0}

    def maybe_fail(self, messages):
        state_["attempts"] += 1
        if state_["broken"]:
            raise ConnectionRefusedError("smtp exploded: canary-mailer-error")
        return original(self, messages)

    monkeypatch.setattr(locmem.EmailBackend, "send_messages", maybe_fail)
    return state_


def test_a_failing_mailer_never_raises_into_the_caller_and_the_trip_still_happens(broken_mailer, mailoutbox, caplog):
    from moderation import breaker

    caplog.set_level(logging.DEBUG)
    hard_spend()  # must not raise
    assert broken_mailer["attempts"] == 1
    assert breaker.is_open() is True
    row = state()
    assert (row.trip_kind, row.trip_reason) == ("hard", "spend_limit")
    assert "canary-mailer-error" in row.last_alert_error
    assert row.last_alert_at is None  # only set when the email was actually sent
    assert mailoutbox == []
    logged = "\n".join(r.getMessage() + str(r.exc_info) for r in caplog.records if r.levelno >= logging.WARNING)
    assert "canary-mailer-error" in logged or "alert" in logged.lower()


def test_a_failing_mailer_does_not_break_the_gateway_call(broken_mailer, install_fake):
    from moderation.errors import BreakerOpen, LLMAPIError

    install_fake(raiser(sdk_status_error("AuthenticationError", 401, "authentication_error", "invalid x-api-key")), reply())
    with pytest.raises(LLMAPIError):  # the provider error, not the mailer's
        run_call()
    with pytest.raises(BreakerOpen):
        run_call()


def test_a_failing_mailer_leaves_an_earlier_last_alert_at_unchanged(broken_mailer, clock):
    from moderation import breaker

    broken_mailer["broken"] = False
    hard_spend()
    first = state().last_alert_at
    assert first == clock.now()
    breaker.reset()
    clock.advance(seconds=10)
    broken_mailer["broken"] = True
    err(401, "authentication_error", None, "invalid x-api-key")
    row = state()
    assert row.last_alert_at == first
    assert "canary-mailer-error" in row.last_alert_error


def test_a_later_successful_send_clears_the_error_and_sets_the_time(broken_mailer, mailoutbox, clock):
    from moderation import breaker

    soft_trip()
    assert "canary-mailer-error" in state().last_alert_error
    assert state().last_alert_at is None
    broken_mailer["broken"] = False
    clock.set(state().cooldown_until)
    breaker.check()
    err()  # a failed probe re-opens soft; no successful alert yet, so it is not rate limited
    row = state()
    assert len(mailoutbox) == 1
    assert row.last_alert_error == ""
    assert row.last_alert_at == clock.now()


def test_a_failing_mailer_is_retried_on_the_next_transition_not_rate_limited(broken_mailer, mailoutbox, clock):
    """A failed attempt must not consume the rate-limit window."""
    from moderation import breaker

    soft_trip()
    broken_mailer["broken"] = False
    clock.set(state().cooldown_until)
    breaker.check()
    err()
    assert len(mailoutbox) == 1


def test_the_email_contains_no_conversation_or_pipeline_wording(mailoutbox):
    """Whitelist by absence: nothing about conversations, transcripts or runs may creep into an alert."""
    hard_spend()
    soft_trip()
    for mail in mailoutbox:
        text = (mail.subject + mail.body).lower()
        for word in ("conversation", "transcript", "run_id", "participant", "username"):
            assert word not in text, word
