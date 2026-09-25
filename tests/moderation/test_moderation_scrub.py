"""moderation/scrub.py: secrets are redacted from provider text before it is stored or emailed. All fake secrets are built
at runtime, so no file in the repo contains a real-looking key."""
import logging
import time

import pytest
from moderation_testkit import FAKE_KEY, raiser, run_call, sdk_status_error

REDACTED = "[redacted]"
KEY = FAKE_KEY + "-scrub-canary-0451"
SK_PREFIX = "sk-" + "ant-"


@pytest.fixture
def key(settings):
    settings.ANTHROPIC_API_KEY = KEY
    return KEY


def scrub(text):
    from moderation.scrub import scrub as _scrub

    return _scrub(text)


# --- (a) the exact configured key ------------------------------------------------------------------------------------


def test_the_configured_key_is_redacted_wherever_it_appears(key):
    out = scrub(f"bad credentials {KEY} for this request")
    assert KEY not in out and REDACTED in out
    assert out.startswith("bad credentials ") and out.endswith(" for this request")


def test_every_occurrence_of_the_key_is_redacted(key):
    out = scrub(f"{KEY} and {KEY}, then {KEY}")
    assert KEY not in out
    assert out.count(REDACTED) == 3


def test_the_key_is_redacted_even_when_it_looks_like_nothing_special(settings):
    odd = "plain.word+with*regex(chars)[x]"
    settings.ANTHROPIC_API_KEY = odd
    out = scrub(f"prefix {odd} suffix")
    assert odd not in out and REDACTED in out


def test_a_unicode_key_and_unicode_text(settings):
    unicode_key = "über-日本-\U0001f511-canary"
    settings.ANTHROPIC_API_KEY = unicode_key
    out = scrub(f"日本語 {unicode_key} \U0001f600")
    assert unicode_key not in out
    assert out.startswith("日本語 ") and out.endswith(" \U0001f600")


def test_an_empty_configured_key_changes_nothing(settings):
    settings.ANTHROPIC_API_KEY = ""
    text = "Overloaded, please retry later"
    assert scrub(text) == text


def test_the_key_is_read_at_call_time(settings):
    settings.ANTHROPIC_API_KEY = "first" + KEY
    assert "first" + KEY not in scrub("x first" + KEY)
    settings.ANTHROPIC_API_KEY = "second" + KEY
    assert "second" + KEY not in scrub("x second" + KEY)


# --- (b) sk-ant- style tokens --------------------------------------------------------------------------------------


@pytest.mark.parametrize("tail", ["a" * 10, "AbC123_-xyzQ9876", "0123456789" * 8])
def test_sk_ant_tokens_are_redacted_without_knowing_the_key(settings, tail):
    settings.ANTHROPIC_API_KEY = ""
    token = SK_PREFIX + tail
    out = scrub(f"key {token} rejected")
    assert token not in out and tail not in out
    assert out == f"key {REDACTED} rejected"


def test_a_token_needs_at_least_ten_characters_after_the_prefix(settings):
    settings.ANTHROPIC_API_KEY = ""
    short = SK_PREFIX + "abcdefghi"  # 9 characters: not a token
    assert scrub(f"see {short}") == f"see {short}"
    assert REDACTED in scrub(f"see {SK_PREFIX}abcdefghij")  # 10: a token


def test_several_tokens_are_all_redacted(settings):
    settings.ANTHROPIC_API_KEY = ""
    a, b = SK_PREFIX + "A" * 20, SK_PREFIX + "b_c-d" * 5
    out = scrub(f"{a} then {b}; again {a}")
    assert a not in out and b not in out
    assert out.count(REDACTED) == 3


# --- (c) header-style values -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "x-api-key: SECRETVALUE123",
        "X-API-KEY: SECRETVALUE123",
        "X-Api-Key=SECRETVALUE123",
        "x-api-key : SECRETVALUE123",
        "authorization: SECRETVALUE123",
        "AUTHORIZATION=SECRETVALUE123",
        "Authorization: Bearer SECRETVALUE123",
        "authorization: bearer SECRETVALUE123",
        "proxy-authorization: SECRETVALUE123",
        "Proxy-Authorization: Bearer SECRETVALUE123",
        'request failed (x-api-key: SECRETVALUE123), giving up',
        'headers {"x-api-key": "SECRETVALUE123"}',
    ],
)
def test_header_style_values_are_redacted(settings, text):
    settings.ANTHROPIC_API_KEY = ""
    out = scrub(text)
    assert "SECRETVALUE123" not in out
    assert REDACTED in out


