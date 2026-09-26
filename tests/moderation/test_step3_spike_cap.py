"""The hard spike cap: BUDGET_SPIKE_USD_TOTAL (default $1.50) and the `spike_total` cap in budget.check_caps.

Written before the change lands (brief section 3, "config/tunables.py and moderation/budget.py"); these fail until it does.
"""
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from moderation_testkit import HAIKU, call_kwargs, run_call, seed_call
from step3_testkit import (
    Scripted,
    find_pair,
    golden,  # noqa: F401
    real_results_untouched,  # noqa: F401
    run_spike,
    spike_env,  # noqa: F401
)

D = Decimal
TUNABLES = Path(__file__).resolve().parents[2] / "config" / "tunables.py"


@pytest.fixture
def roomy(tune, settings):
    """Site caps far above anything here, so only the spike cap can bite; the spike cap itself is left at its default
    unless a test sets it."""
    tune(
        BUDGET_SITE_USD_TOTAL=D("100"),
        BUDGET_SITE_USD_PER_DAY=D("100"),
        BUDGET_PER_CONVERSATION_USD=D("100"),
        BUDGET_EVAL_USD_TOTAL=D("100"),
    )
    return settings


def set_spike_cap(settings, monkeypatch, value):
    from config import tunables

    settings.BUDGET_SPIKE_USD_TOTAL = value
    monkeypatch.setattr(tunables, "BUDGET_SPIKE_USD_TOTAL", value, raising=False)


def caps(**kwargs):
    from moderation import budget

    return budget.check_caps(**kwargs)


# --- the tunable ----------------------------------------------------------------------------------------------------

def test_the_spike_cap_tunable_is_fifty_cents(settings):
    from config import tunables

    assert isinstance(tunables.BUDGET_SPIKE_USD_TOTAL, Decimal)
    assert tunables.BUDGET_SPIKE_USD_TOTAL == D("1.50")  # raised from 0.50 by the user
    assert settings.BUDGET_SPIKE_USD_TOTAL == D("1.50")  # raised from 0.50 by the user


def test_the_tunable_has_a_comment_directly_above_it():
    lines = TUNABLES.read_text(encoding="utf-8").splitlines()
    index = next((i for i, line in enumerate(lines) if re.match(r"BUDGET_SPIKE_USD_TOTAL\s*=", line)), None)
    assert index is not None, "BUDGET_SPIKE_USD_TOTAL is not defined in config/tunables.py"
    assert 'Decimal("1.50")' in lines[index], "the default is written as Decimal(\"1.50\")"
    above = lines[index - 1].strip()
    assert above.startswith("#") and len(above) > 20, f"needs a comment line right above it, found {above!r}"
    assert "spike" in "\n".join(lines[max(0, index - 3):index]).lower()


# --- budget.check_caps ---------------------------------------------------------------------------------------------

def test_spike_spend_plus_the_amount_over_the_cap_raises_spike_total(roomy, settings, monkeypatch):
    from moderation.budget import SessionBudget
    from moderation.errors import BudgetExceeded

    set_spike_cap(settings, monkeypatch, D("0.50"))
    seed_call(purpose="spike", cost="0.45")
    with pytest.raises(BudgetExceeded) as caught:
        caps(purpose="spike", conversation_id=None, amount=D("0.06"), session=SessionBudget("1000"))
    assert caught.value.cap_name == "spike_total"
    assert caught.value.limit == D("0.50")
    assert caught.value.spent == D("0.45")
    assert caught.value.requested == D("0.06")
    assert caught.value.status == "refused_budget"


def test_the_cap_holds_even_with_no_session_and_with_a_much_larger_session_limit(roomy, settings, monkeypatch):
    from moderation.budget import SessionBudget
    from moderation.errors import BudgetExceeded

    set_spike_cap(settings, monkeypatch, D("0.50"))
    seed_call(purpose="spike", cost="0.45")
    for session in (None, SessionBudget("999999")):
        with pytest.raises(BudgetExceeded) as caught:
            caps(purpose="spike", conversation_id=None, amount=D("0.06"), session=session)
        assert caught.value.cap_name == "spike_total"


def test_reaching_the_cap_exactly_is_allowed(roomy, settings, monkeypatch):
    set_spike_cap(settings, monkeypatch, D("0.50"))
    seed_call(purpose="spike", cost="0.45")
    caps(purpose="spike", conversation_id=None, amount=D("0.05"))  # 0.45 + 0.05 == 0.50: no error


def test_a_single_call_bigger_than_the_whole_cap_is_refused(roomy, settings, monkeypatch):
    from moderation.errors import BudgetExceeded

    set_spike_cap(settings, monkeypatch, D("0.50"))
    with pytest.raises(BudgetExceeded) as caught:
        caps(purpose="spike", conversation_id=None, amount=D("0.51"))
    assert caught.value.cap_name == "spike_total"


def test_the_default_cap_is_what_applies_when_nothing_is_tuned(roomy):
    from moderation.errors import BudgetExceeded

    seed_call(purpose="spike", cost="1.499999")
    with pytest.raises(BudgetExceeded) as caught:
        caps(purpose="spike", conversation_id=None, amount=D("0.01"))
    assert caught.value.cap_name == "spike_total"


@pytest.mark.parametrize("purpose", ["moderation", "golden", "replay", "judge"])
def test_other_purposes_are_not_limited_by_the_spike_cap(roomy, settings, monkeypatch, purpose):
    set_spike_cap(settings, monkeypatch, D("0.50"))
    seed_call(purpose="spike", cost="0.49")
    caps(purpose=purpose, conversation_id=None, amount=D("0.10"))  # would break a 0.50 cap if it applied


