"""The no-self-reply rule at the database layer (plan section 2 layer 2, brief decision 4) and the posted-message trigger.
Raw SQL, bulk_create and queryset updates all bypass save(); only the SQLite triggers can stop them. A violated trigger
must surface as django.db.utils.IntegrityError with a message that says what was wrong. Also: the migration's reverse SQL."""
import modmodels_testkit as kit
import pytest
import sqlparse
from django.db import IntegrityError, connection, transaction

pytestmark = pytest.mark.django_db

RUN_TABLE = "moderation_moderationrun"


@pytest.fixture
def world():
    conv, parts, first, reply, second = kit.scenario()
    return type("World", (), dict(conv=conv, parts=parts, first=first, reply=reply, second=second))


def run_ids():
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT id FROM {RUN_TABLE} ORDER BY id")
        return [row[0] for row in cursor.fetchall()]


def column(pk, name):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {name} FROM {RUN_TABLE} WHERE id = %s", [pk])
        return cursor.fetchone()[0]


# --- triggers exist -----------------------------------------------------------------------------------------------------
def test_the_migration_created_insert_and_update_triggers_on_the_run_table():
    found = kit.triggers(RUN_TABLE)
    assert len(found) >= 2
    sqls = [row[2].upper() for row in found]
    assert any("BEFORE INSERT" in s for s in sqls)
    assert any("BEFORE UPDATE" in s and "TRIGGER_MESSAGE_ID" in s for s in sqls)
    assert any("BEFORE UPDATE" in s and "POSTED_MESSAGE_ID" in s for s in sqls)
    assert all("RAISE" in s and "ABORT" in s for s in sqls)


# --- (1) the trigger message must be a user message ------------------------------------------------------------------------
def test_raw_insert_with_a_user_trigger_works(world):
    from moderation.models import ModerationRun

    pk = kit.raw_insert(kit.unsaved_run(world.first))
    run = ModerationRun.objects.get(pk=pk)
    assert run.trigger_message_id == world.first.pk and run.kind == "live"


def test_raw_insert_with_a_moderator_trigger_is_refused(world):
    with pytest.raises(IntegrityError), transaction.atomic():
        kit.raw_insert(kit.unsaved_run(world.reply, snapshot_seq=world.reply.seq_no))
    assert run_ids() == []


def test_raw_insert_of_a_replay_with_a_moderator_trigger_is_refused(world):
    live = kit.make_run(world.first)
    with pytest.raises(IntegrityError), transaction.atomic():
        kit.raw_insert(kit.unsaved_run(world.reply, kind="replay", replay_of=live, snapshot_seq=world.reply.seq_no))
    assert run_ids() == [live.pk]


def test_the_insert_trigger_error_text_is_clear(world):
    with pytest.raises(IntegrityError) as caught, transaction.atomic():
        kit.raw_insert(kit.unsaved_run(world.reply, snapshot_seq=world.reply.seq_no))
    text = str(caught.value).lower()
    assert "trigger" in text and "user" in text
    assert "message" in text


def test_raw_update_of_the_trigger_to_a_moderator_message_is_refused(world):
    run = kit.make_run(world.first)
    with pytest.raises(IntegrityError) as caught, transaction.atomic():
        kit.raw_update(RUN_TABLE, run.pk, trigger_message_id=world.reply.pk)
    text = str(caught.value).lower()
    assert "trigger" in text and "user" in text
    assert column(run.pk, "trigger_message_id") == world.first.pk


def test_raw_update_of_the_trigger_to_another_user_message_works(world):
    run = kit.make_run(world.first)
    kit.raw_update(RUN_TABLE, run.pk, trigger_message_id=world.second.pk)
    assert column(run.pk, "trigger_message_id") == world.second.pk


def test_raw_update_of_other_columns_is_not_blocked(world):
    run = kit.make_run(world.first)
    kit.raw_update(RUN_TABLE, run.pk, status="running", attempts=1)
    assert column(run.pk, "status") == "running"


def test_bulk_create_cannot_bypass_the_trigger(world):
    from moderation.models import ModerationRun

    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.bulk_create([kit.unsaved_run(world.reply, snapshot_seq=world.reply.seq_no)])
    assert run_ids() == []


def test_queryset_update_cannot_bypass_the_trigger(world):
    from moderation.models import ModerationRun

    run = kit.make_run(world.first)
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.filter(pk=run.pk).update(trigger_message=world.reply)
    assert column(run.pk, "trigger_message_id") == world.first.pk


