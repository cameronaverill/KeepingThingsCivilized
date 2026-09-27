"""Step 6c: the happy path. A username and a password create an ACTIVE account, log the person in and lead to the home
page. No email, no confirmation, no mail (docs/step6c_brief.md)."""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

import reg_testkit as kit
from reg_testkit import STRONG_A, STRONG_B

User = get_user_model()


def sign_up(client, username="alice", password=STRONG_A):
    return kit.register(client, username, password)


def test_register_page_renders_a_csrf_protected_post_form(client):
    response = client.get(kit.url("register"))
    assert response.status_code == 200
    form = kit.find_form(response, with_input="username")
    assert form is not None and form["method"] == "post"
    assert form["action"] in ("", None, kit.url("register"))
    names = {i["name"] for i in form["inputs"]}
    assert {"username", "csrfmiddlewaretoken"} <= names
    assert len([i for i in form["inputs"] if i["type"] == "password"]) == 2


def test_register_form_has_no_email_input_at_all(client):
    form = kit.find_form(client.get(kit.url("register")), with_input="username")
    assert [i["name"] for i in form["inputs"] if i["type"] == "email" or "email" in (i["name"] or "").lower()] == []


def test_register_page_is_reachable_at_the_documented_path():
    assert kit.url("register") == "/accounts/register/"


def test_successful_registration_redirects_to_the_forum_home(client):
    response = sign_up(client)
    assert response.status_code == 302
    assert response["Location"] == reverse("forum:home")


def test_the_redirect_target_loads_for_the_new_member(client):
    home = kit.register(client, "alice", STRONG_A)["Location"]
    response = client.get(home)
    assert response.status_code == 200
    assert response.wsgi_request.user.username == "alice"
    assert response.wsgi_request.user.is_authenticated is True


def test_registration_creates_an_active_user_with_no_email(client):
    sign_up(client)
    user = User.objects.get(username="alice")
    assert user.is_active is True
    assert user.email == ""
    assert user.email_key == ""
    assert user.email_verified_at is None
    assert user.is_staff is False and user.is_superuser is False


def test_registration_keeps_the_username_as_typed_and_stores_its_key(client):
    sign_up(client, "Alice_B")
    user = User.objects.get(username_key="alice_b")
    assert user.username == "Alice_B"


def test_registration_logs_the_person_in_and_sets_a_session_cookie(client, settings):
    sign_up(client)
    user = User.objects.get(username="alice")
    assert settings.SESSION_COOKIE_NAME in client.cookies
    assert client.session["_auth_user_id"] == str(user.pk)


def test_the_session_uses_a_configured_backend_so_axes_and_login_agree(client, settings):
    sign_up(client)
    assert client.session["_auth_user_backend"] in settings.AUTHENTICATION_BACKENDS


def test_registration_stamps_last_login_like_a_real_login(client):
    sign_up(client)
    stamp = User.objects.get(username="alice").last_login
    assert stamp is not None
    assert abs(stamp - timezone.now()) < timedelta(seconds=30)


def test_the_new_member_can_log_out_and_log_in_again_with_the_same_password(client, settings):
    sign_up(client)
    assert client.post(kit.url("logout")).status_code == 302
    assert kit.logged_in_user_id(client, settings) is None
    assert client.post(kit.url("login"), {"username": "alice", "password": STRONG_A}).status_code == 302
    assert client.session["_auth_user_id"] == str(User.objects.get(username="alice").pk)


def test_registration_sends_no_email(client, mailoutbox):
    sign_up(client)
    assert mailoutbox == []


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


def test_two_people_register_independently_each_in_their_own_session(client, settings):
    other = type(client)()
    sign_up(client, "alice", STRONG_A)
    kit.register(other, "bobby", STRONG_B)
    assert User.objects.count() == 2
    assert client.session["_auth_user_id"] == str(User.objects.get(username="alice").pk)
    assert other.session["_auth_user_id"] == str(User.objects.get(username="bobby").pk)


def test_two_registrations_never_share_an_email_key_or_collide_on_it(client):
    sign_up(client, "alice", STRONG_A)
    kit.register(type(client)(), "bobby", STRONG_B)
    assert list(User.objects.order_by("username").values_list("email_key", flat=True)) == ["", ""]


def test_registering_after_a_failed_attempt_in_the_same_browser_works(client):
    kit.register(client, "alice", STRONG_A, confirm=STRONG_B)
    assert User.objects.count() == 0
    assert sign_up(client).status_code == 302
    assert User.objects.filter(username="alice").count() == 1


@pytest.mark.parametrize("method", ["put", "delete", "patch"])
def test_register_rejects_other_methods_without_a_server_error(client, method):
    response = getattr(client, method)(kit.url("register"))
    assert response.status_code < 500
    assert not User.objects.exists()


def test_a_get_never_creates_or_logs_in_anyone(client, settings):
    client.get(kit.url("register") + "?username=alice&password1=x")
    assert not User.objects.exists()
    assert kit.logged_in_user_id(client, settings) is None
