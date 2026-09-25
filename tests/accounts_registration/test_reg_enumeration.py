"""Step 6a: no email enumeration. New, pending and confirmed addresses are indistinguishable on the page; only the
emails differ; no second account is ever created; the resend form answers every address alike."""
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

import reg_testkit as kit
from reg_testkit import STRONG_A, STRONG_B

User = get_user_model()

PENDING = "pending@example.com"
CONFIRMED = "confirmed@example.com"
FRESH = "fresh@example.com"
STANDARD = "If that address has an unconfirmed account, we sent a new link"


def make_user(username, email, confirmed=False, password=STRONG_B):
    user = User.objects.create_user(username=username, email=email, password=password, is_active=confirmed)
    if confirmed:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])
    return user


@pytest.fixture
def world(db):
    """A pending account and a confirmed account already exist."""
    return {
        "pending": make_user("pendinguser", PENDING),
        "confirmed": make_user("confirmeduser", CONFIRMED, confirmed=True),
    }


def register_as(kind, client=None, password=STRONG_A):
    """Register (with a new username) using a fresh, pending or confirmed address; returns (client, response)."""
    client = client or Client()
    address, username = {"fresh": (FRESH, "newname1"), "pending": (PENDING, "newname2"), "confirmed": (CONFIRMED, "newname3")}[kind]
    return client, kit.register(client, username, address, password)


def kit_client():
    return Client()


HIDE = [FRESH, PENDING, CONFIRMED, "newname1", "newname2", "newname3"]


# --- registration ----------------------------------------------------------------------------------------------------
def test_registration_looks_identical_for_new_pending_and_confirmed_addresses(world, settings):
    seen = {}
    for kind in ("fresh", "pending", "confirmed"):
        client, response = register_as(kind)
        seen[kind] = (response.status_code, kit.snapshot(client, response, HIDE), kit.cookie_names(client), kit.session_keys(client, settings))
    assert seen["pending"] == seen["fresh"]
    assert seen["confirmed"] == seen["fresh"]
    assert seen["fresh"][0] == 302 and seen["fresh"][1]["chain"][0][1] == kit.url("check_email")


def test_the_redirect_target_carries_no_address(world):
    for kind in ("fresh", "pending", "confirmed"):
        _, response = register_as(kind)
        assert response["Location"] == kit.url("check_email")


def test_registering_a_pending_address_creates_no_second_account_and_changes_nothing(world):
    before = User.objects.get(username="pendinguser")
    register_as("pending")
    assert User.objects.count() == 2
    assert not User.objects.filter(username="newname2").exists()
    after = User.objects.get(username="pendinguser")
    assert (after.password, after.username, after.email, after.is_active) == (
        before.password, before.username, before.email, False,
    )
    assert after.check_password(STRONG_B) and not after.check_password(STRONG_A)


def test_registering_a_confirmed_address_creates_no_second_account_and_changes_nothing(world):
    before = User.objects.get(username="confirmeduser")
    register_as("confirmed")
    assert User.objects.count() == 2
    assert not User.objects.filter(username="newname3").exists()
    after = User.objects.get(username="confirmeduser")
    assert (after.password, after.is_active, after.email_verified_at) == (before.password, True, before.email_verified_at)
    assert after.check_password(STRONG_B)


@pytest.mark.parametrize("variant", ["CONFIRMED@example.com", "Confirmed@Example.COM", "  confirmed@example.com "])
def test_case_and_spacing_variants_of_a_known_address_are_the_same_address(world, variant, mailoutbox):
    client = kit_client()
    response = kit.register(client, "newname3", variant, STRONG_A)
    assert response.status_code == 302 and response["Location"] == kit.url("check_email")
    assert User.objects.count() == 2


def test_a_username_left_free_by_an_ignored_registration_can_be_used_next(world, mailoutbox):
    register_as("confirmed")
    client = kit_client()
    assert kit.register(client, "newname3", "someone.new@example.com", STRONG_A).status_code == 302
    assert User.objects.get(username="newname3").email == "someone.new@example.com"


