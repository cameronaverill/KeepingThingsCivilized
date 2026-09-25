"""Settings behaviour: safe defaults, WAL, custom user model, production strictness (plan sections 2, 10)."""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.utils import ConnectionHandler

REPO_ROOT = Path(__file__).resolve().parent.parent


DEV_FALLBACK_KEY = "dev-only-insecure-key-never-use-in-production"
GOOD_KEY = "k" * 50  # long enough, and not the development fallback


def import_settings_in_subprocess(env_overrides, expression="1", cwd=None):
    """Import config.settings in a fresh interpreter so its module-level checks actually run.

    The child gets the parent's environment minus every variable settings.py reads, so a developer's own
    DJANGO_*/ANTHROPIC_*/EMAIL_* variables can never change the result. Pass cwd to run somewhere other than the
    project folder (PYTHONPATH keeps `import config` working from there).
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "EMAIL_", "ANTHROPIC_", "DEFAULT_FROM"))}
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.update(env_overrides)
    code = f"import config.settings as s; print({expression})"
    return subprocess.run(
        [sys.executable, "-c", code], cwd=cwd or REPO_ROOT, env=env, capture_output=True, text=True
    )


def test_settings_load_without_an_api_key():
    # A clean child environment, so this does not depend on the developer's own ANTHROPIC_API_KEY.
    result = import_settings_in_subprocess({}, "repr(s.ANTHROPIC_API_KEY)")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "''"


def test_a_blank_api_key_counts_as_no_key():
    result = import_settings_in_subprocess({"ANTHROPIC_API_KEY": "   "}, "repr(s.ANTHROPIC_API_KEY)")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "''"


def test_the_api_key_comes_from_the_environment():
    result = import_settings_in_subprocess({"ANTHROPIC_API_KEY": "not-a-real-key"}, "s.ANTHROPIC_API_KEY")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "not-a-real-key"


def _calls_and_imports(path):
    """Names of everything imported from dotenv, and of every function called load_dotenv, found via the AST."""
    tree = ast.parse(path.read_text(), filename=str(path))
    imports_dotenv, calls_load_dotenv = False, False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports_dotenv |= any(alias.name.split(".")[0] == "dotenv" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports_dotenv |= (node.module or "").split(".")[0] == "dotenv"
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            calls_load_dotenv |= name == "load_dotenv"
    return imports_dotenv, calls_load_dotenv


def test_settings_module_does_not_read_dotenv():
    # .env is loaded by manage.py / wsgi.py / asgi.py only, so tests never depend on a developer's local .env.
    # Checked on the AST, so a comment or docstring that mentions load_dotenv is fine and a real call is not.
    assert _calls_and_imports(REPO_ROOT / "config" / "settings.py") == (False, False)


def test_the_dotenv_check_really_detects_imports_and_calls(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text("import dotenv\ndotenv.load_dotenv()\n")
    assert _calls_and_imports(sample) == (True, True)
    sample.write_text("from dotenv import load_dotenv as x\nload_dotenv()\n")
    assert _calls_and_imports(sample) == (True, True)
    sample.write_text('"""load_dotenv is mentioned only in this docstring"""\n# load_dotenv()\n')
    assert _calls_and_imports(sample) == (False, False)


def test_entry_points_load_dotenv():
    for name in ("manage.py", "config/wsgi.py", "config/asgi.py"):
        imports_dotenv, calls_load_dotenv = _calls_and_imports(REPO_ROOT / name)
        assert imports_dotenv and calls_load_dotenv, name


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


def test_database_path_can_be_set_from_the_environment(tmp_path):
    # The parent folder must exist (see the DJANGO_DB_PATH tests below), so use a real temporary one.
    wanted = tmp_path / "forum.sqlite3"
    result = import_settings_in_subprocess({"DJANGO_DB_PATH": str(wanted)}, "s.DATABASES['default']['NAME']")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(wanted)


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


# --- Step 1 fixes, section B: production fails closed --------------------------------------------------------------

PROD = {"DJANGO_ENV": "production", "DJANGO_ALLOWED_HOSTS": "example.com"}


def test_production_refuses_the_development_fallback_key():
    result = import_settings_in_subprocess({**PROD, "DJANGO_SECRET_KEY": DEV_FALLBACK_KEY})
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_production_refuses_a_secret_key_shorter_than_50_characters():
    result = import_settings_in_subprocess({**PROD, "DJANGO_SECRET_KEY": "k" * 49})
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_production_accepts_a_secret_key_of_exactly_50_characters():
    result = import_settings_in_subprocess({**PROD, "DJANGO_SECRET_KEY": GOOD_KEY}, "s.SECRET_KEY == 'k' * 50")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


@pytest.mark.parametrize("hosts", ["*", "example.com,*", " * ", "*,example.com"])
def test_production_refuses_a_wildcard_in_allowed_hosts(hosts):
    result = import_settings_in_subprocess({**PROD, "DJANGO_SECRET_KEY": GOOD_KEY, "DJANGO_ALLOWED_HOSTS": hosts})
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_ALLOWED_HOSTS" in result.stderr


def test_production_still_allows_named_hosts_including_subdomain_patterns():
    result = import_settings_in_subprocess(
        {**PROD, "DJANGO_SECRET_KEY": GOOD_KEY, "DJANGO_ALLOWED_HOSTS": "example.com,.example.org"}, "s.ALLOWED_HOSTS"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['example.com', '.example.org']"


# --- development mode: a public host name without DJANGO_ENV is almost certainly a forgotten setting ------------------


@pytest.mark.parametrize("env", [{}, {"DJANGO_ENV": ""}, {"DJANGO_ENV": "   "}], ids=["unset", "blank", "spaces"])
@pytest.mark.parametrize("hosts", ["example.com", "localhost,example.com", "*", "0.0.0.0", "192.168.1.5"])
def test_development_by_default_refuses_non_local_allowed_hosts(env, hosts):
    result = import_settings_in_subprocess({**env, "DJANGO_ALLOWED_HOSTS": hosts})
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_ENV=production" in result.stderr


def test_explicit_development_environment_allows_other_hosts():
    result = import_settings_in_subprocess(
        {"DJANGO_ENV": "development", "DJANGO_ALLOWED_HOSTS": "example.com,testserver"}, "s.ALLOWED_HOSTS"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['example.com', 'testserver']"


@pytest.mark.parametrize("hosts", ["localhost", "127.0.0.1", "[::1]", "localhost, 127.0.0.1 ,[::1]"])
def test_development_by_default_accepts_local_only_hosts(hosts):
    result = import_settings_in_subprocess({"DJANGO_ALLOWED_HOSTS": hosts}, "s.DEBUG")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_development_by_default_without_any_hosts_still_works():
    result = import_settings_in_subprocess({}, "s.ALLOWED_HOSTS")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['localhost', '127.0.0.1', '[::1]']"


# --- DJANGO_DB_PATH ------------------------------------------------------------------------------------------------


def test_relative_database_path_resolves_against_the_project_folder_not_the_cwd(tmp_path):
    # tests/ exists in the project folder, so the parent check passes; the file itself need not exist.
    result = import_settings_in_subprocess(
        {"DJANGO_DB_PATH": "tests/forum_check.sqlite3"}, "s.DATABASES['default']['NAME']", cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(REPO_ROOT / "tests" / "forum_check.sqlite3")


def test_bare_relative_database_file_name_lands_in_the_project_folder(tmp_path):
    result = import_settings_in_subprocess(
        {"DJANGO_DB_PATH": "other.sqlite3"}, "s.DATABASES['default']['NAME']", cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(REPO_ROOT / "other.sqlite3")


def test_default_database_path_is_in_the_project_folder_whatever_the_cwd(tmp_path):
    result = import_settings_in_subprocess({}, "s.DATABASES['default']['NAME']", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(REPO_ROOT / "db.sqlite3")


def test_absolute_database_path_is_used_as_given(tmp_path):
    wanted = tmp_path / "abs.sqlite3"
    result = import_settings_in_subprocess({"DJANGO_DB_PATH": str(wanted)}, "s.DATABASES['default']['NAME']", cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(wanted)


def test_database_path_with_a_missing_parent_directory_is_rejected(tmp_path):
    result = import_settings_in_subprocess({"DJANGO_DB_PATH": str(tmp_path / "no_such_dir" / "forum.sqlite3")})
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_DB_PATH" in result.stderr


def test_relative_database_path_with_a_missing_parent_directory_is_rejected():
    result = import_settings_in_subprocess({"DJANGO_DB_PATH": "no_such_dir_anywhere/forum.sqlite3"})
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_DB_PATH" in result.stderr


def test_missing_database_directory_is_rejected_in_production_too(tmp_path):
    result = import_settings_in_subprocess(
        {**PROD, "DJANGO_SECRET_KEY": GOOD_KEY, "DJANGO_DB_PATH": str(tmp_path / "nope" / "forum.sqlite3")}
    )
    assert result.returncode != 0
    assert "DJANGO_DB_PATH" in result.stderr
