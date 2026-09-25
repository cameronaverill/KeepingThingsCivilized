"""Repository-level secret protection (step 1 fixes, sections D.1, D.5, D.6): .gitignore, tracked files, docs."""
import os
import subprocess

import pytest

from repo_helpers import REPO_ROOT, GitRepo, git_env


def read_lines(name):
    return [line.strip() for line in (REPO_ROOT / name).read_text().splitlines()]


def real_repo_git(*args):
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, env=git_env(REPO_ROOT), capture_output=True, text=True
    )


# --- .gitignore --------------------------------------------------------------------------------------------------------


def test_gitignore_lists_the_secret_patterns():
    lines = read_lines(".gitignore")
    for pattern in (".env", ".env.*", "!.env.example", "*.pem", "*.key", "secrets/"):
        assert pattern in lines, pattern
    assert lines.index("!.env.example") > lines.index(".env.*"), "the negation must come after the pattern it undoes"


@pytest.mark.parametrize(
    "path, ignored",
    [
        (".env", True),
        (".env.local", True),
        (".env.production", True),
        ("sub/.env", True),
        ("server.pem", True),
        ("keys/private.key", True),
        ("secrets/anthropic.txt", True),
        (".env.example", False),
        ("README.md", False),
        ("config/settings.py", False),
    ],
)
def test_gitignore_really_ignores_secret_files_in_git(tmp_path, path, ignored):
    # Uses git itself on a copy of .gitignore, so ordering and negation are tested as git evaluates them.
    repo = GitRepo(tmp_path / "ignore_check")
    (repo.path / ".gitignore").write_text((REPO_ROOT / ".gitignore").read_text())
    result = repo.git("check-ignore", "-q", path, check=False)
    assert (result.returncode == 0) is ignored, f"{path}: git check-ignore exit code {result.returncode}"


# --- what is tracked ---------------------------------------------------------------------------------------------------


def test_no_env_file_or_key_file_is_tracked():
    listing = real_repo_git("ls-files", "-z")
    assert listing.returncode == 0, listing.stderr
    tracked = [p for p in listing.stdout.split("\0") if p]
    assert tracked, "git ls-files returned nothing"
    bad = [
        p for p in tracked
        if os.path.basename(p) == ".env"
        or (os.path.basename(p).startswith(".env.") and os.path.basename(p) != ".env.example")
        or p.endswith((".pem", ".key"))
    ]
    assert bad == []


def test_env_example_stays_empty_of_values():
    for line in read_lines(".env.example"):
        if line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name in ("ANTHROPIC_API_KEY", "DJANGO_SECRET_KEY"):
            assert value == "", name


def test_check_secrets_all_finds_nothing_in_this_repository():
    script = REPO_ROOT / "scripts" / "check_secrets.py"
    assert script.is_file(), "scripts/check_secrets.py does not exist yet"
    import sys

    result = subprocess.run(
        [sys.executable, str(script), "--all"], cwd=REPO_ROOT, env=git_env(REPO_ROOT), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_file_in_the_working_tree_trips_the_scanner(scanner):
    # Everything git would commit (tracked plus untracked-but-not-ignored): the new scripts, hooks and every test file
    # too, so a real-looking literal in a test file is caught here before the pre-commit hook would catch it.
    listing = real_repo_git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    assert listing.returncode == 0, listing.stderr
    offenders = []
    checked = 0
    for relative in sorted({p for p in listing.stdout.split("\0") if p}):
        path = REPO_ROOT / relative
        if not path.is_file():
            continue  # tracked but deleted in the working tree
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        checked += 1
        offenders += [f"{f.path}:{f.line} {f.rule}" for f in scanner.find_secrets(text, relative)]
    assert checked > 20
    assert offenders == []


# --- docs ---------------------------------------------------------------------------------------------------------------


def test_claude_md_tells_agents_never_to_read_print_or_commit_env_or_keys():
    lines = [line for line in (REPO_ROOT / "CLAUDE.md").read_text().splitlines()]
    matching = [
        line for line in lines
        if ".env" in line and "never" in line.lower() and "hook" in line.lower()
        and all(word in line.lower() for word in ("read", "print", "commit"))
    ]
    assert matching, "CLAUDE.md needs a line: never read, print or commit .env or any API key; the hooks enforce it"
