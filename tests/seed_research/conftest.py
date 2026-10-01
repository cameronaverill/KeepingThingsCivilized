"""Fixtures for the step 15 research-evaluation tests (tests/seed_research/). Helpers live in seed_research_kit.py (which reuses the step 14 judge_kit); nothing imports from this file.

A real Anthropic client can never be built and no socket can connect; the kill switch is on with a dummy key so a FakeLLM can
answer; the evaluation budget is roomy; every test may use the database (pytest-django wraps a test in a transaction and the
gateway refuses calls made inside one, so that guard is off here). Each test runs in its own empty working directory, so
nothing is ever written into the repository's generated/ folder. The fact bank is replaced by a small one written into that
directory, so the tests do not depend on the real bank's contents.
"""
import json
import socket
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed_research_kit  # noqa: E402,F401  (puts tests/seed_judge on sys.path)
from judge_kit import DUMMY_KEY  # noqa: E402


@pytest.fixture(autouse=True)
def _judge_environment(settings, monkeypatch, db, tmp_path):
    from config import tunables
    from moderation import llm

    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = DUMMY_KEY
    settings.LLM_FORBID_ATOMIC_CALLS = False
    for name, value in (("BUDGET_EVAL_USD_TOTAL", Decimal("100")),):
        setattr(settings, name, value)
        monkeypatch.setattr(tunables, name, value, raising=False)

    def blocked_client(*args, **kwargs):
        raise AssertionError("a real Anthropic client was built in a test")

    def blocked_socket(*args, **kwargs):
        raise AssertionError("a network connection was attempted in a test")

    monkeypatch.setattr(llm, "_build_real_client", blocked_client)
    monkeypatch.setattr(socket.socket, "connect", blocked_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked_socket)
    monkeypatch.chdir(tmp_path)
    llm.reset_client()
    yield
    llm.reset_client()


@pytest.fixture(autouse=True)
def bank(monkeypatch, tmp_path):
    """A two-fact bank (a statistic and a law) in place of the real one."""
    import judge_kit as kit
    from seeding import facts

    path = tmp_path / "test_fact_bank.json"
    path.write_text(json.dumps([f.model_dump(mode="json", exclude_none=True) for f in (kit.range_fact(), kit.law_fact())]), encoding="utf-8")
    monkeypatch.setattr(facts, "DEFAULT_PATH", path)
    return path


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


class _Typed:
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
