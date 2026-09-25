"""The half-open probe is claimed atomically: racing callers on a real file-based SQLite database (subprocess with
real threads, as in test_moderation_review2_concurrency.py). No network: the client is the FakeLLM."""
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SCRIPT = textwrap.dedent(
    r'''
    import json, os, sys, threading, time
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django
    django.setup()
    from datetime import timedelta
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections
    from django.utils import timezone

    if os.environ.get("MIGRATE_ONLY") == "1":
        call_command("migrate", verbosity=0, interactive=False)
        connections.close_all()
        sys.exit(0)

    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = "test-key"
    settings.ALERT_EMAIL = ""
    SCENARIO, N = sys.argv[1], int(sys.argv[2])

    from moderation import llm, breaker
    from moderation.errors import BreakerOpen
    from moderation.fake_llm import FakeLLM
    from moderation.models import GuardState, LLMCall
    from pydantic import BaseModel
    from types import SimpleNamespace as NS

    class Verdict(BaseModel):
        label: str
        score: int

    now = timezone.now()
    state = GuardState.load()
    state.breaker_tripped = True
    state.trip_kind = "soft"
    state.trip_reason = "consecutive_errors"
    state.tripped_at = now - timedelta(seconds=400)
    state.cooldown_seconds = 300
    state.cooldown_until = now - timedelta(seconds=100)
    if SCENARIO == "abandoned":
        state.probe_in_flight = True
        state.probe_started_at = now - timedelta(seconds=1000)
    state.save()

    def slow_ok(kwargs):
        time.sleep(0.5)  # hold the probe in flight while the others try
        return NS(parsed_output=Verdict(label="a", score=1), stop_reason="end_turn", id="msg_1",
                  usage=NS(input_tokens=100, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0))

    llm.set_client(FakeLLM([slow_ok] * 20))
    barrier = threading.Barrier(N)
    results = [None] * N

    def worker(i):
        try:
            barrier.wait()
            llm.call(purpose="moderation", agent="master", model="claude-sonnet-5", system="s",
                     messages=[{"role": "user", "content": "hello"}], output_schema=Verdict, max_tokens=500)
            results[i] = "ok"
        except BreakerOpen as e:
            results[i] = "breaker_open:" + e.kind
        except Exception as e:
            results[i] = "error: " + type(e).__name__ + ": " + str(e)[:150]
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    final = GuardState.objects.get(pk=1)
    print("RESULT " + json.dumps({
        "results": sorted(results),
        "statuses": sorted(LLMCall.objects.values_list("status", flat=True)),
        "closed": not final.breaker_tripped, "probe_in_flight": final.probe_in_flight,
    }))
    '''
)


def _env(db_path, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "DJANGO_DB_PATH", "DJANGO_ENV", "ALERT_EMAIL")}
    env["DJANGO_DB_PATH"] = str(db_path)
    env.update(extra)
    return env


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("template") / "template.sqlite3"
    proc = subprocess.run([sys.executable, "-c", SCRIPT, "x", "1"], cwd=REPO_ROOT, env=_env(path, MIGRATE_ONLY="1"),
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return path


def race(migrated_db, tmp_path, scenario, callers):
    db_path = tmp_path / "race.sqlite3"
    shutil.copy(migrated_db, db_path)
    proc = subprocess.run([sys.executable, "-c", SCRIPT, scenario, str(callers)], cwd=REPO_ROOT, env=_env(db_path),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    line = next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))
    return json.loads(line[len("RESULT "):])


@pytest.mark.parametrize("callers", [2, 4])
def test_racing_callers_after_the_cooldown_admit_exactly_one_probe(migrated_db, tmp_path, callers):
    outcome = race(migrated_db, tmp_path, "expired", callers)
    assert outcome["results"] == ["breaker_open:soft"] * (callers - 1) + ["ok"], outcome
    assert outcome["statuses"] == sorted(["ok"] + ["refused_breaker"] * (callers - 1)), outcome
    assert outcome["closed"] is True and outcome["probe_in_flight"] is False, outcome


def test_racing_callers_over_an_abandoned_probe_admit_exactly_one(migrated_db, tmp_path):
    outcome = race(migrated_db, tmp_path, "abandoned", 3)
    assert outcome["results"] == ["breaker_open:soft"] * 2 + ["ok"], outcome
    assert outcome["closed"] is True, outcome
