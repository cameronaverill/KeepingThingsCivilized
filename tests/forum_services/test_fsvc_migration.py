"""Forum migration 0002: the model changes it makes, the constraints it adds at the database level, that migrations are in
step with the models, and that it reverses cleanly (checked on a scratch SQLite file in a subprocess)."""
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import call_command

from fsvc_testkit import make_active, make_prop, make_user, uniq

REPO = Path(__file__).resolve().parents[2]


# --- files and state -----------------------------------------------------------------------------------------------------


def test_exactly_one_0002_migration_exists_and_depends_on_0001():
    files = sorted((REPO / "forum" / "migrations").glob("0002_*.py"))
    assert len(files) == 1, files
    import importlib

    module = importlib.import_module(f"forum.migrations.{files[0].stem}")
    assert ("forum", "0001_initial") in module.Migration.dependencies


@pytest.mark.django_db
def test_makemigrations_check_is_clean_for_forum():
    call_command("makemigrations", "forum", "--check", "--dry-run", stdout=io.StringIO(), stderr=io.StringIO())


@pytest.mark.django_db
def test_the_migration_state_matches_the_models():
    from django.apps import apps
    from django.db import connection
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.state import ProjectState

    loader = MigrationLoader(connection)
    changes = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps)).changes(
        graph=loader.graph, trim_to_apps={"forum"}
    )
    assert changes == {}


def test_the_new_topic_fields():
    from django.conf import settings
    from django.db import models

    from forum.models import Topic

    created_by = Topic._meta.get_field("created_by")
    assert created_by.null is True
    assert created_by.remote_field.model._meta.label == settings.AUTH_USER_MODEL
    assert created_by.remote_field.on_delete is models.PROTECT
    hidden = Topic._meta.get_field("hidden")
    assert isinstance(hidden, models.BooleanField) and hidden.default is False
    assert Topic._meta.get_field("title").blank is True


def test_the_new_conversation_fields():
    from django.db import models

    from forum.models import Conversation

    ended_by = Conversation._meta.get_field("ended_by")
    assert ended_by.null is True
    assert ended_by.remote_field.model._meta.label == "forum.Participant"
    assert ended_by.remote_field.on_delete is models.PROTECT
    ended_at = Conversation._meta.get_field("ended_at")
    assert isinstance(ended_at, models.DateTimeField) and ended_at.null is True


def test_the_4a_choice_checks_are_still_there():
    from forum.models import Conversation, Experiment, Message

    names = {c.name for m in (Conversation, Experiment, Message) for c in m._meta.constraints}
    assert {"forum_conversation_status_valid", "forum_conversation_source_valid", "forum_experiment_kind_valid",
            "forum_message_author_type_valid"} <= names  # fmt: skip


# --- constraints at the database level ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_two_topics_may_both_have_a_blank_title():
    from forum.models import Topic

    Topic.objects.create(title="", proposition="First proposition here")
    Topic.objects.create(title="", proposition="Second proposition here")
    assert Topic.objects.filter(title="").count() == 2


@pytest.mark.django_db
def test_two_topics_may_not_share_a_non_blank_title():
    from django.db import IntegrityError, transaction

    from forum.models import Topic

    Topic.objects.create(title="Rent control", proposition="Rents should be capped")
    with pytest.raises(IntegrityError), transaction.atomic():
        Topic.objects.create(title="Rent control", proposition="A different proposition text")


@pytest.mark.django_db
def test_a_blank_and_a_titled_topic_coexist():
    from forum.models import Topic

    Topic.objects.create(title="", proposition="Untitled proposition one")
    Topic.objects.create(title="Titled", proposition="Titled proposition two")
    Topic.objects.create(title="", proposition="Untitled proposition three")
    assert Topic.objects.count() == 3


@pytest.mark.django_db
def test_the_database_refuses_an_empty_proposition_even_through_bulk_create():
    from django.db import IntegrityError, transaction

    from forum.models import Topic

    with pytest.raises(IntegrityError), transaction.atomic():
        Topic.objects.bulk_create([Topic(title=uniq("t"), proposition="")])


