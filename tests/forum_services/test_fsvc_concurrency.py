"""Races on a real file-based SQLite database (WAL, IMMEDIATE transactions): two simultaneous requests cannot both win.

Each scenario runs in a subprocess with its own database file, real threads and a barrier so the callers start together
(no sleeps). pytest-django's in-memory test database and its wrapping transaction are not involved."""
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
    from datetime import timedelta
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
    from django.utils import timezone
    from forum import services
    from forum.models import Conversation, Message, Participant, Topic
    from moderation.models import ModerationRun

    settings.MODERATION_RUN_MODE = "worker"
    DEFAULT_GAP = settings.MIN_SECONDS_BETWEEN_MESSAGES
    DEFAULT_DAILY = settings.MAX_PROPOSITIONS_PER_USER_PER_DAY
    counter = [0]


    def user():
        counter[0] += 1
        name = f"racer{counter[0]:03d}"
        return get_user_model().objects.create_user(username=name, email=f"{name}@mailbox.example")


    def topic():
        counter[0] += 1
        return Topic.objects.create(title="", proposition=f"Racing proposition number {counter[0]}")


    def parallel(*calls):
        barrier = threading.Barrier(len(calls))
        results = [None] * len(calls)

        def worker(i):
            try:
                barrier.wait(timeout=60)
                results[i] = {"ok": calls[i]()}
            except services.PostRejected as exc:
                results[i] = {"rejected": exc.code}
            except Exception as exc:  # anything else is a failure of the code under test
                results[i] = {"error": type(exc).__name__ + ": " + str(exc)[:300]}
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(calls))]
        [t.start() for t in threads]
        [t.join() for t in threads]
        return results


    def convs_of(t):
        return [
            {"id": c.pk, "status": c.status, "users": sorted(p.user_id for p in c.participants.all()),
             "labels": sorted(p.label for p in c.participants.all()), "seed": c.label_seed}
            for c in Conversation.objects.filter(topic=t).order_by("id")
        ]


    def run(scenario):
        settings.MIN_SECONDS_BETWEEN_MESSAGES = DEFAULT_GAP
        settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = DEFAULT_DAILY
        out = {}
        if scenario == "join":
            u1, u2, u3, t = user(), user(), user(), topic()
            waiting = services.enter_proposition(u1, t)
            out["results"] = parallel(lambda: services.enter_proposition(u2, t).pk, lambda: services.enter_proposition(u3, t).pk)
            out["waiting_id"] = waiting.pk
            out["users"] = [u1.pk, u2.pk, u3.pk]
            out["convs"] = convs_of(t)
        elif scenario == "fresh":
            u1, u2, t = user(), user(), topic()
            out["results"] = parallel(lambda: services.enter_proposition(u1, t).pk, lambda: services.enter_proposition(u2, t).pk)
            out["users"] = [u1.pk, u2.pk]
            out["convs"] = convs_of(t)
        elif scenario == "double":
            u1, t = user(), topic()
            out["results"] = parallel(*[(lambda: services.enter_proposition(u1, t).pk)] * 3)
            out["users"] = [u1.pk]
            out["convs"] = convs_of(t)
        elif scenario == "posts":
            settings.MIN_SECONDS_BETWEEN_MESSAGES = 0  # reset for every scenario at the top of run()
            u1, u2, t = user(), user(), topic()
            conv = services.enter_proposition(u1, t)
            services.enter_proposition(u2, t)
            calls = []
            for who in (u1, u2, u1, u2, u1, u2):
                calls.append(lambda who=who: services.post_message(who, conv, "racing message text").seq_no)
            out["results"] = parallel(*calls)
            out["seqs"] = sorted(Message.objects.filter(conversation=conv).values_list("seq_no", flat=True))
            out["runs"] = list(ModerationRun.objects.filter(conversation=conv).order_by("snapshot_seq").values_list("snapshot_seq", "status", "kind"))
        elif scenario == "same_participant":
            u1, u2, t = user(), user(), topic()
            conv = services.enter_proposition(u1, t)
            services.enter_proposition(u2, t)
            out["results"] = parallel(*[(lambda: services.post_message(u1, conv, "racing message text").seq_no)] * 3)
            out["messages"] = Message.objects.filter(conversation=conv).count()
            out["runs"] = ModerationRun.objects.filter(conversation=conv).count()
        elif scenario == "cap":
            u1, u2, t = user(), user(), topic()
            conv = services.enter_proposition(u1, t)
            services.enter_proposition(u2, t)
            parts = {p.user_id: p for p in conv.participants.all()}
            for i in range(29):
                who = u1 if i % 2 == 0 else u2
                Message.objects.create(conversation=conv, author_type="user", participant=parts[who.pk], content=f"earlier message {i}")
            Message.objects.filter(conversation=conv).update(created_at=timezone.now() - timedelta(hours=1))
            out["results"] = parallel(lambda: services.post_message(u1, conv, "the racing thirtieth").seq_no,
                                      lambda: services.post_message(u2, conv, "the other racing thirtieth").seq_no)
            out["user_messages"] = Message.objects.filter(conversation=conv, author_type="user").count()
            out["status"] = Conversation.objects.get(pk=conv.pk).status
            out["runs"] = ModerationRun.objects.filter(conversation=conv).count()
        elif scenario == "open_limit":
            u1 = user()
            for _ in range(4):
                services.enter_proposition(u1, topic())
            t5, t6 = topic(), topic()
            out["results"] = parallel(lambda: services.enter_proposition(u1, t5).pk, lambda: services.enter_proposition(u1, t6).pk)
            out["open"] = Conversation.objects.filter(participants__user=u1, status__in=["open", "active"]).count()
        elif scenario == "end":
            rounds = []
            for _ in range(12):  # a small window: repeat the race on fresh conversations
                u1, u2, t = user(), user(), topic()
                conv = services.enter_proposition(u1, t)
                services.enter_proposition(u2, t)
                results = parallel(lambda: services.end_conversation(u1, conv).pk, lambda: services.end_conversation(u2, conv).pk)
                fresh = Conversation.objects.get(pk=conv.pk)
                rounds.append({"results": results, "status": fresh.status,
                               "ended_by_user": fresh.ended_by.user_id if fresh.ended_by_id else None, "users": [u1.pk, u2.pk]})
            out["rounds"] = rounds
            out["results"] = [r for rnd in rounds for r in rnd["results"]]
        elif scenario == "props_limit":
            settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 3
            u1 = user()
            for i in range(2):
                services.create_proposition(u1, f"Racing allowance proposition {i}")
            out["results"] = parallel(*[(lambda k=k: services.create_proposition(u1, f"Racing allowance extra {k}").pk) for k in range(3)])
            out["made"] = Topic.objects.filter(created_by=u1).count()
        elif scenario == "props_duplicate":
            u1, u2, u3 = user(), user(), user()
            text = "Racing duplicate proposition text"
            out["results"] = parallel(*[(lambda who=who: services.create_proposition(who, text).pk) for who in (u1, u2, u3)])
            out["made"] = Topic.objects.filter(proposition=text).count()
        elif scenario == "post_and_end":
            u1, u2, t = user(), user(), topic()
            conv = services.enter_proposition(u1, t)
            services.enter_proposition(u2, t)
            out["results"] = parallel(lambda: services.post_message(u1, conv, "racing message text").seq_no,
                                      lambda: services.end_conversation(u2, conv).pk)
            out["status"] = Conversation.objects.get(pk=conv.pk).status
            out["messages"] = Message.objects.filter(conversation=conv).count()
            out["runs"] = ModerationRun.objects.filter(conversation=conv).count()
        return out


    ALL = ["join", "fresh", "double", "posts", "same_participant", "cap", "open_limit", "end", "post_and_end", "props_limit", "props_duplicate"]
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
    """Run every scenario once, in one subprocess (Django takes seconds to start), each on its own users, proposition
    and conversations of one scratch SQLite file."""
    folder = tmp_path_factory.mktemp("fsvc_races")
    template = folder / "template.sqlite3"
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT], cwd=REPO, env=_env(template, MIGRATE_ONLY="1"),
        capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT], cwd=REPO, env=_env(template), capture_output=True, text=True, timeout=900
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    line = next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))
    return json.loads(line[len("RESULT "):])


