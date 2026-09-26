"""Structural failures are retried once; refusals and API errors propagate unchanged with no retry."""
from decimal import Decimal

import pytest

import pipeline_agents_kit as kit

pytestmark = pytest.mark.usefixtures("llm_ready")

AGENTS = ("master", "intervenor")


def _valid(agent):
    return kit.master_out() if agent == "master" else kit.intervenor_out()


def _go(agent, sc):
    ag = kit.agents()
    if agent == "master":
        return ag.call_master(sc.run, sc.transcript, topic=sc.topic)
    if not hasattr(sc, "_issue"):
        sc._issue = kit.make_issue(sc.run, sc.msgs[0])
    issue = sc._issue
    return ag.call_intervenor(sc.run, sc.transcript, topic=sc.topic, valid_issues=[issue])


def _schema(agent):
    from moderation.schemas import IntervenorOutput, MasterOutput

    return MasterOutput if agent == "master" else IntervenorOutput


# --- StructuralFailure ----------------------------------------------------------------------------------------------

def test_structural_failure_is_its_own_exception_type():
    from moderation.errors import LLMAPIError, LLMOutputError, LLMRefused

    ag = kit.agents()
    assert issubclass(ag.StructuralFailure, Exception)
    assert not issubclass(ag.StructuralFailure, (LLMRefused, LLMAPIError, LLMOutputError))


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("kind", sorted(kit.BAD_REPLIES))
def test_bad_then_good_succeeds_on_the_second_attempt(agent, kind, install_fake):
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    fake = install_fake(kit.BAD_REPLIES[kind], _valid(agent))
    result = _go(agent, sc)
    assert result == _schema(agent).model_validate(_valid(agent))
    assert len(fake.calls) == 2 and fake.script == []
    rows = list(LLMCall.objects.order_by("pk"))
    assert [r.attempt for r in rows] == [1, 2]
    assert [r.purpose for r in rows] == ["moderation", "moderation"]
    assert [r.agent for r in rows] == [agent, agent]
    assert [r.run_id for r in rows] == [sc.run.pk, sc.run.pk]
    assert [r.conversation_id for r in rows] == [sc.conv.pk, sc.conv.pk]
    assert rows[0].error_code != "" and rows[0].error != ""  # the unusable first answer is logged as such
    assert rows[1].error == "" and rows[1].error_code == ""
    assert rows[1].parsed is not None


@pytest.mark.parametrize("agent", AGENTS)
def test_truncated_output_is_structural_too(agent, install_fake):
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    fake = install_fake(kit.truncated_reply(_valid(agent)), _valid(agent))
    _go(agent, sc)
    assert len(fake.calls) == 2
    assert [r.attempt for r in LLMCall.objects.order_by("pk")] == [1, 2]
    assert LLMCall.objects.order_by("pk").first().stop_reason == "max_tokens"


@pytest.mark.parametrize("agent", AGENTS)
def test_the_retry_sends_an_identical_request(agent, install_fake):
    sc = kit.make_human_scenario()
    fake = install_fake(kit.SCHEMA_MISMATCH, _valid(agent))
    _go(agent, sc)
    assert fake.calls[0] == fake.calls[1]


@pytest.mark.parametrize("agent", AGENTS)
def test_a_good_first_answer_makes_exactly_one_call(agent, install_fake):
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    fake = install_fake(_valid(agent), _valid(agent))
    _go(agent, sc)
    assert len(fake.calls) == 1 and len(fake.script) == 1
    assert LLMCall.objects.count() == 1


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("kind", sorted(kit.BAD_REPLIES))
def test_bad_twice_raises_structural_failure_after_exactly_two_attempts(agent, kind, install_fake):
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    fake = install_fake(kit.BAD_REPLIES[kind], kit.BAD_REPLIES[kind], _valid(agent))
    with pytest.raises(kit.agents().StructuralFailure):
        _go(agent, sc)
    assert len(fake.calls) == 2  # one retry, never two
    assert len(fake.script) == 1  # the third scripted answer was never asked for
    rows = list(LLMCall.objects.order_by("pk"))
    assert [r.attempt for r in rows] == [1, 2]
    assert all(r.run_id == sc.run.pk and r.agent == agent for r in rows)
    assert all(r.error_code != "" for r in rows)


