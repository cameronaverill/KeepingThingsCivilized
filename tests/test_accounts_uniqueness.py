"""Accounts: derived key columns, ASCII usernames, and real case-insensitive uniqueness (step 1 fixes, section A).

Every path that can create or change a user (create_user, create_superuser, full_clean, plain save(),
createsuperuser --noinput) must give a friendly ValidationError / CommandError, never an IntegrityError.
The unique constraints on the key columns are only the database backstop.
"""
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection, transaction
from django.db.models import Q

from config import tunables

User = get_user_model()

pytestmark = pytest.mark.django_db

PASSWORD = "a-long-test-" + "password-1"
USERNAME_TAKEN = "A user with that username already exists."
KELVIN = "\u212a"


def make(username="alice", email="alice@example.com", **extra):
    return User.objects.create_user(username=username, email=email, password=PASSWORD, **extra)


def all_messages(error):
    return " | ".join(error.messages)


# --- the derived key fields ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["username_key", "email_key"])
def test_key_fields_are_non_editable_and_never_null(name):
    field = User._meta.get_field(name)
    assert field.get_internal_type() == "CharField"
    assert field.editable is False
    assert field.blank is False
    assert field.null is False


def test_username_key_is_unique_outright():
    assert User._meta.get_field("username_key").unique is True


def test_email_key_is_unique_only_when_not_empty():
    assert User._meta.get_field("email_key").unique is False
    matching = [c for c in User._meta.constraints if c.name == "accounts_user_email_key_unique_when_set"]
    assert len(matching) == 1
    constraint = matching[0]
    assert list(constraint.fields) == ["email_key"]
    assert constraint.condition == ~Q(email_key="")


def test_key_fields_are_long_enough_for_what_they_hold():
    assert User._meta.get_field("username_key").max_length >= tunables.USERNAME_MAX_LENGTH
    assert User._meta.get_field("email_key").max_length >= User._meta.get_field("email").max_length


def test_username_field_uses_the_ascii_validator_and_the_tunable_length():
    from accounts.validators import validate_username

    field = User._meta.get_field("username")
    assert field.max_length == tunables.USERNAME_MAX_LENGTH
    assert validate_username in field.validators
    names = [type(v).__name__ for v in field.validators]
    assert "UnicodeUsernameValidator" not in names and "ASCIIUsernameValidator" not in names
    assert "@" not in str(field.help_text), "Django's default '@/./+/-/_' help text must go"


def test_save_sets_both_keys_and_strips_whitespace():
    user = User(username="  Alice  ", email="  Alice@Example.COM ")
    user.set_password(PASSWORD)
    user.save()
    user.refresh_from_db()
    assert user.username == "Alice"  # the display form keeps its case
    assert user.email == "Alice@Example.COM"
    assert user.username_key == "alice"
    assert user.email_key == "alice@example.com"


def test_create_user_sets_the_keys():
    user = make("Bob_Smith", "Bob@Example.com")
    assert user.username_key == "bob_smith"
    assert user.email_key == "bob@example.com"
    assert User.objects.get(username_key="bob_smith").pk == user.pk


def test_email_key_folds_unicode_forms():
    user = make("carol", "carol@Stra\u00dfe.example")
    assert user.email_key == "carol@strasse.example"


def test_keys_follow_a_changed_username_or_email():
    user = make("alice", "alice@example.com")
    user.username = "Alicia"
    user.email = "Alicia@Example.org"
    user.save()
    user.refresh_from_db()
    assert (user.username_key, user.email_key) == ("alicia", "alicia@example.org")


def test_saving_an_existing_user_again_does_not_trip_over_its_own_keys():
    user = make("Alice", "Alice@Example.com")
    user.first_name = "Al"
    user.save()
    user.username = "ALICE"  # a case-only change to your own name is allowed
    user.save()
    user.refresh_from_db()
    assert (user.username, user.username_key) == ("ALICE", "alice")


