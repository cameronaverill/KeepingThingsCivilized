"""Fixtures for the step 9c tests (tests/admin_site/). Nothing imports from this file; helpers live in adm_kit.py.

What the fixtures guarantee: a real Anthropic client can never be built (and a spy client records any call), the
moderation pipeline is a recorder, and the moderation run mode is the worker mode. So a test that touches an admin page
or action can never spend money."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # make the helpers importable however pytest is invoked

import adm_kit as K  # noqa: E402


class SpendSpy:
    """Records every attempt to build or use an LLM client, or to run the moderation pipeline."""

    def __init__(self):
        self.events = []

    def record(self, what):
        self.events.append(what)


@pytest.fixture(autouse=True)
def spend_spy(db, settings, monkeypatch):
    """No admin page or action may build a client, call the model, or run the pipeline. Each attempt is recorded and
    raises; tests assert `spend_spy.events == []` and that no LLMCall or ModerationRun row appeared."""
    from moderation import llm

    spy = SpendSpy()
    settings.MODERATION_RUN_MODE = "worker"
    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = "admin-site-dummy-key"  # secret-scan: allow

    def blocked_build(*args, **kwargs):
        spy.record("build_real_client")
        raise AssertionError("a real Anthropic client was built in an admin test")

    class SpyClient:
        class messages:  # noqa: N801
            @staticmethod
            def parse(*args, **kwargs):
                spy.record("client.messages.parse")
                raise AssertionError("the LLM client was used in an admin test")

            create = parse

    monkeypatch.setattr(llm, "_build_real_client", blocked_build)
    llm.set_client(SpyClient())

    def blocked_call(*args, **kwargs):
        spy.record("llm.call")
        raise AssertionError("llm.call was invoked in an admin test")

    monkeypatch.setattr(llm, "call", blocked_call)

    from moderation import pipeline

    def blocked_pipeline(*args, **kwargs):
        spy.record("pipeline.run_moderation")
        raise AssertionError("the moderation pipeline was run in an admin test")

    monkeypatch.setattr(pipeline, "run_moderation", blocked_pipeline)
    yield spy
    llm.reset_client()


@pytest.fixture
def world():
    return K.World()


@pytest.fixture
def root(world):
    """A logged-in superuser browser (the world exists first so its rows are on every page)."""
    return K.client_for(K.make_superuser())


@pytest.fixture
def superuser():
    return K.make_superuser()
