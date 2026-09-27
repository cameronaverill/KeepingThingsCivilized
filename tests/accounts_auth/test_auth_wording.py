"""Step 6c: the login-side pages say nothing about confirming or resending email, forgotten-password links, or costs."""
import re

import pytest
from auth_testkit import (
    PASSWORD,
    WRONG_PASSWORD,
    alice,
    clean_axes,
    fail_logins,
    force_login,
    hrefs,
    login_post,
    text,
)
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db

# Lower-case fragments that belong to the removed flows or to costs. "confirmation" is left out of the change pages
# because Django labels the second new-password box "New password confirmation".
FLOW_WORDS = ("email", "e-mail", "confirm", "resend", "spam", "forgot", "forgotten", "reset", "new link")
COST_WORDS = ("cost", "budget", "spend", "price")
REMOVED_PATH_PARTS = ("password-reset", "/reset/", "/resend", "/confirm", "check-email", "check_email")


def anonymous_pages(client):
    return {
        "login": client.get(reverse("accounts:login")),
        "login with next": client.get(reverse("accounts:login"), {"next": "/accounts/password-change/"}),
        "wrong password": login_post(client, "alice", WRONG_PASSWORD, ip="10.4.4.4"),
        "unknown user": login_post(client, "ghost_user", WRONG_PASSWORD, ip="10.4.4.5"),
        "blank fields": client.post(reverse("accounts:login"), {}),
    }


def test_no_login_page_carries_email_or_reset_wording(client, alice):
    for label, response in anonymous_pages(client).items():
        page = text(response).lower()
        for word in FLOW_WORDS + COST_WORDS:
            assert word not in page, f"{word!r} found on the {label} page"


def test_no_login_page_links_to_a_removed_page(client, alice):
    for label, response in anonymous_pages(client).items():
        for href in hrefs(response):
            for part in REMOVED_PATH_PARTS:
                assert part not in href, f"{href!r} on the {label} page points at a removed page"


def test_the_login_page_has_no_forgot_password_link(client, clean_axes):
    page = text(client.get(reverse("accounts:login")))
    assert not re.search(r"forgot", page, re.I)
    assert "Forgot your password?" not in page


def test_the_login_page_still_has_the_form_and_the_button(client, clean_axes):
    page = text(client.get(reverse("accounts:login")))
    assert "<h1>Log in</h1>" in page
    assert 'name="username"' in page and 'name="password"' in page
    assert ">Log in</button>" in page


def test_the_lockout_page_has_no_email_or_reset_wording(client, alice):
    fail_logins(client, "alice", settings.LOGIN_MAX_FAILURES)
    response = login_post(client, "alice", PASSWORD, ip="10.0.0.1")
    page = text(response).lower()
    assert response.status_code in (200, 403, 429)
    assert "too many failed attempts." in page
    for word in FLOW_WORDS + COST_WORDS:
        assert word not in page, f"{word!r} found on the lockout page"
    for href in hrefs(response):
        for part in REMOVED_PATH_PARTS:
            assert part not in href


def test_the_logout_page_has_no_email_or_cost_wording(client, alice):
    force_login(client, alice)
    response = client.get(reverse("accounts:logout"))
    page = text(response).lower()
    assert response.status_code == 405
    for word in FLOW_WORDS + COST_WORDS:
        assert word not in page, f"{word!r} found on the logout page"


def test_the_password_change_pages_have_no_email_reset_or_cost_wording(client, alice):
    force_login(client, alice)
    pages = [client.get(reverse("accounts:password_change")), client.get(reverse("accounts:password_change_done"))]
    for response in pages:
        page = text(response).lower()
        for word in ("email", "e-mail", "resend", "spam", "forgot", "forgotten", "reset", "new link") + COST_WORDS:
            assert word not in page, f"{word!r} found on {response.request['PATH_INFO']}"
        for href in hrefs(response):
            for part in REMOVED_PATH_PARTS:
                assert part not in href


def test_the_how_it_works_page_no_longer_talks_about_email_confirmation_or_reset(client, alice):
    """The privacy paragraph used to say the email address is used for confirmation and reset; that is now untrue."""
    page = text(client.get(reverse("forum:how_it_works"))).lower()
    for word in ("email", "e-mail", "confirmation", "password reset"):
        assert word not in page, f"{word!r} is still on the how-it-works page"
