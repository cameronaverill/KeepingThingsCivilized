"""Step 1 placeholder home page, and project-wide health checks."""
import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_home_page_sends_anonymous_visitors_to_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


def test_unknown_path_is_a_404(client):
    assert client.get("/no-such-page/").status_code == 404


@pytest.mark.django_db
def test_django_system_checks_pass():  # JSONField checks ask the database which features it supports
    call_command("check")


@pytest.mark.django_db
def test_no_model_changes_are_missing_a_migration():
    call_command("makemigrations", "--check", "--dry-run")
