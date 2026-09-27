"""Several claimers at once on a real file-based SQLite database (WAL, as in production): no run is ever claimed twice, every run is
claimed exactly once across N workers, and two runs of one conversation are never running at the same time.

Each scenario runs in a subprocess with its own database file, real threads and a barrier so the claimers start together (no
sleeps to line them up). pytest-django's in-memory test database and its wrapping transaction are not involved. FakeLLM is not
needed: only `claim_next_run` is raced, and a "worker" finishes a run by setting its status.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

SCRIPT = textwrap.dedent(
    r'''
    import json, os, sys, threading, time
    from datetime import datetime, timedelta, timezone as dt_timezone
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connections

    if os.environ.get("MIGRATE_ONLY") == "1":
        call_command("migrate", verbosity=0, interactive=False)
        connections.close_all()
        sys.exit(0)

    from forum.models import Conversation, Message, Participant, Topic
    from moderation import worker
    from moderation.models import ModerationRun

    T0 = datetime(2026, 5, 4, 12, 0, 0, tzinfo=dt_timezone.utc)
    counter = [0]


    def make_conversation(runs, base):
        counter[0] += 1
        topic = Topic.objects.create(title="", proposition=f"Racing proposition number {counter[0]}")
        conv = Conversation.objects.create(topic=topic, source="synthetic")
        parts = [Participant.objects.create(conversation=conv, label=l, join_order=i) for i, l in enumerate("AB", 1)]
        out = []
        for k in range(runs):
            msg = Message.objects.create(conversation=conv, author_type="user", participant=parts[k % 2], content=f"racing message {k}")
            run = ModerationRun.objects.create(
                conversation=conv, trigger_message=msg, snapshot_seq=msg.seq_no,
                created_at=T0 + timedelta(seconds=base + k),
            )
            out.append(run.pk)
        return conv.pk, out


    def parallel(count, body):
        barrier = threading.Barrier(count)
        results = [None] * count

        def target(i):
            try:
                barrier.wait(timeout=60)
                results[i] = {"ok": body(i)}
            except Exception as exc:
                results[i] = {"error": type(exc).__name__ + ": " + str(exc)[:300]}
            finally:
                connections.close_all()

        threads = [threading.Thread(target=target, args=(i,)) for i in range(count)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        return results


    def single_claims(rounds, conversations, claimers):
        """Each round: `conversations` conversations with one pending run each, `claimers` threads claim once at the same moment."""
        out = []
        for r in range(rounds):
            made = [make_conversation(1, base=i)[1][0] for i in range(conversations)]

            def body(i):
                run = worker.claim_next_run()
                return None if run is None else run.pk

            results = parallel(claimers, body)
            got = [x["ok"] for x in results if "ok" in x and x["ok"] is not None]
            out.append({
                "made": made, "got": got, "nones": sum(1 for x in results if x.get("ok", 0) is None),
                "errors": [x["error"] for x in results if "error" in x],
                "statuses": sorted(ModerationRun.objects.filter(pk__in=made).values_list("status", flat=True)),
                "claimed_at_missing": ModerationRun.objects.filter(pk__in=made, status="running", claimed_at__isnull=True).count(),
            })
        return out


    def drain(rounds, conversations, per_conversation, claimers):
        """Each round: `conversations` conversations with `per_conversation` pending runs each; `claimers` threads claim, hold
        the run briefly and finish it, until claim_next_run returns None."""
        out = []
        for r in range(rounds):
            created = {}
            for c in range(conversations):
                conv_id, run_ids = make_conversation(per_conversation, base=c * 100)
                created[conv_id] = run_ids
            conv_of = {rid: cid for cid, rids in created.items() for rid in rids}
            lock = threading.Lock()
            active = {cid: 0 for cid in created}
            log = []
            overlaps = []

            def body(i):
                mine = 0
                while True:
                    run = worker.claim_next_run()
                    if run is None:
                        return mine
                    cid = run.conversation_id
                    with lock:
                        active[cid] += 1
                        if active[cid] > 1:
                            overlaps.append(cid)
                        log.append((cid, run.pk))
                    time.sleep(0.003)
                    with lock:
                        active[cid] -= 1
                    ModerationRun.objects.filter(pk=run.pk).update(status="done")
                    mine += 1

            results = parallel(claimers, body)
            claimed = [pk for _, pk in log]
            per_conv_order = {cid: [pk for c, pk in log if c == cid] for cid in created}
            out.append({
                "errors": [x["error"] for x in results if "error" in x],
                "claimed_count": len(claimed), "unique_count": len(set(claimed)),
                "all_made": sorted(conv_of), "claimed_sorted": sorted(claimed),
                "overlaps": overlaps,
                "orders_ok": all(per_conv_order[cid] == created[cid] for cid in created),
                "statuses": sorted(set(ModerationRun.objects.filter(pk__in=list(conv_of)).values_list("status", flat=True))),
            })
        return out


    def more_claimers_than_runs():
        return single_claims(rounds=12, conversations=3, claimers=8)


    def as_many_claimers_as_runs():
        return single_claims(rounds=12, conversations=8, claimers=8)


    result = {
        "more_claimers": more_claimers_than_runs(),
        "as_many": as_many_claimers_as_runs(),
        "drain": drain(rounds=6, conversations=4, per_conversation=5, claimers=6),
        "drain_one_conversation": drain(rounds=6, conversations=1, per_conversation=8, claimers=6),
    }
    print("RESULT " + json.dumps(result))
    '''
)


def _env(db_path, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "DJANGO_DB_PATH", "DJANGO_ENV")}
    env["DJANGO_DB_PATH"] = str(db_path)
    env.update(extra)
    return env


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    """Run every scenario once in one subprocess (Django takes seconds to start) on a scratch SQLite file."""
    folder = tmp_path_factory.mktemp("worker_races")
    template = folder / "race.sqlite3"
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT], cwd=REPO, env=_env(template, MIGRATE_ONLY="1"),
        capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT], cwd=REPO, env=_env(template), capture_output=True, text=True, timeout=600
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    line = next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))
    return json.loads(line[len("RESULT "):])


def no_errors(rounds):
    assert [r["errors"] for r in rounds] == [[] for _ in rounds]


class TestOneClaimEach:
    def test_more_claimers_than_runs_each_run_is_claimed_exactly_once(self, outcomes):
        rounds = outcomes["more_claimers"]
        no_errors(rounds)
        for r in rounds:
            assert sorted(r["got"]) == sorted(r["made"])

    def test_the_claimers_without_a_run_get_none(self, outcomes):
        for r in outcomes["more_claimers"]:
            assert r["nones"] == 8 - 3

    def test_every_claimed_run_is_running_with_a_claimed_at(self, outcomes):
        for r in outcomes["more_claimers"]:
            assert r["statuses"] == ["running"] * 3
            assert r["claimed_at_missing"] == 0

    def test_as_many_claimers_as_runs_each_run_goes_to_exactly_one_claimer(self, outcomes):
        rounds = outcomes["as_many"]
        no_errors(rounds)
        for r in rounds:
            assert sorted(r["got"]) == sorted(r["made"])
            assert r["nones"] == 0
            assert r["statuses"] == ["running"] * 8


class TestManyWorkersDrainingTheQueue:
    def test_every_run_is_claimed_exactly_once_across_all_workers(self, outcomes):
        rounds = outcomes["drain"]
        no_errors(rounds)
        for r in rounds:
            assert r["claimed_count"] == 4 * 5
            assert r["unique_count"] == 4 * 5
            assert r["claimed_sorted"] == r["all_made"]
            assert r["statuses"] == ["done"]

    def test_two_runs_of_one_conversation_are_never_running_at_once(self, outcomes):
        for r in outcomes["drain"] + outcomes["drain_one_conversation"]:
            assert r["overlaps"] == []

    def test_within_a_conversation_runs_are_claimed_oldest_first(self, outcomes):
        for r in outcomes["drain"] + outcomes["drain_one_conversation"]:
            assert r["orders_ok"] is True

    def test_one_conversation_with_many_workers_is_still_fully_drained_in_order(self, outcomes):
        rounds = outcomes["drain_one_conversation"]
        no_errors(rounds)
        for r in rounds:
            assert r["claimed_count"] == 8
            assert r["unique_count"] == 8
            assert r["statuses"] == ["done"]
