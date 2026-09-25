"""Step 6a: expired, tampered, reused and malformed confirmation links; each gets the right page and next step."""
import pytest
from django.contrib.auth import get_user_model
from django.core import signing

import reg_testkit as kit
from reg_testkit import STRONG_A, STRONG_B

User = get_user_model()

EXPIRED = "This link has expired. Request a new one."
INVALID = "This link is not valid."
USED = "Your email is already confirmed. You can log in."
CONFIRMED = "Your email is confirmed. You can now log in"


@pytest.fixture
def signup(client, mailoutbox):
    """Register alice and return (link, token) from her email."""
    kit.register(client, "alice", "alice@example.com", STRONG_A)
    return kit.only_link(mailoutbox[0])


def alice():
    return User.objects.get(username="alice")


def assert_not_activated():
    user = alice()
    assert user.is_active is False
    assert user.email_verified_at is None


def flip(token, index):
    """The token with one character changed (kept inside the URL-safe alphabet)."""
    char = token[index]
    return token[:index] + ("A" if char != "A" else "B") + token[index + 1:]


# --- expiry ----------------------------------------------------------------------------------------------------------
def test_an_expired_link_says_it_expired_and_offers_resend(client, signup, clock, settings):
    clock.advance(days=settings.EMAIL_CONFIRM_MAX_AGE_DAYS, seconds=5)
    response = kit.open_link(client, signup[0])
    assert response.status_code < 500
    assert EXPIRED in kit.page_text(response)
    form = kit.find_form(response, kit.url("resend"), with_input="email")
    assert form is not None and form["method"] == "post"
    assert "csrfmiddlewaretoken" in {i["name"] for i in form["inputs"]}
    assert_not_activated()


def test_a_link_just_inside_the_limit_still_works(client, signup, clock, settings):
    clock.advance(days=settings.EMAIL_CONFIRM_MAX_AGE_DAYS, seconds=-60)
    response = kit.open_link(client, signup[0])
    assert CONFIRMED in kit.page_text(response)
    assert alice().is_active is True


def test_the_limit_is_read_from_the_settings(client, signup, clock, settings):
    settings.EMAIL_CONFIRM_MAX_AGE_DAYS = 1
    clock.advance(seconds=23 * 3600)
    assert CONFIRMED in kit.page_text(kit.open_link(client, signup[0]))


def test_the_limit_from_the_settings_also_expires_links(client, signup, clock, settings):
    settings.EMAIL_CONFIRM_MAX_AGE_DAYS = 1
    clock.advance(seconds=25 * 3600)
    assert EXPIRED in kit.page_text(kit.open_link(client, signup[0]))
    assert_not_activated()


def test_the_resend_form_on_the_expired_page_gets_a_new_working_link(client, signup, mailoutbox, clock, settings):
    clock.advance(days=settings.EMAIL_CONFIRM_MAX_AGE_DAYS, seconds=5)
    expired = kit.open_link(client, signup[0])
    form = kit.find_form(expired, kit.url("resend"), with_input="email")
    data = kit.form_values(form)
    data["email"] = "alice@example.com"
    kit.follow(client, client.post(kit.url("resend"), data))
    assert len(mailoutbox) == 2
    new_link = kit.only_link(mailoutbox[1])[0]
    assert new_link != signup[0]
    response = kit.open_link(client, new_link)
    assert CONFIRMED in kit.page_text(response)
    assert alice().is_active is True


def test_an_expired_link_stays_dead_after_a_new_one_is_sent(client, signup, mailoutbox, clock, settings):
    clock.advance(days=settings.EMAIL_CONFIRM_MAX_AGE_DAYS, seconds=5)
    kit.resend(client, "alice@example.com")
    kit.open_link(client, signup[0])
    assert_not_activated()


