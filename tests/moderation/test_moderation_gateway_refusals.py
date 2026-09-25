"""moderation/llm.py: every refusal path (order of checks, row written, no client call, right exception)."""
from decimal import Decimal

import pytest
from moderation_testkit import FAKE_KEY, HAIKU, SONNET, reply, run_call, seed_call, utc

D = Decimal


def rows():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk"))


def refused_rows():
    return [r for r in rows() if r.status.startswith("refused_")]


@pytest.fixture(autouse=True)
def ready(llm_ready, tune):
    tune(
        BUDGET_PER_CONVERSATION_USD=D("1.25"),
        BUDGET_SITE_USD_PER_DAY=D("1.50"),
        BUDGET_SITE_USD_TOTAL=D("5.00"),
        BUDGET_EVAL_USD_TOTAL=D("10.00"),
    )
    return llm_ready


@pytest.fixture
def fake(install_fake):
    return install_fake(reply(), reply(), reply())  # any call that reaches the client would consume one


def assert_refused(exc_info, exc_class, status, fake, **expected):
    """One refused_* row of `status`, no client call, exception of the right class and status."""
    assert isinstance(exc_info.value, exc_class)
    assert exc_info.value.status == status
    assert fake.calls == [], "a refused call must never reach the client"
    found = rows()
    assert len(found) == 1, found
    row = found[0]
    assert row.status == status
    assert (row.cost_usd or 0) == 0
    assert row.tokens_in == 0 and row.tokens_out == 0
    assert row.finished_at is not None
    assert row.error, "a refusal row should say why"
    for name, value in expected.items():
        assert getattr(row, name) == value, name
    return row


# --- unknown purpose --------------------------------------------------------------------------------------------------


def test_unknown_purpose_is_a_value_error_with_no_row_and_no_call(fake):
    with pytest.raises(ValueError):
        run_call(purpose="chitchat")
    assert fake.calls == []
    assert rows() == []


def test_unknown_purpose_is_checked_before_the_kill_switch(fake, settings):
    settings.LLM_ENABLED = False
    with pytest.raises(ValueError):
        run_call(purpose="chitchat")
    assert rows() == []


# --- kill switch and API key ---------------------------------------------------------------------------------------


def test_kill_switch_off_refuses(fake, settings):
    from moderation.errors import LLMDisabled

    settings.LLM_ENABLED = False
    with pytest.raises(LLMDisabled) as excinfo:
        run_call(conversation_id=5, run_id=6, attempt=2)
    assert_refused(
        excinfo, LLMDisabled, "refused_disabled", fake, purpose="moderation", agent="master", model=SONNET,
        conversation_id=5, run_id=6, attempt=2,
    )  # fmt: skip


def test_kill_switch_is_off_by_default_in_the_shipped_tunables(fake, settings):
    from config import tunables

    assert tunables.LLM_ENABLED is False


def test_missing_api_key_refuses_as_disabled(fake, settings):
    from moderation.errors import LLMDisabled

    settings.ANTHROPIC_API_KEY = ""
    with pytest.raises(LLMDisabled) as excinfo:
        run_call()
    assert_refused(excinfo, LLMDisabled, "refused_disabled", fake)


def test_the_api_key_is_never_written_to_a_refusal_row(fake, settings):
    from moderation.errors import LLMDisabled

    settings.LLM_ENABLED = False
    settings.ANTHROPIC_API_KEY = FAKE_KEY
    with pytest.raises(LLMDisabled):
        run_call()
    row = rows()[0]
    assert FAKE_KEY not in f"{row.error}{row.request}{row.raw_response}"


# --- breaker ----------------------------------------------------------------------------------------------------------


def test_open_breaker_refuses(fake):
    from moderation import breaker
    from moderation.errors import BreakerOpen

    breaker.trip("manual", "test")
    with pytest.raises(BreakerOpen) as excinfo:
        run_call()
    assert_refused(excinfo, BreakerOpen, "refused_breaker", fake, purpose="moderation", model=SONNET)


# --- model ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["claude-opus-4-1", "gpt-4o", "", "claude-sonnet-5-latest"])
def test_a_model_that_is_not_allowed_is_refused(fake, model):
    from moderation.errors import ModelNotAllowed

    with pytest.raises(ModelNotAllowed) as excinfo:
        run_call(model=model)
    assert_refused(excinfo, ModelNotAllowed, "refused_model", fake, model=model)


