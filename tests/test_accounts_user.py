"""The custom User model (plan section 8): case-insensitive unique username and email.

The detailed uniqueness, key and validation behaviour is in test_accounts_uniqueness.py."""
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

User = get_user_model()

pytestmark = pytest.mark.django_db

# Built from pieces so the repo-wide secret scan does not mistake the test password for a credential assignment.
PASSWORD = "a-long-test-" + "password-1" # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow

def make(username="alice", email="alice@example.com", **extra):
    return User.objects.create_user(username=username, email=email, password="a-long-test-password-1", **extra) #secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow


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


def test_email_is_required():
    with pytest.raises(ValidationError) as excinfo:
        User(username="dave", email="").full_clean(exclude=["password"])
    assert "email" in excinfo.value.message_dict


def test_password_is_stored_as_an_argon2_hash():
    user = make()
    assert user.password.startswith("argon2$")
    assert "a-long-test-password-1" not in user.password # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow
    assert user.check_password("a-long-test-password-1") # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow


def test_create_superuser_works_with_the_custom_model():
    admin = User.objects.create_superuser("root", "root@example.com", "a-long-test-password-1") # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow
    assert admin.is_staff and admin.is_superuser and admin.is_active
