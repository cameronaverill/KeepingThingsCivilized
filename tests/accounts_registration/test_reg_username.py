"""Step 6a: username and email field errors are friendly, shown on the form, and the typed values are kept."""
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

import reg_testkit as kit
from accounts.models import USERNAME_TAKEN
from accounts.validators import validate_username
from reg_testkit import STRONG_A

User = get_user_model()


def message_for(value):
    """The model's own friendly message for a username value."""
    try:
        validate_username(value)
    except ValidationError as error:
        return error.messages[0]
    raise AssertionError(f"{value!r} is a valid username")


CHARACTERS = message_for("bad name!")


def length_message():
    return message_for("ab")


@pytest.fixture
def existing(db):
    return User.objects.create_user(username="alice", email="taken@example.com", password=STRONG_A)


def assert_refused(response, mailoutbox, expected, users_before=1):
    assert response.status_code == 200
    page = kit.page_text(response)
    assert expected in page, f"{expected!r} not on the page: {page!r}"
    assert User.objects.count() == users_before
    assert not mailoutbox
    kit.assert_no_internal_text(response)


def test_a_taken_username_is_refused_with_the_models_message(client, existing, mailoutbox):
    response = kit.register(client, "alice", "new@example.com", STRONG_A)
    assert_refused(response, mailoutbox, USERNAME_TAKEN)


@pytest.mark.parametrize("typed", ["ALICE", "Alice", "aLiCe", "alice "])
def test_a_username_taken_ignoring_case_is_refused(client, existing, mailoutbox, typed):
    response = kit.register(client, typed, "new@example.com", STRONG_A)
    assert_refused(response, mailoutbox, USERNAME_TAKEN)


@pytest.mark.parametrize("typed", ["ａｌｉｃｅ", "ＡＬＩＣＥ", "alіce"])
def test_a_lookalike_or_other_unicode_form_of_a_taken_username_is_refused(client, existing, mailoutbox, typed):
    response = kit.register(client, typed, "new@example.com", STRONG_A)
    assert response.status_code == 200
    page = kit.page_text(response)
    assert USERNAME_TAKEN in page or CHARACTERS in page
    assert User.objects.count() == 1
    assert not mailoutbox


@pytest.mark.parametrize("typed", ["bad name", "bad!", "álice", "user@name", "naïve", "名前名", "a.b.c"])
def test_forbidden_characters_are_refused_with_the_models_message(client, mailoutbox, typed):
    response = kit.register(client, typed, "new@example.com", STRONG_A)
    assert_refused(response, mailoutbox, CHARACTERS, users_before=0)


@pytest.mark.parametrize("length_key", ["short", "long"])
def test_a_username_outside_the_length_limits_is_refused_and_the_limits_are_named(
    client, mailoutbox, settings, length_key
):
    typed = "a" * (settings.USERNAME_MIN_LENGTH - 1) if length_key == "short" else "a" * (settings.USERNAME_MAX_LENGTH + 1)
    response = kit.register(client, typed, "new@example.com", STRONG_A)
    assert_refused(response, mailoutbox, length_message(), users_before=0)
    page = kit.page_text(response)
    assert str(settings.USERNAME_MIN_LENGTH) in page and str(settings.USERNAME_MAX_LENGTH) in page


def test_usernames_at_both_length_limits_are_accepted(client, mailoutbox, settings):
    short = "a" * settings.USERNAME_MIN_LENGTH
    long = "b" * settings.USERNAME_MAX_LENGTH
    assert kit.register(client, short, "one@example.com", STRONG_A).status_code == 302
    assert kit.register(client, long, "two@example.com", STRONG_A).status_code == 302
    assert {u.username for u in User.objects.all()} == {short, long}


@pytest.mark.parametrize("typed", ["under_score", "hy-phen", "MixedCase9", "123"])
def test_letters_digits_underscore_and_hyphen_are_accepted(client, typed):
    assert kit.register(client, typed, "new@example.com", STRONG_A).status_code == 302
    assert User.objects.get(username=typed).is_active is False


def test_a_blank_username_is_refused(client, mailoutbox):
    response = kit.register(client, "", "new@example.com", STRONG_A)
    assert response.status_code == 200
    assert "required" in kit.page_text(response).lower() or "enter" in kit.page_text(response).lower()
    assert not User.objects.exists() and not mailoutbox


@pytest.mark.parametrize(
    "case,username,email",
    [
        ("taken", "alice", "keep.me@Example.com"),
        ("characters", "bad name!", "keep.me@Example.com"),
        ("short", "ab", "keep.me@Example.com"),
    ],
)
def test_username_errors_keep_what_was_typed_and_never_the_passwords(client, existing, case, username, email):
    response = kit.register(client, username, email, STRONG_A)
    assert response.status_code == 200
    values = kit.form_values(kit.find_form(response, kit.url("register"), with_input="username"))
    assert values["username"] == username
    assert values["email"] == email
    assert STRONG_A not in response.content.decode()
    passwords = [i for i in kit.find_form(response, kit.url("register"), with_input="username")["inputs"] if i["type"] == "password"]
    assert passwords and all(not p["value"] for p in passwords)


def test_typed_values_are_escaped_when_shown_again(client):
    hostile = "<script>alert(1)</script>"
    response = kit.register(client, hostile, hostile + "@example.com", STRONG_A)
    body = response.content.decode()
    assert "<script>alert(1)</script>" not in body
    values = kit.form_values(kit.find_form(response, kit.url("register"), with_input="username"))
    assert values["username"] == hostile


# --- email field -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "typed",
    ["not-an-email", "a@", "@example.com", "a b@example.com", "a@@example.com", "a@example", "x" * 250 + "@example.com"],
)
def test_a_malformed_email_is_refused_with_a_reason(client, mailoutbox, typed):
    response = kit.register(client, "carol", typed, STRONG_A)
    assert response.status_code == 200
    page = kit.page_text(response).lower()
    assert "email" in page and ("valid" in page or "at most" in page or "no more than" in page)
    assert not User.objects.exists() and not mailoutbox


def test_an_email_with_a_line_break_cannot_inject_headers(client, mailoutbox):
    response = kit.register(client, "carol", "carol@example.com\nBcc: victim@example.com", STRONG_A)
    assert response.status_code == 200
    assert not User.objects.exists() and not mailoutbox


def test_a_blank_email_is_refused(client, mailoutbox):
    response = kit.register(client, "carol", "", STRONG_A)
    assert response.status_code == 200
    assert not User.objects.exists() and not mailoutbox


def test_the_email_is_trimmed_before_it_is_stored_and_mailed(client, mailoutbox):
    kit.register(client, "carol", "  Carol@Example.com  ", STRONG_A)
    assert User.objects.get(username="carol").email.lower() == "carol@example.com"
    assert [a.lower() for a in mailoutbox[0].to] == ["carol@example.com"]
