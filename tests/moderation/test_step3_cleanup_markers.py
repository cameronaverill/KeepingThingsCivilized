"""Step 3 cleanup, job 3: every `secret-scan: allow` marker line carries exactly one marker, and none that is needed is lost.

An outside tool once repeated the marker many times on a line. These tests walk the source tree (not .venv, .git, the
scratchpad, caches, `.env*` files, databases, or Markdown prose that merely talks about the marker) and check the lines.
"""
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".venv", ".git", "scratchpad", "__pycache__", "node_modules", ".pytest_cache", "results"}
SKIP_SUFFIXES = {".md", ".sqlite3", ".pyc", ".png", ".jpg", ".ico", ".woff", ".woff2", ".gz", ".zip"}
MARKER = re.compile(r"secret-scan:\s*allow")  # also matches the no-space spelling "#secret-scan: allow"


def source_files():
    found = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if not path.is_file() or SKIP_DIRS & set(relative.parts):
            continue
        if path.name.startswith(".env") or path.name.startswith("db.sqlite") or path.suffix in SKIP_SUFFIXES:
            continue
        found.append(path)
    return found


def read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def scanner():
    spec = importlib.util.spec_from_file_location("check_secrets_for_marker_tests", ROOT / "scripts" / "check_secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def texts():
    return {path: read(path) for path in source_files()}


def test_the_walk_sees_the_files_this_job_is_about(texts):
    names = {str(path.relative_to(ROOT)) for path in texts}
    assert {"config/settings.py", "tests/test_accounts_user.py", "scripts/check_secrets.py", ".githooks/pre-commit"} <= names


def test_no_line_in_the_tree_carries_the_marker_more_than_once(texts):
    repeated = [
        f"{path.relative_to(ROOT)}:{number}"
        for path, text in texts.items()
        for number, line in enumerate(text.splitlines(), start=1)
        if len(MARKER.findall(line)) > 1
    ]
    assert repeated == []


def test_the_scanner_test_files_that_talk_about_the_marker_have_one_per_line(texts):
    counts = [
        len(MARKER.findall(line))
        for path, text in texts.items()
        if path.name.startswith("test_secret") or path.name == "check_secrets.py"
        for line in text.splitlines()
        if MARKER.search(line)
    ]
    assert (len(counts) > 5, set(counts)) == (True, {1})


def test_the_deliberately_public_dev_key_line_in_settings_keeps_its_one_marker(texts):
    lines = [line for line in texts[ROOT / "config" / "settings.py"].splitlines() if "deliberately public" in line]
    assert [len(MARKER.findall(line)) for line in lines] == [1]


def test_the_marker_lines_in_the_accounts_user_tests_keep_exactly_one(texts):
    lines = [line for line in texts[ROOT / "tests" / "test_accounts_user.py"].splitlines() if MARKER.search(line)]
    assert (len(lines) >= 5, {len(MARKER.findall(line)) for line in lines}) == (True, {1})


def test_the_scanner_finds_nothing_anywhere_in_the_tree(texts):
    module = scanner()
    findings = [
        f"{path.relative_to(ROOT)}: {finding}"
        for path, text in texts.items()
        for finding in module.find_secrets(text, str(path.relative_to(ROOT)))
    ]
    assert findings == []


def test_the_markers_in_settings_and_accounts_tests_are_still_needed(texts):
    """Non-vacuity: with the markers removed those files DO trip the scanner, so a reduction that dropped a needed marker
    would be caught by the test above."""
    module = scanner()
    tripped = {}
    for name in ("config/settings.py", "tests/test_accounts_user.py"):
        stripped = MARKER.sub("", texts[ROOT / name])
        tripped[name] = len(module.find_secrets(stripped, name)) > 0
    assert tripped == {"config/settings.py": True, "tests/test_accounts_user.py": True}