def test_bulk_update_cannot_bypass_the_trigger(world):
    from moderation.models import ModerationRun

    run = kit.make_run(world.first)
    run.trigger_message = world.reply
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.bulk_update([run], ["trigger_message"])
    assert column(run.pk, "trigger_message_id") == world.first.pk


def test_one_bad_row_in_a_bulk_create_aborts_the_whole_statement(world):
    from moderation.models import ModerationRun

    good = kit.unsaved_run(world.first)
    bad = kit.unsaved_run(world.reply, snapshot_seq=world.reply.seq_no)
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.bulk_create([good, bad])
    assert run_ids() == []


def test_the_message_layer_and_the_database_layer_both_refuse_the_same_thing(world):
    """Two independent layers: model validation says ValidationError, the trigger says IntegrityError."""
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        kit.make_run(world.reply)
    with pytest.raises(IntegrityError), transaction.atomic():
        kit.raw_insert(kit.unsaved_run(world.reply, snapshot_seq=world.reply.seq_no))


def test_a_user_authored_trigger_works_through_every_path(world):
    from moderation.models import ModerationRun

    kit.make_run(world.first)
    ModerationRun.objects.bulk_create([kit.unsaved_run(world.second)])
    assert ModerationRun.objects.count() == 2


# --- (2) posted message must be a moderator message -------------------------------------------------------------------------
def test_raw_insert_with_a_user_posted_message_is_refused(world):
    with pytest.raises(IntegrityError) as caught, transaction.atomic():
        kit.raw_insert(kit.unsaved_run(world.first, posted_message=world.second))
    text = str(caught.value).lower()
    assert "posted" in text and "moderator" in text
    assert run_ids() == []


def test_raw_insert_with_a_moderator_posted_message_works(world):
    from moderation.models import ModerationRun

    pk = kit.raw_insert(kit.unsaved_run(world.first, posted_message=world.reply))
    assert ModerationRun.objects.get(pk=pk).posted_message_id == world.reply.pk


def test_raw_insert_with_no_posted_message_works(world):
    kit.raw_insert(kit.unsaved_run(world.first, posted_message=None))
    assert len(run_ids()) == 1


def test_raw_update_of_posted_message_to_a_user_message_is_refused(world):
    run = kit.make_run(world.first)
    with pytest.raises(IntegrityError) as caught, transaction.atomic():
        kit.raw_update(RUN_TABLE, run.pk, posted_message_id=world.second.pk)
    text = str(caught.value).lower()
    assert "posted" in text and "moderator" in text
    assert column(run.pk, "posted_message_id") is None


def test_raw_update_of_posted_message_to_a_moderator_message_works(world):
    run = kit.make_run(world.first)
    kit.raw_update(RUN_TABLE, run.pk, posted_message_id=world.reply.pk)
    assert column(run.pk, "posted_message_id") == world.reply.pk


def test_raw_update_of_posted_message_to_null_works(world):
    run = kit.make_run(world.first, posted_message=world.reply)
    kit.raw_update(RUN_TABLE, run.pk, posted_message_id=None)
    assert column(run.pk, "posted_message_id") is None


def test_queryset_update_and_bulk_create_cannot_post_a_user_message(world):
    from moderation.models import ModerationRun

    run = kit.make_run(world.first)
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.filter(pk=run.pk).update(posted_message=world.second)
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.bulk_create([kit.unsaved_run(world.second, posted_message=world.first)])


# --- triggers on forum_message: a referenced message cannot change its author_type -------------------------------------------------------
MESSAGE_TABLE = "forum_message"


def message_author(pk):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT author_type FROM {MESSAGE_TABLE} WHERE id = %s", [pk])
        return cursor.fetchone()[0]


def test_a_message_that_triggers_a_run_cannot_stop_being_a_user_message(world):
    from forum.models import Message

    kit.make_run(world.first)
    with pytest.raises(IntegrityError) as caught, transaction.atomic():
        kit.raw_update(MESSAGE_TABLE, world.first.pk, author_type="moderator", participant_id=None)
    assert "user" in str(caught.value).lower()
    assert message_author(world.first.pk) == "user"
    with pytest.raises(IntegrityError), transaction.atomic():
        Message.objects.filter(pk=world.first.pk).update(author_type="moderator", participant=None)
    assert message_author(world.first.pk) == "user"


def test_a_message_posted_by_a_run_cannot_stop_being_a_moderator_message(world):
    kit.make_run(world.first, posted_message=world.reply)
    with pytest.raises(IntegrityError) as caught, transaction.atomic():
        kit.raw_update(MESSAGE_TABLE, world.reply.pk, author_type="user", participant_id=world.parts["A"].pk)
    assert "moderator" in str(caught.value).lower()
    assert message_author(world.reply.pk) == "moderator"


