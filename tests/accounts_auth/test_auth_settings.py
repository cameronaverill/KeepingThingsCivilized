"""The django-axes configuration: values come from the tunables, and the wiring in settings is right."""
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.core.checks import run_checks
from django.test import RequestFactory

REPO = Path(__file__).resolve().parents[2]


def test_axes_is_an_installed_app():
    assert "axes" in settings.INSTALLED_APPS


def test_the_axes_middleware_is_last():
    assert settings.MIDDLEWARE[-1] == "axes.middleware.AxesMiddleware"


def test_the_axes_backend_is_first_and_model_backend_second():
    backends = list(settings.AUTHENTICATION_BACKENDS)
    assert backends[0].startswith("axes.backends.")
    assert backends[1] == "django.contrib.auth.backends.ModelBackend"


def test_the_failure_limit_is_the_tunable():
    from axes.helpers import get_failure_limit

    assert get_failure_limit(RequestFactory().post("/"), {}) == settings.LOGIN_MAX_FAILURES


def test_the_cool_off_is_the_tunable_in_minutes():
    """axes reads a bare int or float as HOURS, so this catches a minutes value passed unconverted."""
    from axes.helpers import get_cool_off

    assert get_cool_off(RequestFactory().post("/")) == timedelta(minutes=settings.LOGIN_COOLOFF_MINUTES)


def test_both_the_username_and_the_address_can_lock_and_success_resets():
    parameters = settings.AXES_LOCKOUT_PARAMETERS
    assert "username" in parameters and "ip_address" in parameters  # separate entries: either one locks
    assert settings.AXES_RESET_ON_SUCCESS is True
    assert settings.AXES_ENABLED is True
    assert settings.AXES_LOCK_OUT_AT_FAILURE is True


def test_the_database_handler_keeps_the_attempts():
    assert settings.AXES_HANDLER == "axes.handlers.database.AxesDatabaseHandler"


@pytest.mark.django_db
def test_django_system_checks_raise_no_axes_warning():
    assert [m.id for m in run_checks() if m.id.startswith("axes.")] == []


def test_the_login_urls_are_the_documented_ones():
    assert settings.LOGIN_URL == "accounts:login"
    assert settings.LOGIN_REDIRECT_URL == "/"
    assert settings.LOGOUT_REDIRECT_URL == "/"


def test_changing_the_tunables_changes_the_axes_settings():
    """Load the settings in a fresh interpreter with other tunable values: nothing may be hard-coded."""
    program = (
        "import json, os\n"
        "os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'\n"
        "import config.tunables as t\n"
        "t.LOGIN_MAX_FAILURES = 2\n"
        "t.LOGIN_COOLOFF_MINUTES = 7\n"
        "import django\n"
        "django.setup()\n"
        "from axes.helpers import get_cool_off, get_failure_limit\n"
        "print(json.dumps([get_failure_limit(None, {}), get_cool_off(None).total_seconds()]))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("DJANGO_") and k != "ANTHROPIC_API_KEY"}
    done = subprocess.run(
        [sys.executable, "-c", program], cwd=REPO, env=env, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == [2, 7 * 60]
