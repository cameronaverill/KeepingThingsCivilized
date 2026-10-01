"""moderation/llm.py client access, the guard that keeps real clients out of tests, and the "only llm.py imports
anthropic" rule."""
import ast
import os
from pathlib import Path

import pytest
from moderation_testkit import FAKE_KEY, ORIGINALS, reply, run_call

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKIP_DIRS = {"tests", ".claude", ".venv", "venv", "migrations", "__pycache__", ".git", "node_modules", "site-packages"}
ALLOWED = "moderation/llm.py"


# --- the guard: a real client can never be built in tests -----------------------------------------------------------


def test_the_autouse_fixture_really_blocks_the_real_client_builder():
    from moderation import llm

    with pytest.raises(AssertionError, match="real client built in tests"):
        llm._build_real_client()


def test_get_client_without_an_installed_client_hits_the_guard():
    from moderation import llm

    llm.reset_client()
    with pytest.raises(AssertionError, match="real client built in tests"):
        llm.get_client()


def test_calling_the_gateway_without_a_fake_client_cannot_reach_the_network(llm_ready):
    """No client installed: the block trips, the AssertionError comes out unchanged (never turned into an API error),
    and the row is settled as a client_error that does not feed the breaker."""
    from moderation import llm
    from moderation.errors import LLMAPIError
    from moderation.models import GuardState, LLMCall

    llm.reset_client()
    with pytest.raises(AssertionError) as excinfo:
        run_call()
    assert not isinstance(excinfo.value, LLMAPIError)
    assert [r.status for r in LLMCall.objects.all()] == ["error"]
    assert LLMCall.objects.get().error_code == "client_error"
    assert GuardState.load().consecutive_errors == 0


def test_a_fake_client_can_be_installed_and_removed(install_fake):
    from moderation import llm

    client = install_fake(reply())
    assert llm.get_client() is client
    assert llm.get_client() is client
    llm.reset_client()
    with pytest.raises(AssertionError):
        llm.get_client()


def test_set_client_replaces_the_previous_client(install_fake):
    from moderation import llm

    install_fake(reply())
    second = install_fake(reply())
    assert llm.get_client() is second


# --- the real builder: how it WOULD be called (anthropic.Anthropic itself is stubbed; nothing is built) -----------


def test_the_real_client_builder_passes_the_key_and_max_retries(monkeypatch, settings):
    import anthropic

    from moderation import llm

    built = []

    class StubClient:
        def __init__(self, *args, **kwargs):
            built.append((args, kwargs))

    monkeypatch.setattr(anthropic, "Anthropic", StubClient)
    if hasattr(llm, "Anthropic"):  # in case llm.py did `from anthropic import Anthropic`
        monkeypatch.setattr(llm, "Anthropic", StubClient)
    settings.ANTHROPIC_API_KEY = FAKE_KEY
    settings.LLM_MAX_RETRIES = 3
    settings.LLM_REQUEST_TIMEOUT_SECONDS = 77

    genuine = ORIGINALS["_build_real_client"]
    client = genuine()
    assert isinstance(client, StubClient)
    assert len(built) == 1
    args, kwargs = built[0]
    assert kwargs["api_key"] == FAKE_KEY
    assert kwargs["max_retries"] == 3
    assert kwargs["timeout"] == 77


def test_the_real_client_builder_reads_the_tunable_at_call_time(monkeypatch, settings):
    import anthropic

    from moderation import llm

    built = []

    class StubClient:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", StubClient)
    if hasattr(llm, "Anthropic"):
        monkeypatch.setattr(llm, "Anthropic", StubClient)
    settings.ANTHROPIC_API_KEY = FAKE_KEY
    settings.LLM_MAX_RETRIES = 0
    settings.LLM_REQUEST_TIMEOUT_SECONDS = 5
    ORIGINALS["_build_real_client"]()
    assert built[0]["max_retries"] == 0
    assert built[0]["timeout"] == 5


# --- only moderation/llm.py may import anthropic ----------------------------------------------------------------------


def imports_of_anthropic(source, filename="<src>"):
    """Line numbers where `source` imports anthropic (import, from-import, __import__, importlib.import_module)."""
    hits = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "anthropic" for alias in node.names):
                hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and (node.module or "").split(".")[0] == "anthropic":
                hits.append(node.lineno)
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in ("__import__", "import_module") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and str(first.value).split(".")[0] == "anthropic":
                    hits.append(node.lineno)
    return hits


def scan_repo(root):
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]  # prune, so .venv is never walked
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = Path(dirpath) / filename
            hits = imports_of_anthropic(path.read_text(encoding="utf-8"), filename=str(path))
            if hits:
                found[path.relative_to(root).as_posix()] = hits
    return found


def test_the_scanner_itself_catches_every_import_form(tmp_path):
    (tmp_path / "a.py").write_text("import anthropic\n")
    (tmp_path / "b.py").write_text("from anthropic import Anthropic\n")
    (tmp_path / "c.py").write_text("import anthropic.types as t\n")
    (tmp_path / "d.py").write_text("def f():\n    import anthropic\n")
    (tmp_path / "e.py").write_text("import importlib\nimportlib.import_module('anthropic')\n")
    (tmp_path / "f.py").write_text("m = __import__('anthropic')\n")
    (tmp_path / "g.py").write_text("from anthropic.types import Message\n")
    (tmp_path / "clean.py").write_text("import json\nfrom django.db import models\nx = 'anthropic'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("import anthropic\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "v.py").write_text("import anthropic\n")
    (tmp_path / "app" / "migrations").mkdir(parents=True)
    (tmp_path / "app" / "migrations" / "0001.py").write_text("import anthropic\n")
    assert sorted(scan_repo(tmp_path)) == ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"]


def test_only_moderation_llm_py_imports_anthropic():
    found = scan_repo(REPO_ROOT)
    assert set(found) <= {ALLOWED}, f"anthropic must only be imported in {ALLOWED}: {sorted(found)}"


def test_moderation_llm_py_is_where_anthropic_is_imported():
    """llm.py is the single import site, so the SDK exceptions can be normalized there."""
    assert (REPO_ROOT / ALLOWED).is_file()
    assert scan_repo(REPO_ROOT).get(ALLOWED), "moderation/llm.py should import anthropic"


def test_fake_llm_does_not_import_anthropic():
    source = (REPO_ROOT / "moderation" / "fake_llm.py").read_text(encoding="utf-8")
    assert imports_of_anthropic(source) == []


def test_importing_the_gateway_and_the_fake_needs_no_network_or_key():
    """Importing must be side-effect free: no client built, nothing written to the ledger."""
    import importlib

    from moderation.models import LLMCall

    importlib.import_module("moderation.llm")
    importlib.import_module("moderation.fake_llm")
    assert LLMCall.objects.count() == 0


def test_no_anthropic_client_is_ever_constructed_in_the_suite(install_fake, llm_ready, monkeypatch):
    """Belt and braces: even if the guard were bypassed, constructing anthropic.Anthropic itself fails the test."""
    import anthropic

    def forbidden(*args, **kwargs):
        raise AssertionError("anthropic.Anthropic constructed in a test")

    monkeypatch.setattr(anthropic, "Anthropic", forbidden)
    install_fake(reply())
    result = run_call()
    assert result.call_id