# --- budget: each cap ---------------------------------------------------------------------------------------------
# A normal test call reserves about 0.008 (sonnet, max_tokens=500). "Room left" below is 0.005: too little.

OLD = utc(2026, 9, 10, 9)


def test_site_total_cap_refuses(fake):
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="4.995", created_at=OLD)
    with pytest.raises(BudgetExceeded) as excinfo:
        run_call(purpose="spike", agent="master")
    err = excinfo.value
    assert (err.limit, err.spent) == (D("5.00"), D("4.995"))
    assert err.requested > D("0.005")
    found = [r for r in rows() if r.status.startswith("refused_")]
    assert len(found) == 1
    assert found[0].status == "refused_budget"
    assert fake.calls == []
    assert isinstance(err.cap_name, str) and err.cap_name


def test_day_cap_refuses(fake):
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="1.495", created_at=utc(2026, 9, 25, 2))
    with pytest.raises(BudgetExceeded) as excinfo:
        run_call()
    assert (excinfo.value.limit, excinfo.value.spent) == (D("1.50"), D("1.495"))
    assert len(refused_rows()) == 1 and fake.calls == []


def test_the_day_cap_forgets_yesterday(fake):
    seed_call("ok", cost="1.495", created_at=utc(2026, 9, 24, 23, 59, 59))
    run_call()
    assert len(fake.calls) == 1


def test_conversation_cap_refuses_only_for_that_conversation(fake):
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="1.245", conversation_id=9, created_at=OLD)
    with pytest.raises(BudgetExceeded) as excinfo:
        run_call(conversation_id=9)
    assert (excinfo.value.limit, excinfo.value.spent) == (D("1.25"), D("1.245"))
    assert len(refused_rows()) == 1 and fake.calls == []
    run_call(conversation_id=10)  # another conversation is unaffected
    assert len(fake.calls) == 1


def test_conversation_cap_does_not_apply_without_a_conversation_id_or_to_other_purposes(fake):
    seed_call("ok", cost="1.245", conversation_id=9, created_at=OLD)
    run_call(conversation_id=None)
    run_call(purpose="spike", conversation_id=9)
    assert len(fake.calls) == 2


def test_eval_cap_refuses_and_is_independent_of_the_site_budget(fake):
    from moderation.errors import BudgetExceeded

    seed_call("ok", purpose="replay", cost="9.995", created_at=OLD)
    with pytest.raises(BudgetExceeded) as excinfo:
        run_call(purpose="judge", agent="judge")
    assert (excinfo.value.limit, excinfo.value.spent) == (D("10.00"), D("9.995"))
    assert len(refused_rows()) == 1
    run_call(purpose="moderation")  # site budget still open
    assert len(fake.calls) == 1


def test_session_budget_refuses(fake):
    from moderation.budget import SessionBudget
    from moderation.errors import BudgetExceeded

    with pytest.raises(BudgetExceeded) as excinfo:
        run_call(session=SessionBudget(D("0.005")))
    assert excinfo.value.limit == D("0.005")
    assert len(refused_rows()) == 1 and fake.calls == []


def test_pending_rows_count_so_two_callers_cannot_both_squeeze_under_a_cap(fake):
    from moderation.errors import BudgetExceeded

    seed_call("pending", reserved="1.245", conversation_id=9, created_at=utc(2026, 9, 25, 11, 59))
    with pytest.raises(BudgetExceeded):
        run_call(conversation_id=9)
    assert fake.calls == []


def test_refused_rows_do_not_use_up_the_budget(fake):
    from moderation import budget
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="1.495", created_at=utc(2026, 9, 25, 2))
    for _ in range(3):
        with pytest.raises(BudgetExceeded):
            run_call()
    assert len(refused_rows()) == 3
    assert budget.spend() == D("1.495")


def test_the_refusal_row_records_the_attempted_call(fake):
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="1.499", created_at=utc(2026, 9, 25, 2))  # Haiku, 300 tokens reserves about 0.003
    with pytest.raises(BudgetExceeded):
        run_call(agent="intervenor", model=HAIKU, prompt_version="v9", conversation_id=3, run_id=4, attempt=2, max_tokens=300)
    row = refused_rows()[0]
    assert (row.purpose, row.agent, row.model) == ("moderation", "intervenor", HAIKU)
    assert (row.prompt_version, row.conversation_id, row.run_id, row.attempt, row.max_tokens) == ("v9", 3, 4, 2, 300)


