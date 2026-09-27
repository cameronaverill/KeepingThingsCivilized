"""Moderation migration 0005 (the two preview tables): its place in the graph, that migrations are in step with the models, and that
it applies, reverses and applies again on a scratch SQLite file (in a subprocess; the development database is never touched)."""
import importlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import call_command

REPO = Path(__file__).resolve().parents[2]


def migration_files():
    return sorted((REPO / "moderation" / "migrations").glob("0005_*.py"))


def test_exactly_one_0005_migration_exists_and_depends_on_moderation_0004_and_forum_0001_only():
    files = migration_files()
    assert len(files) == 1, files
    module = importlib.import_module(f"moderation.migrations.{files[0].stem}")
    dependencies = module.Migration.dependencies
    assert ("moderation", "0004_moderation_run_issue_act") in dependencies
    assert sorted(d for d in dependencies if d[0] != "moderation") == [("forum", "0001_initial")]


def test_the_0005_migration_creates_no_table_of_another_app_and_touches_only_the_two_new_models():
    module = importlib.import_module(f"moderation.migrations.{migration_files()[0].stem}")
    operations = module.Migration.operations
    created = sorted(op.name for op in operations if op.__class__.__name__ == "CreateModel")
    assert created == ["PreviewCheck", "PreviewMode"]
    assert [op.__class__.__name__ for op in operations if op.__class__.__name__ not in ("CreateModel", "AddConstraint", "AddIndex")] == []


@pytest.mark.django_db
def test_makemigrations_check_is_clean_for_moderation():
    call_command("makemigrations", "moderation", "--check", "--dry-run", stdout=io.StringIO(), stderr=io.StringIO())


@pytest.mark.django_db
def test_the_migration_state_matches_the_moderation_models():
    from django.apps import apps
    from django.db import connection
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.state import ProjectState

    loader = MigrationLoader(connection)
    changes = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps)).changes(
        graph=loader.graph, trim_to_apps={"moderation"}
    )
    assert changes == {}


