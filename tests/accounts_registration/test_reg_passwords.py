"""Step 6c: password rules on the register form (length 12, not common, not all digits, not similar to the username,
the two boxes must match). Every refusal creates no account and logs nobody in. There is no email to be similar to."""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import CommonPasswordValidator, validate_password
from django.core.exceptions import ValidationError

import reg_testkit as kit
from reg_testkit import STRONG_A, STRONG_B

User = get_user_model()


def common_password():
    """A password that is common but passes every other rule (12+ letters, not similar to the username 'carol')."""
    probe = User(username="carol")
    for candidate in sorted(CommonPasswordValidator().passwords):
        if len(candidate) >= 12 and candidate.isalpha():
            try:
                validate_password(candidate, user=probe)
            except ValidationError as error:
                if [e.code for e in error.error_list] == ["password_too_common"]:
                    return candidate
    raise AssertionError("no suitable common password found")


def validator_messages(password, username="carol"):
    """The messages Django's own validators give for this password (the page also lists the RULES, so only the exact
    complaint counts as proof that the password was refused for that reason)."""
    with pytest.raises(ValidationError) as caught:
        validate_password(password, user=User(username=username))
    return caught.value.messages


def attempt(client, password, confirm=None, username="carol"):
    return kit.register(client, username, password, confirm)


def assert_refused(response, client, settings, *texts):
    assert response.status_code == 200
    page = kit.page_text(response)
    for text in texts:
        assert text in page, f"{text!r} not on the page: {page!r}"
    assert not User.objects.exists()
    assert kit.logged_in_user_id(client, settings) is None
    kit.assert_no_internal_text(response)


def test_too_short_password_is_refused_with_the_reason_and_the_length(client, settings):
    response = attempt(client, "Sh0rt-pw!")
    assert_refused(response, client, settings, *validator_messages("Sh0rt-pw!"))
    assert f"It must contain at least {settings.PASSWORD_MIN_LENGTH} characters." in kit.page_text(response)


def test_password_exactly_at_the_minimum_length_is_accepted(client, settings):
    minimum = ("Wq3" + "-Yz8" * 5)[: settings.PASSWORD_MIN_LENGTH]
    assert len(minimum) == settings.PASSWORD_MIN_LENGTH
    assert attempt(client, minimum).status_code == 302
    assert User.objects.filter(username="carol").exists()


def test_one_character_under_the_minimum_is_refused(client, settings):
    almost = ("Wq3" + "-Yz8" * 5)[: settings.PASSWORD_MIN_LENGTH - 1]
    response = attempt(client, almost)
    assert_refused(response, client, settings, f"It must contain at least {settings.PASSWORD_MIN_LENGTH} characters.")


def test_the_minimum_length_comes_from_the_settings(client, settings):
    settings.AUTH_PASSWORD_VALIDATORS = [
        {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 30}}
    ]
    response = attempt(client, STRONG_A)
    assert_refused(response, client, settings, "It must contain at least 30 characters.")


def test_the_configured_validators_are_the_ones_applied(client, settings):
    """With every validator removed, a password of one character is accepted: the view adds no rules of its own."""
    settings.AUTH_PASSWORD_VALIDATORS = []
    assert attempt(client, "x").status_code == 302
    assert User.objects.filter(username="carol").exists()


def test_common_password_is_refused(client, settings):
    response = attempt(client, common_password())
    assert_refused(response, client, settings, "This password is too common.")


def test_all_digit_password_is_refused(client, settings):
    response = attempt(client, "8392017461539")
    assert_refused(response, client, settings, "This password is entirely numeric.")


def test_password_similar_to_the_username_is_refused(client, settings):
    probe = User(username="wonderwoman")
    with pytest.raises(ValidationError):
        validate_password("wonderwoman-2024", user=probe)  # guards the choice of example
    response = attempt(client, "wonderwoman-2024", username="wonderwoman")
    assert_refused(response, client, settings, *validator_messages("wonderwoman-2024", "wonderwoman"))


def test_a_password_that_only_resembles_a_different_username_is_fine(client):
    assert attempt(client, "wonderwoman-2024", username="carol_c").status_code == 302


def test_mismatched_passwords_are_refused_and_say_they_do_not_match(client, settings):
    response = attempt(client, STRONG_A, confirm=STRONG_B)
    assert_refused(response, client, settings)
    assert "match" in kit.page_text(response).lower()


def test_a_repeat_that_differs_only_by_case_does_not_match(client, settings):
    response = attempt(client, STRONG_A, confirm=STRONG_A.swapcase())
    assert_refused(response, client, settings)
    assert "match" in kit.page_text(response).lower()


def test_a_repeat_that_differs_only_by_a_trailing_space_does_not_match(client, settings):
    response = attempt(client, STRONG_A, confirm=STRONG_A + " ")
    assert_refused(response, client, settings)
    assert "match" in kit.page_text(response).lower()


@pytest.mark.parametrize("password,confirm", [("", ""), (STRONG_A, ""), ("", STRONG_A)])
def test_missing_password_or_repeat_is_refused(client, settings, password, confirm):
    response = attempt(client, password, confirm=confirm)
    assert response.status_code == 200
    assert "required" in kit.page_text(response).lower()
    assert not User.objects.exists()
    assert kit.logged_in_user_id(client, settings) is None


def test_all_the_validators_report_at_once(client, settings):
    response = attempt(client, "12345")
    assert_refused(response, client, settings, *validator_messages("12345"))
    assert "This password is entirely numeric." in kit.page_text(response)


@pytest.mark.parametrize("case", ["short", "common", "numeric", "mismatch"])
def test_a_refused_password_is_never_shown_again(client, case):
    typed, confirm = {
        "short": ("Sh0rt-pw!", None),
        "common": (common_password(), None),
        "numeric": ("8392017461539", None),
        "mismatch": (STRONG_A, STRONG_B),
    }[case]
    response = attempt(client, typed, confirm=confirm)
    body = response.content.decode()
    assert typed not in body
    assert (confirm or typed) not in body
    form = kit.find_form(response, with_input="username")
    passwords = [i for i in form["inputs"] if i["type"] == "password"]
    assert len(passwords) == 2 and all(not p["value"] for p in passwords)


def test_a_refused_registration_keeps_the_username_the_user_typed(client):
    response = attempt(client, "Sh0rt-pw!", username="Carol_C")
    assert kit.form_values(kit.find_form(response, with_input="username"))["username"] == "Carol_C"


def test_a_very_long_password_is_handled_without_a_server_error(client):
    assert attempt(client, "Ab1-" * 1000).status_code < 500
