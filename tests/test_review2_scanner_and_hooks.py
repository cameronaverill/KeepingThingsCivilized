"""Second independent review: scanner and hook behaviours that mutation testing showed were not pinned down.

Fake secrets are built at runtime.
"""
import os
import shutil
import subprocess
import sys

import pytest

from repo_helpers import SCANNER_PATH, git_env

FAKE_KEY = "sk-ant-" + "Ab1_-" * 8
AWS = "AKIA" + "ABCDEF0123456789"
TOKEN = "Zx9Qw7" * 4
CLEAN = "def add(a, b):\n    return a + b\n"


def output(result):
    return result.stdout + result.stderr


# --- content rules ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "disk-" + "a1" * 20,  # "sk-" in the middle of a word
        "risk-ant-" + "a" * 30,
        "task-" + "B2" * 20,
        "XAKIA" + "ABCDEF0123456789",
        "keyghp_" + "A1b2" * 9,
        "myxoxb-" + "1234567890",
    ],
)
def test_key_prefixes_in_the_middle_of_a_word_are_not_findings(scanner, text):
    assert scanner.find_secrets(f"the id is {text} here", "notes.txt") == []


def test_key_prefixes_after_punctuation_still_match(scanner):
    for lead in ("=", ":", '"', "'", "(", " ", "_", "/"):
        assert len(scanner.find_secrets(f"x{lead}{FAKE_KEY}", "notes.txt")) == 1, repr(lead)


@pytest.mark.parametrize("path", ["config.yaml", "notes.txt", "settings.ini", "Makefile"])
@pytest.mark.parametrize(
    "line",
    ["secret: " + "x" * 25, "password = " + "xxxx-1234-abcd-5678-abcd", "api_key=" + "X" * 4 + "yz" * 8 + "xxxx", "token: 1234xxxx56789012345678901"],
)
def test_the_xxxx_placeholder_is_recognised_outside_python_too(scanner, path, line):
    assert scanner.find_secrets(line, path) == [], line


@pytest.mark.parametrize(
    "line",
    [
        "password = Settings_Module_Name_Long.value",
        "token = Make_Token_Value_Long_Name(request)",
        "api_key = Config_Registry_Object_Name[0]",
        "secret: Another_Module_Long_Name.VALUE",
    ],
)
def test_code_that_reads_a_value_through_an_attribute_call_or_index_is_not_a_finding(scanner, line):
    assert scanner.find_secrets(line, "src/app.py") == []
    assert scanner.find_secrets(line, "notes.txt") == []


def test_a_key_and_a_separate_assignment_on_one_line_are_both_reported(scanner):
    line = f"password = {TOKEN}  # and also the key {FAKE_KEY}"
    rules = sorted(f.rule for f in scanner.find_secrets(line, "notes.txt"))
    assert len(rules) == 2 and "anthropic-api-key" in rules


def test_a_key_inside_an_assignment_is_reported_once_not_twice(scanner):
    assert len(scanner.find_secrets(f'api_key = "{FAKE_KEY}"', "app.py")) == 1
    assert len(scanner.find_secrets(f"ANTHROPIC_API_KEY={AWS}", "notes.txt")) == 1


# --- --history details -------------------------------------------------------------------------------------------------


def test_history_reports_the_right_line_number_in_the_right_commit(repo):
    repo.write("f.txt", "\n".join(f"line {i}" for i in range(1, 10)) + "\n")
    repo.add("f.txt")
    repo.commit_plain("nine clean lines")
    repo.write("f.txt", "\n".join(f"line {i}" for i in range(1, 10)) + f"\nkey is {FAKE_KEY}\n")
    repo.add("f.txt")
    repo.commit_plain("adds the key on line 10")
    result = repo.run_scanner("--history")
    assert result.returncode == 1
    assert "f.txt:10 " in output(result), output(result)
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    assert head[:8] in output(result), "the finding should name the commit that added it"


def test_history_reports_line_numbers_for_every_added_line_of_a_hunk(repo):
    repo.write("g.txt", f"a\n{AWS}\nb\nc\n{FAKE_KEY}\n")
    repo.add("g.txt")
    repo.commit_plain()
    text = output(repo.run_scanner("--history"))
    assert "g.txt:2 " in text and "g.txt:5 " in text, text


def test_history_reports_a_secret_looking_file_name_even_when_the_content_is_harmless(repo):
    repo.write(".env.production", "FOO=bar\n")
    repo.add(".env.production")
    repo.commit_plain("oops")
    repo.git("rm", "-q", "-f", ".env.production")
    repo.commit_plain("removed again")
    result = repo.run_scanner("--history")
    assert result.returncode == 1, output(result)
    assert ".env.production" in output(result)