@pytest.mark.parametrize("agent", AGENTS)
def test_mixed_failure_kinds_also_count(agent, install_fake):
    """First a schema mismatch, then invalid JSON: still one retry, then StructuralFailure."""
    sc = kit.make_human_scenario()
    fake = install_fake(kit.SCHEMA_MISMATCH, kit.raises_invalid_json)
    with pytest.raises(kit.agents().StructuralFailure):
        _go(agent, sc)
    assert len(fake.calls) == 2


@pytest.mark.parametrize("agent", AGENTS)
def test_structural_failure_writes_no_domain_rows(agent, install_fake):
    sc = kit.make_human_scenario()
    sc._issue = kit.make_issue(sc.run, sc.msgs[0])
    before = kit.db_snapshot()
    install_fake(kit.SCHEMA_MISMATCH, kit.SCHEMA_MISMATCH)
    with pytest.raises(kit.agents().StructuralFailure):
        _go(agent, sc)
    assert kit.db_snapshot() == before


@pytest.mark.parametrize("agent", AGENTS)
def test_structural_failure_carries_agent_reason_and_both_call_ids(agent, install_fake):
    """Architect ruling: StructuralFailure carries .agent, .reason and .call_ids."""
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    install_fake(kit.SCHEMA_MISMATCH, kit.SCHEMA_MISMATCH)
    with pytest.raises(kit.agents().StructuralFailure) as info:
        _go(agent, sc)
    assert info.value.agent == agent
    assert info.value.reason == "invalid_output"
    assert list(info.value.call_ids) == list(LLMCall.objects.order_by("pk").values_list("pk", flat=True))
    assert len(info.value.call_ids) == 2


def test_structural_failure_reason_reflects_the_last_failure(install_fake):
    sc = kit.make_human_scenario()
    install_fake(kit.SCHEMA_MISMATCH, kit.refusal_reply)
    with pytest.raises(kit.agents().StructuralFailure) as info:
        _go("master", sc)
    assert info.value.reason == "refusal"


def test_each_call_of_the_agent_gets_its_own_retry_budget(install_fake):
    """One call's retry does not use up the next call's: Master fails once then succeeds, Intervenor fails once then succeeds."""
    sc = kit.make_human_scenario()
    fake = install_fake(kit.SCHEMA_MISMATCH, kit.master_out(), kit.SCHEMA_MISMATCH, kit.intervenor_out())
    _go("master", sc)
    _go("intervenor", sc)
    assert len(fake.calls) == 4 and fake.script == []
    from moderation.models import LLMCall

    assert [(r.agent, r.attempt) for r in LLMCall.objects.order_by("pk")] == [
        ("master", 1), ("master", 2), ("intervenor", 1), ("intervenor", 2),
    ]  # fmt: skip


# --- Errors that must propagate unchanged, with no retry ------------------------------------------------------------

@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("status,etype", [(500, "api_error"), (529, "overloaded_error"), (400, "invalid_request_error"), (429, "rate_limit_error")])
def test_api_error_propagates_unchanged_and_is_not_retried(agent, status, etype, install_fake):
    from moderation.errors import LLMAPIError
    from moderation.fake_llm import FakeProviderError
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    fake = install_fake(FakeProviderError(status, etype, "provider said no"), _valid(agent))
    with pytest.raises(LLMAPIError) as info:
        _go(agent, sc)
    assert type(info.value) is LLMAPIError
    assert info.value.status_code == status
    assert len(fake.calls) == 1 and len(fake.script) == 1
    rows = list(LLMCall.objects.all())
    assert len(rows) == 1 and rows[0].status == "error" and rows[0].attempt == 1
    assert info.value.call_id == rows[0].pk


@pytest.mark.parametrize("agent", AGENTS)
def test_api_error_on_the_retry_propagates_as_api_error_not_structural(agent, install_fake):
    from moderation.errors import LLMAPIError
    from moderation.fake_llm import FakeProviderError
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    fake = install_fake(kit.SCHEMA_MISMATCH, FakeProviderError(500, "api_error", "boom"), _valid(agent))
    with pytest.raises(LLMAPIError):
        _go(agent, sc)
    assert len(fake.calls) == 2 and len(fake.script) == 1
    assert [(r.attempt, r.status) for r in LLMCall.objects.order_by("pk")] == [(1, "ok"), (2, "error")]