SCRIPT = r"""
import json, os, sqlite3
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
import django
django.setup()
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

db = os.environ["DJANGO_DB_PATH"]
out = {}


def sql(query, *args):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(query, args).fetchall()
    finally:
        conn.close()


def tables():
    return sorted(r[0] for r in sql("select name from sqlite_master where type='table' and name like 'moderation_preview%'"))


def columns(table):
    return sorted(r[1] for r in sql(f"pragma table_info({table})"))


def foreign_keys(table):
    return sorted((r[3], r[2], r[4]) for r in sql(f"pragma foreign_key_list({table})"))


def attempt(func):
    try:
        with transaction.atomic():
            func()
        return "ok"
    except IntegrityError:
        return "refused"


def make_world():
    from forum.models import Conversation, Message, Participant, Topic
    from moderation.models import ModerationRun

    topic = Topic.objects.create(title="fk topic", description="d", proposition="Cities should plant more trees.")
    conv = Conversation.objects.create(topic=topic, source="synthetic")
    part = Participant.objects.create(conversation=conv, label="A", join_order=1)
    message = Message.objects.create(conversation=conv, author_type="user", participant=part, content="A first point.")
    run = ModerationRun.objects.create(conversation=conv, trigger_message=message, snapshot_seq=message.seq_no, kind="live")
    return conv, part, message, run


def check_values(conv, part, **overrides):
    values = dict(
        conversation_id=conv, participant_id=part, draft_text="d", char_count=1, draft_sha256="0" * 64, snapshot_seq=0,
        mode="on", outcome="no_concern", unavailable_reason="", note_texts=[], master_output=None, intervenor_output=None,
        llm_call_ids=[], action="",
    )
    values.update(overrides)
    return values


def make_check(conv, part, **overrides):
    from moderation.models import PreviewCheck

    return attempt(lambda: PreviewCheck.objects.create(**check_values(conv, part, **overrides)))


def make_mode(conv):
    from moderation.models import PreviewMode

    return attempt(lambda: PreviewMode.objects.create(conversation_id=conv, mode="on", assigned_at=timezone.now()))


def raw_delete(table, pk):
    def run():
        with connection.cursor() as cursor:
            cursor.execute(f"delete from {table} where id = %s", [pk])
    return attempt(run)


def exercise(prefix):
    conv, part, message, run = WORLD
    out[prefix + "_ok"] = make_check(conv.pk, part.pk)
    out[prefix + "_ok_with_links"] = make_check(conv.pk, part.pk, resulting_message_id=message.pk, reused_by_run_id=run.pk)
    out[prefix + "_bad_conversation"] = make_check(conv.pk + 1000, part.pk)
    out[prefix + "_bad_participant"] = make_check(conv.pk, part.pk + 1000)
    out[prefix + "_bad_message"] = make_check(conv.pk, part.pk, resulting_message_id=message.pk + 1000)
    out[prefix + "_bad_run"] = make_check(conv.pk, part.pk, reused_by_run_id=run.pk + 1000)
    out[prefix + "_bad_outcome"] = make_check(conv.pk, part.pk, outcome="maybe")
    out[prefix + "_bad_action"] = make_check(conv.pk, part.pk, action="posted", resolved_at=timezone.now())
    out[prefix + "_resolved_without_time"] = make_check(conv.pk, part.pk, action="edited", resolved_at=None)
    out[prefix + "_mode_once"] = [make_mode(conv.pk), make_mode(conv.pk)]
    out[prefix + "_mode_bad_conversation"] = make_mode(conv.pk + 1000)
    deletes(prefix)


def deletes(prefix):
    # Each target is referenced ONLY by a preview row, so a refusal can only come from the preview foreign key; once the
    # preview row is gone the same delete works.
    from forum.models import Conversation, Message, Participant
    from moderation.models import ModerationRun, PreviewCheck, PreviewMode

    conv, part, message, run = WORLD
    extra_part = Participant.objects.create(conversation=conv, label="B", join_order=2)
    extra_message = Message.objects.create(conversation=conv, author_type="user", participant=part, content="An extra point one.")
    trigger = Message.objects.create(conversation=conv, author_type="user", participant=part, content="An extra point two.")
    extra_run = ModerationRun.objects.create(conversation=conv, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live")
    lone = Conversation.objects.create(topic=conv.topic, source="synthetic")
    cases = [
        ("participant", "forum_participant", extra_part.pk, lambda: PreviewCheck.objects.create(**check_values(conv.pk, extra_part.pk))),
        ("message", "forum_message", extra_message.pk, lambda: PreviewCheck.objects.create(**check_values(conv.pk, part.pk, resulting_message_id=extra_message.pk))),
        ("run", "moderation_moderationrun", extra_run.pk, lambda: PreviewCheck.objects.create(**check_values(conv.pk, part.pk, reused_by_run_id=extra_run.pk))),
        ("conversation_by_mode", "forum_conversation", lone.pk, lambda: PreviewMode.objects.create(conversation_id=lone.pk, mode="on", assigned_at=timezone.now())),
    ]
    for name, table, pk, make in cases:
        holder = make()
        out[f"{prefix}_protected_{name}"] = raw_delete(table, pk)
        holder.delete()
        out[f"{prefix}_freed_{name}"] = raw_delete(table, pk)


call_command("migrate", verbosity=0)
WORLD = make_world()
out["world"] = [WORLD[0].pk, WORLD[1].pk, WORLD[2].pk, WORLD[3].pk]
out["forward_tables"] = tables()
out["forward_check_cols"] = columns("moderation_previewcheck")
out["forward_mode_cols"] = columns("moderation_previewmode")
out["forward_check_fks"] = foreign_keys("moderation_previewcheck")
out["forward_mode_fks"] = foreign_keys("moderation_previewmode")
exercise("forward")
from moderation.models import PreviewCheck, PreviewMode
out["forward_check_rows"] = PreviewCheck.objects.count()
out["forward_mode_rows"] = PreviewMode.objects.count()

call_command("migrate", "moderation", "0004", verbosity=0)
out["back_tables"] = tables()
out["back_rows"] = [
    sql("select count(*) from forum_conversation")[0][0], sql("select count(*) from forum_participant")[0][0],
    sql("select count(*) from forum_message")[0][0], sql("select count(*) from moderation_moderationrun")[0][0],
]

call_command("migrate", verbosity=0)
out["again_tables"] = tables()
out["again_check_fks"] = foreign_keys("moderation_previewcheck")
out["again_mode_fks"] = foreign_keys("moderation_previewmode")
out["again_rows"] = [
    sql("select count(*) from forum_conversation")[0][0], sql("select count(*) from forum_participant")[0][0],
    sql("select count(*) from forum_message")[0][0], sql("select count(*) from moderation_moderationrun")[0][0],
    sql("select count(*) from moderation_previewcheck")[0][0], sql("select count(*) from moderation_previewmode")[0][0],
]
exercise("again")
print("RESULT " + json.dumps(out))
"""


