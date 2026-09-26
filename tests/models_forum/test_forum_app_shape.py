"""The forum app as a whole: migrations are in step with the models and the app still carries its five models.

Step 4a added models only, so this file used to assert that no view, URL, admin, form, service or template existed.
Step 7 adds exactly those, so those assertions are gone; what is still true stays: the models, the migrations being
consistent, and no forum code importing `anthropic` (only moderation/llm.py may)."""
import importlib
import io
from pathlib import Path

import pytest
from django.apps import apps
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def forum_path():
    return Path(apps.get_app_config("forum").path)


def test_the_app_has_the_contract_files():
    root = forum_path()
    for name in ("__init__.py", "apps.py", "models.py", "limits.py", "migrations/__init__.py", "migrations/0001_initial.py"):
        assert (root / name).is_file(), name


def test_makemigrations_check_is_clean_for_forum():
    call_command("makemigrations", "forum", "--check", "--dry-run", stdout=io.StringIO(), stderr=io.StringIO())


def test_the_initial_migration_creates_all_five_models():
    module = importlib.import_module("forum.migrations.0001_initial")
    created = {op.name for op in module.Migration.operations if op.__class__.__name__ == "CreateModel"}
    assert {"Topic", "Experiment", "Conversation", "Participant", "Message"} <= created
    assert module.Migration.initial is True


def test_the_migration_state_matches_the_models():
    from django.db import connection
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.state import ProjectState

    loader = MigrationLoader(connection)
    changes = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps)).changes(
        graph=loader.graph, trim_to_apps={"forum"}
    )
    assert changes == {}


def test_the_five_models_are_registered_under_the_forum_label():
    labels = {m._meta.label for m in apps.get_app_config("forum").get_models()}
    assert labels == {"forum.Topic", "forum.Experiment", "forum.Conversation", "forum.Participant", "forum.Message"}


def test_step_7a_added_its_files_and_the_second_migration():
    root = forum_path()
    for name in ("services.py", "viewmodels.py", "admin.py"):
        assert (root / name).is_file(), name
    assert len(list((root / "migrations").glob("0002_*.py"))) == 1


def test_the_forum_modules_that_step_7_added_can_be_imported():
    for module in ("forum.services", "forum.viewmodels", "forum.admin", "forum.urls"):
        assert importlib.import_module(module)


def test_the_forum_urls_are_routed_under_the_forum_namespace_only():
    from django.urls import reverse

    assert reverse("forum:home") == "/"


def test_no_forum_code_imports_anthropic():
    import ast

    offenders = []
    for path in forum_path().rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "anthropic" or name.startswith("anthropic.") for name in names):
                offenders.append(str(path))
    assert offenders == []


def test_the_forum_admin_registers_topic():
    """Step 7a registers Topic (hide and unhide propositions). Step 9 owns the rest of the admin."""
    from django.contrib import admin

    from forum.models import Topic

    assert Topic in admin.site._registry
