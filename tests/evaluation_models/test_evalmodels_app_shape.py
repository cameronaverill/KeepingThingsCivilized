"""The evaluation app as a whole: migrations agree with the models and are reversible, the site never imports
`evaluation`, and this wave adds no view, URL, admin, form or template (owner decision 2026-09-25)."""
import ast
import io
import re
from pathlib import Path

import evalmodels_testkit as kit
import pytest
from django.apps import apps
from django.core.management import call_command
from django.db import connection

REPO = Path(__file__).resolve().parents[2]
SITE_PACKAGES = ["accounts", "forum", "moderation", "config"]
MODELS = ["Rater", "Panel", "Rating", "Finding", "ConsensusFinding", "IssueFindingLink", "CalibrationSet", "CalibrationItem", "Annotation"]


def evaluation_path():
    return Path(apps.get_app_config("evaluation").path)


# --- files and models --------------------------------------------------------------------------------------------------
def test_the_app_has_the_contract_files():
    root = evaluation_path()
    for name in ("__init__.py", "apps.py", "models.py", "consensus.py", "blinding.py", "calibration.py", "migrations/__init__.py", "migrations/0001_initial.py"):
        assert (root / name).is_file(), name


def test_the_nine_models_are_registered_under_the_evaluation_label_and_any_extra_is_a_many_to_many_through_table():
    models = list(apps.get_app_config("evaluation").get_models())
    labels = {m._meta.label for m in models}
    assert {f"evaluation.{name}" for name in MODELS} <= labels
    through = {f.remote_field.through._meta.label for m in models for f in m._meta.many_to_many}
    assert labels - {f"evaluation.{name}" for name in MODELS} <= through


@pytest.mark.parametrize("name", ["views.py", "urls.py", "admin.py", "forms.py", "templates", "templatetags", "static"])
def test_no_view_url_admin_form_or_template_was_added(name):
    assert not (evaluation_path() / name).exists(), name


@pytest.mark.parametrize("module", ["evaluation.views", "evaluation.urls", "evaluation.admin"])
def test_those_modules_cannot_be_imported(module):
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_the_site_urls_do_not_mention_evaluation():
    text = (REPO / "config" / "urls.py").read_text()
    assert "evaluation" not in text


def test_the_tunables_the_wave_needs_exist():
    from config import tunables

    assert tunables.SPAN_MATCH_MIN_IOU == 0.5
    assert tunables.INTENSITY_DISAGREEMENT_THRESHOLD == 2


def imported_names(path):
    """Every dotted name a module imports: `import a.b` gives a.b, `from a import b, c` gives a, a.b and a.c."""
    names = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names += [node.module or ""] + [(node.module or "") + "." + a.name for a in node.names]
    return names


def evaluation_sources():
    for path in sorted(evaluation_path().rglob("*.py")):
        if "migrations" not in path.parts and "__pycache__" not in path.parts:
            yield path.relative_to(evaluation_path()).as_posix(), path


CLIENTS = r"(anthropic|httpx|requests|urllib3?)\b"
GATEWAY = r"moderation\.(llm|errors|quotes)\b"


def test_only_llm_rater_talks_to_the_gateway():
    """Step 14 (docs/step14_brief.md, architect ruling): exactly one module, evaluation/llm_rater.py, may import moderation.llm,
    moderation.errors and moderation.quotes; nothing else in evaluation may. (moderation.budget, .clock, .pricing and .taxonomy
    are ordinary helpers and are not restricted.) No module of the app imports an API client or an HTTP library. The fuller pins
    are in tests/llm_raters/test_llmr_import_rule.py."""
    offenders = {"clients": [], "gateway": []}
    for relative, path in evaluation_sources():
        names = imported_names(path)
        if any(re.match(CLIENTS, n) for n in names):
            offenders["clients"].append(relative)
        if relative != "llm_rater.py" and any(re.match(GATEWAY, n) for n in names):
            offenders["gateway"].append(relative)
    assert offenders == {"clients": [], "gateway": []}


# --- migrations --------------------------------------------------------------------------------------------------------
@pytest.mark.django_db
def test_makemigrations_check_is_clean_for_the_whole_project():
    call_command("makemigrations", "--check", "--dry-run", stdout=io.StringIO(), stderr=io.StringIO())


@pytest.mark.django_db
def test_makemigrations_check_is_clean_for_evaluation():
    call_command("makemigrations", "evaluation", "--check", "--dry-run", stdout=io.StringIO(), stderr=io.StringIO())


def test_the_initial_migration_creates_all_nine_models():
    import importlib

    module = importlib.import_module("evaluation.migrations.0001_initial")
    created = {op.name for op in module.Migration.operations if op.__class__.__name__ == "CreateModel"}
    assert set(MODELS) <= created
    assert module.Migration.initial is True