@pytest.mark.parametrize("mode", ["--staged", "--all", "--history"])
def test_a_git_failure_is_a_non_zero_exit_with_a_readable_message(tmp_path, mode):
    # Outside any repository the scan cannot run; it must not report "clean".
    env = git_env(extra={"GIT_CEILING_DIRECTORIES": str(tmp_path.parent)})
    result = subprocess.run([sys.executable, str(SCANNER_PATH), mode], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode != 0, output(result)
    assert "Traceback" not in output(result)
    assert output(result).strip()


# --- hooks ---------------------------------------------------------------------------------------------------------------


def restricted_path(tmp_path):
    """A PATH with git and nothing else: no python3 anywhere."""
    bindir = tmp_path / "only_git"
    bindir.mkdir()
    (bindir / "git").symlink_to(shutil.which("git"))
    return str(bindir)


@pytest.mark.parametrize("name", ["pre-commit", "pre-push"])
def test_a_hook_fails_closed_when_no_python_can_be_found(repo, tmp_path, name):
    repo.install_protection()
    repo.write("a.txt", "a\n")
    repo.add("a.txt")
    env = {**repo.env, "PATH": restricted_path(tmp_path)}
    result = subprocess.run(
        [str(repo.path / ".githooks" / name), "origin", "file:///nowhere"],
        cwd=repo.path, env=env, input="", capture_output=True, text=True,
    )
    assert result.returncode != 0, "with no interpreter the hook must block, not wave everything through"
    assert "python" in output(result).lower()


@pytest.mark.parametrize("name", ["pre-commit", "pre-push"])
def test_each_hook_prefers_the_venv_python(repo, tmp_path, name):
    repo.install_protection()
    marker = tmp_path / f"{name}_venv_used"
    repo.write(".venv/bin/python", f'#!/bin/sh\necho used >> "{marker}"\nexec "{sys.executable}" "$@"\n', executable=True)
    repo.write("a.txt", "a\n")
    repo.add("a.txt")
    repo.commit_plain()
    result = subprocess.run(
        [str(repo.path / ".githooks" / name), "origin", "file:///nowhere"],
        cwd=repo.path, env=repo.env, input="", capture_output=True, text=True,
    )
    assert result.returncode == 0, output(result)
    assert marker.exists(), f"{name} did not use .venv/bin/python"


@pytest.mark.parametrize("name", ["pre-commit", "pre-push"])
def test_hook_block_messages_explain_that_deleting_a_key_does_not_remove_it_from_history(name):
    from repo_helpers import REPO_ROOT

    text = " ".join((REPO_ROOT / ".githooks" / name).read_text().split())
    assert "REVOKE" in text
    assert "does not remove it from git history" in text


# --- weaknesses found by the review, now required to be fixed --------------------------------------------------------------


def seconds_to_scan(text, path="big.txt", timeout=25):
    """Scan `text` in a child process; return the time find_secrets itself took (start-up excluded)."""
    code = (
        "import importlib.util, sys, time;"
        f"spec = importlib.util.spec_from_file_location('cs', {str(SCANNER_PATH)!r});"
        "m = importlib.util.module_from_spec(spec); sys.modules['cs'] = m; spec.loader.exec_module(m);"
        "text = sys.stdin.read(); t = time.perf_counter(); m.find_secrets(text, sys.argv[1]);"
        "print(time.perf_counter() - t)"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, path], input=text, timeout=timeout, capture_output=True, text=True
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"scanning {len(text):,} characters took more than {timeout} seconds (quadratic regex backtracking?)")
    assert result.returncode == 0, result.stderr
    return float(result.stdout.strip())