@pytest.mark.django_db
def test_the_database_refuses_blanking_a_proposition_with_an_update():
    from django.db import IntegrityError, transaction

    from forum.models import Topic

    topic = make_prop("A proposition that is fine")
    with pytest.raises(IntegrityError), transaction.atomic():
        Topic.objects.filter(pk=topic.pk).update(proposition="")


@pytest.mark.django_db
def test_a_seeded_topic_has_no_creator_and_is_not_hidden():
    from forum.models import Topic

    topic = Topic.objects.create(title="Seeded", proposition="A seeded proposition", leans={"compass": {}})
    topic.refresh_from_db()
    assert topic.created_by_id is None and topic.hidden is False


@pytest.mark.django_db
def test_the_user_who_created_a_proposition_cannot_be_deleted_while_it_exists():
    from django.db.models import ProtectedError

    user = make_user()
    make_prop(created_by=user)
    with pytest.raises(ProtectedError):
        user.delete()


@pytest.mark.django_db
def test_the_participant_who_ended_a_conversation_cannot_be_deleted():
    from django.db.models import ProtectedError

    from forum.models import Conversation

    w = make_active()
    Conversation.objects.filter(pk=w.conv.pk).update(ended_by=w.a, status="closed")
    with pytest.raises(ProtectedError):
        w.a.delete()


@pytest.mark.django_db
def test_ended_by_and_ended_at_default_to_null():
    w = make_active()
    w.conv.refresh_from_db()
    assert w.conv.ended_by_id is None and w.conv.ended_at is None


# --- reversibility ---------------------------------------------------------------------------------------------------------


SCRIPT = r"""
import json, os, sqlite3, sys
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
import django
django.setup()
from django.core.management import call_command
from django.db import connections

db = os.environ["DJANGO_DB_PATH"]
out = {}


def columns(table):
    conn = sqlite3.connect(db)
    try:
        return sorted(row[1] for row in conn.execute(f"pragma table_info({table})"))
    finally:
        conn.close()


def insert_topic(title, proposition):
    conn = sqlite3.connect(db)
    try:
        names = {row[1] for row in conn.execute("pragma table_info(forum_topic)")}
        cols = ["title", "description", "proposition", "leans", "created_at"]
        vals = [title, "", proposition, "{}", "2026-03-10 12:00:00"]
        if "hidden" in names:
            cols.append("hidden")
            vals.append(0)
        if "opposing_position" in names:
            cols.append("opposing_position")
            vals.append("")
        conn.execute(f"insert into forum_topic ({', '.join(cols)}) values ({', '.join('?' * len(cols))})", vals)
        conn.commit()
        return "ok"
    except sqlite3.IntegrityError as exc:
        return "refused"
    finally:
        conn.close()


def migrate(*args):
    call_command("migrate", *args, verbosity=0, interactive=False)
    connections.close_all()


migrate()
out["forward_topic_cols"] = columns("forum_topic")
out["forward_conv_cols"] = columns("forum_conversation")
out["forward_blank_1"] = insert_topic("", "First proposition here")
out["forward_blank_2"] = insert_topic("", "Second proposition here")
out["forward_empty_proposition"] = insert_topic("titled", "")
conn = sqlite3.connect(db)
conn.execute("update forum_topic set title = 'first' where proposition = 'First proposition here'")
conn.execute("update forum_topic set title = 'second' where proposition = 'Second proposition here'")
conn.commit()
conn.close()

migrate("forum", "0001")
out["back_topic_cols"] = columns("forum_topic")
out["back_conv_cols"] = columns("forum_conversation")
out["back_duplicate_title"] = insert_topic("first", "Yet another proposition")
out["back_empty_proposition"] = insert_topic("third", "")

conn = sqlite3.connect(db)
conn.execute("update forum_topic set proposition = 'Now filled in' where proposition = ''")
conn.commit()
conn.close()
migrate("forum")
out["again_topic_cols"] = columns("forum_topic")
out["again_blank_1"] = insert_topic("", "Blank titled one")
out["again_blank_2"] = insert_topic("", "Blank titled two")
out["again_empty_proposition"] = insert_topic("titled again", "")
print("RESULT " + json.dumps(out))
"""


