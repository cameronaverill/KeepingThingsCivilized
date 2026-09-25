"""Independent review pass: two callers reserving at the same time, on a real file-based SQLite database.

Runs in a subprocess (own database file, own connections, real threads), so it does not depend on pytest-django's
in-memory test database or its wrapping transaction. No network: the client is the FakeLLM.
"""
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
    from decimal import Decimal
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection, connections

    if os.environ.get("MIGRATE_ONLY") == "1":
        call_command("migrate", verbosity=0, interactive=False)
        connections.close_all()
        sys.exit(0)
    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = "test-key"
    CAP = Decimal(sys.argv[1])
    KIND = sys.argv[2]
    settings.BUDGET_SITE_USD_PER_DAY = CAP
    settings.BUDGET_PER_CONVERSATION_USD = CAP
    settings.BUDGET_SITE_USD_TOTAL = Decimal("100")
    settings.BUDGET_EVAL_USD_TOTAL = Decimal("100")

    from moderation import llm, budget
    from moderation.errors import BudgetExceeded, BudgetUnavailable
    from moderation.fake_llm import FakeLLM
    from moderation.models import LLMCall
    from pydantic import BaseModel

    class Verdict(BaseModel):
        label: str
        score: int

    from types import SimpleNamespace as NS
    def slow_ok(kwargs):
        time.sleep(0.4)  # stay in flight long enough for the other caller to try to reserve
        return NS(parsed_output=Verdict(label="a", score=1), stop_reason="end_turn", id="msg_1",
                  usage=NS(input_tokens=100, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0))

    llm.set_client(FakeLLM([slow_ok] * 20))
    N = int(sys.argv[3])
    barrier = threading.Barrier(N)
    results = [None] * N

    def worker(i):
        try:
            barrier.wait()
            kwargs = dict(purpose="moderation", agent="master", model="claude-sonnet-5", system="s",
                          messages=[{"role": "user", "content": "hello"}], output_schema=Verdict, max_tokens=500)
            if KIND == "conversation":
                kwargs["conversation_id"] = 7
            llm.call(**kwargs)
            results[i] = "ok"
        except BudgetExceeded as e:
            results[i] = "refused_budget"
        except BudgetUnavailable as e:
            results[i] = "unavailable: " + str(e)[:120]
        except Exception as e:
            results[i] = "error: " + type(e).__name__ + ": " + str(e)[:120]
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    statuses = sorted(LLMCall.objects.values_list("status", flat=True))
    reserved = [str(x) for x in LLMCall.objects.filter(status="ok").values_list("reserved_usd", flat=True)]
    print("RESULT " + json.dumps({"results": sorted(results), "statuses": statuses, "reserved": reserved}))
    '''
)


def _env(db_path, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "DJANGO_DB_PATH", "DJANGO_ENV")}
    env["DJANGO_DB_PATH"] = str(db_path)
    env.update(extra)
    return env


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory):
    """One migrated SQLite file, copied for each scenario (migrating takes seconds)."""
    path = tmp_path_factory.mktemp("template") / "template.sqlite3"
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT, "0", "day", "1"],
        cwd=REPO_ROOT, env=_env(path, MIGRATE_ONLY="1"), capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for suffix in ("-wal", "-shm"):
        assert not Path(str(path) + suffix).exists() or Path(str(path) + suffix).stat().st_size == 0
    return path


def run_scenario(migrated_db, tmp_path, cap, kind, callers):
    db_path = tmp_path / "concurrency.sqlite3"
    shutil.copy(migrated_db, db_path)
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(cap), kind, str(callers)],
        cwd=REPO_ROOT, env=_env(db_path), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    line = next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))
    return json.loads(line[len("RESULT "):])


# A normal test call reserves about 0.0078 (Sonnet, 500 max tokens); a 0.010 cap fits one but not two.


def test_two_simultaneous_callers_cannot_both_squeeze_under_the_day_cap(migrated_db, tmp_path):
    outcome = run_scenario(migrated_db, tmp_path, "0.010", "day", 2)
    assert outcome["results"] == ["ok", "refused_budget"], outcome
    assert outcome["statuses"] == ["ok", "refused_budget"], outcome


def test_two_simultaneous_callers_cannot_both_squeeze_under_the_conversation_cap(migrated_db, tmp_path):
    outcome = run_scenario(migrated_db, tmp_path, "0.010", "conversation", 2)
    assert outcome["results"] == ["ok", "refused_budget"], outcome


def test_five_simultaneous_callers_with_room_for_two(migrated_db, tmp_path):
    """A 0.017 cap fits two reservations (about 0.0156) but not three."""
    outcome = run_scenario(migrated_db, tmp_path, "0.017", "day", 5)
    assert outcome["results"].count("ok") == 2, outcome
    assert outcome["results"].count("refused_budget") == 3, outcome
    assert not [r for r in outcome["results"] if r.startswith(("error", "unavailable"))], outcome


def test_with_room_for_everyone_all_simultaneous_callers_succeed_without_lock_errors(migrated_db, tmp_path):
    outcome = run_scenario(migrated_db, tmp_path, "1.00", "day", 4)
    assert outcome["results"] == ["ok"] * 4, outcome
