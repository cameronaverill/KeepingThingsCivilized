"""Django settings.

Secrets and per-machine values come from environment variables (see .env.example).
The .env file is loaded by manage.py / wsgi.py / asgi.py, NOT here, so tests never depend on a local .env.
Every number you might want to change lives in config/tunables.py, and is exposed here at the bottom.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from config import tunables

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name, default=""):
    """An environment variable, where blank counts as unset (a copied .env.example leaves blanks behind)."""
    return os.environ.get(name, "").strip() or default


def _env_list(name):
    return [item.strip() for item in _env(name).split(",") if item.strip()]


# --- Environment: "development" (default) or "production" -------------------
# A deployed site must set DJANGO_ENV=production, which turns on the strict checks below.
ENVIRONMENT = _env("DJANGO_ENV", "development")
if ENVIRONMENT not in ("development", "production"):
    raise ImproperlyConfigured(f"DJANGO_ENV must be 'development' or 'production', not {ENVIRONMENT!r}")
IS_PRODUCTION = ENVIRONMENT == "production"

DEBUG = not IS_PRODUCTION

SECRET_KEY = _env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_ENV=production")
    SECRET_KEY = "dev-only-insecure-key-never-use-in-production" # secret-scan: allow

ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    if IS_PRODUCTION:
        raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set when DJANGO_ENV=production")
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Cookies are only marked Secure in production, where the site must be served over HTTPS.
SESSION_COOKIE_SECURE = IS_PRODUCTION
CSRF_COOKIE_SECURE = IS_PRODUCTION
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# --- Secrets ------------------------------------------------------------------
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")

# --- Applications ---------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database: SQLite in WAL mode -------------------------------------------------
# WAL lets the web server and the moderation worker read while one of them writes.
# IMMEDIATE transactions take the write lock up front, which avoids "database is locked" upgrade failures.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # DJANGO_DB_PATH lets a deployment put the file on a persistent disk.
        "NAME": _env("DJANGO_DB_PATH", str(BASE_DIR / "db.sqlite3")),
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": tunables.DB_BUSY_TIMEOUT_SECONDS,
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Accounts and passwords ---------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

# Argon2 is used for new passwords; PBKDF2 stays listed so any older hash can still be verified.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": tunables.PASSWORD_MIN_LENGTH},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Email ---------------------------------------------------------------------------
# Django 6.1's MAILERS setting (EMAIL_BACKEND and the EMAIL_HOST-style settings are deprecated).
# Development prints emails to the terminal. Real SMTP options arrive in step 6.
# EMAIL_BACKEND below is our own environment variable, not Django's deprecated setting.
MAILERS = {
    "default": {
        "BACKEND": _env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"),
        "OPTIONS": {},
    }
}
DEFAULT_FROM_EMAIL = _env("DEFAULT_FROM_EMAIL", "forum@localhost")

# --- Internationalization ------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# --- Tunables: expose every UPPERCASE name from config/tunables.py as a Django setting ---
globals().update({name: value for name, value in vars(tunables).items() if name.isupper()})
