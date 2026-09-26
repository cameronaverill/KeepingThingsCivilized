"""The forum app as a whole: migrations are in step with the models, and no view, URL or admin was added (4a is
models only)."""
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


@pytest.mark.parametrize("name", ["views.py", "urls.py", "admin.py", "forms.py", "services.py", "templates", "templatetags"])
def test_no_view_url_admin_or_template_was_added(name):
    assert not (forum_path() / name).exists(), name


@pytest.mark.parametrize("module", ["forum.views", "forum.urls", "forum.admin"])
def test_those_modules_cannot_be_imported(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_no_url_is_routed_to_the_forum_app():
    from django.urls import URLPattern, URLResolver, get_resolver

    def walk(patterns):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                yield pattern
                yield from walk(pattern.url_patterns)
            elif isinstance(pattern, URLPattern):
                yield pattern

    for pattern in walk(get_resolver().url_patterns):
        module = getattr(pattern, "urlconf_module", None)
        name = getattr(module, "__name__", "")
        callback = getattr(pattern, "callback", None)
        callback_module = getattr(callback, "__module__", "") or ""
        assert not name.startswith("forum"), pattern
        assert not callback_module.startswith("forum"), pattern
        assert getattr(pattern, "app_name", None) != "forum"


def test_no_forum_model_is_registered_in_the_admin():
    from django.contrib import admin

    forum_models = set(apps.get_app_config("forum").get_models())
    assert forum_models.isdisjoint(admin.site._registry)
