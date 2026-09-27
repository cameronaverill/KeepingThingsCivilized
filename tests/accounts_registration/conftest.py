"""Fixtures for the registration tests (tests/accounts_registration/), step 6c: username and password only.
Nothing imports from this file; shared helpers live in reg_testkit.py."""
import sys
from pathlib import Path

import pytest
from django.core.cache import cache
from django.test import Client

sys.path.insert(0, str(Path(__file__).resolve().parent))  # make reg_testkit importable however pytest is invoked


@pytest.fixture(autouse=True)
def _reg_state(db):
    """A clean cache (axes and any limiter may use it) before and after every test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def csrf_client():
    return Client(enforce_csrf_checks=True)
