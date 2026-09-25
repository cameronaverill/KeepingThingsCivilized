"""Step 6a: CSRF, nothing sensitive in logs or storage, and every refusal names its reason and next step."""
import logging
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

import reg_testkit as kit
from reg_testkit import STRONG_A, STRONG_B

User = get_user_model()


# --- CSRF ------------------------------------------------------------------------------------------------------------
def test_registration_without_a_csrf_token_is_refused(csrf_client, mailoutbox):
    fields = kit.register_fields(csrf_client)
    csrf_client.get(kit.url("register"))  # the cookie is set, the form token is deliberately not sent
    response = csrf_client.post(
        kit.url("register"),
        {"username": "carol", "email": "carol@example.com", fields["password"]: STRONG_A, fields["confirm"]: STRONG_A},
    )
    assert response.status_code == 403
    assert not User.objects.exists() and not mailoutbox


def test_registration_with_a_wrong_csrf_token_is_refused(csrf_client, mailoutbox):
    fields = kit.register_fields(csrf_client)
    csrf_client.get(kit.url("register"))
    response = csrf_client.post(
        kit.url("register"),
        {
            "username": "carol", "email": "carol@example.com", fields["password"]: STRONG_A,
            fields["confirm"]: STRONG_A, "csrfmiddlewaretoken": "x" * 64,
        },
    )
    assert response.status_code == 403
    assert not User.objects.exists()


def test_registration_with_a_valid_csrf_token_works_under_enforcement(csrf_client, mailoutbox):
    response = kit.register(csrf_client, "carol", "carol@example.com", STRONG_A)
    assert response.status_code == 302
    assert User.objects.filter(username="carol").exists()
    assert len(mailoutbox) == 1


def test_resend_without_a_csrf_token_is_refused(csrf_client, mailoutbox):
    User.objects.create_user(username="carol", email="carol@example.com", password=STRONG_B, is_active=False)
    csrf_client.get(kit.url("resend"))
    response = csrf_client.post(kit.url("resend"), {"email": "carol@example.com"})
    assert response.status_code == 403
    assert not mailoutbox


def test_resend_with_a_valid_csrf_token_works_under_enforcement(csrf_client, mailoutbox):
    User.objects.create_user(username="carol", email="carol@example.com", password=STRONG_B, is_active=False)
    response = kit.resend(csrf_client, "carol@example.com")
    assert response.status_code < 400
    assert len(mailoutbox) == 1


def test_csrf_refusal_is_a_plain_page_with_no_internal_details(csrf_client):
    response = csrf_client.post(kit.url("resend"), {"email": "carol@example.com"})
    assert response.status_code == 403
    body = response.content.decode()
    assert "Traceback" not in body


def test_the_confirmation_link_is_a_get_and_needs_no_csrf_token(csrf_client, mailoutbox):
    kit.register(csrf_client, "carol", "carol@example.com", STRONG_A)
    response = csrf_client.get(kit.only_link(mailoutbox[0])[0].replace("http://testserver", ""), follow=True)
    assert response.status_code == 200
    assert User.objects.get(username="carol").is_active is True


# --- nothing sensitive stored or echoed ---------------------------------------------------------------------------
def test_the_password_is_stored_nowhere_in_plaintext(client, settings):
    kit.register(client, "carol", "carol@example.com", STRONG_A)
    user = User.objects.get(username="carol")
    for field in user._meta.concrete_fields:
        assert STRONG_A not in str(getattr(user, field.attname)), field.name
    assert STRONG_A not in str(dict(client.session.items()))


def test_no_page_of_the_flow_shows_the_password(client, mailoutbox):
    pages = [client.get(kit.url("register"))]
    response = kit.register(client, "carol", "carol@example.com", STRONG_A)
    pages.append(response)
    pages.append(client.get(response["Location"]))
    pages.append(kit.follow(client, kit.register(client, "dave", "dave@example.com", STRONG_A, confirm=STRONG_B)))
    for page in pages:
        assert STRONG_A not in page.content.decode() and STRONG_B not in page.content.decode()


def test_responses_do_not_leak_the_token_outside_the_email(client, mailoutbox):
    response = kit.register(client, "carol", "carol@example.com", STRONG_A)
    _, token = kit.only_link(mailoutbox[0])
    followed = kit.follow(client, response)
    assert token not in followed.content.decode()
    assert token not in str(response.headers)
    assert token not in str(client.cookies)


# --- logs ------------------------------------------------------------------------------------------------------------
def log_blob(caplog):
    """Everything logged, as text: formatted messages, tracebacks and every attribute of every record."""
    formatter = logging.Formatter()
    return "\n".join(formatter.format(r) + "\n" + repr(r.__dict__) for r in caplog.records)


def exercise_everything(client, mailoutbox, clock, settings):
    """Register, confirm, resend, and hit every failure page; returns (tokens, addresses)."""
    kit.register(client, "carol", "carol@example.com", STRONG_A)
    kit.register(client, "dave", "dave@example.com", STRONG_A)
    kit.register(client, "erin", "erin@example.com", STRONG_A, confirm=STRONG_B)
    kit.register(client, "carol", "other@example.com", STRONG_A)
    kit.register(client, "frank", "carol@example.com", STRONG_A)  # a pending address again
    tokens = [kit.only_link(m)[1] for m in mailoutbox if kit.confirmation_links(m)]
    kit.open_link(client, kit.confirm_path(tokens[0]))  # confirm
    kit.open_link(client, kit.confirm_path(tokens[0]))  # reuse
    kit.open_link(client, kit.confirm_path(tokens[1][:-3] + "abc"))  # tampered
    kit.open_link(client, kit.confirm_path("garbage-" + "value"))  # malformed
    kit.resend(client, "dave@example.com")
    kit.resend(client, "dave@example.com")  # limited
    kit.resend(client, "ghost@example.com")  # unknown
    kit.resend(client, "not an address")
    clock.advance(days=settings.EMAIL_CONFIRM_MAX_AGE_DAYS + 1)
    kit.open_link(client, kit.confirm_path(tokens[1]))  # expired
    return tokens, ["carol@example.com", "dave@example.com", "erin@example.com", "other@example.com", "ghost@example.com"]


