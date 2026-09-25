"""Migration accounts/0002 (step 1 fixes, section A): adds and backfills the key columns, swaps the constraints."""
import re
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO_ROOT / "accounts" / "migrations"
FIRST = ("accounts", "0001_initial")


def second_migration_name():
    found = sorted(p.stem for p in MIGRATIONS.glob("0002_*.py"))
    assert len(found) == 1, f"expected exactly one accounts/migrations/0002_*.py, found {found}"
    return found[0]


@pytest.mark.django_db
def test_migration_0002_exists_and_follows_0001():
    name = second_migration_name()
    loader = MigrationExecutor(connection).loader
    migration = loader.get_migration("accounts", name)
    assert ("accounts", "0001_initial") in migration.dependencies
    assert loader.graph.leaf_nodes("accounts") == [("accounts", name)]


def test_migration_0001_is_left_as_it_was():
    text = (MIGRATIONS / "0001_initial.py").read_text()
    assert "accounts_user_username_ci_unique" in text and "accounts_user_email_ci_unique" in text
    assert "username_key" not in text and "email_key" not in text


def test_migration_0002_mentions_both_key_columns_and_the_old_constraints():
    text = (MIGRATIONS / f"{second_migration_name()}.py").read_text()
    for needle in ("username_key", "email_key", "accounts_user_username_ci_unique", "accounts_user_email_ci_unique"):
        assert needle in text, needle
    assert re.search(r"RunPython|RunSQL", text), "existing rows have to be backfilled"


@pytest.mark.django_db
def test_no_model_changes_are_missing_a_migration_even_after_0002(capsys):
    second_migration_name()
    call_command("makemigrations", "--check", "--dry-run")
    assert "No changes detected" in capsys.readouterr().out


@pytest.mark.django_db(transaction=True)
def test_migrating_forward_backfills_the_keys_of_existing_rows():
    name = second_migration_name()
    target = [("accounts", name)]
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([FIRST])  # 0002 is unapplied; the database is now in its step 1 shape
        old_apps = executor.loader.project_state([FIRST]).apps
        OldUser = old_apps.get_model("accounts", "User")
        assert "username_key" not in {f.name for f in OldUser._meta.get_fields()}
        # These pass the OLD case-insensitive constraints, but they need different keys from one another.
        OldUser.objects.create(username="Alice", email="Alice@Example.COM", password="x")
        OldUser.objects.create(username="bob_2", email="bob@example.org", password="x")
        OldUser.objects.create(username="Stra\u00dfe", email="s@example.net", password="x")
        OldUser.objects.create(username="carol", email="carol@example.net", password="x")

        executor = MigrationExecutor(connection)
        executor.migrate(target)
        NewUser = executor.loader.project_state(target).apps.get_model("accounts", "User")
        keys = {u.username: (u.username_key, u.email_key) for u in NewUser.objects.all()}
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    assert keys == {
        "Alice": ("alice", "alice@example.com"),
        "bob_2": ("bob_2", "bob@example.org"),
        "Stra\u00dfe": ("strasse", "s@example.net"),
        "carol": ("carol", "carol@example.net"),
    }


@pytest.mark.django_db(transaction=True)
def test_after_0002_the_old_constraints_are_gone_and_the_key_columns_are_unique():
    name = second_migration_name()
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([FIRST])
        executor = MigrationExecutor(connection)
        executor.migrate([("accounts", name)])
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "accounts_user")
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    assert "accounts_user_username_ci_unique" not in constraints
    assert "accounts_user_email_ci_unique" not in constraints
    unique = {tuple(c["columns"]) for c in constraints.values() if c["unique"]}
    assert ("username_key",) in unique and ("email_key",) in unique


@pytest.mark.django_db(transaction=True)
def test_0002_can_be_reversed_and_reapplied():
    name = second_migration_name()
    executor = MigrationExecutor(connection)
    executor.migrate([FIRST])
    executor = MigrationExecutor(connection)
    executor.migrate([("accounts", name)])
    assert ("accounts", name) in MigrationExecutor(connection).loader.applied_migrations
