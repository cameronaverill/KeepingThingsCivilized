"""DJANGO_CSRF_TRUSTED_ORIGINS is validated at import, and a bad value is never echoed (it may be a mis-pasted secret)."""
import os
import random
import shutil
import string
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
NAME = "DJANGO_CSRF_TRUSTED_ORIGINS"
PROD = {"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": "k" * 50, "DJANGO_ALLOWED_HOSTS": "example.com"}


def clean_env(overrides):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "EMAIL_", "ANTHROPIC_", "DEFAULT_FROM"))}
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.update(overrides)
    return env


def import_settings(overrides, expression="s.CSRF_TRUSTED_ORIGINS"):
    return subprocess.run(
        [sys.executable, "-c", f"import config.settings as s; print({expression})"],
        cwd=REPO_ROOT, env=clean_env(overrides), capture_output=True, text=True,
    )


def canary(length=67):
    rng = random.Random(20260925)
    return "".join(rng.choice(string.ascii_letters + string.digits + "_-") for _ in range(length))


def assert_not_echoed(result, value):
    text = result.stdout + result.stderr
    assert value not in text, "the bad value was printed"
    assert value[:8] not in text, "the start of the bad value was printed"


MODES = {"development": {}, "production": PROD}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://example.com", "['https://example.com']"),
        ("http://localhost:8000", "['http://localhost:8000']"),
        (" https://a.example , http://b.example:8080 ,https://c.example ", "['https://a.example', 'http://b.example:8080', 'https://c.example']"),
        ("https://a.example,", "['https://a.example']"),
    ],
)
def test_valid_origins_load(mode, value, expected):
    result = import_settings({**MODES[mode], NAME: value})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("value", ["", "   ", ",", " , "])
def test_blank_means_unset(mode, value):
    result = import_settings({**MODES[mode], NAME: value})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def bad_values():
    """label -> (value, the random-looking part that must never be echoed; None when nothing in the value is secret)."""
    return {
        "bare token": (canary(24), canary(24)),
        "67-character random string": (canary(67), canary(67)),
        "bare host": ("example." + canary(12) + ".com", canary(12)),
        "ftp scheme": ("ftp://" + canary(10) + ".example", canary(10)),
        "no host": ("https://", None),
        "no host, http": ("http://", None),
        "only second item bad": ("https://ok.example, " + canary(30), canary(30)),
        "scheme with an empty host and a path": ("https:///" + canary(12), canary(12)),
        "scheme without slashes": ("https:" + canary(12) + ".example", canary(12)),
    }


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("label", sorted(bad_values()))
def test_invalid_origins_are_refused_without_echoing_them(mode, label):
    value, secret = bad_values()[label]
    result = import_settings({**MODES[mode], NAME: value})
    assert result.returncode != 0, result.stdout
    assert "ImproperlyConfigured" in result.stderr
    assert NAME in result.stderr
    if secret is None:
        return  # nothing secret-looking in it (the generic message itself mentions http:// and https://)
    assert value not in result.stdout + result.stderr
    assert_not_echoed(result, secret)


@pytest.mark.parametrize("mode", MODES)
def test_the_canary_is_absent_even_when_it_is_not_the_whole_value(mode):
    secret = canary(67)
    result = import_settings({**MODES[mode], NAME: "https://fine.example," + secret})
    assert result.returncode != 0
    assert NAME in result.stderr
    assert secret[:8] not in result.stdout + result.stderr
    assert secret[-8:] not in result.stdout + result.stderr


# --- manage.py check --------------------------------------------------------------------------------------------------


def run_manage(tmp_path, overrides, *args):
    """Run a COPY of manage.py from tmp_path, so its load_dotenv() looks for a .env there and never reads a real one."""
    script = tmp_path / "manage.py"
    shutil.copy2(REPO_ROOT / "manage.py", script)
    return subprocess.run(
        [sys.executable, str(script), *args], cwd=tmp_path, env=clean_env(overrides), capture_output=True, text=True
    )


def test_manage_check_with_a_bad_value_does_not_print_it(tmp_path):
    secret = canary(67)
    result = run_manage(tmp_path, {NAME: secret}, "check")
    assert result.returncode != 0
    assert NAME in result.stderr
    assert_not_echoed(result, secret)


def test_manage_check_with_a_bad_value_in_production_does_not_print_it(tmp_path):
    secret = canary(67)
    result = run_manage(tmp_path, {**PROD, NAME: secret}, "check")
    assert result.returncode != 0
    assert NAME in result.stderr
    assert_not_echoed(result, secret)


def test_manage_check_with_a_valid_value_passes(tmp_path):
    result = run_manage(tmp_path, {NAME: "https://example.com"}, "check")
    assert result.returncode == 0, result.stderr