@pytest.mark.parametrize("unit", ["password", "token", "secret", "api_key", "passwd", "secret_", "token=", "password:"])
def test_a_100k_character_line_of_repeated_keywords_is_scanned_in_under_two_seconds(unit):
    line = (unit * (100_000 // len(unit) + 1))[:100_000] + "\n"
    assert seconds_to_scan(line) < 2


@pytest.mark.parametrize("unit", ["password", "token", "api_key"])
def test_a_one_megabyte_line_of_repeated_keywords_is_scanned_in_under_two_seconds(unit):
    line = (unit * (1_000_000 // len(unit) + 1))[:1_000_000] + "\n"
    assert seconds_to_scan(line) < 2


def test_one_megabyte_lines_of_other_shapes_are_also_fast():
    assert seconds_to_scan("a" * 1_000_000 + "\n") < 2
    assert seconds_to_scan("password=" + "a" * 1_000_000 + "\n") < 2
    assert seconds_to_scan("sk-" * 333_000 + "\n") < 2
    assert seconds_to_scan("x:" * 500_000 + "\n") < 2
    assert seconds_to_scan("Bearer " * 140_000 + "\n") < 2
    assert seconds_to_scan("://a:" * 200_000 + "\n") < 2


@pytest.mark.parametrize("path", [".ENV", ".Env.local", "config/.ENV.production", ".eNv", "sub/.Env"])
def test_env_file_names_are_recognised_in_any_letter_case(scanner, path):
    assert scanner.find_secrets("FOO=bar\n", path)


@pytest.mark.parametrize("path", [".env.example", "deploy/.env.example"])
def test_the_env_example_template_stays_allowed(scanner, path):
    assert scanner.find_secrets("FOO=\n", path) == []


def test_history_treats_a_python_file_with_a_space_in_its_name_like_the_other_modes(repo):
    repo.write("my file.py", "cache_write_tokens=estimated_input_tokens\n")
    repo.add("my file.py")
    repo.commit_plain()
    assert repo.run_scanner("--all").returncode == 0
    assert repo.run_scanner("--staged").returncode == 0
    result = repo.run_scanner("--history")
    assert result.returncode == 0, output(result)


def test_history_output_names_a_file_with_a_space_without_a_stray_tab(repo):
    repo.write("my notes.txt", f"key is {FAKE_KEY}\n")
    repo.add("my notes.txt")
    repo.commit_plain()
    result = repo.run_scanner("--history")
    assert result.returncode == 1
    assert "my notes.txt:1" in output(result), repr(output(result))
    assert "\t" not in output(result)


# --- staged modifications and renames (not only brand-new files) -------------------------------------------------------


def test_staged_modification_of_a_tracked_file_that_adds_a_key_is_blocked(repo):
    repo.write("app.py", CLEAN)
    repo.add("app.py")
    repo.commit_plain("clean")
    repo.write("app.py", CLEAN + f'KEY = "{FAKE_KEY}"\n')
    repo.add("app.py")
    result = repo.run_scanner("--staged")
    assert result.returncode == 1, output(result)
    assert "app.py:3" in output(result)


def test_staged_rename_that_also_adds_a_key_is_blocked(repo):
    repo.write("old_name.txt", "".join(f"line {i}\n" for i in range(30)))
    repo.add("old_name.txt")
    repo.commit_plain("clean")
    repo.git("mv", "old_name.txt", "new_name.txt")
    repo.write("new_name.txt", "".join(f"line {i}\n" for i in range(30)) + f"key is {FAKE_KEY}\n")
    repo.add("new_name.txt")
    assert "R" in repo.git("diff", "--cached", "--name-status").stdout.split()[0], "expected git to see a rename"
    result = repo.run_scanner("--staged")
    assert result.returncode == 1, output(result)
    assert "new_name.txt:31" in output(result)


def test_renaming_a_file_to_a_secret_looking_name_is_blocked_when_staged_and_found_in_history(repo):
    repo.write("settings_backup.txt", "".join(f"line {i}\n" for i in range(30)))
    repo.add("settings_backup.txt")
    repo.commit_plain("clean")
    repo.git("mv", "settings_backup.txt", ".env.local")
    repo.add(".env.local")
    assert repo.git("diff", "--cached", "--name-status").stdout.startswith("R")
    staged = repo.run_scanner("--staged")
    assert staged.returncode == 1 and ".env.local" in output(staged), output(staged)
    repo.commit_plain("rename")
    history = repo.run_scanner("--history")
    assert history.returncode == 1 and ".env.local" in output(history), output(history)


def test_staged_modification_of_a_tracked_env_file_is_blocked(repo):
    repo.write(".env", "A=1\n")
    repo.add(".env")
    repo.commit_plain("oops, tracked already")
    repo.write(".env", "A=2\n")
    repo.add(".env")
    assert repo.run_scanner("--staged").returncode == 1


@pytest.mark.parametrize("name", ["pre-commit", "pre-push"])
def test_hooks_work_when_started_from_a_subdirectory(repo, name):
    repo.install_protection()
    repo.write("sub/dir/ok.py", CLEAN)
    repo.add("sub/dir/ok.py")
    repo.commit_plain()
    stdin = ""
    hook = str(repo.path / ".githooks" / name)
    clean = subprocess.run([hook, "origin", "x"], cwd=repo.path / "sub" / "dir", env=repo.env, input=stdin, capture_output=True, text=True)
    assert clean.returncode == 0, output(clean)
    repo.write("sub/dir/leak.py", f'K = "{FAKE_KEY}"\n')
    repo.add("sub/dir/leak.py")
    if name == "pre-push":
        repo.commit_plain("leak")
    blocked = subprocess.run([hook, "origin", "x"], cwd=repo.path / "sub" / "dir", env=repo.env, input=stdin, capture_output=True, text=True)
    assert blocked.returncode != 0
    assert "REVOKE" in output(blocked)
