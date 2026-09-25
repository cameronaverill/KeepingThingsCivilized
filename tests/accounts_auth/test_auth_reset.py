"""Password reset, end to end with the in-memory mailbox."""
import pytest
from auth_testkit import (
    NEW_PASSWORD,
    PASSWORD,
    RESET_SENT,
    WRONG_PASSWORD,
    alice,
    clean_axes,
    force_login,
    is_logged_in,
    links_to,
    login_post,
    make_user,
    normalized,
    open_reset_link,
    request_reset,
    reset_link,
    template_names,
    text,
)
from django.conf import settings
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db
EMAIL = "alice@example.com"


def set_password(client, final_url, first=NEW_PASSWORD, second=None):
    return client.post(final_url, {"new_password1": first, "new_password2": first if second is None else second})


def can_log_in_with(password, username="alice"):
    client = Client()
    login_post(client, username, password)
    return is_logged_in(client)


def test_the_request_page_renders_a_form_on_the_accounts_shell(client, clean_axes):
    response = client.get(reverse("accounts:password_reset"))
    assert response.status_code == 200
    assert "accounts/base.html" in template_names(response)
    assert 'name="email"' in text(response)


def test_a_known_address_gets_exactly_one_plain_text_email_with_an_absolute_link(client, alice, mailoutbox):
    response = request_reset(client, EMAIL)
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:password_reset_done")
    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert message.to == [EMAIL]
    assert message.from_email == settings.DEFAULT_FROM_EMAIL
    assert message.subject.strip() and "\n" not in message.subject
    assert list(message.alternatives) == [], "the email is plain text"
    link = reset_link(message)
    assert link.startswith("http://testserver/accounts/reset/")


def test_the_email_never_contains_a_password_or_hash(client, alice, mailoutbox):
    request_reset(client, EMAIL)
    body = mailoutbox[0].subject + mailoutbox[0].body
    assert PASSWORD not in body
    assert alice.password not in body
    assert "argon2" not in body.lower()


def test_the_address_is_matched_ignoring_case(client, alice, mailoutbox):
    request_reset(client, "ALICE@Example.COM")
    assert len(mailoutbox) == 1


def test_the_sent_page_says_if_an_account_exists(client, alice, mailoutbox):
    response = client.post(reverse("accounts:password_reset"), {"email": EMAIL}, follow=True)
    assert response.status_code == 200
    assert RESET_SENT in text(response)
    assert "accounts/base.html" in template_names(response)


def test_an_unknown_address_looks_exactly_the_same_and_sends_nothing(alice, mailoutbox):
    known = Client().post(reverse("accounts:password_reset"), {"email": EMAIL}, follow=True)
    del mailoutbox[:]
    unknown = Client().post(reverse("accounts:password_reset"), {"email": "nobody@example.com"}, follow=True)
    assert mailoutbox == []
    assert unknown.status_code == known.status_code
    assert unknown.redirect_chain == known.redirect_chain
    assert normalized(unknown) == normalized(known)
    assert RESET_SENT in text(unknown)


def test_an_unconfirmed_account_gets_the_same_page_whatever_is_decided_about_the_email(client, clean_axes, mailoutbox):
    make_user("pending_pat", active=False)
    unconfirmed = client.post(reverse("accounts:password_reset"), {"email": "pending_pat@example.com"}, follow=True)
    unknown = Client().post(reverse("accounts:password_reset"), {"email": "nobody@example.com"}, follow=True)
    assert unconfirmed.redirect_chain == unknown.redirect_chain
    assert normalized(unconfirmed) == normalized(unknown)


def test_an_unconfirmed_account_is_not_mailed_a_reset_link(client, clean_axes, mailoutbox):
    """The builder's documented decision (accounts/authviews.py): nobody may use the form to mail an unconfirmed address."""
    make_user("pending_pat", active=False)
    client.post(reverse("accounts:password_reset"), {"email": "pending_pat@example.com"})
    assert mailoutbox == []


def test_a_malformed_address_is_refused_on_the_form_with_a_reason(client, alice, mailoutbox):
    response = client.post(reverse("accounts:password_reset"), {"email": "not-an-address"})
    assert response.status_code == 200
    assert "valid email" in text(response).lower()
    assert mailoutbox == []


# --- the link -------------------------------------------------------------------------------------------------------------


def test_the_full_flow_resets_the_password_and_the_new_one_logs_in(client, alice, mailoutbox):
    request_reset(client, EMAIL)
    response, final = open_reset_link(client, reset_link(mailoutbox[0]))
    assert response.status_code == 200
    assert "new_password1" in text(response)
    assert "accounts/base.html" in template_names(response)
    done = set_password(client, final)
    assert done.status_code == 302
    assert done["Location"] == reverse("accounts:password_reset_complete")
    assert can_log_in_with(NEW_PASSWORD)
    assert not can_log_in_with(PASSWORD)


def test_the_complete_page_tells_the_user_what_happened_and_links_to_login(client, alice, mailoutbox):
    request_reset(client, EMAIL)
    _, final = open_reset_link(client, reset_link(mailoutbox[0]))
    page = client.post(final, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}, follow=True)
    assert page.status_code == 200
    assert "password" in text(page).lower()
    assert links_to(page, "accounts:login")


def test_the_link_works_only_once(alice, mailoutbox):
    request_reset(Client(), EMAIL)
    link = reset_link(mailoutbox[0])
    first = Client()
    _, final = open_reset_link(first, link)
    assert set_password(first, final).status_code == 302
    second = Client()
    response, _ = open_reset_link(second, link)
    assert "new_password1" not in text(response), "a used link must not show the form again"
    assert can_log_in_with(NEW_PASSWORD)
    # Replaying the form post with the old session state must not change the password again.
    attempt = set_password(first, final, first="another-" + "Passphrase-55")
    assert attempt.status_code != 302 or attempt["Location"] != reverse("accounts:password_reset_complete")
    assert can_log_in_with(NEW_PASSWORD)