def test_a_reservation_beyond_a_cap_is_refused_at_the_largest_allowed_max_tokens(fake, tune):
    """32,000 output tokens on Sonnet reserve $0.32 plus input; with a 0.10 day cap that is refused, not sent.
    (Above LLM_MAX_TOKENS_LIMIT it is a ValueError instead; see test_moderation_review3_guards.py.)"""
    from moderation.errors import BudgetExceeded

    tune(BUDGET_SITE_USD_PER_DAY=D("0.10"), BUDGET_PER_CONVERSATION_USD=D("0.10"), LLM_MAX_TOKENS_LIMIT=32000)
    with pytest.raises(BudgetExceeded):
        run_call(max_tokens=32000)
    assert fake.calls == []
    assert len(refused_rows()) == 1


# --- ledger unreadable ------------------------------------------------------------------------------------------------


@pytest.fixture
def ledger_down(monkeypatch):
    """Every SELECT against the ledger table raises, like a corrupt or locked database."""
    import django.db.backends.utils as dbu
    from django.db import OperationalError

    original = dbu.CursorWrapper.execute

    def failing(self, sql, params=None):
        text = str(sql)
        if "moderation_llmcall" in text and text.lstrip().upper().startswith("SELECT"):
            raise OperationalError("simulated: ledger unreadable")
        return original(self, sql, params)

    monkeypatch.setattr(dbu.CursorWrapper, "execute", failing)
    return monkeypatch


def test_an_unreadable_ledger_raises_budget_unavailable_and_makes_no_call(fake, ledger_down):
    from moderation.errors import BudgetUnavailable, LLMRefused

    with pytest.raises(BudgetUnavailable) as excinfo:
        run_call()
    assert not isinstance(excinfo.value, LLMRefused)
    assert fake.calls == []


def test_the_unreadable_ledger_refuses_every_purpose(fake, ledger_down):
    from moderation.errors import BudgetUnavailable

    for purpose in ("moderation", "spike", "golden", "replay", "judge"):
        with pytest.raises(BudgetUnavailable):
            run_call(purpose=purpose, conversation_id=1 if purpose == "moderation" else None)
    assert fake.calls == []


def test_budget_spend_itself_fails_loudly_when_the_ledger_is_unreadable(ledger_down):
    """spend() must not swallow the error and report zero (that would fail open)."""
    from django.db import DatabaseError

    from moderation import budget

    with pytest.raises(DatabaseError):
        budget.spend()


# --- order of checks --------------------------------------------------------------------------------------------------


def test_kill_switch_beats_breaker_model_and_budget(fake, settings):
    from moderation import breaker
    from moderation.errors import LLMDisabled

    settings.LLM_ENABLED = False
    breaker.trip("manual")
    seed_call("ok", cost="5.00", created_at=OLD)
    with pytest.raises(LLMDisabled):
        run_call(model="claude-opus-4-1")
    assert [r.status for r in rows()][-1] == "refused_disabled"


def test_missing_key_beats_the_breaker(fake, settings):
    from moderation import breaker
    from moderation.errors import LLMDisabled

    settings.ANTHROPIC_API_KEY = ""
    breaker.trip("manual")
    with pytest.raises(LLMDisabled):
        run_call()


def test_breaker_beats_model_and_budget(fake):
    from moderation import breaker
    from moderation.errors import BreakerOpen

    breaker.trip("manual")
    seed_call("ok", cost="5.00", created_at=OLD)
    with pytest.raises(BreakerOpen):
        run_call(model="claude-opus-4-1")


def test_model_beats_budget(fake):
    from moderation.errors import ModelNotAllowed

    seed_call("ok", cost="5.00", created_at=OLD)
    with pytest.raises(ModelNotAllowed):
        run_call(model="claude-opus-4-1")


def test_a_disallowed_model_is_refused_even_when_the_ledger_is_down(fake, ledger_down):
    """Model check comes before the ledger read, so it does not need the ledger."""
    from moderation.errors import ModelNotAllowed

    with pytest.raises(ModelNotAllowed):
        run_call(model="claude-opus-4-1")


def test_disabled_and_breaker_do_not_need_the_ledger(fake, ledger_down, settings):
    from moderation import breaker
    from moderation.errors import BreakerOpen, LLMDisabled

    breaker.trip("manual")
    with pytest.raises(BreakerOpen):
        run_call()
    settings.LLM_ENABLED = False
    with pytest.raises(LLMDisabled):
        run_call()
