"""moderation/errors.py: the typed exceptions and the status each refusal is logged with."""
import pytest


def test_refusal_classes_carry_their_ledger_status():
    from moderation import errors

    assert errors.LLMDisabled.status == "refused_disabled"
    assert errors.BreakerOpen.status == "refused_breaker"
    assert errors.ModelNotAllowed.status == "refused_model"
    assert errors.BudgetExceeded.status == "refused_budget"


@pytest.mark.parametrize("name", ["LLMDisabled", "BreakerOpen", "ModelNotAllowed", "BudgetExceeded"])
def test_refusals_are_llm_refused_exceptions(name):
    from moderation import errors

    cls = getattr(errors, name)
    assert issubclass(errors.LLMRefused, Exception)
    assert issubclass(cls, errors.LLMRefused)


def test_other_failures_are_not_refusals():
    """Callers must be able to tell "we chose not to call" from "we called and it failed" from "ledger unreadable"."""
    from moderation import errors

    for cls in (errors.BudgetUnavailable, errors.LLMAPIError, errors.LLMOutputError):
        assert issubclass(cls, Exception)
        assert not issubclass(cls, errors.LLMRefused)
    assert not issubclass(errors.LLMAPIError, errors.LLMOutputError)
    assert not issubclass(errors.LLMOutputError, errors.LLMAPIError)
    assert not issubclass(errors.BudgetUnavailable, errors.LLMAPIError)


def test_refusal_statuses_are_distinct():
    from moderation import errors

    statuses = {c.status for c in (errors.LLMDisabled, errors.BreakerOpen, errors.ModelNotAllowed, errors.BudgetExceeded)}
    assert len(statuses) == 4
