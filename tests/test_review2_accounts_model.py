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


# --- step 6c: the email is optional -------------------------------------------------------------------------------


def make_without_email(username):
    return User.objects.create_user(username=username, password=PASSWORD)


def test_save_with_update_fields_email_to_blank_clears_the_email_key_too():
    user = make("alice", "alice@example.com")
    user.email = ""
    user.save(update_fields=["email"])
    assert stored(user.pk) == {"username": "alice", "username_key": "alice", "email": "", "email_key": ""}


def test_save_with_update_fields_email_from_blank_sets_the_email_key():
    user = make_without_email("alice")
    user.email = "  Late@Example.COM "
    user.save(update_fields=["email"])
    assert stored(user.pk) == {
        "username": "alice", "username_key": "alice", "email": "Late@Example.COM", "email_key": "late@example.com",
    }


def test_validate_unique_never_reports_blank_emails_as_duplicates_of_each_other():
    make_without_email("alice")
    twin = User(username="bobby")
    twin.clean_fields(exclude=["password"])
    twin.validate_unique()  # two accounts without an email are not a collision


def test_validate_unique_still_reports_a_username_collision_for_a_blank_email_twin():
    make_without_email("Alice")
    twin = User(username="ALICE")
    twin.clean_fields(exclude=["password"])
    with pytest.raises(Exception) as excinfo:
        twin.validate_unique()
    assert set(excinfo.value.message_dict) == {"username"}


def test_full_clean_of_a_blank_email_twin_does_not_trip_the_conditional_constraint_check():
    make_without_email("alice")
    User(username="bobby").full_clean(exclude=["password"])


def test_full_clean_reports_a_duplicate_email_once_and_only_on_the_email_field():
    make("alice", "alice@example.com")
    with pytest.raises(Exception) as excinfo:
        User(username="bobby", email="ALICE@example.com").full_clean(exclude=["password"])
    assert excinfo.value.message_dict == {"email": ["A user with that email address already exists."]}