def test_the_migration_reverses_and_reapplies_on_a_scratch_database(tmp_path):
    import json

    db = tmp_path / "reverse.sqlite3"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "DJANGO_DB_PATH", "DJANGO_ENV")}
    env["DJANGO_DB_PATH"] = str(db)
    proc = subprocess.run([sys.executable, "-c", SCRIPT], cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))[len("RESULT "):])

    # forward: new columns, blank titles allowed twice, an empty proposition refused by the database
    assert {"created_by_id", "hidden"} <= set(out["forward_topic_cols"])
    assert {"ended_by_id", "ended_at"} <= set(out["forward_conv_cols"])
    assert (out["forward_blank_1"], out["forward_blank_2"]) == ("ok", "ok")
    assert out["forward_empty_proposition"] == "refused"
    # back to 0001: the columns are gone, the unconditional unique title is back, the empty check is gone
    assert not {"created_by_id", "hidden"} & set(out["back_topic_cols"])
    assert not {"ended_by_id", "ended_at"} & set(out["back_conv_cols"])
    assert out["back_duplicate_title"] == "refused"
    assert out["back_empty_proposition"] == "ok"
    # forward again after a reverse works and behaves as before
    assert {"created_by_id", "hidden"} <= set(out["again_topic_cols"])
    assert (out["again_blank_1"], out["again_blank_2"]) == ("ok", "ok")
    assert out["again_empty_proposition"] == "refused"


# --- migration 0003 (step 7c: sides and opposing positions) ---------------------------------------------------------------------

SCRIPT_0003 = r"""
import json, os, sqlite3
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
import django
django.setup()
from django.core.management import call_command
from django.db import connections

db = os.environ["DJANGO_DB_PATH"]
out = {}


def columns(table):
    conn = sqlite3.connect(db)
    try:
        return sorted(row[1] for row in conn.execute(f"pragma table_info({table})"))
    finally:
        conn.close()


def sql(statement, *args):
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(statement, args).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def attempt(statement, *args):
    try:
        sql(statement, *args)
        return "ok"
    except sqlite3.IntegrityError:
        return "refused"


def migrate(*args):
    call_command("migrate", *args, verbosity=0, interactive=False)
    connections.close_all()


migrate()
from django.contrib.auth import get_user_model
from forum.models import Conversation, Message, Participant, Topic

ua = get_user_model().objects.create_user(username="migrator1", email="migrator1@mailbox.example")
ub = get_user_model().objects.create_user(username="migrator2", email="migrator2@mailbox.example")
topic = Topic.objects.create(title="", proposition="A claim that predates the switch", opposing_position="Its opposite")
conv = Conversation.objects.create(topic=topic, status="active", label_seed=1)
pa = Participant.objects.create(conversation=conv, user=ua, label="A", join_order=1, side="pro")
pb = Participant.objects.create(conversation=conv, user=ub, label="B", join_order=2, side="con")
Message.objects.create(conversation=conv, author_type="user", participant=pa, content="an old message")
waiting = Conversation.objects.create(topic=topic, status="open")
Participant.objects.create(conversation=waiting, user=ua, label="A", join_order=1)
connections.close_all()
out["forward_cols"] = [columns("forum_participant"), columns("forum_topic")]

migrate("forum", "0002")
out["back_participant_cols"] = columns("forum_participant")
out["back_topic_cols"] = columns("forum_topic")
out["back_rows"] = [
    sql("select count(*) from forum_participant")[0][0],
    sql("select count(*) from forum_topic")[0][0],
    sql("select count(*) from forum_message")[0][0],
    sql("select count(*) from forum_conversation")[0][0],
]
# a database at 0002 accepts two participants with no side and cannot store a side
out["back_two_in_one_conversation_ok"] = attempt(
    "insert into forum_participant (conversation_id, user_id, label, join_order, joined_at) "
    "values (?, ?, 'C', 3, '2026-03-10 12:00:00')", waiting.pk, ub.pk)

migrate("forum")
out["again_participant_cols"] = columns("forum_participant")
out["again_topic_cols"] = columns("forum_topic")
out["again_sides"] = [r[0] for r in sql("select side from forum_participant order by id")]
out["again_opposing"] = [r[0] for r in sql("select opposing_position from forum_topic")]
out["again_rows"] = [
    sql("select count(*) from forum_participant")[0][0],
    sql("select count(*) from forum_topic")[0][0],
]
# the constraints exist again and the old rows (blank sides, two in one conversation) survived the forward step
c2 = Conversation.objects.create(topic=topic, status="active")
connections.close_all()
ins = ("insert into forum_participant (conversation_id, user_id, label, join_order, joined_at, side) "
       "values (?, ?, ?, ?, '2026-03-10 12:00:00', ?)")
out["again_pro"] = attempt(ins, c2.pk, ua.pk, "A", 1, "pro")
out["again_second_pro"] = attempt(ins, c2.pk, ub.pk, "B", 2, "pro")
out["again_con"] = attempt(ins, c2.pk, ub.pk, "B", 2, "con")
c3 = Conversation.objects.create(topic=topic, status="active")
connections.close_all()
out["again_bad_side"] = attempt(ins, c3.pk, ua.pk, "A", 1, "left")
out["again_two_blank"] = [attempt(ins, c3.pk, ua.pk, "A", 1, ""), attempt(ins, c3.pk, ub.pk, "B", 2, "")]
print("RESULT " + json.dumps(out))
"""


