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
