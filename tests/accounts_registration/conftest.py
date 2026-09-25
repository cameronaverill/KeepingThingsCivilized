"""Fixtures for the step 6a tests (tests/accounts_registration/). Nothing imports from this file; shared helpers
live in reg_testkit.py."""
import sys
from pathlib import Path
from unittest import mock

import pytest
from django.core.cache import cache
from django.test import Client

sys.path.insert(0, str(Path(__file__).resolve().parent))  # make reg_testkit importable however pytest is invoked

import reg_testkit as kit  # noqa: E402


@pytest.fixture(autouse=True)
def _reg_state(db):
    """A clean cache (the resend limit may live there) and no leftover mail error, before and after every test."""
    cache.clear()
    kit.SwitchBackend.error = None
    yield
    cache.clear()
    kit.SwitchBackend.error = None


@pytest.fixture(autouse=True)
def clock():
    """Frozen time for every test: time.time (signing, cache) and timezone.now (database stamps)."""
    fake = kit.Clock()
    with mock.patch("time.time", fake.time), mock.patch("django.utils.timezone.now", fake.now):
        yield fake


@pytest.fixture
def mail_switch(settings):
    """Route the default mailer through an in-memory backend that raises while `mail_switch.error` is set."""
    settings.MAILERS = kit.SWITCH_MAILERS
    return kit.SwitchBackend


@pytest.fixture
def csrf_client():
    return Client(enforce_csrf_checks=True)
