"""Plan section 4: every refusal says why, in plain words, and what to do next; no internal text reaches the page."""
import pytest
from auth_testkit import (
    CONFIRM_FIRST,
    GENERIC_LOGIN_ERROR,
    LOCKED_PREFIX,
    PASSWORD,
    WRONG_PASSWORD,
    alice,
    clean_axes,
    fail_logins,
    links_to,
    lockout_wait_minutes,
    login_post,
    pending,
    text,
)
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db

INTERNAL_WORDS = ("traceback", "axes", "argon2", "exception", "accessattempt", "stack", "django.", "errno", "sqlite")


def assert_plain(response):
    page = text(response).lower()
    for word in INTERNAL_WORDS:
        assert word not in page, f"internal text {word!r} reached the page"


def test_wrong_credentials_name_the_reason_and_offer_a_retry_and_a_reset(client, alice):
    response = login_post(client, "alice", WRONG_PASSWORD)
    assert GENERIC_LOGIN_ERROR in text(response)
    assert 'name="password"' in text(response), "the form is shown again so the person can retry"
    assert links_to(response, "accounts:password_reset"), "next step for a forgotten password"
    assert_plain(response)


def test_unconfirmed_account_says_why_and_how_to_get_a_new_link(client, pending):
    response = login_post(client, "pending_pat", PASSWORD)
    assert CONFIRM_FIRST in text(response)
    assert links_to(response, "accounts:resend")
    assert_plain(response)


def test_lockout_says_why_and_when_to_come_back(client, alice):
    fail_logins(client, "alice", settings.LOGIN_MAX_FAILURES)
    response = login_post(client, "alice", PASSWORD, ip="10.0.0.1")
    page = text(response)
    assert LOCKED_PREFIX in page
    assert lockout_wait_minutes(page) is not None, "the message must name how long to wait"
    assert_plain(response)


def test_lockout_page_still_offers_the_reset_link_for_someone_who_forgot(client, alice):
    fail_logins(client, "alice", settings.LOGIN_MAX_FAILURES)
    response = login_post(client, "alice", PASSWORD, ip="10.0.0.1")
    assert links_to(response, "accounts:password_reset")


def test_a_bad_reset_link_says_why_and_offers_a_new_request(client, alice):
    response = client.get("/accounts/reset/zz/not-a-token/", follow=True)
    assert links_to(response, "accounts:password_reset")
    assert_plain(response)


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