def test_migration_0003_reverses_and_reapplies_with_existing_rows(tmp_path):
    import json

    db = tmp_path / "m0003.sqlite3"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "DJANGO_DB_PATH", "DJANGO_ENV")}
    env["DJANGO_DB_PATH"] = str(db)
    proc = subprocess.run([sys.executable, "-c", SCRIPT_0003], cwd=REPO, env=env, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))[len("RESULT "):])

    assert "side" in out["forward_cols"][0] and "opposing_position" in out["forward_cols"][1]
    # reverse: the columns are gone, every row is still there
    assert "side" not in out["back_participant_cols"] and "opposing_position" not in out["back_topic_cols"]
    assert out["back_rows"] == [3, 1, 1, 2]
    assert out["back_two_in_one_conversation_ok"] == "ok"
    # forward again: the columns are back, the rows survived, every existing side and opposing position is blank
    assert "side" in out["again_participant_cols"] and "opposing_position" in out["again_topic_cols"]
    assert out["again_rows"] == [4, 1]
    assert out["again_sides"] == [""] * 4
    assert out["again_opposing"] == [""]
    # the constraints are back
    assert out["again_pro"] == "ok"
    assert out["again_second_pro"] == "refused"
    assert out["again_con"] == "ok"
    assert out["again_bad_side"] == "refused"
    assert out["again_two_blank"] == ["ok", "ok"]


def test_the_0003_migration_file_exists_and_follows_0002():
    files = sorted((REPO / "forum" / "migrations").glob("0003_*.py"))
    assert len(files) == 1, files
    import importlib

    module = importlib.import_module(f"forum.migrations.{files[0].stem}")
    assert any(dep[0] == "forum" and dep[1].startswith("0002_") for dep in module.Migration.dependencies)


# --- migration 0004 (step 7c revision 5: forum.Block) -----------------------------------------------------------------------------

