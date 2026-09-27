"""Fixtures for the step 8 tests (tests/worker/). Nothing imports from this file; helpers live in worker_kit.py.

What the fixtures guarantee for every test: the kill switch is off (`LLM_ENABLED` False, the tunable's shipped value), run mode
is "worker", a real Anthropic client can never be built, and a test that hangs is stopped after a minute instead of blocking
the whole suite. A test that wants the model on asks for `llm_on` (dummy key, FakeLLM as the client).
"""
import os
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

HANG_SECONDS = int(os.environ.get("WORKER_TEST_HANG_SECONDS", "60"))  # shorter for mutation runs


@pytest.fixture(autouse=True)
def _worker_environment(settings, monkeypatch):
    from moderation import llm

    settings.MODERATION_RUN_MODE = "worker"
    settings.LLM_ENABLED = False
    # pytest-django wraps a test in a transaction and the gateway refuses calls made inside one, before it looks at the
    # kill switch; the tests that prove the worker holds no transaction of its own use a transactional database.
    settings.LLM_FORBID_ATOMIC_CALLS = False

    def blocked(*args, **kwargs):
        raise AssertionError("a real Anthropic client was built in a test")

    monkeypatch.setattr(llm, "_build_real_client", blocked)
    llm.reset_client()
    yield
    llm.reset_client()


@pytest.fixture(autouse=True)
def _hang_guard():
    """A worker loop that never ends would hang the suite: raise in the main thread after HANG_SECONDS."""

    def expired(signum, frame):
        raise TimeoutError(f"test still running after {HANG_SECONDS} s: a worker loop that does not stop?")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(HANG_SECONDS)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, previous)


@pytest.fixture
def llm_on(settings):
    """The kill switch on with a dummy key (no real client can be built: see _worker_environment)."""
    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = "worker-dummy-key"  # secret-scan: allow
    settings.LLM_FORBID_ATOMIC_CALLS = False


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
def clock(monkeypatch):
    """A frozen `django.utils.timezone.now`. Time only moves when a test moves it."""
    import worker_kit as kit

    frozen = kit.FrozenClock()
    monkeypatch.setattr("django.utils.timezone.now", frozen)
    return frozen


@pytest.fixture
def signal_sentinels():
    """Install recording handlers for SIGINT and SIGTERM (so that a command that fails to wire its own cannot kill the test
    process) and put the old ones back afterwards. Returns the list of signals that reached the sentinels."""
    reached = []
    old = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        old[sig] = signal.signal(sig, lambda signum, frame: reached.append(signum))
    yield reached
    for sig, handler in old.items():
        signal.signal(sig, handler)
