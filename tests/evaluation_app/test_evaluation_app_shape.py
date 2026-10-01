"""The evaluation app after cleanup 1: it holds only the six seeded-error commands (no models, no views/urls/admin), its history
still replays, the site never imports it, and only moderation/llm.py imports `anthropic` (seeding and the commands reach the
gateway through moderation)."""
import ast
import importlib
import io
import re
from pathlib import Path

import pytest
from django.apps import apps
from django.core.management import call_command, get_commands

REPO = Path(__file__).resolve().parents[2]
SITE_PACKAGES = ["accounts", "forum", "moderation", "config"]
COMMANDS = ["generate_conversations", "judge_responses", "summarize_pilot", "run_research_eval", "judge_research", "summarize_research"]
DELETED_FILES = ["llm_rater.py", "matching.py", "consensus.py", "calibration.py", "blinding.py", "targets.py", "schemas.py", "prompts/rater_v1.md"]
DELETED_COMMANDS = ["seed_panel", "run_raters"]
OLD_MODELS = ["Rater", "Panel", "PanelMember", "Rating", "Finding", "ConsensusFinding", "ConsensusFindingMember", "IssueFindingLink",
              "CalibrationSet", "CalibrationItem", "Annotation"]
GATEWAY_USERS = {"seeding/generate.py", "seeding/judge.py", "seeding/research_eval.py"}  # outside moderation/, the only files naming the gateway


def evaluation_path():
    return Path(apps.get_app_config("evaluation").path)


def test_the_app_is_installed_with_its_skeleton():
    root = evaluation_path()
    for name in ("__init__.py", "apps.py", "models.py", "management/commands", "migrations/0001_initial.py"):
        assert (root / name).exists(), name
    assert list(root.glob("migrations/0002_*.py")) and list(root.glob("migrations/0003_delete_retired_rating_models.py"))


def test_the_app_has_no_models():
    assert list(apps.get_app_config("evaluation").get_models()) == []


@pytest.mark.parametrize("name", DELETED_FILES)
def test_retired_files_are_gone(name):
    assert not (evaluation_path() / name).exists(), name


@pytest.mark.parametrize("name", ["views.py", "urls.py", "admin.py", "forms.py", "templates", "templatetags", "static"])
def test_no_view_url_admin_form_or_template(name):
    assert not (evaluation_path() / name).exists(), name


@pytest.mark.parametrize("module", ["evaluation.llm_rater", "evaluation.matching", "evaluation.consensus", "evaluation.calibration",
                                    "evaluation.blinding", "evaluation.targets", "evaluation.schemas", "evaluation.views"])
def test_retired_modules_cannot_be_imported(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_the_six_seeded_error_commands_are_registered_to_evaluation():
    commands = get_commands()
    for name in COMMANDS:
        assert commands.get(name) == "evaluation", name
    for name in DELETED_COMMANDS:
        assert name not in commands, name
        assert not (evaluation_path() / "management" / "commands" / f"{name}.py").exists()


@pytest.mark.parametrize("name", COMMANDS)
def test_each_command_module_imports_cleanly(name):
    importlib.import_module(f"evaluation.management.commands.{name}")


@pytest.mark.django_db
def test_makemigrations_check_is_clean():
    call_command("makemigrations", "--check", "--dry-run", stdout=io.StringIO(), stderr=io.StringIO())


@pytest.mark.django_db
def test_the_old_tables_are_gone_after_migrating():
    from django.db import connection

    tables = set(connection.introspection.table_names())
    assert not {t for t in tables if t.startswith("evaluation_")}


def test_migration_0003_deletes_the_retired_models():
    module = importlib.import_module("evaluation.migrations.0003_delete_retired_rating_models")
    deleted = {op.name for op in module.Migration.operations if op.__class__.__name__ == "DeleteModel"}
    assert deleted == set(OLD_MODELS) or deleted >= {"Rater", "Rating", "Finding", "Annotation"}


# --- no dangling references anywhere in code or tests -------------------------------------------------------------------------------
def python_files(*roots):
    for root in roots:
        for path in sorted((REPO / root).rglob("*.py")):
            if "__pycache__" not in path.parts and ".venv" not in path.parts:
                yield path


def test_no_code_imports_the_retired_modules():
    pattern = re.compile(r"evaluation\.(llm_rater|matching|consensus|calibration|blinding|targets|schemas|models)\b")
    offenders = {}
    for path in python_files("accounts", "forum", "moderation", "config", "seeding", "analysis", "golden", "evaluation", "scripts"):
        if "migrations" in path.parts:
            continue
        hits = pattern.findall(path.read_text())
        if hits:
            offenders[str(path.relative_to(REPO))] = hits
    assert offenders == {}


def test_no_retired_tunable_is_read_anywhere():
    pattern = re.compile(r"\b(JUDGE_MODELS|SPAN_MATCH_MIN_IOU|INTENSITY_DISAGREEMENT_THRESHOLD|CALIBRATION_\w+|RATER_\w+)\b")
    offenders = {}
    for path in python_files("accounts", "forum", "moderation", "config", "seeding", "analysis", "golden", "evaluation", "scripts"):
        if "migrations" in path.parts:
            continue  # history may mention retired names in comments
        hits = pattern.findall(path.read_text())
        if hits:
            offenders[str(path.relative_to(REPO))] = hits
    assert offenders == {}


# --- the site never imports evaluation ---------------------------------------------------------------------------------------------
def evaluation_references(source, allow_bare_name=False):
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name == "evaluation" or a.name.startswith("evaluation.")]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "evaluation" or module.startswith("evaluation."):
                found.append(module)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.fullmatch(r"evaluation(\.[\w.]+)?", node.value) and not (allow_bare_name and node.value == "evaluation"):
                found.append(f"string {node.value!r}")
    return found


def test_no_site_module_refers_to_evaluation():
    offenders = {}
    for path in python_files(*SITE_PACKAGES):
        hits = evaluation_references(path.read_text(), allow_bare_name=(path == REPO / "config" / "settings.py"))
        if hits:
            offenders[str(path.relative_to(REPO))] = hits
    assert offenders == {}


# --- the import rule ---------------------------------------------------------------------------------------------------------------
def imported_names(source):
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = ("." * node.level) + (node.module or "")
            names += [base] + [f"{base}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("import_module", "__import__"):
            names += [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    return names


def test_evaluation_never_imports_a_client_or_the_gateway_directly():
    offenders = []
    for path in python_files("evaluation"):
        if "migrations" in path.parts:
            continue
        for n in imported_names(path.read_text()):
            if re.match(r"(anthropic|httpx|requests|urllib3?)\b", n) or re.match(r"moderation\.(llm|errors|quotes)\b", n):
                offenders.append((str(path.relative_to(REPO)), n))
    assert offenders == []


def test_outside_moderation_only_the_seeding_modules_import_the_gateway():
    pattern = re.compile(r"moderation\.(llm|errors|quotes)\b")
    found = set()
    for path in python_files("accounts", "forum", "config", "seeding", "analysis", "golden", "evaluation"):
        if "migrations" in path.parts:
            continue
        if any(pattern.match(n) for n in imported_names(path.read_text())):
            found.add(path.relative_to(REPO).as_posix())
    assert found <= GATEWAY_USERS, found - GATEWAY_USERS


def test_no_module_outside_moderation_llm_imports_anthropic():
    offenders = []
    for path in python_files("accounts", "forum", "config", "seeding", "analysis", "golden", "evaluation", "moderation", "scripts"):
        if path == REPO / "moderation" / "llm.py":
            continue
        if any(n.split(".")[0] == "anthropic" for n in imported_names(path.read_text())):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []
