"""Step 6c: CSRF, nothing sensitive in logs, storage or pages, and refusals stay plain (no internal text)."""
import logging

import pytest
from django.contrib.auth import get_user_model

import reg_testkit as kit
from reg_testkit import STRONG_A, STRONG_B

User = get_user_model()


# --- CSRF ------------------------------------------------------------------------------------------------------------
def test_registration_without_a_csrf_token_is_refused(csrf_client, settings):
    first, second = kit.password_names(csrf_client)
    csrf_client.get(kit.url("register"))  # the cookie is set, the form token is deliberately not sent
    response = csrf_client.post(kit.url("register"), {"username": "carol", first: STRONG_A, second: STRONG_A})
    assert response.status_code == 403
    assert not User.objects.exists()
    assert kit.logged_in_user_id(csrf_client, settings) is None


def test_registration_with_a_wrong_csrf_token_is_refused(csrf_client, settings):
    first, second = kit.password_names(csrf_client)
    csrf_client.get(kit.url("register"))
    response = csrf_client.post(
        kit.url("register"),
        {"username": "carol", first: STRONG_A, second: STRONG_A, "csrfmiddlewaretoken": "x" * 64},
    )
    assert response.status_code == 403
    assert not User.objects.exists()
    assert kit.logged_in_user_id(csrf_client, settings) is None


def test_registration_with_a_valid_csrf_token_works_under_enforcement(csrf_client, settings):
    response = kit.register(csrf_client, "carol", STRONG_A)
    assert response.status_code == 302
    assert User.objects.filter(username="carol", is_active=True).exists()
    assert kit.logged_in_user_id(csrf_client, settings) == str(User.objects.get(username="carol").pk)


def test_a_csrf_refusal_is_a_plain_page_with_no_internal_details(csrf_client):
    response = csrf_client.post(kit.url("register"), {"username": "carol"})
    assert response.status_code == 403
    assert "Traceback" not in response.content.decode()


# --- nothing sensitive stored or echoed ---------------------------------------------------------------------------
def test_the_password_is_stored_nowhere_in_plaintext(client):
    kit.register(client, "carol", STRONG_A)
    user = User.objects.get(username="carol")
    for field in user._meta.concrete_fields:
        assert STRONG_A not in str(getattr(user, field.attname)), field.name
    assert STRONG_A not in str(dict(client.session.items()))


def test_no_page_of_the_flow_shows_the_password(client):
    pages = [client.get(kit.url("register"))]
    response = kit.register(client, "carol", STRONG_A)
    pages.append(response)
    pages.append(client.get(response["Location"]))
    pages.append(kit.register(type(client)(), "dave", STRONG_A, confirm=STRONG_B))
    for page in pages:
        body = page.content.decode()
        assert STRONG_A not in body and STRONG_B not in body


# --- logs ------------------------------------------------------------------------------------------------------------
def log_blob(caplog):
    """Everything logged, as text: formatted messages, tracebacks and every attribute of every record."""
    formatter = logging.Formatter()
    return "\n".join(formatter.format(r) + "\n" + repr(r.__dict__) for r in caplog.records)


def test_the_logs_never_hold_a_password(client, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    kit.register(client, "carol", STRONG_A)
    kit.register(type(client)(), "dave", STRONG_B, confirm=STRONG_A)  # mismatch
    kit.register(type(client)(), "carol", STRONG_B)  # taken
    kit.register(type(client)(), "erin", "Sh0rt-pw!")  # refused by a validator
    out = capsys.readouterr()
    blob = log_blob(caplog) + out.out + out.err
    for password in (STRONG_A, STRONG_B, "Sh0rt-pw!"):
        assert password not in blob


# --- every refusal: reason, next step, nothing internal ---------------------------------------------------------------
def refusals(client):
    User.objects.create_user(username="taken", password=STRONG_B)
    return [
        ("short password", kit.register(client, "carol", "Short-1!"), ["too short"]),
        ("mismatch", kit.register(client, "carol", STRONG_A, STRONG_B), ["match"]),
        ("bad characters", kit.register(client, "bad name", STRONG_A), ["Usernames may contain only"]),
        ("username taken", kit.register(client, "taken", STRONG_A), [kit.TAKEN]),
    ]


def test_every_refusal_says_why_and_stays_on_the_form_without_internal_text(client):
    for name, response, words in refusals(client):
        assert response.status_code == 200, name
        text = kit.page_text(response)
        for word in words:
            assert word in text, f"{name}: {word!r} missing from {text!r}"
        assert kit.find_form(response, with_input="username")["method"] == "post", name
        kit.assert_no_internal_text(response)
    assert User.objects.count() == 1