def test_a_used_link_shows_the_invalid_link_page_with_a_way_forward(alice, mailoutbox):
    request_reset(Client(), EMAIL)
    link = reset_link(mailoutbox[0])
    first = Client()
    _, final = open_reset_link(first, link)
    set_password(first, final)
    response, _ = open_reset_link(Client(), link)
    page = text(response).lower()
    assert response.status_code in (200, 400, 410)
    assert "link" in page and any(word in page for word in ("invalid", "expired", "already been used", "already used"))
    assert links_to(response, "accounts:password_reset"), "the page must offer a way to ask for a new link"


@pytest.mark.parametrize(
    "path",
    ["/accounts/reset/zz/not-a-token/", "/accounts/reset/MQ/aaaaaa-bbbbbbbbbbbbbbbbbbbb/", "/accounts/reset/NDA0/set-password/"],
)
def test_nonsense_links_show_the_invalid_link_page(client, alice, path):
    response = client.get(path, follow=True)
    page = text(response)
    assert response.status_code in (200, 400, 404, 410)
    assert "new_password1" not in page
    if response.status_code != 404:
        assert "link" in page.lower()
        assert links_to(response, "accounts:password_reset")


def test_a_tampered_token_is_refused(alice, mailoutbox):
    request_reset(Client(), EMAIL)
    link = reset_link(mailoutbox[0])
    head, token = link.rstrip("/").rsplit("/", 1)
    flipped = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    response, _ = open_reset_link(Client(), f"{head}/{flipped}/")
    assert "new_password1" not in text(response)
    assert can_log_in_with(PASSWORD)


def test_a_link_for_one_account_cannot_reset_another(alice, mailoutbox):
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    bob = make_user("bob")
    request_reset(Client(), EMAIL)
    head, _uid, token = reset_link(mailoutbox[0]).rstrip("/").rsplit("/", 2)
    swapped = f"{head}/{urlsafe_base64_encode(force_bytes(bob.pk))}/{token}/"
    response, _ = open_reset_link(Client(), swapped)
    assert "new_password1" not in text(response)


def test_a_password_change_after_asking_for_the_link_kills_the_link(alice, mailoutbox):
    request_reset(Client(), EMAIL)
    link = reset_link(mailoutbox[0])
    alice.set_password("changed-" + "meanwhile-31Q")
    alice.save()
    response, _ = open_reset_link(Client(), link)
    assert "new_password1" not in text(response)


# --- the new password -----------------------------------------------------------------------------------------------------


def _validator_messages(password):
    from accounts.models import User

    try:
        validate_password(password, User.objects.get(username="alice"))
    except ValidationError as error:
        return list(error.messages)
    return []


@pytest.mark.parametrize(
    "bad",
    ["short" + "1", "qwerty" + "123456", "48151623" * 3, "alice@" + "example.com"],
    ids=["too-short", "too-common", "all-digits", "similar-to-email"],
)
def test_the_new_password_must_pass_the_validators_and_the_link_stays_usable(alice, mailoutbox, bad):
    expected = _validator_messages(bad)
    assert expected, "the chosen password must trip a validator, or this test proves nothing"
    request_reset(Client(), EMAIL)
    link = reset_link(mailoutbox[0])
    client = Client()
    _, final = open_reset_link(client, link)
    response = set_password(client, final, first=bad)
    page = text(response)
    assert response.status_code == 200
    for message in expected:
        assert message in page
    alice.refresh_from_db()
    assert alice.check_password(PASSWORD), "a refused reset must not change the password"
    assert bad not in page, "the password is not echoed back"
    # The mistake did not use the link up: a good password now works.
    assert set_password(client, final).status_code == 302
    assert can_log_in_with(NEW_PASSWORD)


def test_mismatched_passwords_are_refused_with_a_reason(alice, mailoutbox):
    request_reset(Client(), EMAIL)
    client = Client()
    _, final = open_reset_link(client, reset_link(mailoutbox[0]))
    response = set_password(client, final, second=NEW_PASSWORD + "x")
    assert response.status_code == 200
    assert str(SetPasswordForm.error_messages["password_mismatch"]) in text(response)
    alice.refresh_from_db()
    assert alice.check_password(PASSWORD)


# --- sessions -------------------------------------------------------------------------------------------------------------


def test_every_existing_session_of_the_user_is_ended_by_a_reset(alice, mailoutbox):
    laptop, phone = Client(), Client()
    force_login(laptop, alice)
    force_login(phone, alice)
    assert laptop.get(reverse("accounts:password_change")).status_code == 200
    assert phone.get(reverse("accounts:password_change")).status_code == 200

    request_reset(Client(), EMAIL)
    resetter = Client()
    _, final = open_reset_link(resetter, reset_link(mailoutbox[0]))
    assert set_password(resetter, final).status_code == 302

    for device in (laptop, phone):
        response = device.get(reverse("accounts:password_change"))
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("accounts:login"))


def test_a_reset_leaves_other_users_sessions_alone(alice, mailoutbox):
    bob = make_user("bob")
    bobs = Client()
    force_login(bobs, bob)
    request_reset(Client(), EMAIL)
    resetter = Client()
    _, final = open_reset_link(resetter, reset_link(mailoutbox[0]))
    set_password(resetter, final)
    assert bobs.get(reverse("accounts:password_change")).status_code == 200