@pytest.mark.parametrize("agent", AGENTS)
def test_kill_switch_refusal_propagates_and_makes_no_call(agent, install_fake, settings):
    from moderation.errors import LLMDisabled
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    settings.LLM_ENABLED = False
    fake = install_fake(_valid(agent))
    with pytest.raises(LLMDisabled) as info:
        _go(agent, sc)
    assert type(info.value) is LLMDisabled
    assert fake.calls == [] and len(fake.script) == 1
    rows = list(LLMCall.objects.all())
    assert [(r.status, r.attempt) for r in rows] == [("refused_disabled", 1)]


@pytest.mark.parametrize("agent", AGENTS)
def test_budget_refusal_propagates_and_makes_no_call(agent, install_fake, tiny_budget):
    from moderation.errors import BudgetExceeded
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    fake = install_fake(_valid(agent))
    with pytest.raises(BudgetExceeded) as info:
        _go(agent, sc)
    assert type(info.value) is BudgetExceeded
    assert fake.calls == []
    assert [r.status for r in LLMCall.objects.all()] == ["refused_budget"]


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("reason", ["spend_limit", "consecutive_errors"])
def test_breaker_refusal_propagates_and_makes_no_call(agent, reason, install_fake):
    from moderation import breaker
    from moderation.errors import BreakerOpen
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    breaker.trip(reason, "test trip")
    fake = install_fake(_valid(agent))
    with pytest.raises(BreakerOpen) as info:
        _go(agent, sc)
    assert type(info.value) is BreakerOpen
    assert fake.calls == []
    assert [r.status for r in LLMCall.objects.all()] == ["refused_breaker"]


@pytest.mark.parametrize("agent", AGENTS)
def test_budget_unavailable_propagates_and_makes_no_call(agent, install_fake, monkeypatch):
    from moderation import budget
    from moderation.errors import BudgetUnavailable

    sc = kit.make_human_scenario()

    def broken(**kwargs):
        raise BudgetUnavailable("ledger unreadable")

    monkeypatch.setattr(budget, "check_caps", broken)
    fake = install_fake(_valid(agent))
    with pytest.raises(BudgetUnavailable, match="ledger unreadable"):
        _go(agent, sc)
    assert fake.calls == []


@pytest.mark.parametrize("agent", AGENTS)
def test_refusal_on_the_retry_propagates_and_makes_no_third_call(agent, install_fake, settings):
    """First attempt unusable; the kill switch flips before the retry: the refusal propagates, nothing more is called."""
    from moderation.errors import LLMDisabled
    from moderation.fake_llm import make_message
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()

    def unusable_then_switch_off(kwargs):
        settings.LLM_ENABLED = False
        return make_message(None)

    fake = install_fake(unusable_then_switch_off, _valid(agent))
    with pytest.raises(LLMDisabled):
        _go(agent, sc)
    assert len(fake.calls) == 1 and len(fake.script) == 1
    assert [(r.attempt, r.status) for r in LLMCall.objects.order_by("pk")] == [(1, "ok"), (2, "refused_disabled")]


@pytest.mark.parametrize("agent", AGENTS)
def test_value_error_from_the_gateway_propagates_unchanged(agent, install_fake, tune):
    """Architect ruling: ValueError (a programming error, e.g. max_tokens above the limit) is not caught and not retried."""
    sc = kit.make_human_scenario()
    tune(MASTER_MAX_TOKENS=10**7, INTERVENOR_MAX_TOKENS=10**7)
    fake = install_fake(_valid(agent), _valid(agent))
    with pytest.raises(ValueError):
        _go(agent, sc)
    assert fake.calls == []


def test_ledger_spend_is_recorded_for_both_attempts(install_fake):
    """Both attempts are billed and logged (an unusable answer still costs money)."""
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    install_fake(kit.SCHEMA_MISMATCH, kit.master_out())
    _go("master", sc)
    rows = list(LLMCall.objects.order_by("pk"))
    assert all(r.cost_usd is not None and r.cost_usd > Decimal("0") for r in rows)
    assert all(r.tokens_in > 0 for r in rows)
