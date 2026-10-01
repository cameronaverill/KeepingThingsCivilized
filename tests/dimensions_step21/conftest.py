"""Fixtures for the step 21 tests (tests/dimensions_step21/): the clarity dimension and the agreement/disagreement note.

Same guarantees as tests/pipeline_run/conftest.py: the kill switch is on with a dummy key, a real Anthropic client can never
be built, and a FakeLLM is the client (`fake(script...)`). Helpers live in dim21_kit.py (built on pipeline_run_kit and
preview_kit).
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for folder in (HERE, HERE.parent / "pipeline_run", HERE.parent / "preview_backend"):
    sys.path.insert(0, str(folder))


@pytest.fixture(autouse=True)
def _dim21_environment(settings, monkeypatch):
    from moderation import llm

    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = "dim21-dummy-key"  # secret-scan: allow
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
