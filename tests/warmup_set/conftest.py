"""Fixtures for the warm-up transcript tests (tests/warmup_set/).

What they guarantee: a real Anthropic client can never be built (the builder is replaced by a function that fails the test)
for every test in this folder, and the fake LLM is the only client a replay test can install. Only the replay tests use the
database (through the `replay_environment` fixture), and pytest-django's test database is never the dev server's db.sqlite3.
"""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

DUMMY_KEY = "warmup-tests-dummy-key"  # secret-scan: allow


@pytest.fixture(autouse=True)
def _block_real_client(monkeypatch):
    from moderation import llm

    def blocked(*args, **kwargs):
        raise AssertionError("a real Anthropic client was built in a warm-up test")

    monkeypatch.setattr(llm, "_build_real_client", blocked)
    llm.reset_client()
    yield
    llm.reset_client()


@pytest.fixture
def replay_environment(settings, monkeypatch, db):
    """Kill switch on with a dummy key (so a FakeLLM can answer), roomy evaluation cap, no transaction guard."""
    from config import tunables

    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = DUMMY_KEY
    settings.LLM_FORBID_ATOMIC_CALLS = False
    settings.BUDGET_EVAL_USD_TOTAL = Decimal("100")
    monkeypatch.setattr(tunables, "BUDGET_EVAL_USD_TOTAL", Decimal("100"), raising=False)


@pytest.fixture
def fake():
    """fake(*script) installs a FakeLLM with that script as the client and returns it."""
    from moderation import llm
    from moderation.fake_llm import FakeLLM

    def install(*script):
        client = FakeLLM(list(script))
        llm.set_client(client)
        return client

    return install