@pytest.mark.parametrize("other", ["moderation", "golden", "replay", "judge"])
def test_spend_on_other_purposes_does_not_count_toward_the_spike_cap(roomy, settings, monkeypatch, other):
    set_spike_cap(settings, monkeypatch, D("0.50"))
    seed_call(purpose=other, cost="0.49")
    caps(purpose="spike", conversation_id=None, amount=D("0.40"))


def test_pending_spike_reservations_count_and_refused_rows_do_not(roomy, settings, monkeypatch):
    from moderation.errors import BudgetExceeded

    set_spike_cap(settings, monkeypatch, D("0.50"))
    seed_call(status="refused_budget", purpose="spike", reserved="0.49")
    seed_call(status="error", purpose="spike", cost="0")
    caps(purpose="spike", conversation_id=None, amount=D("0.50"))  # nothing counted yet
    seed_call(status="pending", purpose="spike", reserved="0.30")
    with pytest.raises(BudgetExceeded) as caught:
        caps(purpose="spike", conversation_id=None, amount=D("0.21"))
    assert caught.value.cap_name == "spike_total"


def test_a_raised_cap_is_honoured(roomy, settings, monkeypatch):
    """The user may raise BUDGET_SPIKE_USD_TOTAL; the guard reads the setting, not a constant."""
    set_spike_cap(settings, monkeypatch, D("2.00"))
    seed_call(purpose="spike", cost="0.90")
    caps(purpose="spike", conversation_id=None, amount=D("1.00"))


def test_the_other_caps_still_apply_to_spike_calls(roomy, settings, monkeypatch, tune):
    from moderation.errors import BudgetExceeded

    set_spike_cap(settings, monkeypatch, D("100"))
    tune(BUDGET_SITE_USD_TOTAL=D("0.10"))
    seed_call(purpose="moderation", cost="0.09")
    with pytest.raises(BudgetExceeded) as caught:
        caps(purpose="spike", conversation_id=None, amount=D("0.02"))
    assert caught.value.cap_name == "site_total"


# --- through the gateway ---------------------------------------------------------------------------------------------

def test_a_spike_call_refused_by_the_cap_is_logged_as_refused_budget_and_no_request_is_made(roomy, settings, monkeypatch, llm_ready, install_fake):
    from moderation.budget import SessionBudget
    from moderation.errors import BudgetExceeded
    from moderation.models import LLMCall

    set_spike_cap(settings, monkeypatch, D("0.001"))
    client = install_fake()
    with pytest.raises(BudgetExceeded) as caught:
        run_call(purpose="spike", agent="master", model=HAIKU, session=SessionBudget("1000"))
    assert caught.value.cap_name == "spike_total"
    assert client.calls == []
    row = LLMCall.objects.get()
    assert (row.purpose, row.status) == ("spike", "refused_budget")
    assert "spike_total" in row.error
    assert row.cost_usd is None or row.cost_usd == 0


def test_the_same_call_for_another_purpose_is_not_refused_by_the_spike_cap(roomy, settings, monkeypatch, llm_ready, install_fake):
    from moderation.models import LLMCall
    from moderation_testkit import make_msg

    set_spike_cap(settings, monkeypatch, D("0.001"))
    client = install_fake(lambda _kw: make_msg())
    run_call(purpose="moderation", agent="master", model=HAIKU)
    assert len(client.calls) == 1
    assert LLMCall.objects.get().status == "ok"


def test_a_spike_call_is_allowed_when_the_cap_has_room(roomy, settings, monkeypatch, llm_ready, install_fake):
    from moderation_testkit import make_msg

    set_spike_cap(settings, monkeypatch, D("0.50"))
    client = install_fake(lambda _kw: make_msg())
    run_call(purpose="spike", agent="master", model=HAIKU)
    assert len(client.calls) == 1


# --- through the spike command ---------------------------------------------------------------------------------------

def test_the_spike_command_stops_at_the_cap_whatever_max_usd_says(spike_env, golden, install_fake, settings, monkeypatch, tmp_path):
    from moderation import budget
    from moderation.models import LLMCall

    scripted = Scripted(golden)  # about 0.0073 per transcript
    client = scripted.install(install_fake)
    cap = D("0.03")
    set_spike_cap(settings, monkeypatch, cap)
    result = run_spike("--max-usd", "100", "--out", str(tmp_path / "res"))
    assert result.exc is None or type(result.exc).__name__ == "CommandError"
    spent = budget.spend(purposes=("spike",))
    assert 0 < spent <= cap
    assert len(client.calls) < 2 * len(golden), "the cap must have stopped the run early"
    refused = LLMCall.objects.filter(purpose="spike", status="refused_budget")
    assert refused.count() >= 1
    assert all("spike_total" in r.error for r in refused)


def test_the_spike_command_makes_no_call_when_the_cap_is_already_used_up(spike_env, golden, install_fake, settings, monkeypatch, tmp_path):
    scripted = Scripted(golden)
    client = scripted.install(install_fake)
    set_spike_cap(settings, monkeypatch, D("0.50"))
    seed_call(purpose="spike", cost="0.50")
    left, _right = find_pair(golden, "factual_accuracy")
    result = run_spike("--max-usd", "100", "--out", str(tmp_path / "res"), "--only", left["id"])
    assert result.exc is None or type(result.exc).__name__ == "CommandError"
    assert client.calls == []
