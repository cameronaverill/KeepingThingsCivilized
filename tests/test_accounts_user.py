"""The custom User model (plan section 8): case-insensitive unique username and email."""
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

User = get_user_model()

pytestmark = pytest.mark.django_db


def make(username="alice", email="alice@example.com", **extra):
    return User.objects.create_user(username=username, email=email, password="a-long-test-password-1", **extra) #secret-scan: allow # secret-scan: allow


def test_new_user_has_no_email_verification_yet():
    assert make().email_verified_at is None


def test_username_is_unique_ignoring_case():
    make("Alice", "alice@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        make("alice", "other@example.com")


def test_email_is_unique_ignoring_case():
    make("alice", "Alice@Example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        make("bob", "alice@example.com")


def test_case_insensitive_duplicates_fail_validation_too():
    # Forms call full_clean(), so the error must surface as a ValidationError, not only at the database.
    make("Alice", "alice@example.com")
    with pytest.raises(ValidationError):
        User(username="ALICE", email="new@example.com").full_clean()
    with pytest.raises(ValidationError):
        User(username="carol", email="ALICE@EXAMPLE.COM").full_clean()


def test_email_is_required():
    with pytest.raises(ValidationError) as excinfo:
        User(username="dave", email="").full_clean(exclude=["password"])
    assert "email" in excinfo.value.message_dict


def test_password_is_stored_as_an_argon2_hash():
    user = make()
    assert user.password.startswith("argon2$")
    assert "a-long-test-password-1" not in user.password # secret-scan: allow # secret-scan: allow
    assert user.check_password("a-long-test-password-1") # secret-scan: allow # secret-scan: allow


def test_create_superuser_works_with_the_custom_model():
    admin = User.objects.create_superuser("root", "root@example.com", "a-long-test-password-1") # secret-scan: allow # secret-scan: allow
    assert admin.is_staff and admin.is_superuser and admin.is_active
