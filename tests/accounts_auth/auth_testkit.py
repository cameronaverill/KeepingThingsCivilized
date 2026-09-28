"""Shared helpers for the step 6b/6c tests (login, logout, throttling, password change; no email, no reset).

Nothing here imports from conftest. Test modules import the fixtures they need from this module by name.
Password literals are built at runtime so the repo-wide secret scan does not mistake them for credentials.
"""
import html
import re
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock
from urllib.parse import urlencode

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

User = get_user_model()

PASSWORD = "correct-horse-" + "battery-9Z"
WRONG_PASSWORD = "not-the-" + "right-one-77"
NEW_PASSWORD = "brand-new-" + "secret-Phrase-42"
MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"

GENERIC_LOGIN_ERROR = "Incorrect username or password."
LOCKED_PREFIX = "Too many failed attempts."

# The lockout page is the login page with the wait stated, served with HTTP 429 (step 6b contract, kept in 6c).
LOCKOUT_STATUSES = (429,)


# --- axes state ----------------------------------------------------------------------------------------------------


def wipe_axes_state():
    """Forget every recorded failed attempt (database handler) and anything cached, so tests never see each other."""
    from axes.utils import reset

    reset()
    cache.clear()


@pytest.fixture
def clean_axes(db):
    wipe_axes_state()
    yield
    wipe_axes_state()


@contextmanager
def frozen_at(target):
    """Make axes believe the time is exactly `target` (a fixed instant, not a moving clock) for as long as this lasts.

    Axes stamps every attempt with its own `now` (handlers.proxy) and reads it again (attempts) to judge cool-off.
    Because the same `target` is returned on every call inside the block, two blocks anchored to one shared instant
    (`base = timezone.now()`, then `frozen_at(base)` and `frozen_at(base + delta)`) give an exact elapsed time with
    no wall-clock drift between them -- unlike two independent `time_travel()` calls, each anchored to its own
    real-time `now()` at the moment it is entered.
    """
    with mock.patch("axes.handlers.proxy.now", lambda: target), mock.patch("axes.attempts.now", lambda: target):
        yield


@contextmanager
def time_travel(delta):
    """Make axes believe `delta` more time has passed. Axes stamps every attempt with its own `now` (handlers.proxy)."""
    with frozen_at(timezone.now() + delta):
        yield


def cooloff():
    return timedelta(minutes=settings.LOGIN_COOLOFF_MINUTES)


# --- accounts ------------------------------------------------------------------------------------------------------


def make_user(username="alice", email="", active=True, password=PASSWORD):
    """An account. Since step 6c people register with no email, so the default is none; pass one for an old account."""
    user = User.objects.create_user(username=username, email=email, password=password)
    if not active:
        user.is_active = False
        user.save(update_fields=["is_active"])
    return user


@pytest.fixture
def alice(clean_axes):
    return make_user("alice")


@pytest.fixture
def old_account(clean_axes):
    """An account from before step 6c: it has an email address."""
    return make_user("olivia", email="olivia@example.com")


@pytest.fixture
def switched_off(clean_axes):
    """An account an administrator has deactivated (is_active False)."""
    return make_user("dormant_dan", active=False)


def is_logged_in(client):
    return "_auth_user_id" in client.session


def force_login(client, user):
    client.force_login(user, backend=MODEL_BACKEND)


# --- requests ------------------------------------------------------------------------------------------------------


def login_post(client, username, password, ip=None, next_in_post=None, next_in_query=None):
    url = reverse("accounts:login")
    if next_in_query is not None:
        url += "?" + urlencode({"next": next_in_query})
    data = {"username": username, "password": password}
    if next_in_post is not None:
        data["next"] = next_in_post
    extra = {"REMOTE_ADDR": ip} if ip else {}
    return client.post(url, data, **extra)


def fail_logins(client, username, count, ip="10.0.0.1"):
    """`count` wrong-password attempts for one username from one address."""
    return [login_post(client, username, WRONG_PASSWORD, ip=ip) for _ in range(count)]


def text(response):
    """The page as plain text: entities decoded, so curly quotes and apostrophes compare like a reader sees them."""
    return html.unescape(response.content.decode())


def normalized(response, *names):
    """The page with the per-request token removed and the typed names blanked, to compare two pages for equality."""
    page = re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', "CSRF", text(response))
    for name in names:
        page = page.replace(name, "USER")
    return page


def template_names(response):
    return [template.name for template in response.templates if template.name]


def hrefs(response):
    return re.findall(r'href="([^"]*)"', text(response))


def links_to(response, url_name, *args):
    """Does the page carry a link whose path is the reverse of `url_name`?"""
    target = reverse(url_name, args=args)
    return any(href == target or href.startswith(target + "?") for href in hrefs(response))


def csrf_token(client):
    return client.cookies["csrftoken"].value


def lockout_wait_minutes(page):
    found = re.search(r"Try again in (\d+) minutes?", page)
    return int(found.group(1)) if found else None


def lock_out_username(client, username, ips=None):
    """Lock `username` by spreading LOGIN_MAX_FAILURES failures over different addresses (the IPs stay under the limit)."""
    ips = ips or [f"10.1.0.{i + 1}" for i in range(settings.LOGIN_MAX_FAILURES)]
    for ip in ips:
        login_post(client, username, WRONG_PASSWORD, ip=ip)


def lock_out_ip(client, ip, names=None):
    """Lock an address by spreading LOGIN_MAX_FAILURES failures over different usernames (each stays under the limit)."""
    names = names or [f"someone_{i}" for i in range(settings.LOGIN_MAX_FAILURES)]
    for name in names:
        login_post(client, name, WRONG_PASSWORD, ip=ip)


# --- logging (accounts/authviews.py deliberately logs only pks, counts and fixed codes, never usernames/passwords) -----

AUTHVIEWS_LOGGER = "accounts.authviews"


def log_messages(caplog, logger_name=AUTHVIEWS_LOGGER):
    """The formatted text of every record this module logged, as one blob (caplog also captures axes' own logging,
    which is not this module's contract, so callers checking this module's privacy promise filter to its logger)."""
    return "\n".join(record.getMessage() for record in caplog.records if record.name == logger_name)


# --- a second authentication backend, for isolating LoginForm.clean()'s own confirm_login_allowed call -----------------


class ReturnsUserRegardlessOfActiveState:
    """Test-only backend: authenticates on username + password alone, with none of ModelBackend's own checks.

    In particular it does not call `user_can_authenticate`, so it hands back an inactive user instead of None.
    Used to prove that `LoginForm.clean()` (accounts/authviews.py) enforces the active-account rule itself, through
    its own call to `confirm_login_allowed`, rather than merely relying on ModelBackend having already filtered
    the user out before `authenticate()` returns -- which is what every other tested login path exercises.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        try:
            user = User._default_manager.get(username=username)
        except User.DoesNotExist:
            return None
        return user if user.check_password(password) else None

    def get_user(self, user_id):
        try:
            return User._default_manager.get(pk=user_id)
        except User.DoesNotExist:
            return None


INACTIVE_AWARE_BACKEND = "auth_testkit.ReturnsUserRegardlessOfActiveState"
