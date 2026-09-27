"""Issue.time_sensitive (brief item 3, step20a): the column, its default, and migration 0006's forward/reverse behaviour.

`time_sensitive` is copied straight from the Master's `MasterIssue.time_sensitive` (see the schemas/pipeline tests for
that side of the contract). This file covers only the model column and its migration: not nullable, defaults to
False, present after a fresh migrate, absent again after reversing 0006, and restored by migrating forward again.
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
def test_issue_has_a_time_sensitive_field():
    from moderation.models import Issue

    names = {f.name for f in Issue._meta.get_fields()}
    assert "time_sensitive" in names


def test_time_sensitive_is_a_plain_not_null_boolean_column():
    from moderation.models import Issue

    field = Issue._meta.get_field("time_sensitive")
    assert isinstance(field, models.BooleanField)
    assert field.null is False
    assert field.blank is False
    assert field.default is False


def test_an_issue_defaults_to_time_sensitive_false_when_not_set(world):
    issue = kit.make_issue(world.run, world.first)  # kit's factory does not pass time_sensitive
    issue.refresh_from_db()
    assert issue.time_sensitive is False


def test_time_sensitive_round_trips_both_values(world):
    false_issue = kit.make_issue(world.run, world.first, local_id="ts-false", time_sensitive=False)
    true_issue = kit.make_issue(world.run, world.first, local_id="ts-true", time_sensitive=True)
    false_issue.refresh_from_db()
    true_issue.refresh_from_db()
    assert (false_issue.time_sensitive, true_issue.time_sensitive) == (False, True)


def test_time_sensitive_survives_bulk_create_default(world):
    from moderation.models import Issue

    Issue.objects.bulk_create([kit.unsaved_issue(world.run, world.first, local_id="ts-bulk")])
    assert Issue.objects.get(local_id="ts-bulk").time_sensitive is False


# --- migration 0006: applies cleanly, depends on 0005, and reverses -----------------------------------------------------
def test_migration_0006_exists_and_depends_on_0005():
    executor, name = load_migration("0006")
    migration = executor.loader.disk_migrations[("moderation", name)]
    assert any(app == "moderation" and n.startswith("0005") for app, n in migration.dependencies)


def test_the_column_is_present_at_the_leaf_migration():
    assert "time_sensitive" in columns()


@pytest.mark.django_db(transaction=True)
def test_migrating_back_past_0006_drops_the_column_and_forward_restores_it():
    from django.db.migrations.executor import MigrationExecutor

    executor, name_0006 = load_migration("0006")
    _, name_0005 = load_migration("0005")
    try:
        executor.migrate([("moderation", name_0005)])
        assert "time_sensitive" not in columns()
        executor = MigrationExecutor(connection)
        executor.migrate([("moderation", name_0006)])
        assert "time_sensitive" in columns()
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    assert "time_sensitive" in columns()


@pytest.fixture
def world():
    conv, parts = kit.make_conversation()
    first = kit.user_msg(conv, parts, "A", "The moon is made of green cheese, everybody knows that.")
    trigger = kit.user_msg(conv, parts, "B", "That is simply not true.", in_reply_to=first)
    run = kit.make_run(trigger)
    return type("World", (), dict(conv=conv, parts=parts, first=first, trigger=trigger, run=run))
