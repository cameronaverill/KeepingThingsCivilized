"""Step 6a: the confirmation email itself (subject, plain text, absolute link, expiry, no password, ignore line)."""
import re

import pytest
from django.contrib.auth import get_user_model

import reg_testkit as kit
from reg_testkit import STRONG_A

User = get_user_model()

SUBJECT = "Confirm your email for the discussion forum"


@pytest.fixture
def message(client, mailoutbox):
    kit.register(client, "alice", "alice@example.com", STRONG_A)
    assert len(mailoutbox) == 1
    return mailoutbox[0]


def test_subject_is_the_documented_one(message):
    assert message.subject == SUBJECT


def test_sent_from_the_configured_address_to_the_registrant_only(message, settings):
    assert message.from_email == settings.DEFAULT_FROM_EMAIL
    assert message.to == ["alice@example.com"]
    assert not message.cc and not message.bcc


def test_plain_text_only(message):
    assert message.content_subtype == "plain"
    assert not getattr(message, "alternatives", [])
    assert not re.search(r"<\s*(a|p|html|body|br|div)\b", message.body, re.I)


def test_the_body_holds_exactly_one_absolute_confirmation_link(message):
    link, token = kit.only_link(message)
    assert link.startswith("http://testserver/accounts/confirm/")
    assert link == f"http://testserver{kit.confirm_path(token)}"


def test_the_link_uses_the_host_the_request_came_in_on(client, mailoutbox, settings):
    settings.ALLOWED_HOSTS = ["forum.example.org", "testserver"]
    kit.register(client, "alice", "alice@example.com", STRONG_A, HTTP_HOST="forum.example.org")
    assert kit.only_link(mailoutbox[0])[0].startswith("http://forum.example.org/accounts/confirm/")


def test_the_link_is_https_for_a_secure_request(client, mailoutbox):
    kit.register(client, "alice", "alice@example.com", STRONG_A, secure=True)
    assert kit.only_link(mailoutbox[0])[0].startswith("https://testserver/accounts/confirm/")


def test_the_link_in_the_email_is_the_one_that_works(client, message):
    kit.open_link(client, kit.only_link(message)[0])
    assert User.objects.get(username="alice").is_active is True


def test_the_link_is_on_one_unbroken_line(message):
    link, _ = kit.only_link(message)
    assert any(line.strip() == link or link in line for line in message.body.splitlines())
    assert "\n" not in link and " " not in link


def test_the_body_names_the_expiry_in_days(message, settings):
    assert f"{settings.EMAIL_CONFIRM_MAX_AGE_DAYS} days" in message.body


def test_the_expiry_in_the_body_follows_the_settings(client, mailoutbox, settings):
    settings.EMAIL_CONFIRM_MAX_AGE_DAYS = 7
    kit.register(client, "alice", "alice@example.com", STRONG_A)
    assert "7 days" in mailoutbox[0].body
    assert "3 days" not in mailoutbox[0].body


def test_the_body_says_to_ignore_it_if_not_requested(message):
    body = message.body.lower()
    assert "ignore" in body
    assert re.search(r"(didn't|did not|not you|not request|wasn't you|weren't you|no request)", body)


def test_no_password_or_hash_in_the_email(client, message):
    user = User.objects.get(username="alice")
    everything = "\n".join([message.subject, message.body, str(message.message())])
    assert STRONG_A not in everything
    assert user.password not in everything
    assert user.password.split("$")[-1] not in everything  # the hash itself


def test_the_token_does_not_contain_the_password_or_address(message):
    _, token = kit.only_link(message)
    assert STRONG_A not in token
    assert "alice@example.com" not in token and "alice@example.com".encode().hex() not in token


def test_the_email_has_no_header_injection_or_stray_recipients(message):
    raw = str(message.message())
    assert raw.count("\nTo:") <= 1
    assert "Bcc" not in raw


def test_resend_uses_the_same_kind_of_email(client, mailoutbox, clock, settings):
    kit.register(client, "alice", "alice@example.com", STRONG_A)
    clock.advance(seconds=settings.RESEND_CONFIRMATION_MIN_SECONDS + 1)
    kit.resend(client, "alice@example.com", HTTP_HOST="testserver")
    first, second = mailoutbox
    assert second.subject == SUBJECT == first.subject
    assert second.to == ["alice@example.com"]
    assert "3 days" in second.body and "ignore" in second.body.lower()
    assert kit.only_link(second)[0].startswith("http://testserver/accounts/confirm/")
    assert STRONG_A not in second.body
