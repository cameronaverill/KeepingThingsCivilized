"""Login (step 6b): success, the one generic failure message, unconfirmed accounts, safe `next`, page shell."""
import pytest
from auth_testkit import (
    CONFIRM_FIRST,
    GENERIC_LOGIN_ERROR,
    PASSWORD,
    WRONG_PASSWORD,
    alice,
    clean_axes,
    is_logged_in,
    links_to,
    login_post,
    make_user,
    normalized,
    pending,
    template_names,
    text,
)
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db
LOGIN = "accounts:login"


def test_login_page_renders_a_form_on_the_accounts_shell(client, clean_axes):
    response = client.get(reverse(LOGIN))
    page = text(response)
    assert response.status_code == 200
    assert "accounts/base.html" in template_names(response)
    assert 'name="username"' in page and 'name="password"' in page
    assert "csrfmiddlewaretoken" in page


def test_correct_credentials_log_in_and_redirect_to_login_redirect_url(client, alice):
    response = login_post(client, "alice", PASSWORD)
    assert response.status_code == 302
    assert response["Location"] == settings.LOGIN_REDIRECT_URL
    assert client.session["_auth_user_id"] == str(alice.pk)


def test_wrong_password_gives_the_generic_message_and_no_session(client, alice):
    response = login_post(client, "alice", WRONG_PASSWORD)
    assert response.status_code == 200
    assert GENERIC_LOGIN_ERROR in text(response)
    assert not is_logged_in(client)


def test_unknown_username_gives_the_generic_message_and_no_session(client, alice):
    response = login_post(client, "ghost_user", WRONG_PASSWORD)
    assert response.status_code == 200
    assert GENERIC_LOGIN_ERROR in text(response)
    assert not is_logged_in(client)


def test_unknown_user_and_wrong_password_pages_are_identical(client, alice):
    known = login_post(client, "alice", WRONG_PASSWORD)
    unknown = login_post(client, "ghost_user", WRONG_PASSWORD)
    assert known.status_code == unknown.status_code
    assert normalized(known, "alice") == normalized(unknown, "ghost_user")


def test_unknown_user_with_the_right_looking_password_of_another_account_is_still_generic(client, alice):
    response = login_post(client, "ghost_user", PASSWORD)
    assert GENERIC_LOGIN_ERROR in text(response)
    assert not is_logged_in(client)


def test_wrong_password_on_an_unconfirmed_account_does_not_reveal_that_it_is_unconfirmed(client, pending):
    response = login_post(client, "pending_pat", WRONG_PASSWORD)
    page = text(response)
    assert GENERIC_LOGIN_ERROR in page
    assert CONFIRM_FIRST not in page
    unknown = login_post(client, "ghost_user", WRONG_PASSWORD)
    assert normalized(response, "pending_pat") == normalized(unknown, "ghost_user")


def test_failed_login_keeps_the_typed_username_but_never_the_password(client, alice):
    response = login_post(client, "alice", WRONG_PASSWORD)
    page = text(response)
    assert 'value="alice"' in page
    assert WRONG_PASSWORD not in page
    assert PASSWORD not in page


@pytest.mark.parametrize("data", [{"username": "", "password": "x"}, {"username": "alice", "password": ""}, {}])
def test_blank_fields_are_refused_with_a_reason_and_the_form_again(client, alice, data):
    response = client.post(reverse(LOGIN), data)
    assert response.status_code == 200
    assert "required" in text(response).lower()
    assert not is_logged_in(client)


def test_unconfirmed_account_with_the_correct_password_is_told_to_confirm_and_gets_the_resend_link(client, pending):
    response = login_post(client, "pending_pat", PASSWORD)
    page = text(response)
    assert response.status_code == 200
    assert CONFIRM_FIRST in page
    assert links_to(response, "accounts:resend")
    assert GENERIC_LOGIN_ERROR not in page
    assert not is_logged_in(client)


def test_a_confirmed_flag_alone_is_not_enough_inactive_means_no_login(client, clean_axes):
    user = make_user("flagged", active=False)
    assert user.email_verified_at is None
    login_post(client, "flagged", PASSWORD)
    assert not is_logged_in(client)


def test_activating_the_account_lets_the_same_credentials_in(client, pending):
    login_post(client, "pending_pat", PASSWORD)
    assert not is_logged_in(client)
    pending.is_active = True
    pending.save(update_fields=["is_active"])
    response = login_post(client, "pending_pat", PASSWORD)
    assert response.status_code == 302 and is_logged_in(client)


def test_get_login_is_safe_and_does_not_log_anyone_in(client, alice):
    response = client.get(reverse(LOGIN), {"username": "alice", "password": PASSWORD})
    assert response.status_code == 200
    assert not is_logged_in(client)


# --- ?next= ----------------------------------------------------------------------------------------------------------

SAFE_NEXT = [
    "/accounts/password-change/",
    "/accounts/password-change/?x=1",
    "http://testserver/accounts/password-change/",
]
UNSAFE_NEXT = [
    "https://evil.example/steal",
    "http://evil.example",
    "//evil.example/x",
    "///evil.example/x",
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "\\\\evil.example",
    "/\\evil.example",
    "http://testserver.evil.example/",
    "https:evil.example",
    "data:text/html,hello",
    "http://evil.example\\@testserver/",
]


@pytest.mark.parametrize("target", SAFE_NEXT)
@pytest.mark.parametrize("via", ["query", "post"])
def test_a_same_host_next_is_honoured(client, alice, target, via):
    kwargs = {"next_in_query": target} if via == "query" else {"next_in_post": target}
    response = login_post(client, "alice", PASSWORD, **kwargs)
    assert response.status_code == 302
    assert response["Location"] == target
    assert is_logged_in(client)


@pytest.mark.parametrize("target", UNSAFE_NEXT)
@pytest.mark.parametrize("via", ["query", "post"])
def test_an_unsafe_next_is_ignored_and_the_user_lands_on_the_default_page(client, alice, target, via):
    kwargs = {"next_in_query": target} if via == "query" else {"next_in_post": target}
    response = login_post(client, "alice", PASSWORD, **kwargs)
    assert response.status_code == 302
    assert response["Location"] == settings.LOGIN_REDIRECT_URL
    assert is_logged_in(client)


@pytest.mark.parametrize("target", ["javascript:alert(1)", "https://evil.example/steal", "//evil.example/x"])
def test_the_login_page_never_echoes_an_unsafe_next_into_the_form(client, clean_axes, target):
    response = client.get(reverse(LOGIN), {"next": target})
    page = text(response)
    assert "javascript:alert" not in page
    assert "evil.example" not in page


def test_a_failed_login_with_next_does_not_redirect_anywhere(client, alice):
    response = login_post(client, "alice", WRONG_PASSWORD, next_in_query="/accounts/password-change/")
    assert response.status_code == 200
    assert not is_logged_in(client)
