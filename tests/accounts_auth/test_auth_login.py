"""Login (step 6b, adapted in 6c): success, the one generic failure message, deactivated and old accounts, safe `next`, page shell."""
import re

import pytest
from accounts.authviews import LoginForm
from auth_testkit import (
    GENERIC_LOGIN_ERROR,
    INACTIVE_AWARE_BACKEND,
    PASSWORD,
    WRONG_PASSWORD,
    alice,
    clean_axes,
    is_logged_in,
    login_post,
    make_user,
    normalized,
    old_account,
    switched_off,
    template_names,
    text,
)
from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS
from django.test import override_settings
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


def test_wrong_password_on_a_deactivated_account_looks_like_an_unknown_user(client, switched_off):
    response = login_post(client, "dormant_dan", WRONG_PASSWORD)
    unknown = login_post(client, "ghost_user", WRONG_PASSWORD)
    assert GENERIC_LOGIN_ERROR in text(response)
    assert response.status_code == unknown.status_code
    assert normalized(response, "dormant_dan") == normalized(unknown, "ghost_user")


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


def test_a_deactivated_account_with_the_correct_password_gets_the_generic_message_and_no_session(client, switched_off):
    """There is no 'unconfirmed' state any more: an inactive account is refused exactly like a wrong password."""
    response = login_post(client, "dormant_dan", PASSWORD)
    unknown = login_post(client, "ghost_user", PASSWORD)
    assert response.status_code == 200
    assert GENERIC_LOGIN_ERROR in text(response)
    assert not is_logged_in(client)
    assert normalized(response, "dormant_dan") == normalized(unknown, "ghost_user")


def test_activating_a_deactivated_account_lets_the_same_credentials_in(client, switched_off):
    login_post(client, "dormant_dan", PASSWORD)
    assert not is_logged_in(client)
    switched_off.is_active = True
    switched_off.save(update_fields=["is_active"])
    response = login_post(client, "dormant_dan", PASSWORD)
    assert response.status_code == 302 and is_logged_in(client)


def test_an_account_with_no_email_logs_in(client, alice):
    assert alice.email == ""
    response = login_post(client, "alice", PASSWORD)
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(alice.pk)


def test_an_old_account_that_has_an_email_still_logs_in(client, old_account):
    assert old_account.email == "olivia@example.com"
    response = login_post(client, "olivia", PASSWORD)
    assert response.status_code == 302
    assert response["Location"] == settings.LOGIN_REDIRECT_URL
    assert client.session["_auth_user_id"] == str(old_account.pk)


def test_an_old_account_cannot_log_in_with_its_email_address_as_the_username(client, old_account):
    response = login_post(client, "olivia@example.com", PASSWORD)
    assert GENERIC_LOGIN_ERROR in text(response)
    assert not is_logged_in(client)


def test_any_spelling_of_the_username_logs_in(client, alice):
    response = login_post(client, "ALICE", PASSWORD)
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(alice.pk)


def test_an_already_logged_in_visitor_is_sent_on_from_the_login_page(client, alice):
    login_post(client, "alice", PASSWORD)
    response = client.get(reverse(LOGIN))
    assert response.status_code == 302
    assert response["Location"] == settings.LOGIN_REDIRECT_URL


def test_get_login_is_safe_and_does_not_log_anyone_in(client, alice):
    response = client.get(reverse(LOGIN), {"username": "alice", "password": PASSWORD})
    assert response.status_code == 200
    assert not is_logged_in(client)


def form_html(response, field_name):
    """The <form> element of the page that holds `field_name` (the site header has its own logout form)."""
    forms = re.findall(r"<form\b.*?</form>", text(response), re.S)
    holding = [f for f in forms if f'name="{field_name}"' in f]
    assert len(holding) == 1
    return holding[0]


def test_the_login_form_itself_carries_a_csrf_token(client, clean_axes):
    assert 'name="csrfmiddlewaretoken"' in form_html(client.get(reverse(LOGIN)), "username")


def test_the_login_form_carries_a_safe_next_along_as_a_hidden_field(client, clean_axes):
    response = client.get(reverse(LOGIN), {"next": "/accounts/password-change/"})
    assert 'name="next" value="/accounts/password-change/"' in form_html(response, "username")


def test_a_failed_login_keeps_the_next_so_the_second_try_still_lands_there(client, alice):
    failed = login_post(client, "alice", WRONG_PASSWORD, next_in_query="/accounts/password-change/")
    assert 'name="next" value="/accounts/password-change/"' in form_html(failed, "username")


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


# --- LoginForm's own guard, isolated from ModelBackend's -------------------------------------------------------------
#
# On every path above, an inactive account never reaches LoginForm.clean()'s own self.confirm_login_allowed() call:
# ModelBackend already refuses to authenticate() an inactive user, so that call is dead code on every tested path.
# This swaps in a backend that skips that check and hands back the inactive user anyway, to prove LoginForm enforces
# the rule itself rather than merely relying on the backend having already filtered it out.


class TestLoginFormOwnInactiveUserGuard:

    @override_settings(AUTHENTICATION_BACKENDS=[INACTIVE_AWARE_BACKEND])
    def test_clean_raises_its_own_inactive_error_when_the_backend_returns_an_inactive_user(self, rf, switched_off):
        request = rf.post(reverse(LOGIN), {"username": "dormant_dan", "password": PASSWORD})
        form = LoginForm(request, data={"username": "dormant_dan", "password": PASSWORD})

        is_valid = form.is_valid()

        assert is_valid is False
        assert form.user_cache is not None
        assert form.user_cache.is_active is False
        assert form.has_error(NON_FIELD_ERRORS, code="inactive")
