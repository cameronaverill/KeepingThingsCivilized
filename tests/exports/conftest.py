"""Fixtures for the step 9a tests (tests/exports/). Nothing imports from this file; helpers live in exports_kit.py.

No test here may reach the network or the real LLM client: a real Anthropic client can never be built, and the kill
switch is off. The exports are read-only queries, so nothing here calls the gateway at all.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _exports_environment(settings, monkeypatch):
    from moderation import llm

    settings.LLM_ENABLED = False

    def blocked(*args, **kwargs):
        raise AssertionError("a real Anthropic client was built in a test")

    monkeypatch.setattr(llm, "_build_real_client", blocked)
    llm.reset_client()
    yield
    llm.reset_client()


@pytest.fixture
def world(db):
    """The rich fixture: a closed human conversation `h`, a synthetic paired one `s` and a waiting one `e`."""
    import exports_kit as kit

    return kit.build_world()
