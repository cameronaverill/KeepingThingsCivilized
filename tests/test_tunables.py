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
# config/settings_live.py exists solely to flip LLM_ENABLED on for a single `dev.sh --live` run;
# config/tunables.py itself is untouched, so it's an allowed, single-purpose exception to the rule below.
LIVE_SETTINGS_FILE = REPO_ROOT / "config" / "settings_live.py"
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
        if path in (TUNABLES_FILE, LIVE_SETTINGS_FILE) or SKIP_DIRS & set(rel.parts):
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
    assert tunables.BUDGET_SITE_USD_PER_DAY == Decimal("1.50")  # raised from 1.00 by the user for step 2
    assert tunables.BUDGET_PER_CONVERSATION_USD == Decimal("1.25")  # raised from 0.50 by the user for step 2
    assert tunables.BUDGET_EVAL_USD_TOTAL == Decimal("25.00")


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
    assert not hasattr(tunables, "EMAIL_CONFIRM_MAX_AGE_DAYS"), "removed in step 6c (no email confirmation)"
    assert tunables.SESSION_COOKIE_AGE == int(timedelta(days=30).total_seconds())
    assert tunables.BREAKER_MAX_CONSECUTIVE_ERRORS == 5
    assert tunables.LLM_MAX_RETRIES == 1
    assert tunables.MIN_SECONDS_BETWEEN_MESSAGES == 30  # raised from 5 by the user


def test_llm_calls_are_off_until_deliberately_enabled():
    # Fail-safe default: no moderation LLM traffic until the user flips this on.
    assert tunables.LLM_ENABLED is False


def test_models_are_named():
    assert tunables.MASTER_MODEL and tunables.INTERVENOR_MODEL and tunables.SPIKE_MODEL
    assert tunables.JUDGE_MODEL_SEEDED
    for gone in ("JUDGE_MODELS", "SPAN_MATCH_MIN_IOU", "INTENSITY_DISAGREEMENT_THRESHOLD"):
        assert not hasattr(tunables, gone), f"{gone} was removed with the old rating stack"


def test_tunable_names_do_not_collide_with_django_settings_except_the_allowed_ones():
    # config/settings.py copies every UPPERCASE tunable onto the Django settings, so a tunable named like a real
    # Django setting would silently override it. SESSION_COOKIE_AGE is the one deliberate override.
    from django.conf import global_settings

    allowed = {"SESSION_COOKIE_AGE"}
    django_names = {name for name in dir(global_settings) if name.isupper()}
    collisions = set(tunable_names()) & django_names
    assert collisions - allowed == set(), sorted(collisions - allowed)
    assert allowed <= set(tunable_names()), "the allowed override is no longer a tunable; drop it from the allowed set"
