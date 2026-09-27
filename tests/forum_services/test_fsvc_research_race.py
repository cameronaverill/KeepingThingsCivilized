"""Step 20b, Group C's forum-layer slice (docs/step20b_brief.md item 6): two rapid/concurrent clicks of "Provide
factual background" on the same offer_research act must never create two ModerationRun(kind="research") rows -- the
"first click claims it" unique constraint (Group A, item 1) catches the loser's INSERT, and the view (Group C) must
turn that IntegrityError into "already requested," not a 500 and not a duplicate.

Uses the same real-subprocess-and-real-threads technique as tests/forum_services/test_fsvc_concurrency.py (a real,
file-based SQLite database, so two callers can genuinely overlap; pytest-django's in-memory test database and its
wrapping transaction cannot produce a real race -- see that file's own docstring). Kept in a file of its own (rather
than added as a new scenario to fsvc_testkit.py's shared script) because this test slice must not touch any existing
test file while a coding agent builds the non-test side of this same feature concurrently.

Written from the brief's contract, calling `forum.views.request_research` directly (via RequestFactory, the same
direct-view-call pattern already used in tests/forum_views/test_fviews_home.py) so the race is isolated to the view's
own create-or-recognize-existing logic. This may fail until moderation/models.py's `kind="research"`/`source_act`
(Group A) and forum/views.py's `request_research` (Group C) both land -- that is expected, not a bug in this file."""
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
    import json, os, sys, threading
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections

    if os.environ.get("MIGRATE_ONLY") == "1":
        call_command("migrate", verbosity=0, interactive=False)
        connections.close_all()
        sys.exit(0)

    from django.contrib.auth import get_user_model
    from django.test import RequestFactory
    from forum import services
    from forum.models import Message, Participant, Topic
    from moderation.models import InterventionAct, ModerationRun

    settings.MODERATION_RUN_MODE = "worker"
    counter = [0]


    def user():
        counter[0] += 1
        name = f"racer{counter[0]:03d}"
        return get_user_model().objects.create_user(username=name, email=f"{name}@mailbox.example")


    def topic():
        counter[0] += 1
        return Topic.objects.create(title="", proposition=f"Racing research proposition {counter[0]}")


    def parallel(*calls):
        barrier = threading.Barrier(len(calls))
        results = [None] * len(calls)

        def worker(i):
            try:
                barrier.wait(timeout=60)
                results[i] = {"status_code": calls[i]()}
            except Exception as exc:
                results[i] = {"error": type(exc).__name__ + ": " + str(exc)[:300]}
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(calls))]
        [t.start() for t in threads]
        [t.join() for t in threads]
        return results


    def call_view(user_obj, conv_id, act_id):
        from forum import views  # imported lazily so a missing view fails the race scenario, not collection

        request = RequestFactory().post(f"/c/{conv_id}/research/{act_id}/")
        request.user = user_obj
        response = views.request_research(request, conv_id, act_id)
        return response.status_code


    def setup_offer():
        u1, u2, t = user(), user(), topic()
        conv = services.enter_proposition(u1, t, "pro")
        services.enter_proposition(u2, t, "con")
        p1 = Participant.objects.get(conversation=conv, user=u1)
        trigger = Message.objects.create(
            conversation=conv, author_type="user", participant=p1, content="A claim worth checking, raced."
        )
        run = ModerationRun.objects.create(
            conversation=conv, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live", status="pending"
        )
        act = InterventionAct.objects.create(
            run=run, order=1, act_type="offer_research", tone="neutral",
            text="An independent check could be requested.", addressee="all", subject="none", validity="valid",
        )
        act.source_messages.set([trigger])
        return conv, u1, u2, act


    def run(scenario):
        out = {}
        if scenario == "same_person_twice":
            conv, u1, u2, act = setup_offer()
            out["results"] = parallel(lambda: call_view(u1, conv.pk, act.pk), lambda: call_view(u1, conv.pk, act.pk))
            out["runs"] = ModerationRun.objects.filter(kind="research", source_act_id=act.pk).count()
        elif scenario == "both_participants":
            conv, u1, u2, act = setup_offer()
            out["results"] = parallel(lambda: call_view(u1, conv.pk, act.pk), lambda: call_view(u2, conv.pk, act.pk))
            out["runs"] = ModerationRun.objects.filter(kind="research", source_act_id=act.pk).count()
        elif scenario == "four_at_once":
            conv, u1, u2, act = setup_offer()
            out["results"] = parallel(*[(lambda: call_view(u1, conv.pk, act.pk))] * 4)
            out["runs"] = ModerationRun.objects.filter(kind="research", source_act_id=act.pk).count()
        return out


    ALL = ["same_person_twice", "both_participants", "four_at_once"]
    results = {name: run(name) for name in ALL}
    print("RESULT " + json.dumps(results))
    '''
)


def _env(db_path, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "DJANGO_DB_PATH", "DJANGO_ENV")}
    env["DJANGO_DB_PATH"] = str(db_path)
    env.update(extra)
    return env


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    """Run every race scenario once, in one subprocess, each on its own users/topic/act of one scratch SQLite file."""
    folder = tmp_path_factory.mktemp("fsvc_research_race")
    db_path = folder / "race.sqlite3"
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT], cwd=REPO, env=_env(db_path, MIGRATE_ONLY="1"),
        capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT], cwd=REPO, env=_env(db_path), capture_output=True, text=True, timeout=900
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    line = next(row for row in proc.stdout.splitlines() if row.startswith("RESULT "))
    return json.loads(line[len("RESULT "):])


def no_errors(out):
    errors = [r for r in out["results"] if "error" in r]
    assert not errors, errors


def test_two_concurrent_clicks_by_the_same_participant_create_exactly_one_research_run(outcomes):
    out = outcomes["same_person_twice"]
    no_errors(out)
    assert out["runs"] == 1, out
    assert all(r["status_code"] == 200 for r in out["results"]), out["results"]


def test_two_concurrent_clicks_by_different_participants_create_exactly_one_research_run(outcomes):
    """Either participant may click first; the second (whoever it is) gets the same run's state, not an error."""
    out = outcomes["both_participants"]
    no_errors(out)
    assert out["runs"] == 1, out
    assert all(r["status_code"] == 200 for r in out["results"]), out["results"]


def test_four_simultaneous_clicks_still_create_exactly_one_research_run(outcomes):
    out = outcomes["four_at_once"]
    no_errors(out)
    assert out["runs"] == 1, out
    assert all(r["status_code"] == 200 for r in out["results"]), out["results"]
