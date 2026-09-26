"""Fixtures for the step 7a tests (tests/forum_services/). Nothing imports from this file; helpers live in
fsvc_testkit.py."""
import sys
import types
from datetime import datetime, timedelta, timezone as dt_timezone

import pytest

T0 = datetime(2026, 3, 10, 12, 0, 0, tzinfo=dt_timezone.utc)


class FrozenClock:
    """A callable standing in for django.utils.timezone.now. Time only moves when a test moves it."""

    def __init__(self, start=T0):
        self.current = start

    def __call__(self):
        return self.current

    def set(self, moment):
        self.current = moment
        return moment

    def advance(self, seconds):
        self.current = self.current + timedelta(seconds=seconds)
        return self.current


@pytest.fixture(autouse=True)
def _worker_mode_by_default(settings):
    """No test here may reach the moderation pipeline by accident; tests that want sync mode ask for it."""
    settings.MODERATION_RUN_MODE = "worker"


@pytest.fixture
def clock(monkeypatch):
    frozen = FrozenClock()
    monkeypatch.setattr("django.utils.timezone.now", frozen)
    return frozen


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Install a fake moderation.pipeline (step 5 builds the real one in parallel). Records every call."""
    from django.db import connection

    calls = []

    def run_moderation(run, *args, **kwargs):
        from forum.models import Message
        from moderation.models import ModerationRun

        calls.append(
            {
                "run_pk": run.pk,
                "trigger_message_id": run.trigger_message_id,
                "in_atomic_block": connection.in_atomic_block,
                "run_row_status": ModerationRun.objects.filter(pk=run.pk).values_list("status", flat=True).first(),
                "message_row_exists": Message.objects.filter(pk=run.trigger_message_id).exists(),
                "args": args,
                "kwargs": kwargs,
            }
        )

    module = types.ModuleType("moderation.pipeline")
    module.run_moderation = run_moderation
    module.calls = calls
    import moderation

    monkeypatch.setitem(sys.modules, "moderation.pipeline", module)
    monkeypatch.setattr(moderation, "pipeline", module, raising=False)
    return module
