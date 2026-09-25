"""Logout: POST only, CSRF protected, redirects with a confirmation message."""
import re

import pytest
from auth_testkit import PASSWORD, alice, clean_axes, csrf_token, force_login, is_logged_in, login_post
from django.conf import settings
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db
LOGOUT = "accounts:logout"


def test_post_logs_out_and_redirects_to_the_logout_redirect_url(client, alice):
    force_login(client, alice)
    assert is_logged_in(client)
    response = client.post(reverse(LOGOUT))
    assert response.status_code == 302
    assert response["Location"] == settings.LOGOUT_REDIRECT_URL
    assert not is_logged_in(client)


def test_logout_says_you_are_logged_out(client, alice):
    force_login(client, alice)
    response = client.post(reverse(LOGOUT))
    shown = [str(message) for message in get_messages(response.wsgi_request)]
    assert any(re.search(r"(logged|signed) out", m, re.I) for m in shown), shown


@pytest.mark.parametrize("method", ["get", "head", "put", "patch", "delete"])
def test_only_post_is_allowed(client, alice, method):
    force_login(client, alice)
    response = getattr(client, method)(reverse(LOGOUT))
    assert response.status_code == 405
    assert is_logged_in(client), "a non-POST request must not log anyone out (it could be forged by an image tag)"


def test_logging_out_when_already_logged_out_is_harmless(client, clean_axes):
    response = client.post(reverse(LOGOUT))
    assert response.status_code == 302


def test_logout_without_a_csrf_token_is_refused_and_keeps_the_session(alice):
    client = Client(enforce_csrf_checks=True)
    force_login(client, alice)
    response = client.post(reverse(LOGOUT))
    assert response.status_code == 403
    assert is_logged_in(client)


def test_logout_with_a_wrong_csrf_token_is_refused(alice):
    client = Client(enforce_csrf_checks=True)
    force_login(client, alice)
    client.get(reverse("accounts:password_change"))  # sets the csrf cookie
    response = client.post(reverse(LOGOUT), {"csrfmiddlewaretoken": "x" * 64})
    assert response.status_code == 403
    assert is_logged_in(client)


def test_logout_with_the_csrf_token_works(alice):
    client = Client(enforce_csrf_checks=True)
    force_login(client, alice)
    client.get(reverse("accounts:password_change"))
    response = client.post(reverse(LOGOUT), {"csrfmiddlewaretoken": csrf_token(client)})
    assert response.status_code == 302
    assert not is_logged_in(client)


def test_the_session_is_really_gone_after_logout(alice):
    client = Client()
    login_post(client, "alice", PASSWORD)
    old_key = client.cookies["sessionid"].value
    client.post(reverse(LOGOUT))
    stale = Client()
    stale.cookies["sessionid"] = old_key
    assert stale.get(reverse("accounts:password_change")).status_code == 302
