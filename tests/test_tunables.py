"""config/tunables.py is the single place for every number the user might change (plan sections 2 and 3)."""
import ast
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings

from config import tunables

REPO_ROOT = Path(__file__).resolve().parent.parent
TUNABLES_FILE = REPO_ROOT / "config" / "tunables.py"
# Directories whose Python files are not application source.
SKIP_DIRS = {".venv", "tests", "migrations", "__pycache__", ".git", "node_modules"}


def tunable_names():
    return sorted(n for n in vars(tunables) if n.isupper())


def test_there_are_tunables():
    assert len(tunable_names()) > 20


@pytest.mark.parametrize("name", tunable_names())
def test_every_tunable_is_exposed_on_django_settings(name):
    assert getattr(settings, name) == getattr(tunables, name)


def test_tunables_are_assigned_only_in_tunables_py():
    names = set(tunable_names())
    offenders = []
    for path in REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT)
        if path == TUNABLES_FILE or SKIP_DIRS & set(rel.parts):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name) and sub.id in names:
                        offenders.append(f"{rel}:{node.lineno} assigns {sub.id}")
    assert offenders == [], "\n".join(offenders)


def test_every_tunable_has_a_comment():
    lines = TUNABLES_FILE.read_text().splitlines()
    tree = ast.parse(TUNABLES_FILE.read_text())
    missing = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        for name in names:
            if not name.isupper():
                continue
            above = lines[node.lineno - 2].strip() if node.lineno >= 2 else ""
            same_line = lines[node.lineno - 1]
            if not above.startswith("#") and "#" not in same_line:
                missing.append(name)
    assert missing == [], f"tunables without a comment: {missing}"


def test_agreed_budget_defaults():
    assert tunables.BUDGET_SITE_USD_TOTAL == Decimal("5.00")
    assert tunables.BUDGET_SITE_USD_PER_DAY == Decimal("1.00")
    assert tunables.BUDGET_PER_CONVERSATION_USD == Decimal("0.50")
    assert tunables.BUDGET_EVAL_USD_TOTAL == Decimal("10.00")


def test_budget_caps_are_consistent():
    assert tunables.BUDGET_SITE_USD_PER_DAY <= tunables.BUDGET_SITE_USD_TOTAL
    assert tunables.BUDGET_PER_CONVERSATION_USD <= tunables.BUDGET_SITE_USD_TOTAL
    assert all(
        isinstance(getattr(tunables, n), Decimal)
        for n in tunable_names()
        if n.startswith("BUDGET_")
    ), "money must be Decimal, never float"


def test_agreed_limits():
    assert tunables.MAX_MESSAGE_CHARS == 3000
    assert tunables.MAX_ACTS_PER_INTERVENTION == 3
    assert tunables.MAX_PARTICIPANTS == 2
    assert tunables.PASSWORD_MIN_LENGTH == 12
    assert tunables.EMAIL_CONFIRM_MAX_AGE_DAYS == 3
    assert tunables.SESSION_COOKIE_AGE == int(timedelta(days=30).total_seconds())
    assert tunables.SPAN_MATCH_MIN_IOU == 0.5
    assert tunables.BREAKER_MAX_CONSECUTIVE_ERRORS == 5
    assert tunables.LLM_MAX_RETRIES == 1


def test_llm_calls_are_off_until_deliberately_enabled():
    # Fail-safe default: no moderation LLM traffic until the user flips this on.
    assert tunables.LLM_ENABLED is False


def test_models_are_named():
    assert tunables.MASTER_MODEL and tunables.INTERVENOR_MODEL and tunables.SPIKE_MODEL
    assert len(tunables.JUDGE_MODELS) == 2


def test_tunable_names_do_not_collide_with_django_settings_except_the_allowed_ones():
    # config/settings.py copies every UPPERCASE tunable onto the Django settings, so a tunable named like a real
    # Django setting would silently override it. SESSION_COOKIE_AGE is the one deliberate override.
    from django.conf import global_settings

    allowed = {"SESSION_COOKIE_AGE"}
    django_names = {name for name in dir(global_settings) if name.isupper()}
    collisions = set(tunable_names()) & django_names
    assert collisions - allowed == set(), sorted(collisions - allowed)
    assert allowed <= set(tunable_names()), "the allowed override is no longer a tunable; drop it from the allowed set"
