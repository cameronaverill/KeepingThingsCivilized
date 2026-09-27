"""Fixtures for the step 20b tests (tests/research/: `moderation/research.py::run_research`).

Same guarantees as tests/pipeline_run/conftest.py, whose conventions this mirrors exactly (run_research is a
smaller cousin of run_moderation, sharing the same gateway, so the same environment guard applies): the kill switch
is on with a dummy key, a real Anthropic client can never be built, and a FakeLLM is the client (`fake(script...)`).
pytest-django wraps a test in a transaction, and the gateway refuses calls made inside one, so that guard is off
here; a test that specifically needs it back on requests a transactional database itself.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _research_environment(settings, monkeypatch):
    from moderation import llm

    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = "research-dummy-key"  # secret-scan: allow
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
