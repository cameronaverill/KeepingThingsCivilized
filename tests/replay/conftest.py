"""Fixtures for the step 13 tests (tests/replay/). Nothing imports from this file; helpers live in replay_kit.py.

What the fixtures guarantee: a real Anthropic client can never be built (the builder is replaced by a function that fails
the test), the kill switch is on with a dummy key so a FakeLLM can answer, roomy evaluation caps, and every test may use
the database. pytest-django wraps a test in a transaction and the gateway refuses calls made inside one, so that guard is
off here (the pipeline tests prove the pipeline itself holds no transaction during a call).
"""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_kit import DUMMY_KEY  # noqa: E402


@pytest.fixture(autouse=True)
def _replay_environment(settings, monkeypatch, db):
    from config import tunables
    from moderation import llm

    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = DUMMY_KEY
    settings.LLM_FORBID_ATOMIC_CALLS = False
    for name, value in (("BUDGET_EVAL_USD_TOTAL", Decimal("100")),):
        setattr(settings, name, value)
        monkeypatch.setattr(tunables, name, value, raising=False)

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


@pytest.fixture
def scratch(tmp_path):
    """A unique folder for transcript files."""
    return tmp_path


class _Typed:
    """What an operator types at a prompt: `reply(answer)` queues a reply (an exception instance is raised instead), and
    `prompts` lists the prompts shown. With nothing queued, any prompt fails the test (a prompt nobody expected)."""

    def __init__(self):
        self.replies, self.prompts = [], []

    def reply(self, *answers):
        self.replies.extend(answers)

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        assert self.replies, f"the command asked for input nobody scripted: {prompt!r}"
        answer = self.replies.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.fixture
def typed(monkeypatch):
    """Replaces the built-in input(): a test says what is typed; any unscripted prompt fails."""
    import builtins

    stand_in = _Typed()
    monkeypatch.setattr(builtins, "input", stand_in)
    return stand_in
