"""The module-import rule of step 14 (docs/step14_brief.md): exactly one module of `evaluation`, `llm_rater.py`, may import
`moderation.llm`, `moderation.errors` and `moderation.quotes`; nothing else in `evaluation` may; no site app imports
`evaluation`. The one-line version lives in tests/evaluation_models/test_evalmodels_app_shape.py; these tests pin it harder, prove
the checker can see each kind of import, and pin the files the step adds."""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVALUATION = ROOT / "evaluation"
SITE_PACKAGES = ["accounts", "forum", "moderation", "config"]
GATEWAY = ("moderation.llm", "moderation.errors", "moderation.quotes")
FORBIDDEN_EVERYWHERE = ("anthropic", "httpx", "requests", "urllib", "urllib3")
ORDINARY_HELPERS = ("moderation.budget", "moderation.clock", "moderation.pricing", "moderation.taxonomy")


def imported_names(source):
    """Every dotted name a module imports, including `from a import b` as a.b, and dynamic imports by string."""
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = ("." * node.level) + (node.module or "")
            names += [base] + [f"{base}.{a.name}" if base else a.name for a in node.names]
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("import_module", "__import__"):
            names += [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
    return names


def touches(names, targets):
    return sorted({n for n in names for t in targets if n == t or n.startswith(t + ".")})


def evaluation_files():
    return {
        path.relative_to(EVALUATION).as_posix(): path
        for path in sorted(EVALUATION.rglob("*.py"))
        if "migrations" not in path.parts and "__pycache__" not in path.parts
    }


class TestTheCheckerSeesEveryKindOfImport:
    @pytest.mark.parametrize(
        "source",
        [
            "import moderation.llm",
            "import moderation.llm as gateway",
            "from moderation import llm",
            "from moderation import budget, llm as gateway",
            "from moderation.llm import call",
            "from moderation.errors import LLMRefused",
            "from moderation.quotes import locate_quote",
            "def f():\n    from moderation import errors",
            "import importlib\nimportlib.import_module('moderation.llm')",
            "__import__('moderation.quotes')",
        ],
    )
    def test_a_gateway_import_is_found(self, source):
        assert touches(imported_names(source), GATEWAY) != []

    @pytest.mark.parametrize(
        "source",
        ["import anthropic", "from anthropic import Anthropic", "import httpx", "import requests.adapters", "import urllib.request", "from urllib3 import PoolManager"],
    )
    def test_a_forbidden_import_is_found(self, source):
        assert touches(imported_names(source), FORBIDDEN_EVERYWHERE) != []

    @pytest.mark.parametrize(
        "source",
        ["from moderation import taxonomy", "from moderation.models import LLMCall", "import json", "x = 'moderation.llm'", "# import moderation.llm", "from moderation import taxonomy, clock"],
    )
    def test_unrelated_code_is_ignored(self, source):
        assert touches(imported_names(source), GATEWAY + FORBIDDEN_EVERYWHERE) == []


class TestTheRule:
    def test_the_files_the_step_adds_exist(self):
        assert [name for name in (
            "schemas.py", "llm_rater.py", "prompts/rater_v1.md", "management/__init__.py", "management/commands/__init__.py",
            "management/commands/run_raters.py", "management/commands/seed_panel.py",
        ) if not (EVALUATION / name).is_file()] == []

    def test_llm_rater_is_the_one_module_that_imports_the_gateway(self):
        importers = [name for name, path in evaluation_files().items() if touches(imported_names(path.read_text()), GATEWAY)]
        assert importers == ["llm_rater.py"]

    def test_llm_rater_really_uses_the_gateway_the_errors_and_the_quote_locator(self):
        names = imported_names((EVALUATION / "llm_rater.py").read_text())
        assert {"moderation.errors", "moderation.llm", "moderation.quotes"} <= set(touches(names, GATEWAY))

    def test_the_ordinary_moderation_helpers_are_not_restricted_and_llm_rater_uses_some(self):
        names = imported_names((EVALUATION / "llm_rater.py").read_text())
        assert {"moderation.budget", "moderation.pricing"} <= set(touches(names, ORDINARY_HELPERS))

    def test_no_evaluation_module_imports_an_api_client_or_an_http_library(self):
        offenders = {
            name: touches(imported_names(path.read_text()), FORBIDDEN_EVERYWHERE) for name, path in evaluation_files().items()
        }
        assert {name: hit for name, hit in offenders.items() if hit} == {}

    def test_the_other_modules_that_talk_about_ratings_do_not_import_the_gateway(self):
        for name in ("consensus.py", "blinding.py", "calibration.py", "matching.py", "models.py", "targets.py", "schemas.py"):
            assert touches(imported_names((EVALUATION / name).read_text()), GATEWAY) == [], name

    def test_the_commands_do_not_import_the_gateway_either(self):
        for name in ("run_raters.py", "seed_panel.py"):
            names = imported_names((EVALUATION / "management" / "commands" / name).read_text())
            assert touches(names, GATEWAY) == [], name

    def test_the_rater_schema_module_imports_no_client_and_no_gateway(self):
        assert touches(imported_names((EVALUATION / "schemas.py").read_text()), GATEWAY + FORBIDDEN_EVERYWHERE) == []


class TestNoSiteAppImportsEvaluation:
    def test_no_site_module_imports_evaluation_or_any_module_of_it(self):
        offenders = {}
        for package in SITE_PACKAGES:
            for path in sorted((ROOT / package).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                hits = [n for n in imported_names(path.read_text()) if re.match(r"(\.+)?evaluation\b", n) and not re.match(r"\.+evaluation_", n)]
                if hits:
                    offenders[str(path.relative_to(ROOT))] = hits
        assert offenders == {}

    def test_no_site_module_names_the_evaluation_commands(self):
        text = " ".join(path.read_text() for package in SITE_PACKAGES for path in (ROOT / package).rglob("*.py") if "__pycache__" not in path.parts)
        assert ("run_raters" in text, "seed_panel" in text, "llm_rater" in text) == (False, False, False)