def test_full_clean_accepts_a_valid_new_user():
    # The keys are not set until save(); full_clean must not complain that they are blank.
    User(username="fine_name", email="fine@example.com").full_clean(exclude=["password"])


def test_a_blank_email_is_allowed_and_gets_a_blank_key():
    user = make("dave", "")
    user.refresh_from_db()
    assert (user.email, user.email_key) == ("", "")
    assert User.objects.filter(username="dave").exists()


def test_a_whitespace_only_email_counts_as_no_email():
    user = make("dave", "   ")
    user.refresh_from_db()
    assert (user.email, user.email_key) == ("", "")


def test_save_no_longer_demands_an_email():
    user = User(username="dave")
    user.set_password(PASSWORD)
    user.save()
    assert User.objects.get(username="dave").email_key == ""


def test_save_without_an_email_never_says_enter_an_email_address():
    user = User(username="ab")  # invalid for another reason: only that reason may be reported
    with pytest.raises(ValidationError) as excinfo:
        user.save()
    assert set(excinfo.value.message_dict) == {"username"}
    assert "Enter an email address." not in all_messages(excinfo.value)


def test_any_number_of_accounts_can_have_no_email():
    for name in ("dave", "erin", "frank"):
        make(name, "")
    User(username="grace", password="x").save()
    User.objects.create_user(username="heidi", password=PASSWORD)
    assert User.objects.filter(email_key="").count() == 5


def test_accounts_without_an_email_do_not_collide_with_one_that_has_one():
    make("dave", "")
    make("erin", "erin@example.com")
    make("frank", "")
    assert User.objects.count() == 3


def test_full_clean_accepts_two_blank_email_accounts():
    make("dave", "")
    User(username="erin", email="").full_clean(exclude=["password"])


def test_clearing_an_email_frees_its_address_and_blanks_the_key():
    user = make("dave", "dave@example.com")
    user.email = ""
    user.save()
    user.refresh_from_db()
    assert (user.email, user.email_key) == ("", "")
    make("erin", "dave@example.com")  # the address is free again


def test_giving_an_account_an_email_later_checks_the_address_for_a_collision():
    make("dave", "")
    make("erin", "erin@example.com")
    user = User.objects.get(username="dave")
    user.email = "ERIN@example.com"
    with pytest.raises(ValidationError) as excinfo:
        user.save()
    assert "email" in excinfo.value.message_dict
    assert User.objects.get(username="dave").email == ""


def test_the_friendly_email_error_is_still_the_documented_sentence():
    make("dave", "dave@example.com")
    with pytest.raises(ValidationError) as excinfo:
        make("erin", "Dave@Example.com")
    assert excinfo.value.message_dict == {"email": ["A user with that email address already exists."]}


# --- username format, on every path ---------------------------------------------------------------------------------

BAD_USERNAMES = ["ab", "x" * (tunables.USERNAME_MAX_LENGTH + 1), "has space", "dot.name", "\u00c9mile", "a@b.c"]
# Django's create_user()/create_superuser() NFKC-normalise the name before saving, which turns these into plain ASCII
# ("KKK", "ALICE"). Either outcome is fine on those two paths; on save() and full_clean() they must be rejected.
FOLDING_TO_ASCII = [KELVIN * 3, "\uff21\uff2c\uff29\uff23\uff25"]


@pytest.mark.parametrize("bad", BAD_USERNAMES)
def test_create_user_rejects_bad_usernames(bad):
    with pytest.raises(ValidationError):
        make(bad, "bad@example.com")
    assert User.objects.count() == 0


@pytest.mark.parametrize("bad", BAD_USERNAMES)
def test_create_superuser_rejects_bad_usernames(bad):
    with pytest.raises(ValidationError):
        User.objects.create_superuser(bad, "root@example.com", PASSWORD)
    assert User.objects.count() == 0


