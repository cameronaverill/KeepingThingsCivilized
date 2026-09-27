"""The migration that adds ModerationRun's "research" kind (docs/step20b_brief.md, item 1): a new migration after
0007 that adds `source_act_id`/`requested_by_id` columns and the `moderationrun_one_research_per_act` partial unique
index. Follows the "addition" idiom already used in this project (tests/evaluation_models/test_evalmodels_fk_migration.py)
rather than the rename idiom (tests/models_moderation/test_modmodels_needs_verification_rename_migration.py), since
this migration only adds columns/a constraint -- it renames nothing and needs no data transform.

Written from the brief's contract, not from any coding agent's diff. Until Group A's migration lands, every test
here is expected to fail cleanly (StopIteration from `load_migration` finding no "0008"-prefixed migration, or the
new columns/index never appearing) -- expected, not a bug in this file.
"""
import modmodels_testkit as kit
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

TABLE = "moderation_moderationrun"
UNIQUE_INDEX = "moderationrun_one_research_per_act"

pytestmark = pytest.mark.django_db(transaction=True)


def load_migration(prefix):
    executor = MigrationExecutor(connection)
    name = next(n for (app, n) in executor.loader.disk_migrations if app == "moderation" and n.startswith(prefix))
    return executor, name


def migrate_to(name):
    MigrationExecutor(connection).migrate([("moderation", name)])


def restore():
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def columns(table=TABLE):
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, table)}


def sqlite_index_exists(name):
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND name = %s", [name])
        return cursor.fetchone() is not None


# --- the migration exists, is singular, and builds on 0007 -----------------------------------------------------------
def test_a_single_new_migration_exists_after_0007_and_depends_on_it():
    executor, name = load_migration("0008")
    migration = executor.loader.disk_migrations[("moderation", name)]
    assert any(app == "moderation" and n.startswith("0007") for app, n in migration.dependencies)
    names_0008 = [n for (app, n) in executor.loader.disk_migrations if app == "moderation" and n.startswith("0008")]
    assert len(names_0008) == 1


def test_makemigrations_check_is_clean_once_the_migration_is_written():
    import io

    from django.core.management import call_command

    call_command("makemigrations", "--check", "--dry-run", stdout=io.StringIO(), stderr=io.StringIO())


# --- forward application on a fresh database adds exactly the new shape ------------------------------------------------
def test_migrating_forward_from_0007_adds_the_new_columns_and_the_unique_index():
    try:
        _, name_0007 = load_migration("0007")
        migrate_to(name_0007)
        before = columns()
        assert "source_act_id" not in before
        assert "requested_by_id" not in before
        assert not sqlite_index_exists(UNIQUE_INDEX)

        _, name_0008 = load_migration("0008")
        migrate_to(name_0008)
        after = columns()
        assert "source_act_id" in after
        assert "requested_by_id" in after
        assert sqlite_index_exists(UNIQUE_INDEX)
    finally:
        restore()


# --- reversible: backward drops exactly what forward added, forward again restores it -----------------------------------
def test_the_migration_reverses_cleanly_and_can_be_reapplied():
    try:
        _, name_0007 = load_migration("0007")
        _, name_0008 = load_migration("0008")

        migrate_to(name_0008)
        assert "source_act_id" in columns()
        assert sqlite_index_exists(UNIQUE_INDEX)

        migrate_to(name_0007)
        cols = columns()
        assert "source_act_id" not in cols
        assert "requested_by_id" not in cols
        assert not sqlite_index_exists(UNIQUE_INDEX)

        migrate_to(name_0008)
        cols = columns()
        assert "source_act_id" in cols
        assert "requested_by_id" in cols
        assert sqlite_index_exists(UNIQUE_INDEX)
    finally:
        restore()


def test_an_empty_database_migrates_both_ways_repeatedly():
    try:
        _, name_0007 = load_migration("0007")
        _, name_0008 = load_migration("0008")
        for _ in range(2):
            migrate_to(name_0007)
            migrate_to(name_0008)
    finally:
        restore()


# --- the constraint is real once at the leaf, exercised through the ORM at the current schema --------------------------
def test_the_unique_constraint_is_enforced_by_the_database_after_migrating(world):
    """A thin end-to-end check that the migrated-in constraint actually behaves like item 1's model tests assume."""
    from django.db import IntegrityError, transaction

    from moderation.models import InterventionAct, ModerationRun

    act = InterventionAct.objects.create(
        run=world.run, order=1, act_type="offer_research", tone="neutral", text="Could a source be given?",
        addressee="all", subject="none",
    )  # fmt: skip
    ModerationRun.objects.create(
        conversation=world.conv, trigger_message=world.first, snapshot_seq=world.first.seq_no,
        kind="research", source_act=act,
    )  # fmt: skip
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.create(
            conversation=world.conv, trigger_message=world.second, snapshot_seq=world.second.seq_no,
            kind="research", source_act=act,
        )  # fmt: skip


@pytest.fixture
def world():
    conv, parts, first, reply, second = kit.scenario()
    run = kit.make_run(first, posted_message=reply, status="done", decision="intervene")
    return type("World", (), dict(conv=conv, parts=parts, first=first, reply=reply, second=second, run=run))
