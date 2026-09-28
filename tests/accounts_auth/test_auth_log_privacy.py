"""Privacy: accounts/authviews.py deliberately logs only account pks, counts and fixed codes -- never a password or
a username (module lines ~91, 116-121, 158). Registration already has the equivalent check
(tests/accounts_registration/test_reg_security.py::test_the_logs_never_hold_a_password); this covers the same
promise for login failure, lockout and password change.

Every check below filters caplog's records to accounts.authviews specifically (auth_testkit.log_messages), so a
username or password that happens to appear in django-axes' own logging (a different module, a different contract)
does not make these tests fail for the wrong reason.
"""
import logging

import pytest
from auth_testkit import (
    AUTHVIEWS_LOGGER,
    NEW_PASSWORD,
    PASSWORD,
    WRONG_PASSWORD,
    alice,
    clean_axes,
    fail_logins,
    force_login,
    log_messages,
    login_post,
)
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db

LIMIT = settings.LOGIN_MAX_FAILURES


class TestFailedLoginLogsNoSecret:

    def test_a_wrong_password_attempt_logs_no_password_or_username(self, client, alice, caplog):
        caplog.set_level(logging.INFO, logger=AUTHVIEWS_LOGGER)

        login_post(client, "alice", WRONG_PASSWORD)

        blob = log_messages(caplog)
        assert "login: failed attempt" in blob
        assert WRONG_PASSWORD not in blob
        assert "alice" not in blob

    def test_an_unknown_username_attempt_logs_no_username(self, client, alice, caplog):
        caplog.set_level(logging.INFO, logger=AUTHVIEWS_LOGGER)

        login_post(client, "ghost_user", WRONG_PASSWORD)

        blob = log_messages(caplog)
        assert "login: failed attempt" in blob
        assert "ghost_user" not in blob
        assert WRONG_PASSWORD not in blob


class TestLockoutLogsNoSecret:

    def test_a_lockout_logs_no_username_or_password(self, client, alice, caplog):
        caplog.set_level(logging.INFO, logger=AUTHVIEWS_LOGGER)

        fail_logins(client, "alice", LIMIT)
        login_post(client, "alice", WRONG_PASSWORD, ip="10.0.0.1")

        blob = log_messages(caplog)
        assert "login: locked out" in blob
        assert "alice" not in blob
        assert WRONG_PASSWORD not in blob


class TestPasswordChangeLogsNoSecret:

    def test_a_password_change_logs_no_password_or_username(self, client, alice, caplog):
        caplog.set_level(logging.INFO, logger=AUTHVIEWS_LOGGER)
        force_login(client, alice)

        client.post(
            reverse("accounts:password_change"),
            {"old_password": PASSWORD, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
        )

        blob = log_messages(caplog)
        assert "password change:" in blob
        assert PASSWORD not in blob
        assert NEW_PASSWORD not in blob
        assert "alice" not in blob