def test_password_errors_look_the_same_whatever_the_address(world):
    texts = {}
    for kind in ("fresh", "pending", "confirmed"):
        client, response = register_as(kind, password="short")
        assert response.status_code == 200
        texts[kind] = kit.snapshot(client, response, HIDE)["text"]
    assert texts["pending"] == texts["fresh"] == texts["confirmed"]
    assert User.objects.count() == 2


# --- the emails ------------------------------------------------------------------------------------------------------
def test_a_fresh_address_gets_a_confirmation_link_for_the_new_account(world, mailoutbox):
    register_as("fresh")
    assert len(mailoutbox) == 1 and mailoutbox[0].to == [FRESH]
    link, _ = kit.only_link(mailoutbox[0])
    kit.open_link(kit_client(), link)
    assert User.objects.get(username="newname1").is_active is True


def test_a_pending_address_gets_a_fresh_link_that_confirms_the_existing_account(world, mailoutbox):
    register_as("pending")
    assert len(mailoutbox) == 1 and mailoutbox[0].to == [PENDING]
    kit.open_link(kit_client(), kit.only_link(mailoutbox[0])[0])
    assert User.objects.get(username="pendinguser").is_active is True
    assert User.objects.count() == 2


def test_a_confirmed_address_is_told_an_account_exists_and_gets_no_confirmation_link(world, mailoutbox):
    register_as("confirmed")
    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert message.to == [CONFIRMED]
    assert kit.confirmation_links(message) == []
    body = message.body.lower()
    assert "already" in body and "account" in body
    assert "http://testserver" + kit.login_url() in message.body
    assert "http://testserver" + kit.reset_url() in message.body
    assert STRONG_A not in message.body + message.subject and STRONG_B not in message.body + message.subject


def test_the_confirmed_account_email_differs_from_the_confirmation_email(world, mailoutbox):
    register_as("fresh")
    register_as("confirmed")
    assert mailoutbox[0].subject != mailoutbox[1].subject or mailoutbox[0].body != mailoutbox[1].body
    assert kit.confirmation_links(mailoutbox[0]) and not kit.confirmation_links(mailoutbox[1])


def test_registering_a_pending_address_again_within_the_limit_sends_no_second_email_but_looks_the_same(world, mailoutbox, clock):
    first_client, first = register_as("pending")
    assert len(mailoutbox) == 1
    clock.advance(seconds=5)
    client, second = register_as("pending")
    assert len(mailoutbox) == 1
    a = kit.snapshot(first_client, first, HIDE)
    b = kit.snapshot(client, second, HIDE)
    assert a == b


def test_registering_twice_in_a_row_with_a_new_address_sends_one_email(mailoutbox, clock, db):
    """The registration email is a send like any other: submitting the same address again straight away must not
    mail it twice, and the second answer must look like the first."""
    first_client, first = register_as("fresh")
    clock.advance(seconds=5)
    client = Client()
    second = kit.register(client, "newname4", FRESH, STRONG_A)
    assert len(mailoutbox) == 1
    hide = HIDE + ["newname4"]
    assert kit.snapshot(first_client, first, hide) == kit.snapshot(client, second, hide)
    assert User.objects.count() == 1


def test_registering_a_pending_address_again_after_the_limit_sends_a_fresh_link(world, mailoutbox, clock, settings):
    register_as("pending")
    clock.advance(seconds=settings.RESEND_CONFIRMATION_MIN_SECONDS + 1)
    register_as("pending")
    assert len(mailoutbox) == 2
    kit.open_link(kit_client(), kit.only_link(mailoutbox[1])[0])
    assert User.objects.get(username="pendinguser").is_active is True


