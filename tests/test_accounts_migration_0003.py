"""Migration accounts/0003 (step 6c): the email becomes optional and email_key is unique only when it is not empty.

Forward and backward runs use MigrationExecutor on the test database and always put it back at the newest migration.
Never run against the dev database: pytest-django uses its own test database.
"""
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Q

from accounts.keys import normalize_key

MIGRATIONS = Path(__file__).resolve().parent.parent / "accounts" / "migrations"
FIRST = ("accounts", "0001_initial")
SECOND = ("accounts", "0002_user_keys")
CONSTRAINT = "accounts_user_email_key_unique_when_set"


def third():
    found = sorted(p.stem for p in MIGRATIONS.glob("0003_*.py"))
    assert len(found) == 1, f"expected exactly one accounts/migrations/0003_*.py, found {found}"
    return ("accounts", found[0])


def migrate(*targets):
    executor = MigrationExecutor(connection)
    executor.migrate(list(targets))
    return MigrationExecutor(connection)  # a fresh loader that sees what is applied now


def model_at(target):
    return MigrationExecutor(connection).loader.project_state([target]).apps.get_model("accounts", "User")


@pytest.fixture
def restore_leaf(transactional_db):
    yield
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def db_constraints():
    with connection.cursor() as cursor:
        return connection.introspection.get_constraints(cursor, "accounts_user")


def unique_columns():
    return {tuple(c["columns"]) for c in db_constraints().values() if c["unique"] and c["columns"]}


def old_row(model, username, email):
    return model.objects.create(
        username=username, email=email, password="x", username_key=normalize_key(username), email_key=normalize_key(email)
    )


def new_row(model, username, email=""):
    return model.objects.create(
        username=username, email=email, password="x", username_key=normalize_key(username), email_key=normalize_key(email)
    )


# --- shape -----------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_0003_exists_follows_0002_and_is_the_only_leaf():
    name = third()
    loader = MigrationExecutor(connection).loader
    assert SECOND in loader.get_migration(*name).dependencies
    assert loader.graph.leaf_nodes("accounts") == [name]


@pytest.mark.django_db
def test_0002_is_left_as_it_was_apart_from_not_being_the_leaf():
    text = (MIGRATIONS / "0002_user_keys.py").read_text()
    assert "unique=True" in text and "email_key" in text and CONSTRAINT not in text


@pytest.mark.django_db
def test_no_model_changes_are_missing_a_migration_after_0003(capsys):
    third()
    call_command("makemigrations", "--check", "--dry-run")
    assert "No changes detected" in capsys.readouterr().out


@pytest.mark.django_db
def test_the_migrated_state_matches_the_model():
    User = model_at(third())
    email = User._meta.get_field("email")
    assert (email.blank, email.null, email.default, email.max_length) == (True, False, "", 254)
    assert User._meta.get_field("email_key").unique is False
    assert User._meta.get_field("username_key").unique is True
    constraint = [c for c in User._meta.constraints if c.name == CONSTRAINT]
    assert len(constraint) == 1
    assert list(constraint[0].fields) == ["email_key"]
    assert constraint[0].condition == ~Q(email_key="")


@pytest.mark.django_db(transaction=True)
def test_after_0003_the_database_has_only_the_conditional_email_key_constraint(restore_leaf):
    migrate(SECOND)
    assert ("email_key",) in unique_columns()  # at 0002 it is unique outright
    assert CONSTRAINT not in db_constraints()
    migrate(third())
    constraints = db_constraints()
    assert constraints[CONSTRAINT]["unique"] is True and constraints[CONSTRAINT]["columns"] == ["email_key"]
    assert ("username_key",) in unique_columns()
    plain = [name for name, c in constraints.items() if c["columns"] == ["email_key"] and name != CONSTRAINT and c["unique"]]
    assert plain == [], "the old unconditional unique constraint on email_key must be gone"


# --- forward --------------------------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_forward_from_0002_keeps_every_existing_row_and_then_allows_many_blank_emails(restore_leaf):
    migrate(SECOND)
    Old = model_at(SECOND)
    old_row(Old, "Alice", "Alice@Example.COM")
    old_row(Old, "Bob_2", "bob@example.org")
    old_row(Old, "nomail", "")  # at most one blank row can exist before 0003
    migrate(third())
    New = model_at(third())
    rows = {u.username: (u.email, u.email_key, u.username_key) for u in New.objects.all()}
    assert rows == {
        "Alice": ("Alice@Example.COM", "alice@example.com", "alice"),
        "Bob_2": ("bob@example.org", "bob@example.org", "bob_2"),
        "nomail": ("", "", "nomail"),
    }
    new_row(New, "second_nomail")
    new_row(New, "third_nomail")
    assert New.objects.filter(email_key="").count() == 3


