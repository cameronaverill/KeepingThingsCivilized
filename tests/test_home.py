"""Step 1 placeholder home page, and project-wide health checks."""
import pytest
from django.core.management import call_command


def test_home_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert "Discussion Forum" in response.content.decode()


def test_unknown_path_is_a_404(client):
    assert client.get("/no-such-page/").status_code == 404


def test_django_system_checks_pass():
    call_command("check")


@pytest.mark.django_db
def test_no_model_changes_are_missing_a_migration():
    call_command("makemigrations", "--check", "--dry-run")
