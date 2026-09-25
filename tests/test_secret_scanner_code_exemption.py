"""Architect ruling on the generic-assignment rule: the unquoted-lowercase-identifier exemption is for Python only.

An UNQUOTED value matching [a-z][a-z_]* is treated as a variable name (not a secret) only in Python source, meaning a
path ending exactly in .py (case-insensitive). In every other file type, and for any quoted value anywhere, the value
is treated as a possible secret (so `password=correcthorsebatterystaple` in a config file is caught).
"""
import pytest

RULE = "secret-assignment"
PASSPHRASE = "correcthorsebatterystaple"  # 25 lowercase letters: long enough to trigger the rule
LONG_WORDS = "thisisaverylongpassphrase"

IDENTIFIER_LINES = [
    "cache_write_tokens=estimated_input_tokens",
    "cache_creation_input_tokens=cache_creation_input_tokens,",
    "api_key=settings_api_key_value_here",
]
PASSPHRASE_LINES = [
    f"password={PASSPHRASE}",
    f"db_password: {PASSPHRASE}",
    f"api_key = {LONG_WORDS}",
]


def assignment_findings(scanner, text, path):
    return [f for f in scanner.find_secrets(text, path) if f.rule == RULE]


@pytest.mark.parametrize("line", IDENTIFIER_LINES)
@pytest.mark.parametrize("path", ["mod.py", "app/services/llm.py", "MOD.PY", "Mod.Py"])
def test_unquoted_lowercase_identifiers_are_not_flagged_in_python_source(scanner, line, path):
    assert scanner.find_secrets(line + "\n", path) == [], (path, line)


@pytest.mark.parametrize("line", PASSPHRASE_LINES)
@pytest.mark.parametrize("path", ["config.yaml", "notes.txt", "settings.ini", "Makefile", "README.md", "sub/dir/.flake8"])
def test_the_same_values_are_flagged_in_other_file_types(scanner, line, path):
    findings = assignment_findings(scanner, line + "\n", path)
    assert len(findings) == 1, (path, line)
    assert findings[0].path == path and findings[0].line == 1


@pytest.mark.parametrize("line", IDENTIFIER_LINES)
@pytest.mark.parametrize("path", ["config.yaml", "notes.txt", "Makefile"])
def test_identifier_looking_values_are_flagged_outside_python_too(scanner, line, path):
    assert len(assignment_findings(scanner, line + "\n", path)) == 1, (path, line)


@pytest.mark.parametrize("line, value", [(PASSPHRASE_LINES[0], PASSPHRASE), (PASSPHRASE_LINES[1], PASSPHRASE), (PASSPHRASE_LINES[2], LONG_WORDS)])
@pytest.mark.parametrize("path", ["config.yaml", "notes.txt", "settings.ini", "Makefile", "README.md"])
def test_redaction_never_contains_the_whole_value(scanner, line, value, path):
    (finding,) = assignment_findings(scanner, line, path)
    assert value not in finding.redacted and value not in repr(finding) and value not in str(finding)
    assert finding.redacted.endswith("…") and len(finding.redacted) <= 7


@pytest.mark.parametrize("quote", ['"', "'"])
@pytest.mark.parametrize("path", ["mod.py", "MOD.PY"])
def test_a_quoted_value_is_still_flagged_in_python(scanner, quote, path):
    line = f"password = {quote}{PASSPHRASE}{quote}"
    assert len(assignment_findings(scanner, line, path)) == 1


def test_a_quoted_identifier_looking_value_is_still_flagged_in_python(scanner):
    assert len(assignment_findings(scanner, 'api_key="' + "settings_api_key" + '_value_here"', "mod.py")) == 1


@pytest.mark.parametrize(
    "value",
    ["Abc123abc123abc123abc1", "ABCDEFGHIJKLMNOPQRSTUVWX", "abcdefghijklmnopqrstuvwx1", "Correcthorsebatterystaple", "abc_DEF_ghi_jkl_mno_pqr"],
)
def test_an_unquoted_value_with_digits_or_uppercase_is_still_flagged_in_python(scanner, value):
    assert len(assignment_findings(scanner, f"password = {value}", "mod.py")) == 1, value


@pytest.mark.parametrize("path", ["mod.pyc", "mod.python", "mod.pyx", "mod.pyi", "mod.py.txt", "mod.pyw", "notpy", "py"])
def test_only_exactly_dot_py_counts_as_python_source(scanner, path):
    line = "cache_write_tokens=estimated_input_tokens"
    assert len(assignment_findings(scanner, line, path)) == 1, path


# --- git modes -------------------------------------------------------------------------------------------------------

LEAKY_LINE = f"password={PASSPHRASE}\n"


def output(result):
    return result.stdout + result.stderr


def test_history_mode_reports_the_line_in_a_text_file_but_not_in_python(repo):
    repo.write("mod.py", LEAKY_LINE)
    repo.add("mod.py")
    repo.commit_plain("python only")
    assert repo.run_scanner("--history").returncode == 0

    repo.write("notes.txt", LEAKY_LINE)
    repo.add("notes.txt")
    repo.commit_plain("notes")
    result = repo.run_scanner("--history")
    assert result.returncode == 1, output(result)
    assert "notes.txt" in output(result)
    assert "mod.py" not in output(result)
    assert PASSPHRASE not in output(result)


def test_staged_and_all_modes_report_the_line_in_a_text_file_but_not_in_python(repo):
    repo.write("mod.py", LEAKY_LINE)
    repo.add("mod.py")
    assert repo.run_scanner("--staged").returncode == 0

    repo.write("notes.txt", LEAKY_LINE)
    repo.add("notes.txt")
    staged = repo.run_scanner("--staged")
    assert staged.returncode == 1, output(staged)
    assert "notes.txt" in output(staged) and "mod.py" not in output(staged)
    assert PASSPHRASE not in output(staged)

    repo.commit_plain("both")
    tracked = repo.run_scanner("--all")
    assert tracked.returncode == 1, output(tracked)
    assert "notes.txt" in output(tracked) and "mod.py" not in output(tracked)


def test_a_python_only_repository_with_that_line_is_clean_in_every_mode(repo):
    repo.write("mod.py", LEAKY_LINE)
    repo.add("mod.py")
    assert repo.run_scanner("--staged").returncode == 0
    repo.commit_plain()
    assert repo.run_scanner("--all").returncode == 0
    assert repo.run_scanner("--history").returncode == 0