@pytest.mark.parametrize("name", FOLDING_TO_ASCII)
@pytest.mark.parametrize("create", [
    lambda n: make(n, "fold@example.com"),
    lambda n: User.objects.create_superuser(n, "fold@example.com", PASSWORD),
], ids=["create_user", "create_superuser"])
def test_managers_never_store_a_non_ascii_username(create, name):
    try:
        user = create(name)
    except ValidationError:
        return
    assert user.username.isascii() and user.username == user.username.strip()
    user.refresh_from_db()
    assert user.username.isascii()


@pytest.mark.parametrize("bad", BAD_USERNAMES + FOLDING_TO_ASCII)
def test_plain_save_rejects_bad_usernames(bad):
    user = User(username=bad, email="bad@example.com")
    user.set_password(PASSWORD)
    with pytest.raises(ValidationError):
        user.save()
    assert User.objects.count() == 0


@pytest.mark.parametrize("bad", BAD_USERNAMES + FOLDING_TO_ASCII)
def test_full_clean_rejects_bad_usernames_on_the_username_field(bad):
    with pytest.raises(ValidationError) as excinfo:
        User(username=bad, email="bad@example.com").full_clean(exclude=["password"])
    assert "username" in excinfo.value.message_dict


def test_a_username_that_is_only_whitespace_is_rejected():
    with pytest.raises(ValidationError):
        make("   ", "ws@example.com")


# --- duplicates, on every path -------------------------------------------------------------------------------------


def test_create_user_rejects_a_username_that_differs_only_by_case():
    make("Alice", "alice@example.com")
    with pytest.raises(ValidationError) as excinfo:
        make("ALICE", "other@example.com")
    assert USERNAME_TAKEN in excinfo.value.messages
    assert User.objects.count() == 1


def test_create_user_rejects_an_email_that_differs_only_by_case():
    make("alice", "Alice@Example.com")
    with pytest.raises(ValidationError) as excinfo:
        make("bobby", "ALICE@EXAMPLE.COM")
    text = all_messages(excinfo.value).lower()
    assert "email" in text and "already exists" in text
    assert User.objects.count() == 1


def test_create_superuser_rejects_duplicates():
    make("Alice", "alice@example.com")
    with pytest.raises(ValidationError) as by_name:
        User.objects.create_superuser("alice", "root@example.com", PASSWORD)
    assert USERNAME_TAKEN in by_name.value.messages
    with pytest.raises(ValidationError) as by_email:
        User.objects.create_superuser("rooty", "ALICE@example.com", PASSWORD)
    assert "email" in all_messages(by_email.value).lower()
    assert User.objects.count() == 1


def test_plain_save_rejects_duplicates():
    make("Alice", "alice@example.com")
    with pytest.raises(ValidationError) as by_name:
        User(username="aLiCe", email="new@example.com", password="x").save()
    assert USERNAME_TAKEN in by_name.value.messages
    with pytest.raises(ValidationError) as by_email:
        User(username="carol", email="ALICE@example.COM", password="x").save()
    assert "email" in all_messages(by_email.value).lower()
    assert User.objects.count() == 1


def test_full_clean_reports_case_variant_duplicates_on_the_right_field():
    # Kept from the step 1 review: the password is excluded so only the duplicate can cause the error.
    make("Alice", "alice@example.com")
    with pytest.raises(ValidationError) as by_name:
        User(username="ALICE", email="new@example.com").full_clean(exclude=["password"])
    assert set(by_name.value.message_dict) == {"username"}
    assert USERNAME_TAKEN in by_name.value.message_dict["username"]
    with pytest.raises(ValidationError) as by_email:
        User(username="carol", email="ALICE@EXAMPLE.COM").full_clean(exclude=["password"])
    assert set(by_email.value.message_dict) == {"email"}
    assert "already exists" in all_messages(by_email.value)


def test_full_clean_lets_a_user_keep_its_own_keys():
    user = make("Alice", "alice@example.com")
    user.full_clean(exclude=["password"])
    user.username = "ALICE"
    user.email = "ALICE@example.com"
    user.full_clean(exclude=["password"])


