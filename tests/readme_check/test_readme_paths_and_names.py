"""Paths, tunables, environment variables and values named in the README are real and current."""
import re

import pytest

import readme_kit


def test_every_repository_path_in_the_readme_exists(readme):
    assert readme_kit.missing_paths(readme) == []


def test_the_readme_names_some_repository_paths(readme):
    assert len(readme_kit.path_candidates(readme)) >= 15


def test_runtime_created_paths_are_only_the_allow_listed_ones():
    assert readme_kit.RUNTIME_CREATED_PATHS == (".env", "db.sqlite3", ".venv", "scratchpad", "index.json")


REPOSITORY_MAP_DIRECTORIES = [
    "accounts", "analysis", "config", "docs", "evaluation", "forum", "golden", "moderation", "rubrics", "scripts",
    "tests",
]


@pytest.mark.parametrize("directory", REPOSITORY_MAP_DIRECTORIES)
def test_the_repository_map_mentions_each_top_level_directory(readme, directory):
    assert (readme_kit.REPO_ROOT / directory).is_dir()
    assert re.search(r"(?<![A-Za-z0-9_/.-])" + directory + r"/", readme.text), directory


REQUIRED_FILE_MENTIONS = [
    "config/tunables.py", "docs/plan.md", "docs/neutrality.md", "docs/plan_summary.md", "scripts/dev.sh",
    "scripts/install_hooks.sh", "scripts/check_secrets.py", ".env.example", "manage.py", "requirements.txt",
]


@pytest.mark.parametrize("name", REQUIRED_FILE_MENTIONS)
def test_the_readme_mentions_key_file(readme, name):
    assert (readme_kit.REPO_ROOT / name).exists()
    assert name in readme.text


def test_every_upper_snake_name_in_code_is_a_tunable_or_env_variable(readme):
    assert readme_kit.unknown_upper_snake(readme) == []


def test_the_readme_names_some_tunables(readme):
    named = set(readme_kit.upper_snake_in_code(readme)) & readme_kit.tunable_names()
    assert len(named) >= 5


def test_every_environment_variable_named_is_in_env_example(readme):
    assert readme_kit.unknown_env_names(readme) == []


@pytest.mark.parametrize("variable", ["ANTHROPIC_API_KEY", "DJANGO_ENV", "DJANGO_SECRET_KEY", "DJANGO_DB_PATH"])
def test_the_readme_explains_the_key_environment_variables(readme, variable):
    assert variable in readme_kit.env_example_names()
    assert variable in readme.text


def test_tunable_values_quoted_in_the_readme_match_config_tunables(readme):
    assert readme_kit.wrong_tunable_values(readme) == []


@pytest.mark.parametrize("name", ["LLM_ENABLED", "BUDGET_SITE_USD_TOTAL", "BUDGET_SITE_USD_PER_DAY"])
def test_the_readme_names_the_cost_control_tunables(readme, name):
    assert name in readme_kit.tunable_names()
    assert name in readme_kit.upper_snake_in_code(readme)


def test_every_wildcard_tunable_prefix_matches_at_least_one_tunable(readme):
    names = readme_kit.tunable_names()
    unmatched = [p for p in readme_kit.wildcard_prefixes(readme) if not any(n.startswith(p) for n in names)]
    assert unmatched == []


@pytest.mark.parametrize("hook", ["pre-commit", "pre-push"])
def test_the_git_hooks_the_readme_names_exist(readme, hook):
    assert hook in readme.text
    assert (readme_kit.REPO_ROOT / ".githooks" / hook).is_file()


# (regex with one number in group 1, name of the tunable that number must equal, how the tunable is written in prose)
PROSE_NUMBERS = [
    (r"([\d,]+) characters per message", "MAX_MESSAGE_CHARS", lambda v: f"{v:,}"),
    (r"one message every (\d+) seconds", "MIN_SECONDS_BETWEEN_MESSAGES", str),
    (r"(\d+) user messages per conversation", "MAX_USER_MESSAGES_PER_CONVERSATION", str),
    (r"(\d+) characters per proposition", "MAX_PROPOSITION_CHARS", str),
    (r"(\d+) new propositions per\s+person per day", "MAX_PROPOSITIONS_PER_USER_PER_DAY", str),
    (r"After (\d+) failed logins", "LOGIN_MAX_FAILURES", str),
    (r"locked out for (\d+) minutes", "LOGIN_COOLOFF_MINUTES", str),
    (r"at least (\d+) characters, must not be common", "PASSWORD_MIN_LENGTH", str),
    (r"Usernames are (\d+) to \d+ characters", "USERNAME_MIN_LENGTH", str),
    (r"Usernames are \d+ to (\d+) characters", "USERNAME_MAX_LENGTH", str),
    (r"(?i)the (\d+)-second gap", "MIN_SECONDS_BETWEEN_MESSAGES", str),
    (r"(?i)the (\d+)-message cap", "MAX_USER_MESSAGES_PER_CONVERSATION", str),
    (r"Sessions last (\d+)\s+days", "SESSION_COOKIE_AGE", lambda v: str(v // 86400)),
]


@pytest.mark.parametrize(("pattern", "name", "written"), PROSE_NUMBERS)
def test_numbers_quoted_in_prose_match_the_tunable(readme, pattern, name, written):
    from config import tunables

    expected = written(getattr(tunables, name))
    found = re.findall(pattern, readme.text)
    assert [value for value in found if value != expected] == []


@pytest.mark.parametrize(("pattern", "name", "written"), PROSE_NUMBERS)
def test_the_prose_number_claims_this_test_knows_about_are_still_in_the_readme(readme, pattern, name, written):
    """If the wording changes so that a pattern no longer matches, update PROSE_NUMBERS (else the check goes blind)."""
    assert re.search(pattern, readme.text)
