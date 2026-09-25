"""Architect rulings after the break-it pass: no gateway calls inside a transaction, a max_tokens ceiling, and messages
validation. (conftest.py switches the atomic guard off for the whole folder; `forbid_atomic_calls` turns it on.)"""
from decimal import Decimal

import pytest
from django.db import connection, transaction
from moderation_testkit import call_kwargs, reply, run_call

D = Decimal


def rows():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk"))


@pytest.fixture(autouse=True)
def ready(llm_ready):
    return llm_ready


# --- B: LLM_FORBID_ATOMIC_CALLS -----------------------------------------------------------------------------------


def test_the_shipped_default_forbids_calls_inside_a_transaction():
    from config import tunables

    assert tunables.LLM_FORBID_ATOMIC_CALLS is True


def test_the_atomic_guard_tunable_has_a_comment_directly_above():
    import ast
    from pathlib import Path

    path = Path(__import__("config.tunables", fromlist=["x"]).__file__)
    lines = path.read_text().splitlines()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "LLM_FORBID_ATOMIC_CALLS" for t in node.targets):
            assert lines[node.lineno - 2].lstrip().startswith("#")
            return
    pytest.fail("LLM_FORBID_ATOMIC_CALLS is not assigned in tunables.py")


def test_the_atomic_guard_is_exposed_as_a_django_setting_that_conftest_turns_off(settings):
    assert settings.LLM_FORBID_ATOMIC_CALLS is False  # the autouse fixture in tests/moderation/conftest.py


@pytest.mark.django_db(transaction=True)
def test_a_call_inside_atomic_raises_and_touches_nothing(install_fake, forbid_atomic_calls, settings):
    """Guard on + inside transaction.atomic(): RuntimeError before any row, reservation, breaker change or client call."""
    from moderation.models import GuardState

    assert settings.LLM_FORBID_ATOMIC_CALLS is True
    client = install_fake(reply())
    with pytest.raises(RuntimeError, match=r"(?i)rolled|rollback|roll back|erase"):
        with transaction.atomic():
            assert connection.in_atomic_block
            run_call()
    assert client.calls == []
    assert rows() == []
    assert GuardState.objects.count() in (0, 1)
    assert GuardState.load().consecutive_errors == 0 and GuardState.load().breaker_tripped is False


@pytest.mark.django_db(transaction=True)
def test_the_guard_message_explains_why(install_fake, forbid_atomic_calls):
    install_fake(reply())
    with pytest.raises(RuntimeError) as excinfo:
        with transaction.atomic():
            run_call()
    text = str(excinfo.value).lower()
    assert "transaction" in text or "atomic" in text
    assert "ledger" in text or "cost" in text


@pytest.mark.django_db(transaction=True)
def test_the_guard_comes_before_the_other_checks(install_fake, forbid_atomic_calls, settings):
    """Even with the kill switch off, a call inside a transaction is a RuntimeError with no refusal row."""
    settings.LLM_ENABLED = False
    install_fake(reply())
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            run_call()
    assert rows() == []


@pytest.mark.django_db(transaction=True)
def test_a_call_outside_any_atomic_block_works_with_the_guard_on(install_fake, forbid_atomic_calls):
    client = install_fake(reply(input_tokens=1000, output_tokens=500))
    assert not connection.in_atomic_block
    result = run_call()
    assert len(client.calls) == 1
    assert [r.status for r in rows()] == ["ok"]
    assert result.cost_usd == D("0.007")


@pytest.mark.django_db(transaction=True)
def test_the_ledger_survives_a_later_failure_of_the_caller_when_the_call_was_made_outside_atomic(install_fake, forbid_atomic_calls):
    """The point of the rule: call first, then do the caller's own transactional work; its rollback cannot erase the cost."""
    install_fake(reply(input_tokens=1000, output_tokens=500))

    class Boom(Exception):
        pass

    run_call()
    with pytest.raises(Boom):
        with transaction.atomic():
            raise Boom
    assert [r.status for r in rows()] == ["ok"]


@pytest.mark.django_db(transaction=True)
def test_with_the_guard_off_a_call_inside_atomic_works(install_fake, settings):
    """Tests only. Production forbids this: a rollback in the caller would erase the ledger rows."""
    settings.LLM_FORBID_ATOMIC_CALLS = False
    client = install_fake(reply())
    with transaction.atomic():
        run_call()
    assert len(client.calls) == 1
    assert [r.status for r in rows()] == ["ok"]


def test_with_the_guard_off_the_default_wrapped_test_transaction_is_fine(install_fake):
    client = install_fake(reply())
    assert connection.in_atomic_block  # pytest-django's own wrapper
    run_call()
    assert len(client.calls) == 1


def test_with_the_guard_on_the_test_wrapper_transaction_trips_it(install_fake, forbid_atomic_calls):
    """A plain (non-transactional) test is itself inside an atomic block, so the guard fires: proof it looks at
    connection.in_atomic_block and not at anything the gateway itself opens."""
    client = install_fake(reply())
    with pytest.raises(RuntimeError):
        run_call()
    assert client.calls == []


