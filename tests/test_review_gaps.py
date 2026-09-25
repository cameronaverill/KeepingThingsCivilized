"""Gap-fillers found by independent review of step 1 (mutations that the original 80 tests did not catch).

The review's case-variant duplicate test (full_clean with the password excluded) now lives in
test_accounts_uniqueness.py, rewritten for the new key columns (the old Lower() constraint names are gone)."""
import ast
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings

from config import tunables

REPO_ROOT = Path(__file__).resolve().parent.parent


def _settings_in_subprocess(env_overrides, expression):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "EMAIL_", "ANTHROPIC_"))}
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", f"import config.settings as s; print({expression})"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )


def test_csrf_and_security_middleware_are_enabled():
    assert "django.middleware.csrf.CsrfViewMiddleware" in settings.MIDDLEWARE
    assert "django.middleware.security.SecurityMiddleware" in settings.MIDDLEWARE


def test_database_busy_timeout_and_sync_mode_are_configured():
    options = settings.DATABASES["default"]["OPTIONS"]
    assert options["timeout"] == tunables.DB_BUSY_TIMEOUT_SECONDS
    assert "synchronous=NORMAL" in options["init_command"].replace(" ", "")


def test_csrf_trusted_origins_come_from_the_environment():
    result = _settings_in_subprocess(
        {"DJANGO_CSRF_TRUSTED_ORIGINS": "https://a.example, https://b.example,"}, "s.CSRF_TRUSTED_ORIGINS"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['https://a.example', 'https://b.example']"


def test_allowed_hosts_are_trimmed_and_blank_items_dropped():
    result = _settings_in_subprocess(
        {"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": "x" * 50, "DJANGO_ALLOWED_HOSTS": " a.com , b.com ,,"},
        "s.ALLOWED_HOSTS",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['a.com', 'b.com']"


def test_development_hosts_are_local_only():
    assert "*" not in settings.ALLOWED_HOSTS


def test_environment_name_is_case_sensitive_and_fails_loudly():
    # "Production" must not silently fall back to development (which would turn DEBUG on).
    result = _settings_in_subprocess({"DJANGO_ENV": "Production"}, "s.DEBUG")
    assert result.returncode != 0
    assert "DJANGO_ENV" in result.stderr


def test_gitignore_also_covers_sqlite_wal_and_shm_files():
    lines = [line.strip() for line in (REPO_ROOT / ".gitignore").read_text().splitlines()]
    assert any(p in lines for p in ("db.sqlite3-*", "*.sqlite3-*")), "WAL/SHM sidecar files could be committed"


def test_tunables_are_not_overridden_through_attribute_assignment():
    # The original AST guard only sees bare names, so `tunables.MAX_MESSAGE_CHARS = 1` would slip past it.
    names = {n for n in vars(tunables) if n.isupper()}
    offenders = []
    skip = {".venv", "tests", "__pycache__", ".git", "node_modules", "migrations"}
    for path in REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT)
        if skip & set(rel.parts) or rel == Path("config/tunables.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr in names:
                    offenders.append(f"{rel}:{node.lineno} sets .{target.attr}")
    assert offenders == []


def test_dev_requirements_are_pinned_exactly_too():
    entries = [
        line.strip() for line in (REPO_ROOT / "requirements-dev.txt").read_text().splitlines()
        if line.strip() and not line.startswith(("#", "-r"))
    ]
    assert entries and all("==" in line for line in entries), entries
