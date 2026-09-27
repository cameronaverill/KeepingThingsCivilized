"""Issue.needs_verification (step20a revision, brief item 1): the column, its default, and migration 0007's
forward/reverse behaviour.

`needs_verification` is copied straight from the Master's `MasterIssue.needs_verification` (see the schemas/pipeline
tests for that side of the contract). This file covers only the model column and its migration: not nullable,
defaults to False, present after a fresh migrate, and — since migration 0007 is a `RenameField` on top of the
already-committed 0006 (which added the column under its old name `time_sensitive`) — named `time_sensitive` again
after reversing 0007 back to 0006, and `needs_verification` once more after migrating forward.
"""
import modmodels_testkit as kit
import pytest
from django.db import connection, models

pytestmark = pytest.mark.django_db

TABLE = "moderation_issue"


def columns(table=TABLE):
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, table)}


def load_migration(prefix):
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    name = next(n for (app, n) in executor.loader.disk_migrations if app == "moderation" and n.startswith(prefix))
    return executor, name


# --- the field itself --------------------------------------------------------------------------------------------------
def test_issue_has_a_needs_verification_field():
    from moderation.models import Issue

    names = {f.name for f in Issue._meta.get_fields()}
    assert "needs_verification" in names


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


def test_needs_verification_round_trips_both_values(world):
    false_issue = kit.make_issue(world.run, world.first, local_id="nv-false", needs_verification=False)
    true_issue = kit.make_issue(world.run, world.first, local_id="nv-true", needs_verification=True)
    false_issue.refresh_from_db()
    true_issue.refresh_from_db()
    assert (false_issue.needs_verification, true_issue.needs_verification) == (False, True)


def test_needs_verification_survives_bulk_create_default(world):
    from moderation.models import Issue

    Issue.objects.bulk_create([kit.unsaved_issue(world.run, world.first, local_id="nv-bulk")])
    assert Issue.objects.get(local_id="nv-bulk").needs_verification is False


# --- migration 0007: applies cleanly, depends on 0006, and reverses -----------------------------------------------------
def test_migration_0007_exists_and_depends_on_0006():
    executor, name = load_migration("0007")
    migration = executor.loader.disk_migrations[("moderation", name)]
    assert any(app == "moderation" and n.startswith("0006") for app, n in migration.dependencies)


def test_the_column_is_present_at_the_leaf_migration():
    assert "needs_verification" in columns()
    assert "time_sensitive" not in columns()


@pytest.mark.django_db(transaction=True)
def test_migrating_back_past_0007_renames_the_column_back_and_forward_restores_it():
    from django.db.migrations.executor import MigrationExecutor

    executor, name_0007 = load_migration("0007")
    _, name_0006 = load_migration("0006")
    try:
        executor.migrate([("moderation", name_0006)])
        assert "time_sensitive" in columns()
        assert "needs_verification" not in columns()
        executor = MigrationExecutor(connection)
        executor.migrate([("moderation", name_0007)])
        assert "needs_verification" in columns()
        assert "time_sensitive" not in columns()
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    assert "needs_verification" in columns()


@pytest.fixture
def world():
    conv, parts = kit.make_conversation()
    first = kit.user_msg(conv, parts, "A", "The moon is made of green cheese, everybody knows that.")
    trigger = kit.user_msg(conv, parts, "B", "That is simply not true.", in_reply_to=first)
    run = kit.make_run(trigger)
    return type("World", (), dict(conv=conv, parts=parts, first=first, trigger=trigger, run=run))
