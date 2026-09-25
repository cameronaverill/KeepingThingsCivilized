"""Fixtures for the step 2 tests (tests/moderation/).

Rules these fixtures enforce: a real Anthropic client can never be built, time is patchable, and no real API key is used.
"""
import importlib
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # make moderation_testkit importable however pytest is invoked

import moderation_testkit as kit  # noqa: E402


@pytest.fixture(autouse=True)
def _database(db):
    """Every test in this folder may touch the database."""
    return db


@pytest.fixture(autouse=True)
def _allow_calls_inside_transactions(settings):
    """pytest-django wraps every test in a transaction, so the production guard (llm.call refuses to run inside
    transaction.atomic) would trip everywhere. Turn it off for this folder; a test that exercises the guard requests
    the `forbid_atomic_calls` fixture below. Harmless before the setting exists."""
    settings.LLM_FORBID_ATOMIC_CALLS = False


@pytest.fixture
def forbid_atomic_calls(settings, _allow_calls_inside_transactions):
    """Opt-out of the opt-out: run with the production value (guard on)."""
    settings.LLM_FORBID_ATOMIC_CALLS = True


@pytest.fixture(autouse=True)
def _block_real_client(monkeypatch):
    """A real client must never be constructed in tests. The genuine builder is kept in kit.ORIGINALS for one test
    that checks how it would be called (with anthropic.Anthropic itself stubbed there)."""
    try:
        llm = importlib.import_module("moderation.llm")
    except ModuleNotFoundError as exc:
        if exc.name not in ("moderation", "moderation.llm"):
            raise
        yield  # the feature does not exist yet; tests that need it fail on their own import
        return

    kit.ORIGINALS["_build_real_client"] = llm._build_real_client

    def blocked(*args, **kwargs):
        raise AssertionError("real client built in tests")

    monkeypatch.setattr(llm, "_build_real_client", blocked)
    llm.reset_client()
    yield
    llm.reset_client()


@pytest.fixture
def install_fake():
    """install_fake(*script) -> the FakeLLM now installed as the client; reset afterwards."""
    from moderation import llm
    from moderation.fake_llm import FakeLLM

    def install(*script):
        client = FakeLLM(list(script))
        llm.set_client(client)
        return client

    yield install
    llm.reset_client()


class Clock:
    """Controls moderation.clock.now()."""

    def __init__(self, start):
        self.current = start

    def now(self):
        return self.current

    def set(self, moment):
        self.current = moment

    def advance(self, **kwargs):
        self.current = self.current + timedelta(**kwargs)
        return self.current


@pytest.fixture
def frozen_clock(monkeypatch):
    """Patches moderation.clock.now; starts at 2026-09-25 12:00:00 UTC."""
    from moderation import clock

    controller = Clock(kit.utc(2026, 9, 25, 12))
    monkeypatch.setattr(clock, "now", controller.now)
    return controller


@pytest.fixture
def tune(settings, monkeypatch):
    """tune(NAME=value, ...) overrides tunables both as Django settings and on config.tunables, so the tests do not
    care which of the two the code reads."""
    from config import tunables

    def apply(**values):
        for name, value in values.items():
            setattr(settings, name, value)
            monkeypatch.setattr(tunables, name, value, raising=False)

    return apply


@pytest.fixture
def llm_ready(settings, frozen_clock):
    """Kill switch on, a fake API key, frozen clock. (The key is a dummy string; nothing ever uses it.)"""
    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = kit.FAKE_KEY
    return frozen_clock


@pytest.fixture
def small_budget(tune):
    """Tiny caps: per conversation 0.02, per day 0.03, site 0.05, eval 0.04 (a normal test call reserves about 0.008)."""
    from decimal import Decimal

    tune(
        BUDGET_PER_CONVERSATION_USD=Decimal("0.02"),
        BUDGET_SITE_USD_PER_DAY=Decimal("0.03"),
        BUDGET_SITE_USD_TOTAL=Decimal("0.05"),
        BUDGET_EVAL_USD_TOTAL=Decimal("0.04"),
    )
