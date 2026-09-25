"""Settings behaviour: safe defaults, WAL, custom user model, production strictness (plan sections 2, 10)."""
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.utils import ConnectionHandler

REPO_ROOT = Path(__file__).resolve().parent.parent


def import_settings_in_subprocess(env_overrides, expression="1"):
    """Import config.settings in a fresh interpreter so its module-level checks actually run."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "EMAIL_", "ANTHROPIC_"))}
    env.update(env_overrides)
    code = f"import config.settings as s; print({expression})"
    return subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, env=env, capture_output=True, text=True
    )


def test_settings_load_without_an_api_key():
    assert settings.ANTHROPIC_API_KEY == ""


def test_settings_module_does_not_read_dotenv():
    # .env is loaded by manage.py / wsgi.py / asgi.py only, so tests never depend on a developer's local .env.
    assert "load_dotenv" not in (REPO_ROOT / "config" / "settings.py").read_text()


def test_entry_points_load_dotenv():
    for name in ("manage.py", "config/wsgi.py", "config/asgi.py"):
        assert "load_dotenv" in (REPO_ROOT / name).read_text(), name


def test_auth_user_model_is_the_custom_user():
    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert get_user_model()._meta.label == "accounts.User"


def test_sqlite_is_configured_for_wal_with_immediate_transactions():
    options = settings.DATABASES["default"]["OPTIONS"]
    assert options["transaction_mode"] == "IMMEDIATE"
    assert "journal_mode=WAL" in options["init_command"].replace(" ", "")


def test_a_new_connection_really_runs_in_wal_mode(tmp_path, django_db_blocker):
    # A separate connection to a throwaway file: it never touches the test database.
    config = {**settings.DATABASES["default"], "NAME": str(tmp_path / "wal_check.sqlite3")}
    handler = ConnectionHandler({"default": config})
    try:
        with django_db_blocker.unblock(), handler["default"].cursor() as cursor:
            cursor.execute("PRAGMA journal_mode")
            assert cursor.fetchone()[0].lower() == "wal"
    finally:
        handler["default"].close()


def test_database_path_can_be_set_from_the_environment():
    result = import_settings_in_subprocess(
        {"DJANGO_DB_PATH": "/data/forum.sqlite3"}, "s.DATABASES['default']['NAME']"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/data/forum.sqlite3"


def test_development_is_the_default_environment():
    result = import_settings_in_subprocess({}, "(s.DEBUG, s.SESSION_COOKIE_SECURE)")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "(True, False)"


def test_production_refuses_to_start_without_a_secret_key():
    result = import_settings_in_subprocess(
        {"DJANGO_ENV": "production", "DJANGO_ALLOWED_HOSTS": "example.com"}
    )
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_production_refuses_to_start_without_allowed_hosts():
    result = import_settings_in_subprocess({"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": "x" * 50})
    assert result.returncode != 0
    assert "DJANGO_ALLOWED_HOSTS" in result.stderr


def test_production_is_strict_when_configured():
    result = import_settings_in_subprocess(
        {"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": "x" * 50, "DJANGO_ALLOWED_HOSTS": "example.com"},
        "(s.DEBUG, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.ALLOWED_HOSTS)",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "(False, True, True, ['example.com'])"


def test_unknown_environment_name_is_rejected():
    result = import_settings_in_subprocess({"DJANGO_ENV": "prod"})
    assert result.returncode != 0
    assert "DJANGO_ENV" in result.stderr


def test_session_cookie_hardening():
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.SESSION_COOKIE_SAMESITE == "Lax"
    assert settings.USE_TZ is True


def test_argon2_is_the_primary_password_hasher():
    assert settings.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"


def test_password_validators_use_the_tunable_minimum_length():
    by_name = {v["NAME"].rsplit(".", 1)[-1]: v for v in settings.AUTH_PASSWORD_VALIDATORS}
    assert by_name["MinimumLengthValidator"]["OPTIONS"]["min_length"] == settings.PASSWORD_MIN_LENGTH
    assert {"UserAttributeSimilarityValidator", "CommonPasswordValidator", "NumericPasswordValidator"} <= set(by_name)


def test_email_uses_mailers_not_the_deprecated_email_backend_setting():
    # Django 6.1 deprecates EMAIL_BACKEND and friends in favour of MAILERS (removed in Django 7.0).
    assert "default" in settings.MAILERS
    assert not settings.is_overridden("EMAIL_BACKEND")


def test_copying_env_example_as_is_gives_working_development_settings():
    # A user who copies .env.example to .env leaves blank values behind; blank must mean "use the default".
    values = dict(
        line.split("=", 1)
        for line in (REPO_ROOT / ".env.example").read_text().splitlines()
        if line.strip() and not line.startswith("#") and "=" in line
    )
    result = import_settings_in_subprocess(
        values, "(s.ENVIRONMENT, s.DATABASES['default']['NAME'].endswith('/db.sqlite3'), s.MAILERS['default']['BACKEND'])"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "('development', True, 'django.core.mail.backends.console.EmailBackend')"


def test_blank_environment_variables_fall_back_to_defaults():
    blanks = {"DJANGO_ENV": "", "DJANGO_DB_PATH": "", "EMAIL_BACKEND": "", "DEFAULT_FROM_EMAIL": ""}
    result = import_settings_in_subprocess(
        blanks, "(s.ENVIRONMENT, s.DATABASES['default']['NAME'].endswith('/db.sqlite3'), s.DEFAULT_FROM_EMAIL)"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "('development', True, 'forum@localhost')"
