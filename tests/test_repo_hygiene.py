"""Secrets stay out of git; dependencies are pinned; dev-only packages stay out of the runtime list."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def read_lines(name):
    return [line.strip() for line in (REPO_ROOT / name).read_text().splitlines()]


def test_gitignore_keeps_secrets_and_local_data_out_of_git():
    lines = read_lines(".gitignore")
    for pattern in (".env", "db.sqlite3", "golden/results/", ".venv/"):
        assert pattern in lines, pattern


def test_env_example_has_the_secret_slots_and_no_secret_values():
    values = dict(
        line.split("=", 1) for line in read_lines(".env.example") if line and not line.startswith("#") and "=" in line
    )
    for name in ("ANTHROPIC_API_KEY", "DJANGO_SECRET_KEY"):
        assert name in values, name
        assert values[name] == "", f"{name} must be empty in .env.example"
    assert not any(re.search(r"sk-ant-", v) for v in values.values())


def test_runtime_requirements_are_pinned_exactly():
    entries = [line for line in read_lines("requirements.txt") if line and not line.startswith("#")]
    assert entries
    assert all("==" in line for line in entries), entries


def test_dev_packages_are_not_in_the_runtime_requirements():
    runtime = "\n".join(read_lines("requirements.txt")).lower()
    assert "pytest" not in runtime
    dev = "\n".join(read_lines("requirements-dev.txt")).lower()
    assert "-r requirements.txt" in dev and "pytest" in dev


def test_python_version_file_matches_the_running_interpreter():
    import sys

    wanted = (REPO_ROOT / ".python-version").read_text().strip()
    assert f"{sys.version_info.major}.{sys.version_info.minor}" == wanted


def test_runtime_requirements_are_exactly_the_expected_packages_all_pinned():
    entries = [line for line in read_lines("requirements.txt") if line and not line.startswith("#")]
    pinned = {}
    for line in entries:
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==(\d+(?:\.\d+)*)", line)
        assert match, f"not an exact pin (name==version): {line!r}"
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        assert name not in pinned, f"{name} listed twice"
        pinned[name] = match.group(2)
    assert set(pinned) == {
        "django", "anthropic", "pydantic", "argon2-cffi", "django-axes", "python-dotenv", "gunicorn", "whitenoise",
    }