def assert_clean(text, tokens, addresses, passwords=(STRONG_A, STRONG_B)):
    for token in tokens:
        assert token not in text, "a confirmation token was logged"
    for address in addresses:
        assert address.lower() not in text.lower(), f"{address} was logged"
    for password in passwords:
        assert password not in text, "a password was logged"


def test_the_logs_never_hold_a_token_an_address_or_a_password(client, mailoutbox, clock, settings, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    tokens, addresses = exercise_everything(client, mailoutbox, clock, settings)
    out = capsys.readouterr()
    assert_clean(log_blob(caplog), tokens, addresses)
    assert_clean(out.out + out.err, tokens, addresses)


def test_the_logs_stay_clean_when_the_mail_server_fails(client, mail_switch, mailoutbox, clock, settings, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    kit.register(client, "carol", "carol@example.com", STRONG_A)  # sent, so a real token exists
    tokens = [kit.only_link(mailoutbox[0])[1]]
    mail_switch.error = OSError("the mail server is unreachable")
    clock.advance(seconds=settings.RESEND_CONFIRMATION_MIN_SECONDS + 1)
    kit.register(client, "dave", "dave@example.com", STRONG_A)
    kit.resend(client, "carol@example.com")
    out = capsys.readouterr()
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert_clean(log_blob(caplog), tokens, ["carol@example.com", "dave@example.com"])
    assert_clean(out.out + out.err, tokens, ["carol@example.com", "dave@example.com"])


def test_the_logs_stay_clean_even_when_the_mail_error_itself_names_the_recipient(
    client, mail_switch, mailoutbox, caplog, capsys
):
    """Real SMTP errors often quote the rejected address. The log line must not repeat it (log the kind of error only)."""
    caplog.set_level(logging.DEBUG)
    mail_switch.error = OSError("550 5.1.1 <dave@example.com>: Recipient address rejected")
    kit.register(client, "dave", "dave@example.com", STRONG_A)
    out = capsys.readouterr()
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert_clean(log_blob(caplog), [], ["dave@example.com"])
    assert_clean(out.out + out.err, [], ["dave@example.com"])


def test_an_invalid_token_does_not_put_itself_in_the_log_through_the_request_logger(client, caplog):
    caplog.set_level(logging.DEBUG)
    token = "probe-" + "value-" + "12345"
    kit.open_link(client, kit.confirm_path(token))
    assert token not in log_blob(caplog)


# --- every refusal: reason, next step, nothing internal ---------------------------------------------------------------
def refusals(client, mailoutbox, clock, settings):
    """(name, response, words that must appear, paths the page must lead to) for each way registration can be refused."""
    User.objects.create_user(username="taken", email="taken@example.com", password=STRONG_B, is_active=True)
    register_path = kit.url("register")
    cases = [
        ("short password", kit.register(client, "carol", "carol@example.com", "Short-1!"), ["too short"], [register_path]),
        ("mismatch", kit.register(client, "carol", "carol@example.com", STRONG_A, STRONG_B), ["match"], [register_path]),
        ("bad characters", kit.register(client, "bad name", "carol@example.com", STRONG_A), ["Usernames may contain only"], [register_path]),
        ("username taken", kit.register(client, "taken", "carol@example.com", STRONG_A), ["already exists"], [register_path]),
        ("bad email", kit.register(client, "carol", "nope", STRONG_A), ["valid email"], [register_path]),
    ]
    kit.register(client, "carol", "carol@example.com", STRONG_A)
    token = kit.only_link(mailoutbox[-1])[1]
    kit.resend(client, "carol@example.com")
    cases.append(("resend limited", kit.follow(client, kit.resend(client, "carol@example.com")), ["Please wait", "seconds"], [kit.url("resend")]))
    cases.append(("resend bad address", kit.follow(client, kit.resend(client, "nope")), ["valid email"], [kit.url("resend")]))
    cases.append(("invalid link", kit.open_link(client, kit.confirm_path("zzz")), ["not valid"], [kit.url("resend"), register_path]))
    clock.advance(days=settings.EMAIL_CONFIRM_MAX_AGE_DAYS + 1)
    cases.append(("expired link", kit.open_link(client, kit.confirm_path(token)), ["expired", "Request a new one"], [kit.url("resend")]))
    return cases


def test_every_refusal_says_why_and_offers_the_next_step_without_internal_text(client, mailoutbox, clock, settings):
    for name, response, words, paths in refusals(client, mailoutbox, clock, settings):
        assert response.status_code < 500, name
        text = kit.page_text(response)
        for word in words:
            assert word in text or word.lower() in text.lower(), f"{name}: {word!r} missing from {text!r}"
        assert kit.leads_to(response, *paths) or any(
            (f["action"] in ("", None) and f["method"] == "post") for f in kit.parse(response).forms
        ), f"{name}: no next step on the page"
        kit.assert_no_internal_text(response)
        assert not re.search(r"this field is invalid|error occurred|something went wrong", text, re.I), name
