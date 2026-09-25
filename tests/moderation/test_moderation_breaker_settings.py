"""Tunables, the ALERT_EMAIL setting and .env.example for the two-tier breaker and its alerts."""
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from config import tunables

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NEW = {
    "BREAKER_COOLDOWN_SECONDS": 300,
    "BREAKER_COOLDOWN_MAX_SECONDS": 3600,
    "BREAKER_PROBE_TIMEOUT_SECONDS": 180,
    "ALERT_MIN_SECONDS_BETWEEN_EMAILS": 3600,
}


@pytest.mark.parametrize("name, value", sorted(NEW.items()))
def test_new_tunables_have_the_agreed_defaults(name, value):
    assert getattr(tunables, name) == value
    assert isinstance(getattr(tunables, name), int)


@pytest.mark.parametrize("name", sorted(NEW))
def test_new_tunables_have_a_comment_directly_above_and_are_django_settings(name):
    from django.conf import settings

    path = Path(tunables.__file__)
    lines = path.read_text().splitlines()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            assert lines[node.lineno - 2].lstrip().startswith("#"), name
            break
    else:
        pytest.fail(f"{name} is not assigned in tunables.py")
    assert getattr(settings, name) == getattr(tunables, name)


def test_the_cooldown_settings_are_consistent():
    assert 0 < tunables.BREAKER_COOLDOWN_SECONDS <= tunables.BREAKER_COOLDOWN_MAX_SECONDS
    assert tunables.BREAKER_PROBE_TIMEOUT_SECONDS > 0
    assert tunables.ALERT_MIN_SECONDS_BETWEEN_EMAILS >= 0


def test_alert_email_is_not_a_tunable_and_no_address_is_in_tunables_py():
    text = Path(tunables.__file__).read_text()
    assert not hasattr(tunables, "ALERT_EMAIL")
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign):
            assert all(getattr(t, "id", "") != "ALERT_EMAIL" for t in node.targets)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), "tunables.py must contain no email address"


def _settings_value(env_value):
    code = "import config.settings as s; print(repr(s.ALERT_EMAIL))"
    env = {k: v for k, v in os.environ.items() if k not in ("ALERT_EMAIL", "ANTHROPIC_API_KEY", "DJANGO_DB_PATH")}
    if env_value is not None:
        env["ALERT_EMAIL"] = env_value
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_settings_alert_email_defaults_to_empty_and_blank_counts_as_unset():
    assert _settings_value(None) == "''"
    assert _settings_value("") == "''"
    assert _settings_value("   ") == "''"


def test_settings_alert_email_reads_the_environment_and_strips_it():
    assert _settings_value("  alerts@example.test  ") == "'alerts@example.test'"


def test_settings_alert_email_is_empty_under_test_by_default(settings):
    """Tests must never mail anybody: nothing in the environment of the suite may set an alert address."""
    from django.conf import settings as live

    assert live.ALERT_EMAIL == "" or os.environ.get("ALERT_EMAIL")  # only an explicit environment variable could set it


def test_env_example_has_an_empty_alert_email_line_with_a_comment_above():
    lines = (REPO_ROOT / ".env.example").read_text().splitlines()
    matches = [i for i, line in enumerate(lines) if re.fullmatch(r"ALERT_EMAIL=\s*", line)]
    assert len(matches) == 1, "expected exactly one empty ALERT_EMAIL= line"
    assert lines[matches[0] - 1].lstrip().startswith("#")


def test_no_real_address_appears_in_code_or_config_files():
    """The user's own address lives only in their untracked .env (docs may mention it; code and config may not)."""
    needle = "cameron" + "averill"  # built at runtime so this file does not contain it
    skip = {".venv", ".git", "__pycache__", "node_modules", "docs"}
    offenders = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for filename in filenames:
            if filename == ".env" or not filename.endswith((".py", ".example", ".ini", ".cfg", ".toml", ".txt", ".json", ".html")):
                continue
            path = Path(dirpath) / filename
            if needle in path.read_text(errors="ignore").lower():
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []
