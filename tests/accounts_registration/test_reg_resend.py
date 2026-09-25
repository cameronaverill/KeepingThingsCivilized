"""Step 6a: the resend form, its per-address rate limit, and recovery from a failed send."""
import logging
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

import reg_testkit as kit
from reg_testkit import STRONG_A, STRONG_B

User = get_user_model()

STANDARD = "If that address has an unconfirmed account, we sent a new link"
WAIT = re.compile(r"Please wait (\d+) seconds? before requesting another link")


def pending(username="pendinguser", email="pending@example.com"):
    return User.objects.create_user(username=username, email=email, password=STRONG_B, is_active=False)


def answer(client, address, **extra):
    response = kit.follow(client, kit.resend(client, address, **extra))
    return response


def test_resend_page_has_an_email_form_with_csrf(client):
    response = client.get(kit.url("resend"))
    assert response.status_code == 200
    form = kit.find_form(response, kit.url("resend"), with_input="email")
    assert form is not None and form["method"] == "post"
    assert "csrfmiddlewaretoken" in {i["name"] for i in form["inputs"]}


def test_resend_says_the_standard_sentence_for_a_pending_address(client, mailoutbox):
    pending()
    response = answer(client, "pending@example.com")
    assert response.status_code == 200
    assert STANDARD in kit.page_text(response)
    assert len(mailoutbox) == 1


def test_resend_says_the_same_sentence_for_an_unknown_address_and_sends_nothing(client, mailoutbox):
    response = answer(client, "nobody@example.com")
    assert STANDARD in kit.page_text(response)
    assert not mailoutbox


def test_resend_sends_nothing_to_a_confirmed_account(client, mailoutbox):
    user = pending()
    user.is_active = True
    user.email_verified_at = timezone.now()
    user.save()
    response = answer(client, "pending@example.com")
    assert STANDARD in kit.page_text(response)
    assert not mailoutbox


def test_the_new_link_confirms_the_account(client, mailoutbox):
    pending()
    kit.resend(client, "pending@example.com")
    message = mailoutbox[0]
    assert message.to == ["pending@example.com"]
    kit.open_link(client, kit.only_link(message)[0])
    assert User.objects.get(username="pendinguser").is_active is True


def test_resend_does_not_activate_or_change_the_account(client):
    user = pending()
    kit.resend(client, "pending@example.com")
    after = User.objects.get(pk=user.pk)
    assert (after.is_active, after.email_verified_at, after.password) == (False, None, user.password)


def test_resend_finds_the_account_whatever_the_case_of_the_address(client, mailoutbox):
    pending(email="Pending@Example.com")
    kit.resend(client, "PENDING@example.COM")
    assert len(mailoutbox) == 1
    assert [a.lower() for a in mailoutbox[0].to] == ["pending@example.com"]


@pytest.mark.parametrize("typed", ["", "not-an-email", "a@", "a b@example.com", "x" * 260 + "@example.com"])
def test_resend_with_a_malformed_address_says_what_to_fix(client, mailoutbox, typed):
    response = answer(client, typed)
    assert response.status_code == 200
    page = kit.page_text(response)
    assert STANDARD not in page
    assert "email" in page.lower()
    assert kit.find_form(response, kit.url("resend"), with_input="email") is not None
    assert not mailoutbox
    kit.assert_no_internal_text(response)


def test_a_malformed_address_is_shown_again_escaped(client):
    hostile = "<script>alert(1)</script>"
    response = answer(client, hostile)
    assert hostile not in response.content.decode()


# --- the rate limit --------------------------------------------------------------------------------------------------
def test_a_second_request_within_the_limit_is_refused_and_names_the_wait(client, mailoutbox, settings):
    pending()
    answer(client, "pending@example.com")
    response = answer(client, "pending@example.com")
    text = kit.page_text(response)
    match = WAIT.search(text)
    assert match, text
    assert 1 <= int(match.group(1)) <= settings.RESEND_CONFIRMATION_MIN_SECONDS
    assert STANDARD not in text
    assert len(mailoutbox) == 1
    assert kit.find_form(response, kit.url("resend"), with_input="email") is not None  # the way to try again


def test_the_wait_shrinks_as_time_passes(client, mailoutbox, clock, settings):
    pending()
    answer(client, "pending@example.com")
    clock.advance(seconds=20)
    wait = int(WAIT.search(kit.page_text(answer(client, "pending@example.com"))).group(1))
    assert wait == pytest.approx(settings.RESEND_CONFIRMATION_MIN_SECONDS - 20, abs=1)


