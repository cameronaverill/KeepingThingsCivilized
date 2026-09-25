"""Every state-changing form on the login side enforces CSRF (a real token is required, a forged request is refused)."""
import pytest
from auth_testkit import (
    NEW_PASSWORD,
    PASSWORD,
    alice,
    clean_axes,
    csrf_token,
    force_login,
    is_logged_in,
    login_post,
    open_reset_link,
    request_reset,
    reset_link,
)
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def strict():
    return Client(enforce_csrf_checks=True)


def test_login_post_needs_a_csrf_token(alice):
    client = strict()
    response = client.post(reverse("accounts:login"), {"username": "alice", "password": PASSWORD})
    assert response.status_code == 403
    assert not is_logged_in(client)


def test_login_post_with_the_token_works(alice):
    client = strict()
    client.get(reverse("accounts:login"))
    response = client.post(
        reverse("accounts:login"),
        {"username": "alice", "password": PASSWORD, "csrfmiddlewaretoken": csrf_token(client)},
    )
    assert response.status_code == 302
    assert is_logged_in(client)


def test_a_csrf_refusal_does_not_count_as_a_failed_login(alice):
    from django.conf import settings

    client = strict()
    for _ in range(settings.LOGIN_MAX_FAILURES + 2):
        client.post(reverse("accounts:login"), {"username": "alice", "password": PASSWORD})
    ok = Client()
    assert login_post(ok, "alice", PASSWORD).status_code == 302


def test_password_reset_request_needs_a_csrf_token(alice, mailoutbox):
    response = strict().post(reverse("accounts:password_reset"), {"email": "alice@example.com"})
    assert response.status_code == 403
    assert mailoutbox == []


def test_password_reset_request_with_the_token_works(alice, mailoutbox):
    client = strict()
    client.get(reverse("accounts:password_reset"))
    response = client.post(
        reverse("accounts:password_reset"),
        {"email": "alice@example.com", "csrfmiddlewaretoken": csrf_token(client)},
    )
    assert response.status_code == 302
    assert len(mailoutbox) == 1


def test_password_change_needs_a_csrf_token(alice):
    client = strict()
    force_login(client, alice)
    response = client.post(
        reverse("accounts:password_change"),
        {"old_password": PASSWORD, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
    )
    assert response.status_code == 403
    alice.refresh_from_db()
    assert alice.check_password(PASSWORD)


def test_password_change_with_the_token_works(alice):
    client = strict()
    force_login(client, alice)
    client.get(reverse("accounts:password_change"))
    response = client.post(
        reverse("accounts:password_change"),
        {
            "old_password": PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
            "csrfmiddlewaretoken": csrf_token(client),
        },
    )
    assert response.status_code == 302
    alice.refresh_from_db()
    assert alice.check_password(NEW_PASSWORD)


def test_setting_the_new_password_after_a_reset_needs_a_csrf_token(alice, mailoutbox):
    request_reset(Client(), "alice@example.com")
    client = strict()
    _, final = open_reset_link(client, reset_link(mailoutbox[0]))
    response = client.post(final, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
    assert response.status_code == 403
    alice.refresh_from_db()
    assert alice.check_password(PASSWORD)
