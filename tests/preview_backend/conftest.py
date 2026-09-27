"""Fixtures for the step 19 back-end tests (tests/preview_backend/). Nothing imports from this file; helpers live in
preview_kit.py (which builds on tests/pipeline_run/pipeline_run_kit.py).

What the fixtures guarantee: the kill switch is on with a dummy key, a real Anthropic client can never be built, and a
FakeLLM is the client (`fake(script...)`). A FakeLLM whose script runs out raises, so "no model call" is enforced by
installing an empty script. pytest-django wraps a test in a transaction and the gateway refuses calls made inside one, so
that guard is off here; the one test that proves a check holds no transaction during a call turns it back on.
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "pipeline_run"))


@pytest.fixture(autouse=True)
def _preview_environment(settings, monkeypatch):
    from moderation import llm

    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = "preview-backend-dummy-key"  # secret-scan: allow
    settings.LLM_FORBID_ATOMIC_CALLS = False

    def blocked(*args, **kwargs):
        raise AssertionError("a real Anthropic client was built in a test")

    monkeypatch.setattr(llm, "_build_real_client", blocked)
    llm.reset_client()
    yield
    llm.reset_client()


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


@pytest.fixture
def tune(settings, monkeypatch):
    """tune(NAME=value, ...) overrides a tunable both as a Django setting and on config.tunables."""
    from config import tunables

    def apply(**values):
        for name, value in values.items():
            setattr(settings, name, value)
            monkeypatch.setattr(tunables, name, value, raising=False)

    return apply
