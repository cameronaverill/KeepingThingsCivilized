"""moderation/budget.py, part 2: check_caps for each cap separately (site total, UTC day, conversation, eval, session).

Default caps (per conversation 1.25, per day 1.50, site total 5.00, eval 10.00) are set explicitly by the fixture
below so these tests do not depend on how tunables.py is edited. Clock frozen at 2026-09-25 12:00 UTC.
"""
from decimal import Decimal

import pytest
from moderation_testkit import seed_call, utc

D = Decimal


@pytest.fixture(autouse=True)
def caps(tune, frozen_clock):
    tune(
        BUDGET_PER_CONVERSATION_USD=D("1.25"),
        BUDGET_SITE_USD_PER_DAY=D("1.50"),
        BUDGET_SITE_USD_TOTAL=D("5.00"),
        BUDGET_EVAL_USD_TOTAL=D("10.00"),
    )
    return frozen_clock


OLD = utc(2026, 9, 10, 9)  # an earlier day: counts toward all-time caps, not the day cap


def check(purpose="moderation", conversation_id=None, amount="0.01", session=None):
    from moderation import budget

    return budget.check_caps(purpose=purpose, conversation_id=conversation_id, amount=D(amount), session=session)


def test_check_caps_passes_on_an_empty_ledger():
    assert check() is None


@pytest.mark.parametrize("purpose", ["moderation", "spike", "golden", "replay", "judge"])
def test_every_purpose_can_pass(purpose):
    check(purpose=purpose, amount="0.10")


# --- site total (all time) ---------------------------------------------------------------------------------------


def test_site_total_cap_allows_exactly_reaching_the_limit_and_refuses_beyond():
    seed_call("ok", purpose="moderation", cost="4.90", created_at=OLD)
    check(purpose="spike", amount="0.10")  # 4.90 + 0.10 == 5.00: not over
    from moderation.errors import BudgetExceeded

    with pytest.raises(BudgetExceeded) as excinfo:
        check(purpose="spike", amount="0.11")
    err = excinfo.value
    assert err.limit == D("5.00")
    assert err.spent == D("4.90")
    assert err.requested == D("0.11")
    assert err.status == "refused_budget"


@pytest.mark.parametrize("purpose", ["moderation", "spike", "golden"])
def test_site_total_applies_to_all_site_purposes_and_spike_and_golden_spend_counts(purpose):
    from moderation.errors import BudgetExceeded

    seed_call("ok", purpose="spike", cost="2.50", created_at=OLD)
    seed_call("ok", purpose="golden", cost="2.45", created_at=OLD)
    with pytest.raises(BudgetExceeded):
        check(purpose=purpose, amount="0.10")


def test_site_total_counts_pending_rows_at_their_reservation():
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="4.00", created_at=OLD)
    seed_call("pending", reserved="0.95", created_at=OLD)
    with pytest.raises(BudgetExceeded):
        check(amount="0.10")


def test_site_total_ignores_refused_rows():
    seed_call("refused_budget", reserved="99", created_at=OLD)
    seed_call("refused_breaker", reserved="99", created_at=OLD)
    check(amount="1.00")


# --- per day (UTC) --------------------------------------------------------------------------------------------------


def test_day_cap_limit_spent_and_requested():
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="1.40", created_at=utc(2026, 9, 25, 3))
    check(amount="0.10")  # 1.40 + 0.10 == 1.50
    with pytest.raises(BudgetExceeded) as excinfo:
        check(amount="0.20")
    assert excinfo.value.limit == D("1.50")
    assert excinfo.value.spent == D("1.40")
    assert excinfo.value.requested == D("0.20")


def test_day_cap_counts_a_row_at_exactly_midnight_utc_today():
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="1.40", created_at=utc(2026, 9, 25, 0, 0, 0))
    with pytest.raises(BudgetExceeded):
        check(amount="0.20")


def test_day_cap_ignores_a_row_one_second_before_midnight_utc():
    seed_call("ok", cost="1.40", created_at=utc(2026, 9, 24, 23, 59, 59))
    check(amount="0.20")