def no_errors(out):
    errors = [r for r in out["results"] if "error" in r]
    assert not errors, errors


# --- entering ------------------------------------------------------------------------------------------------------------


def test_two_people_entering_the_same_waiting_conversation_only_one_joins(outcomes):
    out = outcomes["join"]
    no_errors(out)
    u1, u2, u3 = out["users"]
    waiting = next(c for c in out["convs"] if c["id"] == out["waiting_id"])
    assert waiting["status"] == "active"
    assert len(waiting["users"]) == 2 and u1 in waiting["users"]
    winner = (set(waiting["users"]) - {u1}).pop()
    assert winner in (u2, u3)
    assert sorted(waiting["labels"]) == ["A", "B"] and waiting["seed"] is not None
    loser = ({u2, u3} - {winner}).pop()
    others = [c for c in out["convs"] if c["id"] != out["waiting_id"]]
    assert len(others) == 1
    assert others[0]["users"] == [loser] and others[0]["status"] == "open"
    ids = [r["ok"] for r in out["results"]]
    assert sorted(ids) == sorted([out["waiting_id"], others[0]["id"]])


def test_two_people_entering_an_empty_proposition_at_once_end_up_together(outcomes):
    """Entering is serialised, so one creates the waiting conversation and the other joins it: never two lonely ones."""
    out = outcomes["fresh"]
    no_errors(out)
    assert len(out["convs"]) == 1, out["convs"]
    (conv,) = out["convs"]
    assert conv["status"] == "active" and sorted(conv["users"]) == sorted(out["users"])
    assert [r["ok"] for r in out["results"]] == [conv["id"], conv["id"]]