# --- C: LLM_MAX_TOKENS_LIMIT -----------------------------------------------------------------------------------------


def test_the_max_tokens_limit_tunable_is_32000_with_a_comment():
    import ast
    from pathlib import Path

    from config import tunables

    assert tunables.LLM_MAX_TOKENS_LIMIT == 32000
    path = Path(tunables.__file__)
    lines = path.read_text().splitlines()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "LLM_MAX_TOKENS_LIMIT" for t in node.targets):
            assert lines[node.lineno - 2].lstrip().startswith("#")
            return
    pytest.fail("LLM_MAX_TOKENS_LIMIT is not assigned")


def test_the_limit_is_at_least_the_agents_max_tokens():
    from config import tunables

    assert tunables.LLM_MAX_TOKENS_LIMIT >= max(tunables.MASTER_MAX_TOKENS, tunables.INTERVENOR_MAX_TOKENS)


@pytest.mark.parametrize("over", ["limit+1", 10**7, 10**12, 10**30])
def test_a_max_tokens_above_the_limit_is_a_value_error_before_anything_is_written(install_fake, settings, over):
    client = install_fake(reply())
    value = settings.LLM_MAX_TOKENS_LIMIT + 1 if over == "limit+1" else over
    with pytest.raises(ValueError):
        run_call(max_tokens=value)
    assert client.calls == []
    assert rows() == []


def test_exactly_the_limit_is_accepted_when_the_budget_allows_it(install_fake, tune, settings):
    """32,000 output tokens on Sonnet reserve about $0.33, so the caps are raised for this test."""
    tune(
        BUDGET_PER_CONVERSATION_USD=D("5"), BUDGET_SITE_USD_PER_DAY=D("5"), BUDGET_SITE_USD_TOTAL=D("5"),
        LLM_MAX_TOKENS_LIMIT=32000,
    )
    client = install_fake(reply())
    run_call(max_tokens=32000)
    assert len(client.calls) == 1
    assert rows()[0].max_tokens == 32000


def test_the_limit_is_read_from_settings(install_fake, tune):
    client = install_fake(reply())
    tune(LLM_MAX_TOKENS_LIMIT=100)
    with pytest.raises(ValueError):
        run_call(max_tokens=101)
    run_call(max_tokens=100)
    assert len(client.calls) == 1


def test_a_limit_value_error_beats_the_kill_switch(install_fake, settings):
    settings.LLM_ENABLED = False
    install_fake(reply())
    with pytest.raises(ValueError):
        run_call(max_tokens=10**30)
    assert rows() == []


# --- D: messages validation -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        [],
        None,
        "hello",
        {"role": "user", "content": "hi"},
        ("not", "a list"),
        [{"role": "assistant", "content": "I speak first"}],
        [{"role": "system", "content": "x"}, {"role": "user", "content": "hi"}],
        [{"content": "no role"}],
        ["just a string"],
    ],
    ids=["empty", "none", "str", "dict", "tuple", "first-assistant", "first-system", "no-role", "bare-string"],
)
def test_bad_messages_are_a_value_error_before_anything_is_written(install_fake, bad):
    client = install_fake(reply())
    with pytest.raises(ValueError):
        run_call(messages=bad)
    assert client.calls == []
    assert rows() == []


def test_bad_messages_beat_the_kill_switch(install_fake, settings):
    settings.LLM_ENABLED = False
    install_fake(reply())
    with pytest.raises(ValueError):
        run_call(messages=[])
    assert rows() == []


def test_bad_messages_do_not_feed_the_breaker(install_fake):
    from moderation.models import GuardState

    install_fake(*[reply() for _ in range(10)])
    for _ in range(10):
        with pytest.raises(ValueError):
            run_call(messages=[])
    assert GuardState.load().consecutive_errors == 0
    assert GuardState.load().breaker_tripped is False


def test_a_valid_single_user_message_works(install_fake):
    client = install_fake(reply())
    run_call(messages=[{"role": "user", "content": "hello"}])
    assert len(client.calls) == 1


def test_a_valid_alternating_conversation_works(install_fake):
    client = install_fake(reply())
    run_call(
        messages=[
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
        ]
    )
    assert len(client.calls) == 1


def test_a_user_message_with_content_blocks_works(install_fake):
    client = install_fake(reply())
    run_call(messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}])
    assert len(client.calls) == 1


# --- E: LLM_MAX_INPUT_TOKENS -----------------------------------------------------------------------------------------


def _estimate(system, messages):
    from moderation import budget
    from moderation_testkit import Verdict

    return budget.estimate_input_tokens(system=system, messages=messages, schema=Verdict.model_json_schema())


USER = [{"role": "user", "content": "hi"}]


