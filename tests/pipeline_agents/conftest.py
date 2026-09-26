"""Fixtures for tests/pipeline_agents (step 5a). Nothing imports from this file; helpers live in pipeline_agents_kit.py.

Rules these fixtures enforce: a real Anthropic client can never be built, and no real API key is used.
"""
import importlib
from decimal import Decimal

import pytest


@pytest.fixture(autouse=True)
def _database(db):
    return db


@pytest.fixture(autouse=True)
def _allow_calls_inside_transactions(settings):
    """pytest-django wraps each test in a transaction; the production guard (llm.call refuses inside atomic) is off here."""
    settings.LLM_FORBID_ATOMIC_CALLS = False


@pytest.fixture(autouse=True)
def _block_real_client(monkeypatch):
    try:
        llm = importlib.import_module("moderation.llm")
    except ModuleNotFoundError:
        yield
        return

    def blocked(*args, **kwargs):
        raise AssertionError("real client built in tests")

    monkeypatch.setattr(llm, "_build_real_client", blocked)
    llm.reset_client()
    yield
    llm.reset_client()


@pytest.fixture
def install_fake():
    """install_fake(*script) -> the FakeLLM now installed as the client."""
    from moderation import llm
    from moderation.fake_llm import FakeLLM

    def install(*script):
        client = FakeLLM(list(script))
        llm.set_client(client)
        return client

    yield install
    llm.reset_client()


@pytest.fixture
def tune(settings, monkeypatch):
    """tune(NAME=value, ...) overrides tunables both as Django settings and on config.tunables."""
    from config import tunables

    def apply(**values):
        for name, value in values.items():
            setattr(settings, name, value)
            monkeypatch.setattr(tunables, name, value, raising=False)

    return apply


@pytest.fixture
def llm_ready(settings):
    """Kill switch on and a dummy key (nothing ever uses it: the client is a FakeLLM)."""
    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = "test-key"


@pytest.fixture
def tiny_budget(tune):
    tune(
        BUDGET_PER_CONVERSATION_USD=Decimal("0.0001"),
        BUDGET_SITE_USD_PER_DAY=Decimal("0.0001"),
        BUDGET_SITE_USD_TOTAL=Decimal("0.0001"),
        BUDGET_EVAL_USD_TOTAL=Decimal("0.0001"),
    )