def test_whitespace_does_not_hide_a_duplicate():
    make("alice", "alice@example.com")
    with pytest.raises(ValidationError):
        make("  alice  ", "other@example.com")
    with pytest.raises(ValidationError):
        make("carol", "  alice@example.com ")
    assert User.objects.count() == 1


def test_a_failed_duplicate_leaves_no_partial_row_and_the_connection_stays_usable():
    make("alice", "alice@example.com")
    with pytest.raises(ValidationError):
        make("alice", "second@example.com")
    assert User.objects.count() == 1
    make("bobby", "bobby@example.com")  # no transaction.atomic() needed after a ValidationError
    assert User.objects.count() == 2


# --- Unicode look-alikes -------------------------------------------------------------------------------------------

# (first, second): two spellings that must count as one address once normalised. Emails use a non-ASCII DOMAIN because
# that is the part of an address Django's EmailValidator accepts in Unicode.
EMAIL_DUPLICATE_PAIRS = [
    ("\u00c9mile", "\u00e9mile"),
    ("Stra\u00dfe", "STRASSE"),
    ("\u03a3", "\u03c2"),
    (KELVIN, "k"),
    ("\uff45xample", "example"),  # fullwidth "e"
    ("caf\u00e9", "cafe\u0301"),  # precomposed versus combining accent
]


@pytest.mark.parametrize("first, second", EMAIL_DUPLICATE_PAIRS)
def test_emails_that_differ_only_by_unicode_form_are_duplicates(first, second):
    make("first_user", f"person@{first}.example")
    with pytest.raises(ValidationError) as excinfo:
        make("second_user", f"person@{second}.example")
    assert "email" in all_messages(excinfo.value).lower()
    assert User.objects.count() == 1


@pytest.mark.parametrize("first, second", EMAIL_DUPLICATE_PAIRS)
def test_unicode_email_duplicates_are_caught_by_full_clean_and_save_too(first, second):
    make("first_user", f"person@{first}.example")
    twin = User(username="second_user", email=f"person@{second}.example", password="x")
    with pytest.raises(ValidationError) as excinfo:
        twin.full_clean(exclude=["password"])
    assert "email" in excinfo.value.message_dict
    with pytest.raises(ValidationError):
        twin.save()


def test_dotted_capital_i_in_an_email_is_not_plain_i():
    # NFKC(casefold("\u0130")) is "i" plus a combining dot, which is different from "i": they are two addresses.
    make("first_user", "person@\u0130.example")
    make("second_user", "person@i.example")
    assert User.objects.count() == 2


@pytest.mark.parametrize(
    "lookalike",
    ["\u00c9mile", "\u00e9mile", "stra\u00dfe", "\u0130stanbul", "\u03a3\u03a3\u03a3", KELVIN * 3, "\uff21\uff2c\uff29\uff23\uff25"],
)
def test_non_ascii_usernames_are_refused_by_save_and_full_clean(lookalike):
    with pytest.raises(ValidationError):
        User(username=lookalike, email="u@example.com", password="x").save()
    with pytest.raises(ValidationError):
        User(username=lookalike, email="u@example.com").full_clean(exclude=["password"])
    assert User.objects.count() == 0


def test_ascii_lookalike_of_an_existing_name_is_a_duplicate_not_a_new_account():
    make("kelvin", "kelvin@example.com")
    with pytest.raises(ValidationError):
        make("Kelvin", "kelvin2@example.com")
    with pytest.raises(ValidationError):
        make("KELVIN", "kelvin3@example.com")


# --- createsuperuser --noinput -------------------------------------------------------------------------------------


@pytest.fixture
def superuser_password(monkeypatch):
    """createsuperuser --noinput reads the password from this environment variable (no prompt, no --password)."""
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", PASSWORD)


