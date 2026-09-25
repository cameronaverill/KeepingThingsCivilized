"""Step 6a: the happy path (register, one email, confirm once, never logged in), from docs/step6_brief.md."""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

import reg_testkit as kit
from reg_testkit import STRONG_A, STRONG_B

User = get_user_model()

CHECK_EMAIL_TEXT = "We sent a confirmation link to that address. It expires in {days} days. Check your spam folder."


def sign_up(client, username="alice", email="alice@example.com", password=STRONG_A):
    return kit.register(client, username, email, password)


def test_register_page_renders_a_csrf_protected_form(client):
    response = client.get(kit.url("register"))
    assert response.status_code == 200
    form = kit.find_form(response, kit.url("register"), with_input="username")
    assert form is not None and form["method"] == "post"
    names = {i["name"] for i in form["inputs"]}
    assert {"username", "email", "csrfmiddlewaretoken"} <= names
    assert len([i for i in form["inputs"] if i["type"] == "password"]) == 2


def test_register_page_is_reachable_at_the_documented_path():
    assert kit.url("register") == "/accounts/register/"
    assert kit.url("check_email") == "/accounts/register/check-email/"
    assert kit.url("resend") == "/accounts/resend-confirmation/"
    assert kit.url("confirm", "abc") == "/accounts/confirm/abc/"


def test_successful_registration_redirects_to_check_email(client):
    response = sign_up(client)
    assert response.status_code == 302
    assert response["Location"] == kit.url("check_email")


def test_registration_creates_an_inactive_unverified_user(client):
    sign_up(client)
    user = User.objects.get(username="alice")
    assert user.is_active is False
    assert user.email_verified_at is None
    assert user.email == "alice@example.com"


def test_registration_sends_exactly_one_email_to_the_address(client, mailoutbox):
    sign_up(client)
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == ["alice@example.com"]


def test_registration_creates_no_login_session(client, settings):
    sign_up(client)
    kit.follow(client, client.get(kit.url("check_email")))
    assert "_auth_user_id" not in client.session


def test_check_email_page_says_what_the_contract_says(client, settings):
    response = kit.follow(client, sign_up(client))
    assert response.status_code == 200
    assert CHECK_EMAIL_TEXT.format(days=settings.EMAIL_CONFIRM_MAX_AGE_DAYS) in kit.page_text(response)


def test_check_email_page_names_the_configured_number_of_days(client, settings):
    settings.EMAIL_CONFIRM_MAX_AGE_DAYS = 5
    response = kit.follow(client, sign_up(client))
    assert "It expires in 5 days." in kit.page_text(response)


def test_check_email_page_links_to_resend(client):
    response = kit.follow(client, sign_up(client))
    assert kit.leads_to(response, kit.url("resend"))


def test_check_email_page_can_be_opened_directly(client):
    response = client.get(kit.url("check_email"))
    assert response.status_code == 200
    assert "Check your spam folder." in kit.page_text(response)


def test_password_is_stored_as_an_argon2_hash(client):
    sign_up(client)
    user = User.objects.get(username="alice")
    assert user.password.startswith("argon2$")
    assert user.check_password(STRONG_A)
    assert STRONG_A not in user.password


def test_password_is_kept_exactly_as_typed_including_edge_spaces(client):
    typed = STRONG_A + " "
    sign_up(client, password=typed)
    user = User.objects.get(username="alice")
    assert user.check_password(typed)
    assert not user.check_password(STRONG_A)


def test_the_emailed_link_activates_the_account(client, mailoutbox):
    sign_up(client)
    link, _ = kit.only_link(mailoutbox[0])
    response = kit.open_link(client, link)
    assert response.status_code == 200
    user = User.objects.get(username="alice")
    assert user.is_active is True
    assert user.email_verified_at is not None
    assert abs(user.email_verified_at - timezone.now()) < timedelta(seconds=5)


def test_confirmation_page_says_so_and_links_to_login(client, mailoutbox):
    sign_up(client)
    response = kit.open_link(client, kit.only_link(mailoutbox[0])[0])
    assert "Your email is confirmed. You can now log in" in kit.page_text(response)
    assert kit.leads_to(response, kit.login_url())


def test_confirming_never_logs_the_user_in(client, mailoutbox, settings):
    sign_up(client)
    kit.open_link(client, kit.only_link(mailoutbox[0])[0])
    assert "_auth_user_id" not in client.session
    # Not from a second browser either, and the home page does not know the user.
    other = type(client)()
    kit.open_link(other, kit.only_link(mailoutbox[0])[0])
    assert "_auth_user_id" not in other.session


def test_confirm_stamps_the_time_of_confirmation(client, mailoutbox, clock):
    sign_up(client)
    clock.advance(days=2)
    kit.open_link(client, kit.only_link(mailoutbox[0])[0])
    user = User.objects.get(username="alice")
    assert abs(user.email_verified_at - clock.now()) < timedelta(seconds=5)


def test_the_link_works_exactly_once(client, mailoutbox):
    sign_up(client)
    link = kit.only_link(mailoutbox[0])[0]
    kit.open_link(client, link)
    stamp = User.objects.get(username="alice").email_verified_at
    second = kit.open_link(client, link)
    text = kit.page_text(second)
    assert "Your email is already confirmed. You can log in." in text
    assert "Your email is confirmed." not in text
    assert User.objects.get(username="alice").email_verified_at == stamp
    assert kit.leads_to(second, kit.login_url())


def test_a_used_link_cannot_reactivate_an_account_that_was_deactivated_afterwards(client, mailoutbox):
    sign_up(client)
    link = kit.only_link(mailoutbox[0])[0]
    kit.open_link(client, link)
    User.objects.filter(username="alice").update(is_active=False)  # for example a moderator banned the account
    response = kit.open_link(client, link)
    assert User.objects.get(username="alice").is_active is False
    assert "Your email is confirmed. You can now log in" not in kit.page_text(response)


def test_a_link_only_confirms_its_own_account(client, mailoutbox):
    sign_up(client, "alice", "alice@example.com")
    sign_up(client, "bobby", "bobby@example.com")
    alice_link = kit.only_link(mailoutbox[0])[0]
    kit.open_link(client, alice_link)
    assert User.objects.get(username="alice").is_active is True
    assert User.objects.get(username="bobby").is_active is False
    assert User.objects.get(username="bobby").email_verified_at is None


def test_two_people_register_independently(client, mailoutbox):
    sign_up(client, "alice", "alice@example.com", STRONG_A)
    sign_up(client, "bobby", "bobby@example.com", STRONG_B)
    assert [m.to for m in mailoutbox] == [["alice@example.com"], ["bobby@example.com"]]
    assert kit.only_link(mailoutbox[0])[1] != kit.only_link(mailoutbox[1])[1]
    assert User.objects.count() == 2


@pytest.mark.parametrize("method", ["put", "delete", "patch"])
def test_register_rejects_other_methods_without_a_server_error(client, method):
    response = getattr(client, method)(kit.url("register"))
    assert response.status_code < 500
    assert not User.objects.exists()


def test_a_second_registration_of_the_same_person_after_confirming_creates_no_second_account(client, mailoutbox):
    sign_up(client)
    kit.open_link(client, kit.only_link(mailoutbox[0])[0])
    sign_up(client, "alice2", "alice@example.com", STRONG_B)
    assert User.objects.count() == 1