def _no_side_effects(client, session):
    from moderation.models import GuardState

    assert client.calls == []
    assert rows() == []
    assert session.spent == D("0")
    assert GuardState.load().consecutive_errors == 0
    assert GuardState.load().breaker_tripped is False


def test_the_input_ceiling_tunable_is_50000_with_a_comment():
    import ast
    from pathlib import Path

    from config import tunables

    assert tunables.LLM_MAX_INPUT_TOKENS == 50000
    path = Path(tunables.__file__)
    lines = path.read_text().splitlines()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "LLM_MAX_INPUT_TOKENS" for t in node.targets):
            assert lines[node.lineno - 2].lstrip().startswith("#")
            return
    pytest.fail("LLM_MAX_INPUT_TOKENS is not assigned")


def test_the_default_ceiling_is_above_the_worst_case_legitimate_input():
    """A legitimate transcript (the full window of maximum-length messages plus the system prompt) must never be
    refused by the ceiling."""
    from django.conf import settings

    legitimate = (
        settings.TRANSCRIPT_MAX_MESSAGES * settings.MAX_MESSAGE_CHARS / settings.TOKEN_ESTIMATE_CHARS_PER_TOKEN
        + settings.TOKEN_ESTIMATE_OVERHEAD_TOKENS
        + settings.SYSTEM_PROMPT_TOKENS_ESTIMATE
    )
    assert settings.LLM_MAX_INPUT_TOKENS > legitimate


def test_an_oversized_system_prompt_is_a_value_error_before_anything_happens(install_fake, settings):
    from moderation.budget import SessionBudget

    settings.LLM_MAX_INPUT_TOKENS = 2000
    system = "s" * 10_000  # about 4000 estimated tokens plus overhead: well over 2000
    estimate = _estimate(system, call_kwargs()["messages"])  # run_call's default messages, the same schema llm.call uses
    assert estimate > 2000
    client, session = install_fake(reply()), SessionBudget(D("1"))
    with pytest.raises(ValueError) as excinfo:
        run_call(system=system, session=session)
    assert str(estimate) in str(excinfo.value)  # names the estimate...
    assert "2000" in str(excinfo.value)  # ...and the limit
    _no_side_effects(client, session)


def test_an_oversized_message_list_is_a_value_error_before_anything_happens(install_fake, settings):
    from moderation.budget import SessionBudget

    settings.LLM_MAX_INPUT_TOKENS = 2000
    messages = [{"role": "user", "content": "m" * 5000}, {"role": "assistant", "content": "n" * 5000}, {"role": "user", "content": "o" * 5000}]
    assert _estimate("sys", messages) > 2000
    client, session = install_fake(reply()), SessionBudget(D("1"))
    with pytest.raises(ValueError):
        run_call(system="sys", messages=messages, session=session)
    _no_side_effects(client, session)


def test_a_request_exactly_at_the_limit_is_accepted_and_one_over_is_not(install_fake, settings):
    system, messages = "boundary " * 300, [{"role": "user", "content": "hello " * 200}]
    n = _estimate(system, messages)
    client = install_fake(reply(), reply())
    settings.LLM_MAX_INPUT_TOKENS = n
    run_call(system=system, messages=messages)  # exactly at the limit
    assert len(client.calls) == 1
    settings.LLM_MAX_INPUT_TOKENS = n - 1
    with pytest.raises(ValueError):
        run_call(system=system, messages=messages)
    assert len(client.calls) == 1
    settings.LLM_MAX_INPUT_TOKENS = n + 1
    run_call(system=system, messages=messages)  # just under
    assert len(client.calls) == 2


def test_the_ceiling_counts_the_schema_json_as_well_as_the_text(install_fake, settings):
    """Text alone fits under the limit; text plus the schema JSON does not."""
    from moderation import budget
    from moderation_testkit import Verdict

    system = "t" * 500
    text_only = budget.estimate_input_tokens(system=system, messages=USER)
    with_schema = _estimate(system, USER)
    assert with_schema > text_only
    settings.LLM_MAX_INPUT_TOKENS = text_only  # fits without the schema, not with it
    client = install_fake(reply())
    with pytest.raises(ValueError):
        run_call(system=system, output_schema=Verdict)
    assert client.calls == [] and rows() == []


def test_the_oversized_input_value_error_beats_the_kill_switch(install_fake, settings):
    settings.LLM_ENABLED = False
    settings.LLM_MAX_INPUT_TOKENS = 2000
    install_fake(reply())
    with pytest.raises(ValueError):
        run_call(system="s" * 10_000)
    assert rows() == []


def test_the_input_ceiling_is_read_from_settings(install_fake, tune):
    client = install_fake(reply())
    tune(LLM_MAX_INPUT_TOKENS=1)
    with pytest.raises(ValueError):
        run_call()
    tune(LLM_MAX_INPUT_TOKENS=50000)
    run_call()
    assert len(client.calls) == 1