SCRIPT_0004 = r"""
import json, os, sqlite3
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
import django
django.setup()
from django.core.management import call_command
from django.db import connections

db = os.environ["DJANGO_DB_PATH"]
out = {}


def tables():
    conn = sqlite3.connect(db)
    try:
        return sorted(r[0] for r in conn.execute("select name from sqlite_master where type='table'"))
    finally:
        conn.close()


def count(table):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"select count(*) from {table}").fetchone()[0]
    finally:
        conn.close()


def attempt(statement, *args):
    conn = sqlite3.connect(db)
    try:
        conn.execute(statement, args)
        conn.commit()
        return "ok"
    except sqlite3.IntegrityError:
        return "refused"
    finally:
        conn.close()


def migrate(*args):
    call_command("migrate", *args, verbosity=0, interactive=False)
    connections.close_all()


migrate()
from django.contrib.auth import get_user_model
from forum.models import Block, Conversation, Participant, Topic

users = [get_user_model().objects.create_user(username=f"blocker{i}", email=f"blocker{i}@mailbox.example") for i in range(3)]
topic = Topic.objects.create(title="", proposition="A claim that predates blocks")
conv = Conversation.objects.create(topic=topic, status="active", label_seed=1)
Participant.objects.create(conversation=conv, user=users[0], label="A", join_order=1, side="pro")
Participant.objects.create(conversation=conv, user=users[1], label="B", join_order=2, side="con")
Block.objects.create(blocker=users[0], blocked=users[1])
connections.close_all()
out["forward_has_table"] = "forum_block" in tables()
out["forward_rows"] = count("forum_block")

migrate("forum", "0003")
out["back_has_table"] = "forum_block" in tables()
out["back_other_rows"] = [count("forum_participant"), count("forum_conversation"), count("forum_topic")]

migrate("forum")
out["again_has_table"] = "forum_block" in tables()
out["again_rows"] = count("forum_block")
out["again_other_rows"] = [count("forum_participant"), count("forum_conversation"), count("forum_topic")]
ins = "insert into forum_block (blocker_id, blocked_id, created_at) values (?, ?, '2026-03-10 12:00:00')"
ids = [u.pk for u in users]
out["ins_pair"] = attempt(ins, ids[0], ids[1])
out["ins_duplicate"] = attempt(ins, ids[0], ids[1])
out["ins_reverse"] = attempt(ins, ids[1], ids[0])
out["ins_self"] = attempt(ins, ids[2], ids[2])
out["ins_other"] = attempt(ins, ids[0], ids[2])
print("RESULT " + json.dumps(out))
"""


def test_the_0004_migration_file_exists_and_follows_0003():
    files = sorted((REPO / "forum" / "migrations").glob("0004_*.py"))
    assert len(files) == 1, files
    import importlib

    module = importlib.import_module(f"forum.migrations.{files[0].stem}")
    assert any(dep[0] == "forum" and dep[1].startswith("0003_") for dep in module.Migration.dependencies)


def test_migration_0004_reverses_and_reapplies_and_keeps_its_constraints(tmp_path):
    import json

    db = tmp_path / "m0004.sqlite3"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "DJANGO_DB_PATH", "DJANGO_ENV")}
    env["DJANGO_DB_PATH"] = str(db)
    proc = subprocess.run([sys.executable, "-c", SCRIPT_0004], cwd=REPO, env=env, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))[len("RESULT "):])

    assert out["forward_has_table"] is True and out["forward_rows"] == 1
    # reverse: the table goes, everything else stays
    assert out["back_has_table"] is False
    assert out["back_other_rows"] == [2, 1, 1]
    # forward again: an empty table, the other rows intact
    assert out["again_has_table"] is True and out["again_rows"] == 0
    assert out["again_other_rows"] == [2, 1, 1]
    # constraints at the database
    assert out["ins_pair"] == "ok"
    assert out["ins_duplicate"] == "refused"
    assert out["ins_reverse"] == "ok"
    assert out["ins_self"] == "refused"
    assert out["ins_other"] == "ok"