def test_unreferenced_messages_are_not_locked_by_those_triggers(world):
    """(The other columns are changed together so the forum CHECK constraints stay satisfied.) world.second triggers no run and world.reply is posted by none: the rule only protects referenced messages."""
    kit.raw_update(MESSAGE_TABLE, world.second.pk, author_type="moderator", participant_id=None)
    assert message_author(world.second.pk) == "moderator"


def test_other_columns_of_a_referenced_message_can_still_change(world):
    kit.make_run(world.first)
    kit.raw_update(MESSAGE_TABLE, world.first.pk, content="edited text")
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT content FROM {MESSAGE_TABLE} WHERE id = %s", [world.first.pk])
        assert cursor.fetchone()[0] == "edited text"


# --- migrations: reverse SQL --------------------------------------------------------------------------------------------------
def load_migration(prefix):
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    name = next(n for (app, n) in executor.loader.disk_migrations if app == "moderation" and n.startswith(prefix))
    return executor, name


def statements(sql):
    if sql is None:
        return []
    if isinstance(sql, str):
        return [s for s in sqlparse.split(sql) if s.strip()]
    out = []
    for item in sql:
        out.extend(statements(item[0] if isinstance(item, (tuple, list)) else item))
    return out


def run_sql_operations():
    from django.db import migrations

    executor, name = load_migration("0004")
    migration = executor.loader.disk_migrations[("moderation", name)]
    return [op for op in migration.operations if isinstance(op, migrations.RunSQL)]


def test_migration_0004_depends_on_forum_and_moderation_0003():
    executor, name = load_migration("0004")
    migration = executor.loader.disk_migrations[("moderation", name)]
    deps = set(migration.dependencies)
    assert any(app == "forum" for app, _ in deps)
    assert any(app == "moderation" and n.startswith("0003") for app, n in deps)


def test_every_runsql_in_the_migration_is_reversible_and_drops_triggers():
    from django.db import migrations

    ops = run_sql_operations()
    assert ops, "the migration must create the triggers with RunSQL"
    for op in ops:
        assert op.reverse_sql not in (None, migrations.RunSQL.noop)
        assert all("DROP TRIGGER" in s.upper() for s in statements(op.reverse_sql))
        assert all("CREATE TRIGGER" in s.upper() for s in statements(op.sql))


def test_executing_the_reverse_sql_removes_the_triggers_and_the_rule_stops_applying(world):
    ops = run_sql_operations()
    before = kit.moderation_triggers()
    assert len(before) >= 6
    assert {t[1] for t in before} == {RUN_TABLE, "forum_message"}
    for op in ops:
        for stmt in statements(op.reverse_sql):
            with connection.cursor() as cursor:
                cursor.execute(stmt)
    assert kit.moderation_triggers() == []  # including those on forum_message, which no table drop would remove
    # with the triggers gone, a raw INSERT of a moderator trigger goes through: proves the triggers were what refused it
    kit.raw_insert(kit.unsaved_run(world.reply, snapshot_seq=world.reply.seq_no))
    # and re-applying the forward SQL puts every trigger back
    kit.raw_update(RUN_TABLE, run_ids()[0], status="failed")
    for op in ops:
        for stmt in statements(op.sql):
            with connection.cursor() as cursor:
                cursor.execute(stmt)
    assert sorted(r[0] for r in kit.moderation_triggers()) == sorted(r[0] for r in before)
    with pytest.raises(IntegrityError), transaction.atomic():
        kit.raw_insert(kit.unsaved_run(world.reply, snapshot_seq=world.reply.seq_no))


@pytest.mark.django_db(transaction=True)
def test_migrating_back_to_0003_drops_triggers_and_tables_and_forward_restores_them():
    from django.db.migrations.executor import MigrationExecutor

    executor, name_0004 = load_migration("0004")
    _, name_0003 = load_migration("0003")
    try:
        executor.migrate([("moderation", name_0003)])
        tables = set(connection.introspection.table_names())
        assert not {t for t in tables if t.startswith("moderation_") and t not in ("moderation_llmcall", "moderation_guardstate")}
        assert kit.moderation_triggers() == []  # also the ones on forum_message, which dropping a table would not remove
        executor = MigrationExecutor(connection)
        executor.migrate([("moderation", name_0004)])
        assert len(kit.triggers(RUN_TABLE)) >= 2
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    assert len(kit.triggers(RUN_TABLE)) >= 2