def test_a_pending_address_that_just_used_resend_is_limited_when_registered_again(world, mailoutbox, clock):
    kit.resend(kit_client(), PENDING)
    assert len(mailoutbox) == 1
    clock.advance(seconds=5)
    register_as("pending")
    assert len(mailoutbox) == 1


def test_a_link_that_expired_for_a_pending_account_can_be_replaced_by_registering_again(world, mailoutbox, clock, settings):
    register_as("pending")
    clock.advance(days=settings.EMAIL_CONFIRM_MAX_AGE_DAYS, seconds=5)
    register_as("pending")
    assert len(mailoutbox) == 2
    assert "confirmed" in kit.page_text(kit.open_link(kit_client(), kit.only_link(mailoutbox[1])[0])).lower()
    assert User.objects.get(username="pendinguser").is_active is True


# --- when the mail server fails --------------------------------------------------------------------------------------
def test_registration_looks_the_same_when_sending_fails_for_every_kind_of_address(world, mail_switch, clock, settings):
    baseline = {}
    for kind in ("fresh", "pending", "confirmed"):
        client, response = register_as(kind)
        baseline[kind] = kit.snapshot(client, response, HIDE)
    User.objects.filter(username="newname1").delete()
    clock.advance(seconds=settings.RESEND_CONFIRMATION_MIN_SECONDS + 1)  # so the send is really attempted
    mail_switch.error = OSError("the mail server is unreachable")
    for kind in ("fresh", "pending", "confirmed"):
        client, response = register_as(kind)
        assert kit.snapshot(client, response, HIDE) == baseline[kind]


# --- resend ----------------------------------------------------------------------------------------------------------
def test_resend_answers_unknown_pending_and_confirmed_addresses_identically(world, mailoutbox, settings):
    seen = {}
    for address in ("nobody@example.com", PENDING, CONFIRMED):
        client = kit_client()
        response = kit.resend(client, address)
        seen[address] = (kit.snapshot(client, response, [address]), kit.cookie_names(client), kit.session_keys(client, settings))
    assert seen[PENDING] == seen["nobody@example.com"]
    assert seen[CONFIRMED] == seen["nobody@example.com"]
    assert STANDARD in seen[PENDING][0]["text"]


def test_resend_sends_mail_only_to_the_pending_address(world, mailoutbox):
    for address in ("nobody@example.com", CONFIRMED):
        kit.resend(kit_client(), address)
    assert len(mailoutbox) == 0
    kit.resend(kit_client(), PENDING)
    assert len(mailoutbox) == 1 and mailoutbox[0].to == [PENDING]
    assert len(kit.confirmation_links(mailoutbox[0])) == 1


def test_rate_limited_resend_answers_every_address_alike_and_names_the_wait(world, mailoutbox, clock, settings):
    addresses = ("nobody@example.com", PENDING, CONFIRMED)
    for address in addresses:
        kit.resend(kit_client(), address)
    assert len(mailoutbox) == 1  # only the pending account was actually mailed
    waits = {}
    for address in addresses:
        client = kit_client()
        response = kit.resend(client, address)
        waits[address] = kit.snapshot(client, response, [address])
    assert waits[PENDING] == waits["nobody@example.com"] == waits[CONFIRMED]
    match = re.search(r"Please wait (\d+) seconds? before requesting another link", waits[PENDING]["text"])
    assert match, waits[PENDING]["text"]
    assert 1 <= int(match.group(1)) <= settings.RESEND_CONFIRMATION_MIN_SECONDS
    assert len(mailoutbox) == 1  # the limited request sent nothing


def test_resend_with_a_broken_mailer_answers_like_it_does_for_unknown_addresses(world, mail_switch, mailoutbox, settings):
    unknown_client = kit_client()
    unknown = kit.snapshot(unknown_client, kit.resend(unknown_client, "nobody@example.com"), ["nobody@example.com"])
    mail_switch.error = OSError("the mail server is unreachable")
    client = kit_client()
    known = kit.snapshot(client, kit.resend(client, PENDING), [PENDING])
    assert known == unknown