def test_the_limit_ends_after_the_configured_seconds(client, mailoutbox, clock, settings):
    pending()
    answer(client, "pending@example.com")
    clock.advance(seconds=settings.RESEND_CONFIRMATION_MIN_SECONDS - 1)
    assert WAIT.search(kit.page_text(answer(client, "pending@example.com")))
    clock.advance(seconds=2)
    response = answer(client, "pending@example.com")
    assert STANDARD in kit.page_text(response)
    assert len(mailoutbox) == 2


def test_the_limit_comes_from_the_settings(client, mailoutbox, clock, settings):
    settings.RESEND_CONFIRMATION_MIN_SECONDS = 120
    pending()
    answer(client, "pending@example.com")
    clock.advance(seconds=100)
    assert 19 <= int(WAIT.search(kit.page_text(answer(client, "pending@example.com"))).group(1)) <= 20
    clock.advance(seconds=25)
    answer(client, "pending@example.com")
    assert len(mailoutbox) == 2


def test_the_limit_applies_to_an_unknown_address_too(client, mailoutbox, settings):
    answer(client, "nobody@example.com")
    text = kit.page_text(answer(client, "nobody@example.com"))
    match = WAIT.search(text)
    assert match, text
    assert 1 <= int(match.group(1)) <= settings.RESEND_CONFIRMATION_MIN_SECONDS


def test_the_limit_is_per_address_not_per_browser_and_not_global(client, mailoutbox):
    pending()
    pending("secondone", "second@example.com")
    answer(client, "pending@example.com")
    assert WAIT.search(kit.page_text(answer(Client(), "pending@example.com")))  # another browser, same address
    assert STANDARD in kit.page_text(answer(client, "second@example.com"))  # same browser, another address
    assert len(mailoutbox) == 2


def test_the_limit_ignores_the_case_of_the_address(client, mailoutbox):
    pending()
    answer(client, "pending@example.com")
    assert WAIT.search(kit.page_text(answer(client, "PENDING@Example.com")))
    assert len(mailoutbox) == 1


def test_the_limit_does_not_depend_on_the_client_address(client, mailoutbox):
    pending()
    answer(client, "pending@example.com", REMOTE_ADDR="10.0.0.1")
    assert WAIT.search(kit.page_text(answer(Client(), "pending@example.com", REMOTE_ADDR="10.0.0.2")))


def test_a_limited_request_does_not_extend_the_wait(client, mailoutbox, clock, settings):
    pending()
    answer(client, "pending@example.com")
    for _ in range(5):
        clock.advance(seconds=10)
        answer(client, "pending@example.com")  # limited each time
    clock.advance(seconds=settings.RESEND_CONFIRMATION_MIN_SECONDS - 50 + 1)
    answer(client, "pending@example.com")
    assert len(mailoutbox) == 2


# --- recovery from a failed send ---------------------------------------------------------------------------------
def test_failed_send_at_registration_leaves_a_recoverable_account(client, mail_switch, mailoutbox, clock, settings, caplog):
    caplog.set_level(logging.DEBUG)
    mail_switch.error = OSError("the mail server is unreachable")
    response = kit.register(client, "carol", "carol@example.com", STRONG_A)
    assert response.status_code == 302 and response["Location"] == kit.url("check_email")
    user = User.objects.get(username="carol")
    assert user.is_active is False and user.email_verified_at is None
    assert "We sent a confirmation link to that address." in kit.page_text(kit.follow(client, response))
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], "the failure must be logged as an error"
    assert not mailoutbox
    # The mail server comes back; after the limit has passed, resend delivers a working link.
    mail_switch.error = None
    clock.advance(seconds=settings.RESEND_CONFIRMATION_MIN_SECONDS + 1)
    answer(client, "carol@example.com")
    assert len(mailoutbox) == 1
    kit.open_link(client, kit.only_link(mailoutbox[0])[0])
    assert User.objects.get(username="carol").is_active is True


def test_a_send_that_failed_does_not_start_the_wait(client, mail_switch, mailoutbox):
    """Nothing was delivered, so a person who retries at once is not told to wait (natural reading of 'resend works')."""
    mail_switch.error = OSError("the mail server is unreachable")
    kit.register(client, "carol", "carol@example.com", STRONG_A)
    mail_switch.error = None
    response = answer(client, "carol@example.com")
    assert STANDARD in kit.page_text(response)
    assert len(mailoutbox) == 1


def test_failed_send_at_resend_does_not_show_an_error_or_create_anything(client, mail_switch, caplog):
    caplog.set_level(logging.DEBUG)
    pending()
    mail_switch.error = OSError("the mail server is unreachable")
    response = answer(client, "pending@example.com")
    assert response.status_code == 200
    assert STANDARD in kit.page_text(response)
    kit.assert_no_internal_text(response)
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], "the failure must be logged as an error"
    assert User.objects.count() == 1
