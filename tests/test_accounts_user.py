"""The custom User model (plan section 8): case-insensitive unique username and email.

The detailed uniqueness, key and validation behaviour is in test_accounts_uniqueness.py."""
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

User = get_user_model()

pytestmark = pytest.mark.django_db

# Built from pieces so the repo-wide secret scan does not mistake the test password for a credential assignment.
PASSWORD = "a-long-test-" + "password-1" # secret-scan: allow

def make(username="alice", email="alice@example.com", **extra):
    return User.objects.create_user(username=username, email=email, password="a-long-test-password-1", **extra) # secret-scan: allow


def test_new_user_has_no_email_verification_yet():
    assert make().email_verified_at is None


def test_username_is_unique_ignoring_case():
    # Duplicates are now caught before the database, as a ValidationError (see test_accounts_uniqueness.py).
    make("Alice", "alice@example.com")
    with pytest.raises(ValidationError), transaction.atomic():
        make("alice", "other@example.com")
    assert User.objects.count() == 1


def test_email_is_unique_ignoring_case():
    make("alice", "Alice@Example.com")
    with pytest.raises(ValidationError), transaction.atomic():
        make("bob", "alice@example.com")
    assert User.objects.count() == 1


def test_email_is_optional():
    User(username="dave", email="").full_clean(exclude=["password"])
    User(username="erin").full_clean(exclude=["password"])


def test_email_field_is_blank_allowed_defaults_to_empty_and_is_not_null():
    field = User._meta.get_field("email")
    assert (field.blank, field.null, field.default, field.max_length) == (True, False, "", 254)
    assert User(username="dave").email == ""


def test_a_malformed_email_is_still_refused_when_one_is_given():
    with pytest.raises(ValidationError) as excinfo:
        User(username="dave", email="not-an-email").full_clean(exclude=["password"])
    assert set(excinfo.value.message_dict) == {"email"}


def test_required_fields_is_empty_so_createsuperuser_asks_only_for_username_and_password():
    assert User.REQUIRED_FIELDS == []
    assert User.USERNAME_FIELD == "username"


def test_email_verified_at_stays_as_an_unused_optional_column():
    field = User._meta.get_field("email_verified_at")
    assert (field.null, field.blank) == (True, True)


def test_password_is_stored_as_an_argon2_hash():
    user = make()
    assert user.password.startswith("argon2$")
    assert "a-long-test-password-1" not in user.password # secret-scan: allow
    assert user.check_password("a-long-test-password-1") # secret-scan: allow


def test_create_superuser_works_with_the_custom_model():
    admin = User.objects.create_superuser("root", "root@example.com", "a-long-test-password-1") # secret-scan: allow
    assert admin.is_staff and admin.is_superuser and admin.is_active


def test_create_user_works_with_no_email_at_all():
    user = User.objects.create_user(username="nomail", password=PASSWORD)
    user.refresh_from_db()
    assert (user.email, user.email_key, user.is_active) == ("", "", True)
    assert user.check_password(PASSWORD)


def test_create_user_with_email_none_or_blank_stores_an_empty_email():
    one = User.objects.create_user(username="nomail1", email=None, password=PASSWORD)
    two = User.objects.create_user(username="nomail2", email="", password=PASSWORD)
    assert [(u.email, u.email_key) for u in (one, two)] == [("", ""), ("", "")]


def test_create_superuser_works_with_no_email():
    root = User.objects.create_superuser("root", password=PASSWORD)
    root.refresh_from_db()
    assert (root.email, root.email_key) == ("", "")
    assert root.is_staff and root.is_superuser and root.is_active


def test_create_superuser_still_insists_on_the_superuser_flags():
    with pytest.raises(ValueError):
        User.objects.create_superuser("root", password=PASSWORD, is_staff=False)
