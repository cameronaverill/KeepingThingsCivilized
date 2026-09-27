"""Every `manage.py <name>` in the README is a real command, and every real project command is described."""
import re
from pathlib import Path

import pytest
from django.core.management import get_commands

import readme_kit

# The project's own commands (moderation/, forum/, evaluation/); the README must mention each one.
PROJECT_COMMANDS = [
    "run_moderator", "replay", "export_conversation", "export_all", "seed_topics", "seed_panel", "run_raters",
    "budget", "reset_breaker", "spike",
]


def test_readme_mentions_at_least_one_manage_command(readme):
    assert readme_kit.mentioned_commands(readme.text) != []


def test_every_manage_command_the_readme_mentions_is_real(readme):
    real = set(get_commands())
    unknown = [name for name in readme_kit.mentioned_commands(readme.text) if name not in real]
    assert unknown == []


def test_the_command_list_in_this_test_still_matches_the_repository():
    """If a command is added or removed, this list (and the README) must be updated together."""
    assert readme_kit.project_command_names() == sorted(PROJECT_COMMANDS)


def test_the_project_commands_are_registered_with_django():
    real = set(get_commands())
    assert [c for c in PROJECT_COMMANDS if c not in real] == []


@pytest.mark.parametrize("command", PROJECT_COMMANDS)
def test_every_project_command_is_mentioned_in_the_readme(readme, command):
    assert re.search(r"(?<![A-Za-z0-9_])" + re.escape(command) + r"(?![A-Za-z0-9_])", readme.text), command


@pytest.mark.parametrize("command", PROJECT_COMMANDS)
def test_every_project_command_is_named_in_code_font(readme, command):
    named = [span for span in readme.code_spans if re.search(r"(?<![A-Za-z0-9_])" + command + r"(?![A-Za-z0-9_])", span)]
    assert named != []


def test_manage_py_exists_at_the_repository_root():
    assert (Path(readme_kit.REPO_ROOT) / "manage.py").is_file()


def test_the_readme_uses_the_virtualenv_python_not_a_bare_python_for_manage_py(readme):
    """CLAUDE.md: the system Python cannot run Django, so commands are shown as `.venv/bin/python manage.py ...`."""
    bare = readme_kit.lines_matching(readme.text, r"(?<![/\w.])python3? manage\.py")
    assert bare == []


def test_the_readme_has_a_table_row_for_each_project_command(readme):
    rows = readme_kit.command_table_rows(readme.text)
    assert [c for c in PROJECT_COMMANDS if c not in rows] == []


@pytest.mark.parametrize("command", PROJECT_COMMANDS)
def test_every_option_shown_in_a_commands_table_row_is_a_real_option(readme, command):
    row = readme_kit.command_table_rows(readme.text)[command]
    shown = readme_kit.option_flags(row)
    real = readme_kit.command_options(command)
    assert [flag for flag in shown if flag not in real] == []


def test_the_readme_says_spike_requires_max_usd_and_it_does(readme):
    row = readme_kit.command_table_rows(readme.text)["spike"]
    assert "--max-usd" in row
    from django.core.management import get_commands, load_command_class

    parser = load_command_class(get_commands()["spike"], "spike").create_parser("manage.py", "spike")
    required = [a.option_strings for a in parser._actions if a.required]
    assert ["--max-usd"] in required


def test_budget_has_no_options_of_its_own_as_the_readme_says():
    """`budget` is described as having no options of its own."""
    own = readme_kit.command_options("budget") - readme_kit.django_base_options()
    assert own == set()
