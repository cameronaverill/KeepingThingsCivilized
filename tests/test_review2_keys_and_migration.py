"""Second independent review of the step 1 fixes: gaps found by mutation testing of accounts.keys and migration 0002.

The characters below were found by brute force: each one tells a correct NFKC(casefold(NFKC(strip))) apart from a
variant that drops one of the steps.
"""
import ast
import importlib
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from accounts.keys import normalize_key

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION = "accounts.migrations.0002_user_keys"
FIRST = ("accounts", "0001_initial")
SECOND = ("accounts", "0002_user_keys")


@pytest.mark.parametrize(
    "value, expected",
    [
        ("ᴬ", "a"),  # modifier capital A: needs the FIRST NFKC (drop it and the result stays "A")
        ("Ϲ", "σ"),  # capital lunate sigma: needs the first NFKC (drop it and you get final sigma)
        ("ͺ", " ι"),  # Greek ypogegrammeni: first NFKC turns it into space + iota
        ("ẖ", "ẖ"),  # h with line below: needs the LAST NFKC to recompose after casefold
        ("ǰ", "ǰ"),  # j with caron: same
        ("²", "2"),  # superscript two: NFKC (compatibility) but not NFC
        ("①", "1"),  # circled digit one
        ("ﬃ", "ffi"),  # ffi ligature
        ("  \tÉmile \n", "émile"),  # whitespace stripped before anything else
        ("　alice　", "alice"),  # ideographic space is whitespace too
    ],
)
def test_normalize_key_pins_each_step_of_the_formula(value, expected):
    assert normalize_key(value) == expected


def test_normalize_key_only_strips_outer_whitespace():
    assert normalize_key(" a b ") == "a b"


# --- the migration keeps its own frozen copy of the normalisation ------------------------------------------------------


def test_migration_normalisation_is_a_frozen_copy_and_matches_the_app_code_today():
    migration = importlib.import_module(MIGRATION)
    tree = ast.parse((REPO_ROOT / "accounts" / "migrations" / "0002_user_keys.py").read_text())
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "") + "." + alias.name for alias in node.names] + [node.module or ""]
        assert "accounts.keys" not in names and "accounts.keys.normalize_key" not in names, (
            "migration 0002 must not import accounts.keys: it would change when the app code changes"
        )
        assert not (isinstance(node, ast.ImportFrom) and node.level and (node.module or "").endswith("keys"))
    sample = [chr(cp) for cp in range(0x20, 0x2600)] + [chr(cp) for cp in range(0xFB00, 0xFFF0) if not 0xD800 <= cp < 0xE000]
    sample += ["  Alice  ", "Straße", "İ", "ẞ", "Ω"]
    assert [migration.normalize_key(s) for s in sample] == [normalize_key(s) for s in sample]


# --- a database that already holds look-alike accounts: the migration must fail loudly and change nothing ----------------


def unapplied_state():
    executor = MigrationExecutor(connection)
    applied = executor.loader.applied_migrations
    with connection.cursor() as cursor:
        columns = {c.name for c in connection.introspection.get_table_description(cursor, "accounts_user")}
        constraints = set(connection.introspection.get_constraints(cursor, "accounts_user"))
    return SECOND not in applied, columns, constraints


@pytest.mark.parametrize(
    "first, second, field",
    [
        (("Émile", "a@example.com"), ("émile", "b@example.com"), "username"),
        (("straight", "u@Straße.example"), ("crooked", "u@STRASSE.example"), "email"),
    ],
    ids=["username", "email"],
)
@pytest.mark.django_db(transaction=True)
def test_migration_0002_refuses_existing_look_alike_accounts_and_changes_nothing(first, second, field):
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([FIRST])
        OldUser = executor.loader.project_state([FIRST]).apps.get_model("accounts", "User")
        OldUser.objects.create(username=first[0], email=first[1], password="x")
        OldUser.objects.create(username=second[0], email=second[1], password="x")
        before = unapplied_state()

        with pytest.raises(Exception) as excinfo:
            MigrationExecutor(connection).migrate([SECOND])

        message = str(excinfo.value)
        assert "collide" in message and field in message, message  # a clear, actionable message, not a bare IntegrityError
        assert all(value in message for value in (first[0] if field == 'username' else first[1], second[0] if field == 'username' else second[1])), message
        after = unapplied_state()
        assert after == before, "a failed migration must leave the schema exactly as it was"
        assert after[0] is True, "0002 must not be recorded as applied"
        assert "username_key" not in after[1] and "email_key" not in after[1]
        assert {"accounts_user_username_ci_unique", "accounts_user_email_ci_unique"} <= after[2]
        assert OldUser.objects.count() == 2, "no row may be changed or deleted"
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate([FIRST])
        OldUser = executor.loader.project_state([FIRST]).apps.get_model("accounts", "User")
        OldUser.objects.all().delete()
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_after_resolving_the_collision_the_migration_runs():
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([FIRST])
        OldUser = executor.loader.project_state([FIRST]).apps.get_model("accounts", "User")
        OldUser.objects.create(username="Émile", email="a@example.com", password="x")
        loser = OldUser.objects.create(username="émile", email="b@example.com", password="x")
        with pytest.raises(Exception):
            MigrationExecutor(connection).migrate([SECOND])
        loser.delete()  # what the error message tells the operator to do
        executor = MigrationExecutor(connection)
        executor.migrate([SECOND])
        NewUser = executor.loader.project_state([SECOND]).apps.get_model("accounts", "User")
        assert [(u.username, u.username_key) for u in NewUser.objects.all()] == [("Émile", "émile")]
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate([FIRST])
        executor.loader.project_state([FIRST]).apps.get_model("accounts", "User").objects.all().delete()
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db
def test_email_key_column_has_room_for_the_worst_case_expansion():
    # Casefold/NFKC can expand one character into many (one Arabic ligature becomes 18); SQLite does not enforce
    # max_length but other databases do, so the declared length must cover a maximum-length email.
    from accounts.models import User

    worst = normalize_key("ﷺ" * 254)
    assert User._meta.get_field("email_key").max_length >= len(worst)
    assert User._meta.get_field("username_key").max_length >= 30
