"""Independent review pass: tests added for mutants that survived the first mutation run of the suite."""
from decimal import Decimal

import pytest
from moderation_testkit import SONNET, Verdict, call_kwargs, reply, run_call, seed_call, utc
from pydantic import BaseModel

D = Decimal


def rows():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk"))


@pytest.fixture(autouse=True)
def ready(llm_ready, tune):
    tune(
        BUDGET_PER_CONVERSATION_USD=D("1.25"),
        BUDGET_SITE_USD_PER_DAY=D("1.50"),
        BUDGET_SITE_USD_TOTAL=D("5.00"),
        BUDGET_EVAL_USD_TOTAL=D("10.00"),
    )
    return llm_ready


def check(purpose="moderation", conversation_id=None, amount="0.01", session=None):
    from moderation import budget

    return budget.check_caps(purpose=purpose, conversation_id=conversation_id, amount=D(amount), session=session)


# S1: max_tokens=True is an int in Python but not a token count.
@pytest.mark.parametrize("bad", [True, False])
def test_a_boolean_is_not_a_valid_max_tokens(install_fake, bad):
    client = install_fake(reply())
    with pytest.raises(ValueError):
        run_call(max_tokens=bad)
    assert client.calls == [] and rows() == []


# S2: pydantic keeps field order in "properties", so an unsorted dump would give a different hash.
class ZedFirst(BaseModel):
    zebra: int
    apple: int


def test_prompt_sha256_sorts_the_schema_keys(install_fake):
    import hashlib
    import json

    from moderation.errors import LLMOutputError

    schema = ZedFirst.model_json_schema()
    assert list(schema["properties"]) == ["zebra", "apple"], "premise: field order is not alphabetical"
    install_fake(reply(parsed=None))
    with pytest.raises(LLMOutputError):
        run_call(system="sys", output_schema=ZedFirst)
    expected = hashlib.sha256(("sys" + "\x00" + json.dumps(schema, sort_keys=True)).encode("utf-8")).hexdigest()
    assert rows()[0].prompt_sha256 == expected


# S3: the estimator rounds a fraction of a token UP.
@pytest.mark.parametrize("chars, expected", [(1, 1), (2, 1), (3, 2), (4, 2), (5, 2), (6, 3)])
def test_estimator_rounds_partial_tokens_up(tune, chars, expected):
    from moderation import budget

    tune(TOKEN_ESTIMATE_CHARS_PER_TOKEN=2.5, TOKEN_ESTIMATE_OVERHEAD_TOKENS=0)
    assert budget.estimate_input_tokens(system="a" * chars, messages=[]) >= expected


# S4: a row dated after the current UTC day is not part of "today".
def test_the_day_cap_does_not_count_rows_from_a_later_day():
    seed_call("ok", cost="1.45", created_at=utc(2026, 9, 26, 0, 0, 0))  # tomorrow, relative to the frozen clock
    check(amount="0.20")


def test_spend_until_excludes_later_rows():
    from moderation import budget

    seed_call("ok", cost="1", created_at=utc(2026, 9, 25, 10))
    seed_call("ok", cost="2", created_at=utc(2026, 9, 27, 10))
    assert budget.spend(since=utc(2026, 9, 25), until=utc(2026, 9, 26)) == D("1")


# S5: the per-conversation cap is about MODERATION spend in the conversation, not any row carrying its id.
@pytest.mark.parametrize("other", ["spike", "golden", "replay", "judge"])
def test_per_conversation_spend_counts_only_moderation_rows(other):
    seed_call("ok", purpose=other, cost="1.20", conversation_id=7, created_at=utc(2026, 9, 10))
    check(purpose="moderation", conversation_id=7, amount="0.10")


# S6: a success must not close a breaker that tripped after errors had been counted (soft trip: only a probe closes it).
def test_a_success_never_closes_a_breaker_tripped_by_consecutive_errors():
    from moderation import breaker

    for _ in range(5):
        breaker.record_error(status_code=500, error_type="api_error", error_code=None, message="x")
    assert breaker.is_open() is True
    breaker.record_success()
    assert breaker.is_open() is True


def test_a_success_never_closes_a_breaker_tripped_by_a_spend_limit_after_earlier_errors():
    from moderation import breaker

    breaker.record_error(status_code=500, error_type="api_error", error_code=None, message="x")
    breaker.record_error(status_code=400, error_type="invalid_request_error", error_code=None, message="You have reached your specified API usage limits")
    breaker.record_success()
    assert breaker.is_open() is True


# S7: GuardState.save() forces pk=1 (amendment): a stray pk lands on the single row instead of failing or adding one.
def test_saving_a_guard_state_with_another_pk_updates_the_single_row():
    from moderation.models import GuardState

    GuardState.load()
    GuardState(pk=5, breaker_tripped=True, trip_reason="manual").save()
    assert list(GuardState.objects.values_list("pk", flat=True)) == [1]
    assert GuardState.objects.get().breaker_tripped is True


def test_saving_a_fresh_guard_state_when_none_exists_creates_pk_1():
    from moderation.models import GuardState

    GuardState.objects.all().delete()
    state = GuardState(pk=9)
    state.save()
    assert state.pk == 1
    assert list(GuardState.objects.values_list("pk", flat=True)) == [1]


# S8: latency is measured and stored (lower bound only; the callable sleeps so the bound cannot flake).
def test_latency_is_measured_around_the_client_call(install_fake):
    import time

    from moderation_testkit import make_msg

    def slow(kwargs):
        time.sleep(0.06)
        return make_msg()

    install_fake(slow)
    result = run_call()
    assert result.latency_ms >= 50
    assert rows()[0].latency_ms == result.latency_ms


def test_latency_is_stored_for_a_provider_error_too(install_fake):
    import time

    from moderation.errors import LLMAPIError
    from moderation.fake_llm import FakeProviderError

    def slow_failure(kwargs):
        time.sleep(0.06)
        raise FakeProviderError(500, "api_error", "slow failure")

    install_fake(slow_failure)
    with pytest.raises(LLMAPIError):
        run_call()
    assert rows()[0].latency_ms >= 50


# S9: the client receives the model the caller asked for, whichever it is.
@pytest.mark.parametrize("model", ["claude-sonnet-5", "claude-haiku-4-5"])
def test_the_client_gets_exactly_the_requested_model(install_fake, model):
    client = install_fake(reply())
    run_call(model=model)
    assert client.calls[0]["model"] == model
    assert rows()[0].request["model"] == model
    assert rows()[0].model == model


# S10: settings must not invent an API key: with no environment variable the key is empty (so calls are refused
# even though the shipped kill switch is ON).
def test_settings_have_no_default_api_key_and_blank_counts_as_unset():
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent.parent
    code = "import django.conf, config.settings as s; print(repr(s.ANTHROPIC_API_KEY), s.LLM_ENABLED)"
    for value in (None, "", "   "):
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "LLM_ENABLED")}
        if value is not None:
            env["ANTHROPIC_API_KEY"] = value
        out = subprocess.run([sys.executable, "-c", code], cwd=repo, env=env, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert out.stdout.split() == ["''", "True"], out.stdout