# --- tampering and malformed tokens -------------------------------------------------------------------------------
@pytest.mark.parametrize("where", ["first", "middle", "last"])
def test_a_token_with_one_changed_character_is_not_valid(client, signup, where):
    token = signup[1]
    index = {"first": 0, "middle": len(token) // 2, "last": len(token) - 1}[where]
    response = kit.open_link(client, kit.confirm_path(flip(token, index)))
    assert response.status_code < 500
    assert INVALID in kit.page_text(response)
    assert_not_activated()


def test_a_token_with_another_users_signature_is_not_valid(client, mailoutbox):
    kit.register(client, "alice", "alice@example.com", STRONG_A)
    kit.register(client, "bobby", "bobby@example.com", STRONG_B)
    a, b = kit.only_link(mailoutbox[0])[1].split(":"), kit.only_link(mailoutbox[1])[1].split(":")
    spliced = ":".join(a[:-1] + [b[-1]])
    response = kit.open_link(client, kit.confirm_path(spliced))
    assert INVALID in kit.page_text(response)
    assert not User.objects.filter(is_active=True).exists()


def test_a_truncated_token_is_not_valid(client, signup):
    response = kit.open_link(client, kit.confirm_path(signup[1][:-8]))
    assert INVALID in kit.page_text(response)
    assert_not_activated()


@pytest.mark.parametrize(
    "token",
    [
        "x",
        "not-a-token",
        ":",
        "::",
        "a:b:c",
        "MQ:1abc:def",
        "0" * 200,
        "A" * 5000,
        "é" * 20,
        "..",
        "%00",
        "<script>alert(1)</script>",
        "1:1:1",
    ],
)
def test_malformed_tokens_get_the_invalid_page_not_an_error(client, token):
    response = kit.open_link(client, kit.confirm_path(token))
    assert response.status_code < 500
    assert INVALID in kit.page_text(response)
    kit.assert_no_internal_text(response)


def test_the_invalid_page_does_not_echo_the_token(client):
    token = "sneaky-" + "marker-9"
    response = kit.open_link(client, kit.confirm_path(token))
    assert token not in response.content.decode()


def test_a_token_signed_for_another_purpose_is_not_valid(client, signup):
    user = alice()
    for forged in (
        signing.dumps({"u": user.pk}),
        signing.dumps(user.pk),
        signing.dumps({"u": user.pk}, salt="some.other.purpose"),
        signing.TimestampSigner().sign(str(user.pk)),
        signing.Signer().sign(str(user.pk)),
    ):
        response = kit.open_link(client, kit.confirm_path(forged))
        assert INVALID in kit.page_text(response)
    assert_not_activated()


def test_a_token_signed_with_another_secret_is_not_valid(client, signup, settings):
    settings.SECRET_KEY = "another-" + "signing-" + "value-" + "x" * 40
    response = kit.open_link(client, signup[0])
    assert INVALID in kit.page_text(response)
    assert_not_activated()


def test_a_link_for_a_deleted_account_is_not_valid(client, signup):
    User.objects.all().delete()
    response = kit.open_link(client, signup[0])
    assert response.status_code < 500
    assert INVALID in kit.page_text(response)


# --- reuse -----------------------------------------------------------------------------------------------------------
def test_a_reused_link_says_already_confirmed_and_points_to_login(client, signup):
    kit.open_link(client, signup[0])
    response = kit.open_link(client, signup[0])
    assert response.status_code < 500
    assert USED in kit.page_text(response)
    assert kit.leads_to(response, kit.login_url())


def test_an_older_link_is_used_up_once_a_newer_one_confirmed_the_account(client, signup, mailoutbox, clock):
    clock.advance(seconds=61)
    kit.resend(client, "alice@example.com")
    assert len(mailoutbox) == 2
    kit.open_link(client, kit.only_link(mailoutbox[1])[0])
    response = kit.open_link(client, signup[0])
    assert USED in kit.page_text(response)


# --- every failure page: reason, next step, no internals ----------------------------------------------------------
def test_expired_page_names_reason_and_next_step(client, signup, clock, settings):
    clock.advance(days=settings.EMAIL_CONFIRM_MAX_AGE_DAYS + 1)
    response = kit.open_link(client, signup[0])
    text = kit.page_text(response)
    assert "expired" in text and "Request a new one" in text
    assert kit.leads_to(response, kit.url("resend"))
    kit.assert_no_internal_text(response)


def test_invalid_page_names_reason_and_a_next_step(client, signup):
    response = kit.open_link(client, kit.confirm_path(flip(signup[1], 3)))
    assert "not valid" in kit.page_text(response)
    assert kit.leads_to(response, kit.url("resend"), kit.url("register"))
    kit.assert_no_internal_text(response)


def test_used_page_names_reason_and_next_step(client, signup):
    kit.open_link(client, signup[0])
    response = kit.open_link(client, signup[0])
    assert "already confirmed" in kit.page_text(response)
    assert kit.leads_to(response, kit.login_url())
    kit.assert_no_internal_text(response)


def test_a_link_still_works_two_days_later(client, signup, clock):
    clock.advance(days=2)
    assert CONFIRMED in kit.page_text(kit.open_link(client, signup[0]))