def test_the_migration_applies_reverses_and_applies_again_on_a_scratch_database_with_existing_rows(tmp_path):
    db = tmp_path / "preview.sqlite3"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "DJANGO_DB_PATH", "DJANGO_ENV")}
    env["DJANGO_DB_PATH"] = str(db)
    proc = subprocess.run([sys.executable, "-c", SCRIPT], cwd=REPO, env=env, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(next(line for line in proc.stdout.splitlines() if line.startswith("RESULT "))[len("RESULT "):])

    both = ["moderation_previewcheck", "moderation_previewmode"]
    assert out["forward_tables"] == both
    assert {
        "conversation_id", "participant_id", "draft_text", "char_count", "draft_sha256", "snapshot_seq", "mode", "outcome",
        "unavailable_reason", "note_texts", "master_output", "intervenor_output", "llm_call_ids", "action",
        "resulting_message_id", "reused_by_run_id", "created_at", "resolved_at",
    } <= set(out["forward_check_cols"])
    assert {"conversation_id", "mode", "assigned_at"} <= set(out["forward_mode_cols"])
    fks = [
        ["conversation_id", "forum_conversation", "id"], ["participant_id", "forum_participant", "id"],
        ["resulting_message_id", "forum_message", "id"], ["reused_by_run_id", "moderation_moderationrun", "id"],
    ]
    for key in ("forward_check_fks", "again_check_fks"):
        assert out[key] == sorted(fks), key
    for key in ("forward_mode_fks", "again_mode_fks"):
        assert out[key] == [["conversation_id", "forum_conversation", "id"]], key

    for prefix in ("forward", "again"):
        assert out[prefix + "_ok"] == "ok", prefix
        assert out[prefix + "_ok_with_links"] == "ok", prefix
        for refused in ("bad_conversation", "bad_participant", "bad_message", "bad_run", "bad_outcome", "bad_action",
                        "resolved_without_time", "mode_bad_conversation", "protected_participant", "protected_message",
                        "protected_run", "protected_conversation_by_mode"):
            assert out[f"{prefix}_{refused}"] == "refused", (prefix, refused)
        for freed in ("participant", "message", "run", "conversation_by_mode"):
            assert out[f"{prefix}_freed_{freed}"] == "ok", (prefix, freed)
        assert out[prefix + "_mode_once"] == ["ok", "refused"], prefix
    # back to 0004: the two tables are gone and the rows of the other tables are untouched
    assert out["back_tables"] == []
    assert out["back_rows"] == [1, 1, 2, 1]  # conversation, participant, messages (one is the extra run's trigger), run
    # forward again with those rows in place: the tables are back and empty, the other rows are intact
    assert out["again_tables"] == both
    assert out["again_rows"] == [1, 1, 2, 1, 0, 0]