@pytest.mark.django_db(transaction=True)
def test_forward_still_enforces_uniqueness_of_a_given_email_key(restore_leaf):
    migrate(SECOND)
    old_row(model_at(SECOND), "Alice", "alice@example.com")
    migrate(third())
    New = model_at(third())
    with pytest.raises(IntegrityError), transaction.atomic():
        new_row(New, "carol", "ALICE@example.com")
    assert New.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_forward_from_the_start_through_all_migrations_on_mixed_rows(restore_leaf):
    migrate(FIRST)
    Old = model_at(FIRST)
    Old.objects.create(username="Alice", email="Alice@Example.COM", password="x")
    Old.objects.create(username="bob_2", email="", password="x")  # 0001 only made LOWER(email) unique: one blank is fine
    Old.objects.create(username="Straße", email="s@example.net", password="x")
    migrate(third())
    New = model_at(third())
    keys = {u.username: (u.username_key, u.email, u.email_key) for u in New.objects.all()}
    assert keys == {
        "Alice": ("alice", "Alice@Example.COM", "alice@example.com"),
        "bob_2": ("bob_2", "", ""),
        "Straße": ("strasse", "s@example.net", "s@example.net"),
    }
    new_row(New, "another_nomail")
    assert New.objects.filter(email_key="").count() == 2


# --- backward -------------------------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_backward_to_0002_on_a_database_without_blank_emails_changes_nothing_in_the_data(restore_leaf):
    New = model_at(third())
    new_row(New, "Alice", "Alice@Example.COM")
    new_row(New, "bob_2", "bob@example.org")
    migrate(SECOND)
    Old = model_at(SECOND)
    assert {u.username: (u.email, u.email_key) for u in Old.objects.all()} == {
        "Alice": ("Alice@Example.COM", "alice@example.com"),
        "bob_2": ("bob@example.org", "bob@example.org"),
    }
    assert CONSTRAINT not in db_constraints()
    assert ("email_key",) in unique_columns()
    with pytest.raises(IntegrityError), transaction.atomic():
        old_row(Old, "carol", "ALICE@example.com")


@pytest.mark.django_db(transaction=True)
def test_backward_to_0002_works_when_several_accounts_have_no_email(restore_leaf):
    """The old schema needs a distinct email per account, so the reverse step must cope with many blank ones."""
    New = model_at(third())
    new_row(New, "Alice", "alice@example.com")
    for name in ("nomail_1", "nomail_2", "nomail_3"):
        new_row(New, name)
    migrate(SECOND)
    Old = model_at(SECOND)
    rows = list(Old.objects.order_by("username"))
    assert len(rows) == 4
    assert all(u.email != "" and u.email_key == normalize_key(u.email) for u in rows)
    assert len({u.email_key for u in rows}) == 4
    assert [u.email for u in rows if u.username == "Alice"] == ["alice@example.com"]
    assert ("email_key",) in unique_columns() and CONSTRAINT not in db_constraints()


@pytest.mark.django_db(transaction=True)
def test_backward_never_reuses_an_address_a_real_account_already_has(restore_leaf):
    New = model_at(third())
    new_row(New, "dave", "")
    new_row(New, "real", "dave@no-email.invalid")  # the kind of address a placeholder scheme might pick
    new_row(New, "real2", "dave-2@no-email.invalid")
    migrate(SECOND)
    Old = model_at(SECOND)
    assert len({u.email_key for u in Old.objects.all()}) == 3
    assert Old.objects.get(username="real").email == "dave@no-email.invalid"
    assert Old.objects.get(username="real2").email == "dave-2@no-email.invalid"


@pytest.mark.django_db(transaction=True)
def test_backward_on_an_empty_table_works(restore_leaf):
    migrate(SECOND)
    assert model_at(SECOND).objects.count() == 0
    assert CONSTRAINT not in db_constraints()


@pytest.mark.django_db(transaction=True)
def test_backward_all_the_way_to_0001_and_forward_again_keeps_the_data(restore_leaf):
    New = model_at(third())
    new_row(New, "Alice", "alice@example.com")
    new_row(New, "nomail_1")
    new_row(New, "nomail_2")
    migrate(FIRST)
    assert model_at(FIRST).objects.count() == 3
    migrate(third())
    again = {u.username: u.username_key for u in model_at(third()).objects.all()}
    assert again == {"Alice": "alice", "nomail_1": "nomail_1", "nomail_2": "nomail_2"}


@pytest.mark.django_db(transaction=True)
def test_backward_then_forward_lets_more_blank_accounts_be_added_again(restore_leaf):
    migrate(SECOND)
    migrate(third())
    New = model_at(third())
    new_row(New, "one")
    new_row(New, "two")
    assert New.objects.filter(email_key="").count() == 2


@pytest.mark.django_db(transaction=True)
def test_backward_keeps_placeholders_distinct_even_when_they_could_chase_each_other(restore_leaf):
    New = model_at(third())
    new_row(New, "real", "dave@no-email.invalid")
    new_row(New, "dave", "")
    new_row(New, "dave-2", "")
    new_row(New, "dave-3", "")
    migrate(SECOND)
    Old = model_at(SECOND)
    assert Old.objects.count() == 4
    assert len({u.email_key for u in Old.objects.all()}) == 4
    assert Old.objects.filter(email="").count() == 0