def test_day_boundary_as_the_clock_crosses_midnight(caps):
    """A row from 20:00 on the 24th counts at 23:59:59 that day and is forgotten at 00:00:00 the next."""
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="1.40", created_at=utc(2026, 9, 24, 20))
    caps.set(utc(2026, 9, 24, 23, 59, 59))
    with pytest.raises(BudgetExceeded):
        check(amount="0.20")
    caps.set(utc(2026, 9, 25, 0, 0, 0))
    check(amount="0.20")


def test_day_is_a_utc_day_even_for_a_row_stamped_in_another_timezone(caps):
    """01:00 at UTC+2 on the 25th is 23:00 UTC on the 24th: yesterday."""
    from datetime import datetime, timedelta, timezone

    seed_call("ok", cost="1.40", created_at=datetime(2026, 9, 25, 1, 0, tzinfo=timezone(timedelta(hours=2))))
    caps.set(utc(2026, 9, 25, 0, 30))
    check(amount="0.20")


def test_day_cap_counts_pending_rows_and_all_site_purposes():
    from moderation.errors import BudgetExceeded

    seed_call("pending", purpose="spike", reserved="0.70", created_at=utc(2026, 9, 25, 8))
    seed_call("ok", purpose="golden", cost="0.70", created_at=utc(2026, 9, 25, 9))
    with pytest.raises(BudgetExceeded):
        check(purpose="moderation", amount="0.20")


def test_day_cap_does_not_apply_to_eval_purposes():
    seed_call("ok", purpose="replay", cost="1.40", created_at=utc(2026, 9, 25, 8))
    check(purpose="judge", amount="2.00")
    check(purpose="replay", amount="5.00")


def test_eval_spend_today_does_not_count_against_the_site_day_cap():
    seed_call("ok", purpose="judge", cost="1.40", created_at=utc(2026, 9, 25, 8))
    check(purpose="moderation", amount="1.00")


# --- per conversation (purpose "moderation" with a conversation id only) ------------------------------------------


def test_conversation_cap_applies_to_moderation_with_a_conversation_id():
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="1.20", conversation_id=7, created_at=OLD)
    check(conversation_id=7, amount="0.05")  # 1.20 + 0.05 == 1.25
    with pytest.raises(BudgetExceeded) as excinfo:
        check(conversation_id=7, amount="0.06")
    assert excinfo.value.limit == D("1.25")
    assert excinfo.value.spent == D("1.20")
    assert excinfo.value.requested == D("0.06")


def test_conversation_cap_counts_pending_rows():
    from moderation.errors import BudgetExceeded

    seed_call("pending", reserved="1.20", conversation_id=7, created_at=OLD)
    with pytest.raises(BudgetExceeded):
        check(conversation_id=7, amount="0.10")


def test_conversation_cap_is_per_conversation():
    seed_call("ok", cost="1.20", conversation_id=7, created_at=OLD)
    check(conversation_id=8, amount="0.10")


def test_conversation_cap_is_not_applied_without_a_conversation_id():
    seed_call("ok", cost="1.20", conversation_id=7, created_at=OLD)
    check(purpose="moderation", conversation_id=None, amount="1.30")  # over 1.25, but no conversation


@pytest.mark.parametrize("purpose", ["spike", "golden", "replay", "judge"])
def test_conversation_cap_is_not_applied_to_other_purposes(purpose):
    seed_call("ok", cost="1.20", conversation_id=7, created_at=OLD)
    check(purpose=purpose, conversation_id=7, amount="0.10")


def test_conversation_cap_ignores_refused_rows():
    seed_call("refused_budget", reserved="9", conversation_id=7, created_at=OLD)
    check(conversation_id=7, amount="1.00")


# --- eval total, independent of the site ------------------------------------------------------------------------