def run_createsuperuser(username):
    call_command("createsuperuser", interactive=False, username=username, verbosity=0)


def test_createsuperuser_noinput_creates_a_valid_superuser_with_no_email(superuser_password):
    run_createsuperuser("Root")
    root = User.objects.get(username="Root")
    assert root.is_superuser and root.is_staff and root.is_active
    assert (root.username_key, root.email, root.email_key) == ("root", "", "")
    assert root.check_password(PASSWORD)


def test_createsuperuser_takes_no_email_option(superuser_password):
    with pytest.raises(TypeError):
        call_command("createsuperuser", interactive=False, username="root", email="root@example.com", verbosity=0)
    assert User.objects.count() == 0


def test_two_superusers_can_be_created_without_an_email(superuser_password):
    run_createsuperuser("Root")
    run_createsuperuser("Other")
    assert User.objects.filter(is_superuser=True, email_key="").count() == 2


def test_createsuperuser_noinput_reports_a_duplicate_username_as_a_command_error(superuser_password):
    make("Alice", "alice@example.com")
    with pytest.raises(CommandError) as excinfo:
        run_createsuperuser("ALICE")
    assert "already" in str(excinfo.value).lower()
    assert User.objects.count() == 1


def test_createsuperuser_noinput_reports_a_bad_username_as_a_command_error(superuser_password):
    with pytest.raises(CommandError):
        run_createsuperuser("bad name")
    with pytest.raises(CommandError):
        run_createsuperuser("\u00c9mile")
    assert User.objects.count() == 0


# --- the database backstop -----------------------------------------------------------------------------------------


def test_database_rejects_a_duplicate_username_key_even_when_save_is_bypassed():
    first = make("alice", "alice@example.com")
    second = make("bobby", "bobby@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=second.pk).update(username_key=first.username_key)


def test_database_rejects_a_duplicate_email_key_even_when_save_is_bypassed():
    first = make("alice", "alice@example.com")
    second = make("bobby", "bobby@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=second.pk).update(email_key=first.email_key)


def test_bulk_create_cannot_smuggle_in_a_duplicate_key():
    make("alice", "alice@example.com")
    twin = User(username="ALICE", email="other@example.com", password="x", username_key="alice", email_key="other@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.bulk_create([twin])
    twin = User(username="carol", email="x@example.com", password="x", username_key="carol", email_key="alice@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.bulk_create([twin])


def unique_column_sets(table="accounts_user"):
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    return constraints, {tuple(c["columns"]) for c in constraints.values() if c["unique"] and c["columns"]}


def test_database_has_unique_constraints_on_both_key_columns():
    _, unique = unique_column_sets()
    assert ("username_key",) in unique
    assert ("email_key",) in unique


def test_old_lower_constraints_are_gone_from_the_model_and_the_database():
    old_names = {"accounts_user_username_ci_unique", "accounts_user_email_ci_unique"}
    assert not old_names & {c.name for c in User._meta.constraints}
    constraints, _ = unique_column_sets()
    assert not old_names & set(constraints)
    assert not any(getattr(c, "expressions", None) for c in User._meta.constraints)


def test_database_allows_any_number_of_blank_email_keys():
    User.objects.bulk_create(
        [User(username=f"nomail{n}", username_key=f"nomail{n}", email_key="", password="x") for n in range(3)]
    )
    assert User.objects.filter(email_key="").count() == 3


def test_database_still_rejects_a_duplicate_non_empty_email_key_on_insert():
    make("alice", "alice@example.com")
    twin = User(username="carol", username_key="carol", email_key="alice@example.com", password="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.bulk_create([twin])


def test_database_constraint_on_email_key_is_named_and_only_covers_non_empty_keys():
    constraints, _ = unique_column_sets()
    assert constraints["accounts_user_email_key_unique_when_set"]["unique"] is True
    assert constraints["accounts_user_email_key_unique_when_set"]["columns"] == ["email_key"]
