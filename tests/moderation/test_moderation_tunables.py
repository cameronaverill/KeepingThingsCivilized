"""Step 2's changes to config/tunables.py: the new caps, the new tunables, and the invariants between them.
(The old caps assertions in tests/test_tunables.py are updated separately at integration.)"""
import ast
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings

from config import tunables

TUNABLES_FILE = Path(tunables.__file__)
NEW_TUNABLES = {
    "TOKEN_ESTIMATE_CHARS_PER_TOKEN": 2.5,
    "TOKEN_ESTIMATE_OVERHEAD_TOKENS": 1000,
    "SYSTEM_PROMPT_TOKENS_ESTIMATE": 6000,
    "PENDING_CALL_STALE_MINUTES": 15,
    "LLM_REQUEST_TIMEOUT_SECONDS": 120,
}


def test_the_four_caps_are_the_users_numbers():
    assert tunables.BUDGET_PER_CONVERSATION_USD == Decimal("1.25")
    assert tunables.BUDGET_SITE_USD_PER_DAY == Decimal("1.50")
    assert tunables.BUDGET_SITE_USD_TOTAL == Decimal("5.00")
    assert tunables.BUDGET_EVAL_USD_TOTAL == Decimal("10.00")


def test_the_caps_are_decimals_not_floats():
    for name in ("BUDGET_PER_CONVERSATION_USD", "BUDGET_SITE_USD_PER_DAY", "BUDGET_SITE_USD_TOTAL", "BUDGET_EVAL_USD_TOTAL"):
        assert isinstance(getattr(tunables, name), Decimal), name


def test_cap_invariants():
    assert 0 < tunables.BUDGET_PER_CONVERSATION_USD <= tunables.BUDGET_SITE_USD_PER_DAY <= tunables.BUDGET_SITE_USD_TOTAL
    assert tunables.BUDGET_EVAL_USD_TOTAL > 0


def test_the_kill_switch_is_still_off_by_default():
    assert tunables.LLM_ENABLED is False


@pytest.mark.parametrize("name, value", sorted(NEW_TUNABLES.items()))
def test_new_tunables_exist_with_the_agreed_values(name, value):
    assert getattr(tunables, name) == value
    assert type(getattr(tunables, name)) is type(value)


@pytest.mark.parametrize("name", sorted(NEW_TUNABLES))
def test_new_tunables_are_exposed_as_django_settings(name):
    assert getattr(settings, name) == getattr(tunables, name)


@pytest.mark.parametrize("name", sorted(NEW_TUNABLES))
def test_new_tunables_have_a_comment_directly_above(name):
    lines = TUNABLES_FILE.read_text().splitlines()
    tree = ast.parse(TUNABLES_FILE.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            assert lines[node.lineno - 2].lstrip().startswith("#"), f"{name} needs a comment on the line above"
            return
    pytest.fail(f"{name} is not assigned in tunables.py")


def test_the_estimator_tunables_over_count_rather_than_under_count():
    assert tunables.TOKEN_ESTIMATE_CHARS_PER_TOKEN > 0
    assert tunables.TOKEN_ESTIMATE_CHARS_PER_TOKEN <= 3.5  # English text is about 4 chars per token; smaller is safer
    assert tunables.TOKEN_ESTIMATE_OVERHEAD_TOKENS >= 0
    assert tunables.SYSTEM_PROMPT_TOKENS_ESTIMATE > 0
    assert tunables.PENDING_CALL_STALE_MINUTES > 0


def test_the_moderation_app_is_installed():
    assert "moderation" in settings.INSTALLED_APPS


def test_every_model_named_in_the_tunables_is_on_the_allow_list():
    """A tunable that names a model the guard would refuse is a misconfiguration waiting to happen."""
    from moderation.pricing import ALLOWED_MODELS

    named = {tunables.MASTER_MODEL, tunables.INTERVENOR_MODEL, tunables.SPIKE_MODEL, *tunables.JUDGE_MODELS}
    assert named <= set(ALLOWED_MODELS)


def test_token_limits_and_retries_are_sane_for_the_gateway():
    assert isinstance(tunables.MASTER_MAX_TOKENS, int) and tunables.MASTER_MAX_TOKENS > 0
    assert isinstance(tunables.INTERVENOR_MAX_TOKENS, int) and tunables.INTERVENOR_MAX_TOKENS > 0
    assert tunables.LLM_MAX_RETRIES >= 0
    assert tunables.BREAKER_MAX_CONSECUTIVE_ERRORS >= 1
    assert tunables.BREAKER_ERROR_WINDOW_SECONDS > 0
