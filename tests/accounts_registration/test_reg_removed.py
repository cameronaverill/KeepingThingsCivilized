"""Step 6c: the email flow is gone. The modules, templates, URL names, paths and tunables of check-email, confirm and
resend no longer exist, and an old path is a plain 404."""
import importlib.util
from pathlib import Path

import pytest
from django.conf import settings
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.urls import NoReverseMatch, reverse

import reg_testkit as kit
from config import tunables

ACCOUNTS = Path(__file__).resolve().parents[2] / "accounts"
REMOVED_TEMPLATES = [
    "check_email", "confirm_done", "confirm_expired", "confirm_invalid", "confirm_used", "resend", "resend_form",
]
REMOVED_URL_NAMES = ["check_email", "confirm", "resend"]
OLD_PATHS = [
    "/accounts/register/check-email/",
    "/accounts/confirm/abc/",
    "/accounts/confirm/abc:def:ghi/",
    "/accounts/resend-confirmation/",
]


@pytest.mark.parametrize("name", ["accounts.emails", "accounts.tokens"])
def test_the_email_and_token_modules_are_gone(name):
    assert importlib.util.find_spec(name) is None


@pytest.mark.parametrize("name", ["emails.py", "tokens.py"])
def test_the_module_files_are_gone(name):
    assert not (ACCOUNTS / name).exists()


@pytest.mark.parametrize("name", REMOVED_TEMPLATES)
def test_the_removed_templates_are_gone_from_disk_and_the_loader(name):
    assert not (ACCOUNTS / "templates" / "accounts" / f"{name}.html").exists()
    with pytest.raises(TemplateDoesNotExist):
        get_template(f"accounts/{name}.html")


@pytest.mark.parametrize("name", REMOVED_URL_NAMES)
def test_the_removed_url_names_do_not_reverse(name):
    args = ("abc",) if name == "confirm" else ()
    with pytest.raises(NoReverseMatch):
        reverse(f"accounts:{name}", args=args)


@pytest.mark.parametrize("path", OLD_PATHS)
def test_an_old_path_is_a_plain_404(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", OLD_PATHS)
def test_an_old_path_is_a_404_for_a_post_too(client, path):
    assert client.post(path, {"email": "a@example.com"}).status_code == 404


def test_the_registration_module_has_no_mail_or_token_machinery():
    from accounts import registration

    for name in ("send_confirmation_email", "send_account_exists_email", "claim_send", "release_send", "mark_sent",
                 "check_email", "confirm", "resend", "ResendForm", "EmailField", "tokens", "RESEND_ANSWER"):
        assert not hasattr(registration, name), name


def test_the_registration_module_owns_only_the_register_pattern():
    from accounts import registration

    assert [p.name for p in registration.urlpatterns] == ["register"]


@pytest.mark.parametrize("name", ["EMAIL_CONFIRM_MAX_AGE_DAYS", "RESEND_CONFIRMATION_MIN_SECONDS"])
def test_the_unused_tunables_are_gone(name):
    assert not hasattr(tunables, name)
    assert not hasattr(settings, name)


def test_the_tunables_source_no_longer_mentions_them():
    source = (Path(tunables.__file__)).read_text()
    assert "EMAIL_CONFIRM_MAX_AGE_DAYS" not in source and "RESEND_CONFIRMATION_MIN_SECONDS" not in source


def test_the_register_and_login_pages_are_still_there(client):
    assert client.get(kit.url("register")).status_code == 200
    assert client.get(kit.url("login")).status_code == 200
