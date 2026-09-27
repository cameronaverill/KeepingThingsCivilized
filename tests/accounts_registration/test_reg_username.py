"""Step 6c: username rules on the register form. A taken name (compared through username_key: case, whitespace and
Unicode form ignored) gives "That username is taken."; format and length errors use the model's validator messages;
no refusal creates an account or logs anyone in."""
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from unittest import mock

import reg_testkit as kit
from accounts.validators import validate_username
from reg_testkit import STRONG_A, STRONG_B, TAKEN

User = get_user_model()


def message_for(value):
    """The model's own friendly message for a username value."""
    try:
        validate_username(value)
    except ValidationError as error:
        return error.messages[0]
    raise AssertionError(f"{value!r} is a valid username")


CHARACTERS = message_for("bad name!")


@pytest.fixture
def existing(db):
    return User.objects.create_user(username="alice", password=STRONG_B)


def assert_refused(response, settings, client, expected, users_before=1):
    assert response.status_code == 200
    page = kit.page_text(response)
    assert expected in page, f"{expected!r} not on the page: {page!r}"
    assert User.objects.count() == users_before
    assert kit.logged_in_user_id(client, settings) is None
    kit.assert_no_internal_text(response)


def test_the_taken_message_is_the_contract_sentence():
    assert TAKEN == "That username is taken."


def test_a_taken_username_is_refused_with_the_taken_message(client, settings, existing):
    response = kit.register(client, "alice", STRONG_A)
    assert_refused(response, settings, client, TAKEN)
    assert User.objects.get(username="alice").pk == existing.pk


@pytest.mark.parametrize("typed", ["ALICE", "Alice", "aLiCe", "alice ", "  alice", "\talice\t"])
def test_a_username_taken_ignoring_case_or_edge_whitespace_is_refused(client, settings, existing, typed):
    response = kit.register(client, typed, STRONG_A)
    assert_refused(response, settings, client, TAKEN)


def test_a_username_taken_only_in_another_case_by_a_mixed_case_account_is_refused(client, settings):
    User.objects.create_user(username="MixedCase", password=STRONG_B)
    response = kit.register(client, "mixedcase", STRONG_A)
    assert_refused(response, settings, client, TAKEN)
    assert list(User.objects.values_list("username", flat=True)) == ["MixedCase"]


@pytest.mark.parametrize("typed", ["ａｌｉｃｅ", "ＡＬＩＣＥ", "alіce", "K" * 3])
def test_a_lookalike_or_other_unicode_form_is_never_registered(client, settings, existing, typed):
    """Full-width and look-alike spellings fold onto an existing key or break the ASCII rule; either way refused."""
    response = kit.register(client, typed, STRONG_A)
    assert response.status_code == 200
    page = kit.page_text(response)
    assert TAKEN in page or CHARACTERS in page
    assert User.objects.count() == 1
    assert kit.logged_in_user_id(client, settings) is None


def test_a_unicode_form_of_a_taken_name_is_taken_when_the_key_matches(client, settings):
    """Kelvin sign: its NFKC/casefold key is 'k', the same key as the ASCII name."""
    User.objects.create_user(username="kkk", password=STRONG_B)
    response = kit.register(client, "KKK", STRONG_A)
    assert response.status_code == 200
    assert TAKEN in kit.page_text(response) or CHARACTERS in kit.page_text(response)
    assert User.objects.count() == 1


def test_a_different_name_is_not_taken(client, existing):
    assert kit.register(client, "alice2", STRONG_A).status_code == 302
    assert kit.register(type(client)(), "a_lice", STRONG_A).status_code == 302
    assert User.objects.count() == 3


def test_the_taken_message_is_not_shown_for_other_errors(client, existing):
    response = kit.register(client, "carol", "short")
    assert TAKEN not in kit.page_text(response)


@pytest.mark.parametrize("typed", ["bad name", "bad!", "álice", "user@name", "naïve", "名前名", "a.b.c"])
def test_forbidden_characters_are_refused_with_the_models_message(client, settings, typed):
    response = kit.register(client, typed, STRONG_A)
    assert_refused(response, settings, client, CHARACTERS, users_before=0)


