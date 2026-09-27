"""The users admin with optional emails (step 6c): accounts without an email are listed and searchable by username, a
search by email still finds the account that has one, and the password hash is still never shown."""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()

pytestmark = pytest.mark.django_db

PASSWORD = "a-long-test-" + "password-1"  # secret-scan: allow


@pytest.fixture
def root(client):
    admin = User.objects.create_superuser("rooty", password=PASSWORD)  # no email: createsuperuser asks for none
    client.force_login(admin)
    return client


@pytest.fixture
def people():
    User.objects.create_user(username="nomail_person", password=PASSWORD)
    User.objects.create_user(username="another_blank", password=PASSWORD)
    User.objects.create_user(username="with_mail", email="findme@mailbox.example", password=PASSWORD)


def changelist(root, **params):
    response = root.get(reverse("admin:accounts_user_changelist"), params)
    assert response.status_code == 200
    return response


def listed(response):
    return {u.username for u in response.context["cl"].result_list}


def test_the_list_shows_accounts_with_and_without_an_email(root, people):
    assert listed(changelist(root)) == {"rooty", "nomail_person", "another_blank", "with_mail"}


def test_the_list_still_has_an_email_column(root, people):
    assert "email" in changelist(root).context["cl"].list_display


def test_search_by_email_finds_only_the_account_that_has_it(root, people):
    assert listed(changelist(root, q="findme@mailbox.example")) == {"with_mail"}


def test_search_by_part_of_an_email_domain_finds_only_accounts_that_have_one(root, people):
    assert listed(changelist(root, q="mailbox.example")) == {"with_mail"}


def test_search_by_username_finds_an_account_without_an_email(root, people):
    assert listed(changelist(root, q="nomail_person")) == {"nomail_person"}


def test_the_change_page_of_an_account_without_an_email_renders_and_hides_the_password_hash(root, people):
    user = User.objects.get(username="nomail_person")
    response = root.get(reverse("admin:accounts_user_change", args=[user.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "argon2" not in body and user.password not in body


def test_the_list_page_never_contains_a_password_hash(root, people):
    body = changelist(root).content.decode()
    assert "argon2" not in body


def test_a_staff_member_still_cannot_add_or_delete_accounts(root, people):
    user = User.objects.get(username="nomail_person")
    assert root.get(reverse("admin:accounts_user_add")).status_code == 403
    assert root.post(reverse("admin:accounts_user_delete", args=[user.pk]), {"post": "yes"}).status_code == 403
    assert User.objects.filter(pk=user.pk).exists()