def test_one_person_double_clicking_gets_one_conversation(outcomes):
    out = outcomes["double"]
    no_errors(out)
    assert len(out["convs"]) == 1 and out["convs"][0]["users"] == out["users"]
    assert {r["ok"] for r in out["results"]} == {out["convs"][0]["id"]}


def test_two_simultaneous_entries_cannot_both_take_the_last_open_slot(outcomes):
    """A person with four open conversations enters two new propositions at once: one succeeds, one is refused."""
    out = outcomes["open_limit"]
    kinds = sorted(next(iter(r)) for r in out["results"])
    assert kinds == ["ok", "rejected"], out["results"]
    assert [r["rejected"] for r in out["results"] if "rejected" in r] == ["too_many_open"]
    assert out["open"] == 5


# --- posting -------------------------------------------------------------------------------------------------------------


def test_simultaneous_posts_get_distinct_sequential_seq_nos_and_one_run_each(outcomes):
    out = outcomes["posts"]
    no_errors(out)
    assert sorted(r["ok"] for r in out["results"]) == [1, 2, 3, 4, 5, 6]
    assert out["seqs"] == [1, 2, 3, 4, 5, 6]
    assert out["runs"] == [[i, "pending", "live"] for i in range(1, 7)]


def test_the_same_person_posting_three_times_at_once_gets_exactly_one_message(outcomes):
    out = outcomes["same_participant"]
    no_errors(out)
    assert sorted(next(iter(r)) for r in out["results"]) == ["ok", "rejected", "rejected"]
    assert {r["rejected"] for r in out["results"] if "rejected" in r} == {"too_fast"}
    assert out["messages"] == 1 and out["runs"] == 1


def test_two_people_racing_for_the_thirtieth_message_only_one_gets_it(outcomes):
    out = outcomes["cap"]
    no_errors(out)
    assert sorted(next(iter(r)) for r in out["results"]) == ["ok", "rejected"]
    assert [r["ok"] for r in out["results"] if "ok" in r] == [30]
    assert [r["rejected"] for r in out["results"] if "rejected" in r] == ["closed"]
    assert out["user_messages"] == 30
    assert out["status"] == "closed"
    assert out["runs"] == 1  # only the accepted thirtieth message went through the service


# --- ending --------------------------------------------------------------------------------------------------------------


def test_two_people_ending_at_once_one_ends_it_and_the_other_is_told_it_is_closed(outcomes):
    out = outcomes["end"]
    no_errors(out)
    assert len(out["rounds"]) == 12
    for rnd in out["rounds"]:
        assert sorted(next(iter(r)) for r in rnd["results"]) == ["ok", "rejected"], rnd
        assert [r["rejected"] for r in rnd["results"] if "rejected" in r] == ["closed"]
        assert rnd["status"] == "closed"
        assert rnd["ended_by_user"] in rnd["users"]


def test_a_post_racing_an_end_either_lands_before_it_or_is_refused_never_half_saved(outcomes):
    out = outcomes["post_and_end"]
    no_errors(out)
    assert out["status"] == "closed"
    post, end = out["results"]
    assert "ok" in end
    if "ok" in post:
        assert out["messages"] == 1 and out["runs"] == 1
    else:
        assert post["rejected"] == "closed"
        assert out["messages"] == 0 and out["runs"] == 0


# --- propositions --------------------------------------------------------------------------------------------------------


def test_simultaneous_propositions_cannot_squeeze_past_the_daily_limit(outcomes):
    """Limit 3, two already made today, three more submitted at once: exactly one gets in."""
    out = outcomes["props_limit"]
    no_errors(out)
    assert sorted(next(iter(r)) for r in out["results"]) == ["ok", "rejected", "rejected"]
    assert {r["rejected"] for r in out["results"] if "rejected" in r} == {"daily_limit"}
    assert out["made"] == 3


def test_the_same_proposition_submitted_by_three_people_at_once_is_created_once(outcomes):
    out = outcomes["props_duplicate"]
    no_errors(out)
    assert sorted(next(iter(r)) for r in out["results"]) == ["ok", "rejected", "rejected"]
    assert {r["rejected"] for r in out["results"] if "rejected" in r} == {"duplicate"}
    assert out["made"] == 1
