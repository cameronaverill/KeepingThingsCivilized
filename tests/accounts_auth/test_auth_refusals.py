"""Plan section 4: every refusal says why, in plain words, and what to do next; no internal text reaches the page.

Since step 6c there is no password reset by email, so no refusal points at a reset, a resend or an email."""
import pytest
from auth_testkit import (
    GENERIC_LOGIN_ERROR,
    LOCKED_PREFIX,
    PASSWORD,
    WRONG_PASSWORD,
    alice,
    clean_axes,
    fail_logins,
    lockout_wait_minutes,
    login_post,
    switched_off,
    text,
)
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db

# Words that would point at the removed email flows (confirmation, resend, reset by email) or at costs.
BANNED_WORDS = (
    "email",
    "e-mail",
    "confirm",
    "resend",
    "spam",
    "forgot",
    "reset",
    "new link",
    "cost",
    "budget",
    "spend",
)
INTERNAL_WORDS = ("traceback", "axes", "argon2", "exception", "accessattempt", "stack", "django.", "errno", "sqlite")


def assert_plain(response):
    page = text(response).lower()
    for word in INTERNAL_WORDS:
        assert word not in page, f"internal text {word!r} reached the page"


def assert_no_email_wording(response):
    page = text(response).lower()
    for word in BANNED_WORDS:
        assert word not in page, f"{word!r} must not appear on this page"


def test_wrong_credentials_name_the_reason_and_offer_a_retry(client, alice):
    response = login_post(client, "alice", WRONG_PASSWORD)
    assert GENERIC_LOGIN_ERROR in text(response)
    assert 'name="password"' in text(response), "the form is shown again so the person can retry"
    assert_plain(response)
    assert_no_email_wording(response)


def test_a_deactivated_account_is_refused_with_the_generic_reason_only(client, switched_off):
    response = login_post(client, "dormant_dan", PASSWORD)
    assert GENERIC_LOGIN_ERROR in text(response)
    assert 'name="password"' in text(response)
    assert_plain(response)
    assert_no_email_wording(response)


def test_lockout_says_why_and_when_to_come_back(client, alice):
    fail_logins(client, "alice", settings.LOGIN_MAX_FAILURES)
    response = login_post(client, "alice", PASSWORD, ip="10.0.0.1")
    page = text(response)
    assert LOCKED_PREFIX in page
    assert lockout_wait_minutes(page) is not None, "the message must name how long to wait"
    assert_plain(response)


def test_the_lockout_page_does_not_point_at_a_reset_or_an_email(client, alice):
    fail_logins(client, "alice", settings.LOGIN_MAX_FAILURES)
    response = login_post(client, "alice", PASSWORD, ip="10.0.0.1")
    assert LOCKED_PREFIX in text(response)
    assert_no_email_wording(response)


def test_password_change_refusals_keep_the_form_for_another_try(client, alice):
    from auth_testkit import force_login

    force_login(client, alice)
    response = client.post(
        reverse("accounts:password_change"),
        {"old_password": WRONG_PASSWORD, "new_password1": "x", "new_password2": "y"},
    )
    page = text(response)
    assert 'name="old_password"' in page and 'name="new_password1"' in page
    assert_plain(response)
    assert "email" not in page.lower()
    assert "reset" not in page.lower()