def test_the_header_name_stays_so_the_message_still_makes_sense(settings):
    settings.ANTHROPIC_API_KEY = ""
    out = scrub("invalid x-api-key: SECRETVALUE123")
    assert out.lower().startswith("invalid x-api-key")


def test_several_headers_in_one_text(settings):
    settings.ANTHROPIC_API_KEY = ""
    out = scrub("x-api-key: AAAASECRET1 / Authorization: Bearer BBBBSECRET2 / proxy-authorization=CCCCSECRET3")
    for secret in ("AAAASECRET1", "BBBBSECRET2", "CCCCSECRET3"):
        assert secret not in out
    assert out.count(REDACTED) == 3


# --- ordinary text and odd input ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Overloaded",
        "You have reached your specified API usage limits. You will regain access on 2026-10-01 at 00:00 UTC.",
        "max_tokens: must be greater than 0",
        "messages.0.content: Input should be a valid string",
        "authorization failed for this workspace",  # the word alone, no header syntax
        "the x-api-key header is missing",
        "sk-ant is a prefix, and sk-ant-short is too short",
        "unicode é日本\U0001f600 stays as it is",
        "line one\nline two\ttabbed",
    ],
)
def test_ordinary_text_is_unchanged(settings, text):
    settings.ANTHROPIC_API_KEY = KEY
    assert scrub(text) == text


def test_scrubbing_twice_changes_nothing_more(key):
    once = scrub(f"x-api-key: {KEY} / {SK_PREFIX}{'z' * 15}")
    assert scrub(once) == once


@pytest.mark.parametrize("value", [None, 0, 12345, 3.14, b"bytes x-api-key: SECRETVALUE123", ["a", "b"], {"k": "v"}, object()])
def test_it_never_raises_and_always_returns_a_string(settings, value):
    settings.ANTHROPIC_API_KEY = KEY
    assert isinstance(scrub(value), str)


def test_a_value_whose_str_raises_does_not_raise():
    class Broken:
        def __str__(self):
            raise RuntimeError("no string for you")

    assert isinstance(scrub(Broken()), str)


def test_none_and_empty_give_an_empty_or_redacted_string_not_the_word_none(settings):
    settings.ANTHROPIC_API_KEY = ""
    assert scrub("") == ""
    assert scrub(None) in ("", REDACTED)


def test_it_never_raises_when_settings_are_broken(monkeypatch, settings):
    from django.conf import settings as live

    class Exploding:
        def __getattr__(self, name):
            raise RuntimeError("settings unavailable")

    import moderation.scrub as module

    monkeypatch.setattr("django.conf.settings", Exploding(), raising=False)
    assert isinstance(module.scrub("plain text"), str)


# --- big and hostile inputs finish quickly ---------------------------------------------------------------------------


def _timed(text):
    started = time.monotonic()
    out = scrub(text)
    return out, time.monotonic() - started


@pytest.mark.parametrize(
    "text",
    [
        "a" * 1_000_000,
        "x-api-key: " + "S" * 1_000_000,
        SK_PREFIX + "A" * 1_000_000,
        "authorization" + " " * 1_000_000 + "x",
        "authorization:" + " " * 1_000_000,
        ("x-api-key:" * 100_000),
        (SK_PREFIX * 100_000),
        "proxy-authorization=" + "Bearer " * 100_000,
        "日" * 1_000_000,
    ],
    ids=["1MB-plain", "1MB-header-value", "1MB-token", "spaces-after-name", "spaces-after-colon", "repeated-header",
         "repeated-prefix", "repeated-bearer", "1MB-unicode"],
)
def test_huge_and_pathological_inputs_finish_fast(settings, text):
    settings.ANTHROPIC_API_KEY = KEY
    out, seconds = _timed(text)
    assert seconds < 1.0
    assert isinstance(out, str) and len(out) <= len(text) + 100


def test_a_secret_in_a_huge_text_is_still_removed(settings):
    settings.ANTHROPIC_API_KEY = KEY
    text = "n" * 5000 + f" x-api-key: {KEY} " + "n" * 5000
    out, seconds = _timed(text)
    assert KEY not in out and seconds < 1.0


# --- integration: what is stored, raised and emailed is scrubbed -----------------------------------------------------

ECHO = f"invalid x-api-key: {KEY} (also {SK_PREFIX}{'Q' * 24}; Authorization: Bearer HEADERSECRET77)"
SECRETS = (KEY, SK_PREFIX + "Q" * 24, "HEADERSECRET77")


