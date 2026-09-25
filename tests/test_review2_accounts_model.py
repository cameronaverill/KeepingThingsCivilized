"""Second independent review: model behaviours that mutation testing showed were not pinned down."""
import pytest
from django.contrib.auth import get_user_model

User = get_user_model()

pytestmark = pytest.mark.django_db

PASSWORD = "a-long-test-" + "password-1"


def make(username="alice", email="alice@example.com"):
    return User.objects.create_user(username=username, email=email, password=PASSWORD)


def stored(pk):
    return User.objects.filter(pk=pk).values("username", "username_key", "email", "email_key").get()


def test_save_with_update_fields_username_keeps_the_username_key_in_step():
    user = make("alice", "alice@example.com")
    user.username = "Alicia"
    user.save(update_fields=["username"])
    assert stored(user.pk) == {
        "username": "Alicia", "username_key": "alicia", "email": "alice@example.com", "email_key": "alice@example.com",
    }


def test_save_with_update_fields_email_keeps_the_email_key_in_step():
    user = make("alice", "alice@example.com")
    user.email = "  New@Example.COM "
    user.save(update_fields=["email"])
    assert stored(user.pk) == {
        "username": "alice", "username_key": "alice", "email": "New@Example.COM", "email_key": "new@example.com",
    }


def test_save_with_update_fields_that_touch_neither_leaves_both_keys_alone():
    user = make("alice", "alice@example.com")
    user.first_name = "Al"
    user.save(update_fields=["first_name"])
    assert stored(user.pk)["username_key"] == "alice"


def test_validate_unique_honours_exclude_for_each_field():
    make("Alice", "alice@example.com")
    twin = User(username="ALICE", email="ALICE@example.com")
    twin.clean_fields(exclude=["password"])  # fills the derived keys, as full_clean() and every ModelForm do first
    twin.validate_unique(exclude=["username", "email"])  # nothing left to check
    with pytest.raises(Exception) as only_email:
        twin.validate_unique(exclude=["username"])
    assert set(only_email.value.message_dict) == {"email"}
    with pytest.raises(Exception) as only_username:
        twin.validate_unique(exclude=["email"])
    assert set(only_username.value.message_dict) == {"username"}


def test_full_clean_with_an_excluded_field_does_not_report_it():
    make("Alice", "alice@example.com")
    twin = User(username="ALICE", email="other@example.com")
    twin.full_clean(exclude=["password", "username"])