def test_eval_total_cap():
    from moderation.errors import BudgetExceeded

    seed_call("ok", purpose="replay", cost="9.00", created_at=OLD)
    seed_call("pending", purpose="judge", reserved="0.95", created_at=OLD)
    check(purpose="judge", amount="0.05")  # 9.95 + 0.05 == 10.00
    with pytest.raises(BudgetExceeded) as excinfo:
        check(purpose="judge", amount="0.06")
    assert excinfo.value.limit == D("10.00")
    assert excinfo.value.spent == D("9.95")
    assert excinfo.value.requested == D("0.06")


def test_eval_purposes_share_one_eval_total():
    from moderation.errors import BudgetExceeded

    seed_call("ok", purpose="replay", cost="9.99", created_at=OLD)
    with pytest.raises(BudgetExceeded):
        check(purpose="judge", amount="0.02")


def test_exhausting_the_eval_budget_leaves_the_site_budget_alone():
    seed_call("ok", purpose="replay", cost="10.00", created_at=OLD)
    check(purpose="moderation", conversation_id=3, amount="1.00")
    check(purpose="spike", amount="0.40")  # under the spike-only cap (BUDGET_SPIKE_USD_TOTAL = 1.50)


def test_exhausting_the_site_budget_leaves_the_eval_budget_alone():
    from moderation.errors import BudgetExceeded

    seed_call("ok", purpose="moderation", cost="5.00", created_at=OLD)
    with pytest.raises(BudgetExceeded):
        check(purpose="moderation", amount="0.01")
    check(purpose="judge", amount="9.00")
    check(purpose="replay", amount="10.00")


# --- session budget (a command's --max-usd) -----------------------------------------------------------------------


def test_session_budget_caps_a_command():
    from moderation.budget import SessionBudget
    from moderation.errors import BudgetExceeded

    session = SessionBudget(D("0.50"))
    session.spent = D("0.40")
    check(amount="0.10", session=session)  # 0.40 + 0.10 == 0.50
    with pytest.raises(BudgetExceeded) as excinfo:
        check(amount="0.11", session=session)
    assert excinfo.value.limit == D("0.50")
    assert excinfo.value.spent == D("0.40")
    assert excinfo.value.requested == D("0.11")


def test_session_budget_applies_to_eval_purposes_too():
    from moderation.budget import SessionBudget
    from moderation.errors import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        check(purpose="judge", amount="0.60", session=SessionBudget(D("0.50")))


def test_no_session_budget_means_no_session_cap():
    check(amount="1.00", session=None)


def test_a_generous_session_budget_does_not_override_the_other_caps():
    from moderation.budget import SessionBudget
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="1.45", created_at=utc(2026, 9, 25, 3))
    with pytest.raises(BudgetExceeded):
        check(amount="0.10", session=SessionBudget(D("100")))


def test_each_cap_reports_its_own_cap_name():
    from moderation.budget import SessionBudget
    from moderation.errors import BudgetExceeded
    from moderation.models import LLMCall

    def refused_name(setup, **kwargs):
        LLMCall.objects.all().delete()
        setup()
        with pytest.raises(BudgetExceeded) as excinfo:
            check(**kwargs)
        return excinfo.value.cap_name

    names = {
        "site_total": refused_name(lambda: seed_call("ok", cost="4.95", created_at=OLD), purpose="spike", amount="0.10"),
        "site_day": refused_name(lambda: seed_call("ok", cost="1.45", created_at=utc(2026, 9, 25, 1)), amount="0.10"),
        "conversation": refused_name(
            lambda: seed_call("ok", cost="1.20", conversation_id=1, created_at=OLD), conversation_id=1, amount="0.10"
        ),
        "eval_total": refused_name(lambda: seed_call("ok", purpose="judge", cost="9.95", created_at=OLD), purpose="judge", amount="0.10"),
        "session": refused_name(lambda: None, amount="0.10", session=SessionBudget(D("0.05"))),
    }
    assert names == {k: k for k in names}


def test_check_caps_arguments_are_keyword_only():
    from moderation import budget

    with pytest.raises(TypeError):
        budget.check_caps("moderation", None, D("0.01"))
