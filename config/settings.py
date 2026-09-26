"""Django settings.

Secrets and per-machine values come from environment variables (see .env.example).
The .env file is loaded by manage.py / wsgi.py / asgi.py, NOT here, so tests never depend on a local .env.
Every number you might want to change lives in config/tunables.py, and is exposed here at the bottom.
"""
import os
from datetime import timedelta
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
DJANGO_ENV_WAS_SET = bool(_env("DJANGO_ENV"))
ENVIRONMENT = _env("DJANGO_ENV", "development")
if ENVIRONMENT not in ("development", "production"):
    raise ImproperlyConfigured(f"DJANGO_ENV must be 'development' or 'production', not {ENVIRONMENT!r}")
IS_PRODUCTION = ENVIRONMENT == "production"

DEBUG = not IS_PRODUCTION

DEV_SECRET_KEY = "dev-only-insecure-key-never-use-in-production"  # secret-scan: allow (deliberately public) # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow
SECRET_KEY = _env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_ENV=production")
    SECRET_KEY = "dev-only-insecure-key-never-use-in-production" #secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow # secret-scan: allow
elif IS_PRODUCTION and (SECRET_KEY == DEV_SECRET_KEY or len(SECRET_KEY) < 50):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be at least 50 characters and not the development key when DJANGO_ENV=production"
    )

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    if IS_PRODUCTION:
        raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set when DJANGO_ENV=production")
    ALLOWED_HOSTS = list(_LOCAL_HOSTS)
elif IS_PRODUCTION and "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must not contain '*' when DJANGO_ENV=production")
elif not DJANGO_ENV_WAS_SET and not set(ALLOWED_HOSTS) <= set(_LOCAL_HOSTS):
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS lists a host other than localhost, but DJANGO_ENV is not set. "
        "Set DJANGO_ENV=production for a deployed site (or DJANGO_ENV=development to allow this on purpose)."
    )

CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
for _origin in CSRF_TRUSTED_ORIGINS:
    _scheme, _sep, _rest = _origin.partition("://")
    if _scheme.lower() not in ("http", "https") or not _sep or not _rest.split("/", 1)[0].strip():
        # Deliberately does not include the value: a secret pasted into this variable by mistake must never be echoed.
        raise ImproperlyConfigured(
            "Every entry in DJANGO_CSRF_TRUSTED_ORIGINS must start with http:// or https:// and have a host, "
            "like https://example.com (the offending value is not shown)"
        )

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
    "axes",
    "accounts",
    "moderation",
    "forum",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",  # last, as django-axes requires
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
# A relative DJANGO_DB_PATH is relative to the project folder (not the working directory), and its folder must exist.
DB_PATH = BASE_DIR / _env("DJANGO_DB_PATH", "db.sqlite3")  # an absolute path replaces BASE_DIR
if not DB_PATH.parent.is_dir():
    raise ImproperlyConfigured(f"DJANGO_DB_PATH points into a folder that does not exist: {DB_PATH.parent}")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # DJANGO_DB_PATH lets a deployment put the file on a persistent disk.
        "NAME": str(DB_PATH),
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
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# --- Login throttling (django-axes 8.x) ------------------------------------------------
# The axes backend comes first: it refuses the attempt while a client is locked out, then ModelBackend does the real check.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AXES_FAILURE_LIMIT = tunables.LOGIN_MAX_FAILURES  # locked on the Nth failed attempt
AXES_COOLOFF_TIME = timedelta(minutes=tunables.LOGIN_COOLOFF_MINUTES)  # a timedelta, not hours
# A lock on the username OR on the IP address is enough to refuse (two separate counters).
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
AXES_RESET_ON_SUCCESS = True
# Attempts made during a lockout do not extend it, so the wait the page states stays true.
AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False
# "Bob" and "bob" are one account (see accounts/keys.py), so they share one username counter.
AXES_USERNAME_CALLABLE = "accounts.authviews.axes_username"
# Show the login page (with a message naming the wait) instead of axes' bare text response.
AXES_LOCKOUT_CALLABLE = "accounts.authviews.lockout_response"

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
# Where the circuit breaker's alert email goes (moderation/breaker.py). Per-machine, so it comes from .env, never from
# a tracked file. Empty = no email is sent (the trip is only logged).
ALERT_EMAIL = _env("ALERT_EMAIL")

# --- Internationalization ------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# --- Tunables: expose every UPPERCASE name from config/tunables.py as a Django setting ---
globals().update({name: value for name, value in vars(tunables).items() if name.isupper()})
