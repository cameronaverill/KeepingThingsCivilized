"""Step 6a: password rules (length 12, not common, not all digits, not similar to username or email, must match)."""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import CommonPasswordValidator, validate_password
from django.core.exceptions import ValidationError

import reg_testkit as kit
from reg_testkit import STRONG_A, STRONG_B

User = get_user_model()


def common_password():
    """A password that is common but passes every other rule (12+ letters, not similar to carol / carol@example.com)."""
    probe = User(username="carol", email="carol@example.com")
    for candidate in sorted(CommonPasswordValidator().passwords):
        if len(candidate) >= 12 and candidate.isalpha():
            try:
                validate_password(candidate, user=probe)
            except ValidationError as error:
                if [e.code for e in error.error_list] == ["password_too_common"]:
                    return candidate
    raise AssertionError("no suitable common password found")


def validator_messages(password, username="carol", email="carol@example.com"):
    """The messages Django's own validators give for this password (the page also lists the RULES, so only the exact
    complaint counts as proof that the password was refused for that reason)."""
    with pytest.raises(ValidationError) as caught:
        validate_password(password, user=User(username=username, email=email))
    return caught.value.messages


def attempt(client, password, confirm=None, username="carol", email="carol@example.com"):
    response = kit.register(client, username, email, password, confirm)
    return response


def assert_refused(response, mailoutbox, *texts):
    assert response.status_code == 200
    page = kit.page_text(response)
    for text in texts:
        assert text in page, f"{text!r} not on the page: {page!r}"
    assert not User.objects.exists()
    assert not mailoutbox
    kit.assert_no_internal_text(response)


def test_too_short_password_is_refused_with_the_reason_and_the_length(client, mailoutbox, settings):
    response = attempt(client, "Sh0rt-pw!")
    assert_refused(response, mailoutbox, *validator_messages("Sh0rt-pw!"))
    assert f"It must contain at least {settings.PASSWORD_MIN_LENGTH} characters." in kit.page_text(response)


def test_password_exactly_at_the_minimum_length_is_accepted(client, mailoutbox, settings):
    minimum = ("Wq3" + "-Yz8" * 5)[: settings.PASSWORD_MIN_LENGTH]
    assert len(minimum) == settings.PASSWORD_MIN_LENGTH
    response = attempt(client, minimum)
    assert response.status_code == 302
    assert User.objects.filter(username="carol").exists()


def test_the_minimum_length_comes_from_the_settings(client, mailoutbox, settings):
    # The validators live in AUTH_PASSWORD_VALIDATORS; this pins that the view uses the configured ones.
    settings.AUTH_PASSWORD_VALIDATORS = [
        {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 30}}
    ]
    response = attempt(client, STRONG_A)
    assert_refused(response, mailoutbox, "It must contain at least 30 characters.")


def test_common_password_is_refused(client, mailoutbox):
    response = attempt(client, common_password())
    assert_refused(response, mailoutbox, "This password is too common.")


def test_all_digit_password_is_refused(client, mailoutbox):
    response = attempt(client, "8392017461539")
    assert_refused(response, mailoutbox, "This password is entirely numeric.")


def test_password_similar_to_the_username_is_refused(client, mailoutbox):
    probe = User(username="wonderwoman", email="someone@example.com")
    with pytest.raises(ValidationError):
        validate_password("wonderwoman-2024", user=probe)  # guards the choice of example
    response = attempt(client, "wonderwoman-2024", username="wonderwoman", email="someone@example.com")
    assert_refused(response, mailoutbox, *validator_messages("wonderwoman-2024", "wonderwoman", "someone@example.com"))


def test_password_similar_to_the_email_is_refused(client, mailoutbox):
    probe = User(username="bob-b", email="bartholomew@example.com")
    with pytest.raises(ValidationError):
        validate_password("bartholomew-99", user=probe)  # guards the choice of example
    response = attempt(client, "bartholomew-99", username="bob-b", email="bartholomew@example.com")
    assert_refused(response, mailoutbox, *validator_messages("bartholomew-99", "bob-b", "bartholomew@example.com"))


def test_mismatched_passwords_are_refused_and_say_they_do_not_match(client, mailoutbox):
    response = attempt(client, STRONG_A, confirm=STRONG_B)
    assert response.status_code == 200
    assert "match" in kit.page_text(response).lower()
    assert not User.objects.exists()
    assert not mailoutbox


def test_a_confirmation_that_differs_only_by_case_does_not_match(client, mailoutbox):
    response = attempt(client, STRONG_A, confirm=STRONG_A.swapcase())
    assert response.status_code == 200
    assert not User.objects.exists()


@pytest.mark.parametrize("password,confirm", [("", ""), (STRONG_A, ""), ("", STRONG_A)])
def test_missing_password_or_confirmation_is_refused(client, mailoutbox, password, confirm):
    response = attempt(client, password, confirm=confirm)
    assert response.status_code == 200
    assert "required" in kit.page_text(response).lower()
    assert not User.objects.exists()
    assert not mailoutbox


def test_all_the_validators_report_at_once(client, mailoutbox):
    response = attempt(client, "12345")
    page = kit.page_text(response)
    for message in validator_messages("12345"):
        assert message in page
    assert "This password is entirely numeric." in page
    assert not User.objects.exists()


@pytest.mark.parametrize(
    "case",
    ["short", "common", "numeric", "mismatch"],
)
def test_a_refused_password_is_never_shown_again(client, mailoutbox, case):
    typed, confirm = {
        "short": ("Sh0rt-pw!", None),
        "common": (common_password(), None),
        "numeric": ("8392017461539", None),
        "mismatch": (STRONG_A, STRONG_B),
    }[case]
    response = attempt(client, typed, confirm=confirm)
    body = response.content.decode()
    assert typed not in body
    if confirm:
        assert confirm not in body
    form = kit.find_form(response, kit.url("register"), with_input="username")
    for field in form["inputs"]:
        if field["type"] == "password":
            assert not field["value"]


def test_a_refused_registration_keeps_the_username_and_email_the_user_typed(client):
    response = attempt(client, "Sh0rt-pw!", username="Carol_C", email="Carol.C@Example.com")
    values = kit.form_values(kit.find_form(response, kit.url("register"), with_input="username"))
    assert values["username"] == "Carol_C"
    assert values["email"] == "Carol.C@Example.com"


def test_a_very_long_password_is_handled_without_a_server_error(client):
    response = attempt(client, "Ab1-" * 1000)
    assert response.status_code < 500
