"""Issue.needs_verification (step20a revision, docs/step20a_revision_brief.md): the renamed column, its field
definition, and the rename migration's own mechanics (the migration whose number is the next integer after
0006_issue_time_sensitive.py).

This file is deliberately separate from tests/models_moderation/test_modmodels_needs_verification.py (the renamed
test_modmodels_time_sensitive.py, owned by the coding agent doing the models/migration rename) so the two agents
never edit the same file. This file covers:
  - the field itself: still a plain, not-null, default-False BooleanField, now named needs_verification.
  - the rename migration applies cleanly and depends on 0006.
  - the migration is a genuine RenameField, not a drop-and-add: it does not lose data on an existing row, and
    reversing it renames the column back to time_sensitive (with the data still there) rather than dropping
    needs_verification and "restoring" an added time_sensitive column from nothing.

Until the coding agent's rename lands (models.py's field renamed and the new migration added), every test here is
expected to fail cleanly (AttributeError on Issue._meta.get_field("needs_verification"), or the migration lookup
finding nothing).
"""
import modmodels_testkit as kit
import pytest
from django.db import connection, models

pytestmark = pytest.mark.django_db

TABLE = "moderation_issue"

# The columns every raw INSERT below must supply, since Django does not add DB-level defaults for these fields
# (only Python-side defaults applied by the ORM). One name is deliberately left as a `{bool_column}` slot so the
# same statement can target the pre-rename or post-rename schema.
_BASE_COLUMNS = (
    "run_id", "local_id", "message_id", "issue_type", "dimension", "quote", "quote_start", "quote_end",
    "quote_match", "explanation", "confidence", "intensity", "validity", "rejection_reason",
)  # fmt: skip


def columns(table=TABLE):
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, table)}


def load_migration(prefix):
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    name = next(n for (app, n) in executor.loader.disk_migrations if app == "moderation" and n.startswith(prefix))
    return executor, name


def raw_insert_issue(run, message, bool_column, bool_value, local_id):
    """INSERT an Issue row by raw SQL, naming the boolean column explicitly, so the statement works whichever
    schema (pre- or post-rename) is currently applied."""
    columns_sql = ", ".join((*_BASE_COLUMNS, bool_column))
    marks = ", ".join(["%s"] * (len(_BASE_COLUMNS) + 1))
    values = [
        run.id, local_id, message.id, "unsupported_claim", "", "", None, None,
        "not_found", "because", 0.5, None, "valid", "", bool_value,
    ]  # fmt: skip
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {TABLE} ({columns_sql}) VALUES ({marks})", values)


def read_bool_column(bool_column, local_id):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {bool_column} FROM {TABLE} WHERE local_id = %s", [local_id])
        row = cursor.fetchone()
        return row[0] if row else None


# --- the field itself --------------------------------------------------------------------------------------------------
def test_issue_has_a_needs_verification_field():
    from moderation.models import Issue

    names = {f.name for f in Issue._meta.get_fields()}
    assert "needs_verification" in names
    assert "time_sensitive" not in names


def test_needs_verification_is_a_plain_not_null_boolean_column():
    from moderation.models import Issue

    field = Issue._meta.get_field("needs_verification")
    assert isinstance(field, models.BooleanField)
    assert field.null is False
    assert field.blank is False
    assert field.default is False


def test_an_issue_defaults_to_needs_verification_false_when_not_set(world):
    issue = kit.make_issue(world.run, world.first)  # kit's factory does not pass needs_verification
    issue.refresh_from_db()
    assert issue.needs_verification is False


# --- the rename migration: exists, depends on 0006, and is a genuine RenameField -----------------------------------------
def test_rename_migration_exists_and_depends_on_0006():
    executor, name = load_migration("0007")
    migration = executor.loader.disk_migrations[("moderation", name)]
    assert any(app == "moderation" and n.startswith("0006") for app, n in migration.dependencies)


def test_rename_migration_uses_renamefield_not_a_drop_and_add():
    from django.db.migrations.operations.fields import AddField, RemoveField, RenameField

    executor, name = load_migration("0007")
    migration = executor.loader.disk_migrations[("moderation", name)]
    ops_on_issue = [
        op for op in migration.operations
        if getattr(op, "model_name", None) == "issue"
        and ("time_sensitive" in (getattr(op, "name", ""), getattr(op, "old_name", ""), getattr(op, "new_name", "")))
    ]  # fmt: skip
    assert any(isinstance(op, RenameField) for op in ops_on_issue)
    assert not any(isinstance(op, (AddField, RemoveField)) for op in ops_on_issue)


def test_the_renamed_column_is_present_at_the_leaf_migration_and_old_name_is_gone():
    cols = columns()
    assert "needs_verification" in cols
    assert "time_sensitive" not in cols


# --- forward application preserves existing row data ----------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_renaming_forward_preserves_an_existing_rows_value(world):
    from django.db.migrations.executor import MigrationExecutor

    executor, name_0006 = load_migration("0006")
    _, name_0007 = load_migration("0007")
    try:
        executor.migrate([("moderation", name_0006)])
        assert "time_sensitive" in columns()
        raw_insert_issue(world.run, world.first, "time_sensitive", 1, local_id="rename-data-forward")

        executor = MigrationExecutor(connection)
        executor.migrate([("moderation", name_0007)])
        assert "needs_verification" in columns()
        assert read_bool_column("needs_verification", "rename-data-forward") == 1
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


# --- reversal renames the column back; it does not drop/re-add ------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_migrating_back_past_the_rename_restores_the_old_column_name_with_data_intact(world):
    from django.db.migrations.executor import MigrationExecutor

    executor, name_0006 = load_migration("0006")
    _, name_0007 = load_migration("0007")
    try:
        # Start from the leaf (needs_verification present) and insert through the current schema.
        executor.migrate(executor.loader.graph.leaf_nodes())
        raw_insert_issue(world.run, world.first, "needs_verification", 1, local_id="rename-data-back")
        assert "needs_verification" in columns()

        # Reversing 0007 renames the column back to time_sensitive (RenameField's actual behaviour) rather than
        # dropping needs_verification and leaving a fresh, empty time_sensitive column.
        executor = MigrationExecutor(connection)
        executor.migrate([("moderation", name_0006)])
        cols = columns()
        assert "time_sensitive" in cols
        assert "needs_verification" not in cols
        assert read_bool_column("time_sensitive", "rename-data-back") == 1

        # Forward again restores needs_verification, data intact.
        executor = MigrationExecutor(connection)
        executor.migrate([("moderation", name_0007)])
        assert "needs_verification" in columns()
        assert read_bool_column("needs_verification", "rename-data-back") == 1
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.fixture
def world():
    conv, parts = kit.make_conversation()
    first = kit.user_msg(conv, parts, "A", "The moon is made of green cheese, everybody knows that.")
    trigger = kit.user_msg(conv, parts, "B", "That is simply not true.", in_reply_to=first)
    run = kit.make_run(trigger)
    return type("World", (), dict(conv=conv, parts=parts, first=first, trigger=trigger, run=run))
