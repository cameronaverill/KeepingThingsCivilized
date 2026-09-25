"""Password change (login required, old password required, validators, the session stays valid) and LOGIN_URL redirects."""
import pytest
from auth_testkit import (
    NEW_PASSWORD,
    PASSWORD,
    WRONG_PASSWORD,
    alice,
    clean_axes,
    force_login,
    is_logged_in,
    login_post,
    template_names,
    text,
)
from django.conf import settings
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import Client, override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db
CHANGE = "accounts:password_change"
DONE = "accounts:password_change_done"


def change(client, old=PASSWORD, new=NEW_PASSWORD, confirm=None):
    return client.post(
        reverse(CHANGE),
        {"old_password": old, "new_password1": new, "new_password2": new if confirm is None else confirm},
    )


@pytest.fixture
def member(client, alice):
    force_login(client, alice)
    return alice


def unchanged(user):
    user.refresh_from_db()
    return user.check_password(PASSWORD)


# --- login required -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [CHANGE, DONE])
def test_anonymous_visitors_are_sent_to_login_with_a_way_back(client, clean_axes, name):
    response = client.get(reverse(name))
    assert response.status_code == 302
    assert response["Location"] == f"{reverse('accounts:login')}?next={reverse(name)}"


def test_an_anonymous_post_changes_nothing(client, alice):
    response = change(client)
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:login"))
    assert unchanged(alice)


def test_login_url_redirect_for_a_protected_view_and_back_again(clean_axes, alice):
    """A throwaway page behind @login_required, served from a test-only URL table."""
    with override_settings(ROOT_URLCONF="auth_test_urls"):
        client = Client()
        response = client.get("/secret/")
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('accounts:login')}?next=/secret/"
        landed = login_post(client, "alice", PASSWORD, next_in_query="/secret/")
        assert landed.status_code == 302 and landed["Location"] == "/secret/"
        assert client.get("/secret/").content == b"the secret page"


def test_login_url_setting_is_the_named_login_route(clean_axes):
    assert reverse(settings.LOGIN_URL) == "/accounts/login/"


# --- the form -----------------------------------------------------------------------------------------------------------


def test_the_form_page_renders_on_the_accounts_shell(client, member):
    response = client.get(reverse(CHANGE))
    page = text(response)
    assert response.status_code == 200
    assert "accounts/base.html" in template_names(response)
    for field in ("old_password", "new_password1", "new_password2"):
        assert f'name="{field}"' in page
    assert "csrfmiddlewaretoken" in page


def test_a_correct_change_stores_the_new_password_and_shows_the_done_page(client, member):
    response = change(client)
    assert response.status_code == 302
    assert response["Location"] == reverse(DONE)
    member.refresh_from_db()
    assert member.check_password(NEW_PASSWORD)
    assert not member.check_password(PASSWORD)
    assert member.password.startswith("argon2$")
    page = client.get(reverse(DONE))
    assert page.status_code == 200
    assert "accounts/base.html" in template_names(page)
    assert "changed" in text(page).lower()


def test_the_session_stays_logged_in_after_the_change(client, member):
    change(client)
    assert is_logged_in(client)
    assert client.get(reverse(CHANGE)).status_code == 200
    assert client.get(reverse(DONE)).status_code == 200


def test_after_the_change_only_the_new_password_logs_in(client, member):
    change(client)
    fresh = Client()
    login_post(fresh, "alice", PASSWORD)
    assert not is_logged_in(fresh)
    login_post(fresh, "alice", NEW_PASSWORD)
    assert is_logged_in(fresh)


def test_the_other_devices_sessions_end_when_the_password_changes(client, member):
    other = Client()
    force_login(other, member)
    assert other.get(reverse(CHANGE)).status_code == 200
    change(client)
    assert other.get(reverse(CHANGE)).status_code == 302


# --- refusals -----------------------------------------------------------------------------------------------------------


def test_the_old_password_is_required_and_a_wrong_one_is_refused_with_a_reason(client, member):
    response = change(client, old=WRONG_PASSWORD)
    assert response.status_code == 200
    assert "old password" in text(response).lower()
    assert unchanged(member)
    assert is_logged_in(client)


def test_a_blank_old_password_is_refused(client, member):
    response = client.post(reverse(CHANGE), {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
    assert response.status_code == 200
    assert "required" in text(response).lower()
    assert unchanged(member)


def test_mismatched_new_passwords_are_refused_with_a_reason(client, member):
    response = change(client, confirm=NEW_PASSWORD + "x")
    assert response.status_code == 200
    assert str(SetPasswordForm.error_messages["password_mismatch"]) in text(response)
    assert unchanged(member)


@pytest.mark.parametrize(
    "bad",
    ["short" + "1", "qwerty" + "123456", "48151623" * 3, "alice@" + "example.com"],
    ids=["too-short", "too-common", "all-digits", "similar-to-email"],
)
def test_the_password_validators_apply(client, member, bad):
    try:
        validate_password(bad, member)
        expected = []
    except ValidationError as error:
        expected = list(error.messages)
    assert expected, "the chosen password must trip a validator, or this test proves nothing"
    response = change(client, new=bad)
    page = text(response)
    assert response.status_code == 200
    for message in expected:
        assert message in page
    assert bad not in page
    assert unchanged(member)


def test_passwords_typed_into_the_form_are_never_shown_back(client, member):
    page = text(change(client, old=WRONG_PASSWORD, new=NEW_PASSWORD, confirm=NEW_PASSWORD + "x"))
    for secret in (WRONG_PASSWORD, NEW_PASSWORD, NEW_PASSWORD + "x"):
        assert secret not in page


def test_a_failed_change_does_not_count_toward_the_login_lockout(client, member):
    for _ in range(settings.LOGIN_MAX_FAILURES + 2):
        change(client, old=WRONG_PASSWORD)
    fresh = Client()
    assert login_post(fresh, "alice", PASSWORD).status_code == 302
