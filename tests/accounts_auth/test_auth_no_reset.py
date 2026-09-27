"""Step 6c: password reset by email is gone. No views, URL names, templates or emails; the owner resets with changepassword."""
from unittest import mock

import pytest
from auth_testkit import NEW_PASSWORD, PASSWORD, alice, clean_axes, is_logged_in, login_post, old_account
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.test import Client
from django.urls import NoReverseMatch, reverse

pytestmark = pytest.mark.django_db

OLD_PATHS = [
    "/accounts/password-reset/",
    "/accounts/password-reset/sent/",
    "/accounts/reset/complete/",
    "/accounts/reset/MQ/abc-0123456789abcdef0123/",
    "/accounts/reset/zz/not-a-token/",
]
REMOVED_TEMPLATES = [
    "accounts/password_reset_form.html",
    "accounts/password_reset_done.html",
    "accounts/password_reset_confirm.html",
    "accounts/password_reset_complete.html",
    "accounts/password_reset_email.txt",
    "accounts/password_reset_subject.txt",
]
REMOVED_VIEW_NAMES = [
    "ResetForm",
    "PasswordResetView",
    "PasswordResetDoneView",
    "PasswordResetConfirmView",
    "PasswordResetCompleteView",
    "UNCONFIRMED",
    "_resend_url",
]


@pytest.mark.parametrize("name", ["password_reset", "password_reset_done", "password_reset_complete"])
def test_the_reset_url_names_are_gone(name):
    with pytest.raises(NoReverseMatch):
        reverse(f"accounts:{name}")


def test_the_reset_confirm_url_name_is_gone():
    with pytest.raises(NoReverseMatch):
        reverse("accounts:password_reset_confirm", args=["MQ", "abc-0123456789abcdef0123"])


@pytest.mark.parametrize("path", OLD_PATHS)
def test_an_old_reset_path_is_a_normal_404_for_a_visitor(client, alice, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", OLD_PATHS)
def test_an_old_reset_path_is_a_normal_404_for_a_logged_in_member(client, alice, path):
    login_post(client, "alice", PASSWORD)
    assert is_logged_in(client)
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", OLD_PATHS)
def test_posting_to_an_old_reset_path_is_a_404_and_sends_nothing(client, old_account, mailoutbox, path):
    response = client.post(path, {"email": "olivia@example.com"})
    assert response.status_code == 404
    assert mailoutbox == []


@pytest.mark.parametrize("path", OLD_PATHS)
def test_a_csrf_checking_client_also_gets_a_404_not_a_403(old_account, path):
    response = Client(enforce_csrf_checks=True).post(path, {"email": "olivia@example.com"})
    assert response.status_code == 404


@pytest.mark.parametrize("name", REMOVED_TEMPLATES)
def test_the_reset_templates_are_deleted(name):
    with pytest.raises(TemplateDoesNotExist):
        get_template(name)


@pytest.mark.parametrize("name", REMOVED_VIEW_NAMES)
def test_the_reset_and_unconfirmed_code_is_gone_from_authviews(name):
    from accounts import authviews

    assert not hasattr(authviews, name)


def test_the_login_form_has_no_unconfirmed_state():
    from accounts.authviews import LoginForm

    assert not hasattr(LoginForm, "unconfirmed")
    assert "confirm" not in str(LoginForm.error_messages).lower()
    assert "email" not in str(LoginForm.error_messages).lower()


def test_no_login_or_change_request_sends_any_email(client, alice, mailoutbox):
    login_post(client, "alice", PASSWORD)
    client.post(
        reverse("accounts:password_change"),
        {"old_password": PASSWORD, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
    )
    client.post(reverse("accounts:logout"))
    assert mailoutbox == []


def test_the_owner_can_reset_a_forgotten_password_with_changepassword(alice):
    getpass = "django.contrib.auth.management.commands.changepassword.getpass.getpass"
    with mock.patch(getpass, return_value=NEW_PASSWORD):
        call_command("changepassword", "alice", verbosity=0)
    old_way, new_way = Client(), Client()
    login_post(old_way, "alice", PASSWORD)
    login_post(new_way, "alice", NEW_PASSWORD)
    assert not is_logged_in(old_way)
    assert is_logged_in(new_way)


def test_changepassword_works_for_an_account_that_has_no_email(alice):
    getpass = "django.contrib.auth.management.commands.changepassword.getpass.getpass"
    assert get_user_model().objects.get(username="alice").email == ""
    with mock.patch(getpass, return_value=NEW_PASSWORD):
        call_command("changepassword", "alice", verbosity=0)
    assert get_user_model().objects.get(username="alice").check_password(NEW_PASSWORD)