@pytest.mark.parametrize("length_key", ["short", "long"])
def test_a_username_outside_the_length_limits_is_refused_and_the_limits_are_named(client, settings, length_key):
    typed = "a" * (settings.USERNAME_MIN_LENGTH - 1) if length_key == "short" else "a" * (settings.USERNAME_MAX_LENGTH + 1)
    response = kit.register(client, typed, STRONG_A)
    assert_refused(response, settings, client, message_for("ab"), users_before=0)
    page = kit.page_text(response)
    assert str(settings.USERNAME_MIN_LENGTH) in page and str(settings.USERNAME_MAX_LENGTH) in page


def test_usernames_at_both_length_limits_are_accepted(client, settings):
    short = "a" * settings.USERNAME_MIN_LENGTH
    long = "b" * settings.USERNAME_MAX_LENGTH
    assert kit.register(client, short, STRONG_A).status_code == 302
    assert kit.register(type(client)(), long, STRONG_A).status_code == 302
    assert {u.username for u in User.objects.all()} == {short, long}


@pytest.mark.parametrize("typed", ["under_score", "hy-phen", "MixedCase9", "123", "-_-"])
def test_letters_digits_underscore_and_hyphen_are_accepted_and_the_account_is_active(client, typed):
    assert kit.register(client, typed, STRONG_A).status_code == 302
    assert User.objects.get(username=typed).is_active is True


def test_a_blank_username_is_refused(client, settings):
    response = kit.register(client, "", STRONG_A)
    assert response.status_code == 200
    assert "required" in kit.page_text(response).lower() or "enter" in kit.page_text(response).lower()
    assert not User.objects.exists()
    assert kit.logged_in_user_id(client, settings) is None


def test_a_whitespace_only_username_is_refused(client):
    response = kit.register(client, "     ", STRONG_A)
    assert response.status_code == 200
    assert not User.objects.exists()


@pytest.mark.parametrize(
    "username",
    ["alice", "bad name!", "ab"],
    ids=["taken", "characters", "short"],
)
def test_username_errors_keep_what_was_typed_and_never_the_passwords(client, existing, username):
    response = kit.register(client, username, STRONG_A)
    assert response.status_code == 200
    form = kit.find_form(response, with_input="username")
    assert kit.form_values(form)["username"] == username
    assert STRONG_A not in response.content.decode()
    passwords = [i for i in form["inputs"] if i["type"] == "password"]
    assert len(passwords) == 2 and all(not p["value"] for p in passwords)


def test_typed_values_are_escaped_when_shown_again(client):
    hostile = "<script>alert(1)</script>"
    response = kit.register(client, hostile, STRONG_A)
    assert "<script>alert(1)</script>" not in response.content.decode()
    assert kit.form_values(kit.find_form(response, with_input="username"))["username"] == hostile


def test_a_username_taken_a_moment_ago_is_a_friendly_refusal_not_a_server_error(client, settings):
    """A race: the name is free when the form is checked, and taken by the time the account is saved."""
    original_save = User.save
    saves = []

    def racing_save(self, *args, **kwargs):
        saves.append(self.username)
        User.objects.bulk_create([User(username="Alice", username_key="alice", email_key="", password="x")])
        return original_save(self, *args, **kwargs)

    with mock.patch.object(User, "save", racing_save):
        response = kit.register(client, "alice", STRONG_A)
    assert response.status_code == 200
    assert saves == ["alice"]
    assert TAKEN in kit.page_text(response)
    assert not User.objects.filter(password__startswith="argon2").exists()  # nothing was created for this person
    assert kit.logged_in_user_id(client, settings) is None
    kit.assert_no_internal_text(response)


@pytest.mark.parametrize("typed", ["ALICE", "Alice"])
def test_a_taken_username_is_reported_together_with_other_problems(client, settings, existing, typed):
    """The taken check must not depend on the save step: with a weak password the form never reaches it."""
    response = kit.register(client, typed, "short")
    page = kit.page_text(response)
    assert TAKEN in page
    assert "This password is too short." in page
    assert User.objects.count() == 1