def assert_no_secret(*texts):
    for text in texts:
        for secret in SECRETS:
            assert secret not in str(text), f"{secret!r} leaked into {text!r}"


def test_a_fake_provider_error_is_scrubbed_in_the_row_the_exception_and_the_breaker(install_fake, key, llm_ready):
    from moderation.errors import LLMAPIError
    from moderation.fake_llm import FakeProviderError
    from moderation.models import GuardState, LLMCall

    install_fake(FakeProviderError(401, "authentication_error", ECHO))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    row = LLMCall.objects.get()
    assert_no_secret(row.error, row.error_code, excinfo.value.message, str(excinfo.value), GuardState.objects.get(pk=1).trip_detail)
    assert REDACTED in row.error
    assert REDACTED in excinfo.value.message
    assert REDACTED in GuardState.objects.get(pk=1).trip_detail
    assert "invalid x-api-key" in row.error.lower()  # the useful part of the message survives


def test_a_real_sdk_error_is_scrubbed_too(install_fake, key, llm_ready):
    from moderation.errors import LLMAPIError
    from moderation.models import GuardState, LLMCall

    install_fake(raiser(sdk_status_error("AuthenticationError", 401, "authentication_error", ECHO)))
    with pytest.raises(LLMAPIError) as excinfo:
        run_call()
    assert_no_secret(LLMCall.objects.get().error, excinfo.value.message, GuardState.objects.get(pk=1).trip_detail)
    assert REDACTED in excinfo.value.message


def test_a_soft_trip_detail_is_scrubbed(install_fake, key, llm_ready):
    from moderation.errors import BreakerOpen, LLMAPIError
    from moderation.fake_llm import FakeProviderError
    from moderation.models import GuardState, LLMCall

    install_fake(*[FakeProviderError(500, "api_error", ECHO) for _ in range(5)])
    for _ in range(5):
        with pytest.raises((LLMAPIError, BreakerOpen)):
            run_call()
    assert_no_secret(GuardState.objects.get(pk=1).trip_detail, *[c.error for c in LLMCall.objects.all()])


@pytest.mark.django_db(transaction=True)
def test_the_alert_email_is_scrubbed(install_fake, key, llm_ready, settings, mailoutbox):
    from moderation.errors import LLMAPIError
    from moderation.fake_llm import FakeProviderError

    settings.ALERT_EMAIL = "alerts@example.test"
    install_fake(FakeProviderError(401, "authentication_error", ECHO))
    with pytest.raises(LLMAPIError):
        run_call()
    assert len(mailoutbox) == 1
    assert_no_secret(mailoutbox[0].subject, mailoutbox[0].body)
    assert REDACTED in mailoutbox[0].body


@pytest.mark.django_db(transaction=True)
def test_last_alert_error_is_scrubbed_when_the_mailer_itself_echoes_the_key(install_fake, key, llm_ready, settings, monkeypatch):
    from django.core.mail.backends import locmem

    from moderation.errors import LLMAPIError
    from moderation.fake_llm import FakeProviderError
    from moderation.models import GuardState

    def failing(self, messages):
        raise ConnectionRefusedError(f"login failed for smtp user with password {KEY} and x-api-key: HEADERSECRET77")

    monkeypatch.setattr(locmem.EmailBackend, "send_messages", failing)
    settings.ALERT_EMAIL = "alerts@example.test"
    install_fake(FakeProviderError(401, "authentication_error", "invalid x-api-key"))
    with pytest.raises(LLMAPIError):
        run_call()
    error = GuardState.objects.get(pk=1).last_alert_error
    assert error, "the mailer failure should be recorded"
    assert_no_secret(error)
    assert "ConnectionRefusedError" in error


def test_the_log_lines_do_not_carry_the_secrets_either(install_fake, key, llm_ready, caplog):
    from moderation.errors import LLMAPIError
    from moderation.fake_llm import FakeProviderError

    caplog.set_level(logging.DEBUG)
    install_fake(FakeProviderError(401, "authentication_error", ECHO))
    with pytest.raises(LLMAPIError):
        run_call()
    assert_no_secret(*[r.getMessage() for r in caplog.records])


def test_the_budget_command_never_prints_the_secrets(install_fake, key, llm_ready):
    from io import StringIO

    from django.core.management import call_command

    from moderation.errors import LLMAPIError
    from moderation.fake_llm import FakeProviderError

    install_fake(FakeProviderError(401, "authentication_error", ECHO))
    with pytest.raises(LLMAPIError):
        run_call()
    out = StringIO()
    call_command("budget", stdout=out)
    assert_no_secret(out.getvalue())