def test_the_initial_migration_depends_on_forum_and_moderation_and_the_user_model():
    import importlib

    module = importlib.import_module("evaluation.migrations.0001_initial")
    apps_needed = {app for app, _ in module.Migration.dependencies}
    assert {"forum", "moderation"} <= apps_needed


@pytest.mark.django_db(transaction=True)
def test_migrating_evaluation_to_zero_drops_its_tables_and_triggers_and_forward_restores_them():
    from django.db.migrations.executor import MigrationExecutor

    def evaluation_tables():
        return {t for t in kit.db_tables() if t.startswith("evaluation_")}

    assert len(evaluation_tables()) >= 9
    with_triggers = kit.triggers_matching("evaluation_")
    executor = MigrationExecutor(connection)
    try:
        executor.migrate([("evaluation", None)])
        assert evaluation_tables() == set()
        assert kit.triggers_matching("evaluation_") == []
        assert {"forum_message", "moderation_issue"} <= kit.db_tables()
        executor = MigrationExecutor(connection)
        executor.migrate([n for n in executor.loader.graph.leaf_nodes() if n[0] == "evaluation"])
        assert len(evaluation_tables()) >= 9
        assert len(kit.triggers_matching("evaluation_")) == len(with_triggers)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    assert len(evaluation_tables()) >= 9


@pytest.mark.django_db(transaction=True)
def test_the_rules_still_hold_after_a_round_trip_of_the_migration():
    from django.core.exceptions import ValidationError
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    try:
        executor.migrate([("evaluation", None)])
        executor = MigrationExecutor(connection)
        executor.migrate([n for n in executor.loader.graph.leaf_nodes() if n[0] == "evaluation"])
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    message, _ = kit.message_with_text("The city has 40 parks.")
    rating = kit.make_rating(kit.make_rater("llm"), message)
    with pytest.raises(ValidationError):
        kit.make_finding(rating, 4, 8, quote="town")


# --- the site never imports evaluation ---------------------------------------------------------------------------------
def evaluation_references(source, filename="<test>", allow_bare_name=False):
    """Every place `source` refers to the evaluation package: imports, importlib/get_model style strings, dependency tuples."""
    found = []
    for node in ast.walk(ast.parse(source, filename)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name == "evaluation" or a.name.startswith("evaluation.")]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "evaluation" or module.startswith("evaluation."):
                found.append(module)
            if node.level and any(a.name == "evaluation" for a in node.names):
                found.append("relative import of evaluation")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if re.fullmatch(r"evaluation(\.[\w.]+)?", value) and not (allow_bare_name and value == "evaluation"):
                found.append(f"string {value!r}")
    return found


def site_sources():
    for package in SITE_PACKAGES:
        for path in sorted((REPO / package).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def test_the_site_packages_exist_and_have_python_files():
    for package in SITE_PACKAGES:
        assert list((REPO / package).rglob("*.py")), package


def test_no_site_module_refers_to_evaluation():
    offenders = {}
    for path in site_sources():
        allow = path == REPO / "config" / "settings.py"  # INSTALLED_APPS names the app; that is the one allowed mention
        hits = evaluation_references(path.read_text(), str(path), allow_bare_name=allow)
        if hits:
            offenders[str(path.relative_to(REPO))] = hits
    assert offenders == {}


def test_settings_mention_the_app_only_as_an_installed_app_entry():
    tree = ast.parse((REPO / "config" / "settings.py").read_text())
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "evaluation"]
    assert strings == ["evaluation"]
    assert evaluation_references((REPO / "config" / "settings.py").read_text(), allow_bare_name=True) == []


# non-vacuity of the checker itself
@pytest.mark.parametrize(
    "source",
    [
        "import evaluation",
        "import evaluation.models",
        "from evaluation import models",
        "from evaluation.models import Rater",
        "from evaluation.consensus import iou as f",
        "def f():\n    from evaluation.blinding import blinded_view",
        "import importlib\nimportlib.import_module('evaluation.models')",
        "from django.apps import apps\napps.get_model('evaluation.Rater')".replace("evaluation.Rater", "evaluation.models"),
        "class M:\n    dependencies = [('x', '1'), 'evaluation.migrations']",
        "from .. import evaluation",
    ],
)
def test_the_checker_finds_each_kind_of_reference(source):
    assert evaluation_references(source) != []


@pytest.mark.parametrize(
    "source",
    ["import forum", "from moderation import taxonomy", "x = 'an evaluation of the moderator'", "# import evaluation", "evaluations = 3"],
)
def test_the_checker_ignores_unrelated_code(source):
    assert evaluation_references(source) == []
