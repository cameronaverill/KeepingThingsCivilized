"""moderation/llm.py, the success path: what call() returns and what the ledger row holds."""
import hashlib
import json
from decimal import Decimal

import pytest
from django.conf import settings
from moderation_testkit import FAKE_KEY, HAIKU, SONNET, OtherSchema, Verdict, call_kwargs, make_msg, reply, run_call

D = Decimal

# Sonnet: 1000 in * 2 + 500 out * 10 + 2000 cache-write * 2.5 + 4000 cache-read * 0.2 = 2000 + 5000 + 5000 + 800 micro-dollars.
SONNET_USAGE = dict(input_tokens=1000, output_tokens=500, cache_write=2000, cache_read=4000)
SONNET_COST = D("0.0128")


def rows():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk"))


def only_row():
    found = rows()
    assert len(found) == 1, found
    return found[0]


def flatten(value):
    """Every string and number inside a nested JSON value (keys included)."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from flatten(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from flatten(v)
    else:
        yield value


@pytest.fixture(autouse=True)
def ready(llm_ready):
    return llm_ready


def test_call_returns_a_result_with_the_parsed_output_usage_and_cost(install_fake):
    install_fake(reply(parsed={"label": "fine", "score": 3}, stop_reason="end_turn", msg_id="msg_abc", **SONNET_USAGE))
    result = run_call()
    row = only_row()
    assert isinstance(result.parsed, Verdict)
    assert (result.parsed.label, result.parsed.score) == ("fine", 3)
    assert result.call_id == row.pk
    assert result.stop_reason == "end_turn"
    assert result.input_tokens == 1000
    assert result.output_tokens == 500
    assert result.cache_write_tokens == 2000
    assert result.cache_read_tokens == 4000
    assert result.cost_usd == SONNET_COST
    assert isinstance(result.latency_ms, int) and result.latency_ms >= 0


def test_haiku_call_is_priced_at_haiku_rates(install_fake):
    """3000 in * 1 + 1000 out * 5 + 10000 cache-read * 0.1 = 3000 + 5000 + 1000 micro-dollars."""
    install_fake(reply(input_tokens=3000, output_tokens=1000, cache_read=10_000))
    result = run_call(model=HAIKU, max_tokens=1000)
    assert result.cost_usd == D("0.009")
    assert only_row().cost_usd == D("0.009")


def test_success_row_contents(install_fake):
    install_fake(reply(parsed={"label": "fine", "score": 3}, stop_reason="end_turn", msg_id="msg_abc", **SONNET_USAGE))
    result = run_call(prompt_version="master-v1", conversation_id=41, run_id=17)
    row = only_row()
    assert row.status == "ok"
    assert row.purpose == "moderation"
    assert row.agent == "master"
    assert row.attempt == 1
    assert row.provider == "anthropic"
    assert row.model == SONNET
    assert row.prompt_version == "master-v1"
    assert row.conversation_id == 41
    assert row.run_id == 17
    assert row.max_tokens == 500
    assert row.temperature is None
    assert (row.tokens_in, row.tokens_out, row.cache_write_tokens, row.cache_read_tokens) == (1000, 500, 2000, 4000)
    assert row.cost_usd == SONNET_COST == result.cost_usd
    assert row.stop_reason == "end_turn"
    assert row.provider_request_id == "msg_abc"
    assert row.parsed == {"label": "fine", "score": 3}
    assert isinstance(row.raw_response, str)
    assert isinstance(row.latency_ms, int) and row.latency_ms >= 0
    assert row.finished_at is not None
    assert row.error == "" and not row.error_code


def test_reserved_usd_is_kept_on_the_row_and_matches_the_reservation_formula(install_fake):
    from moderation import budget

    install_fake(reply(input_tokens=100, output_tokens=50))  # a small, ordinary reply
    kwargs = call_kwargs()
    run_call()
    row = only_row()
    estimate = budget.estimate_input_tokens(system=kwargs["system"], messages=kwargs["messages"], schema=Verdict)
    assert row.reserved_usd == budget.reservation_usd(SONNET, estimated_input_tokens=estimate, max_tokens=500)
    assert row.reserved_usd > row.cost_usd  # a normal call costs less than its worst case


def test_a_call_that_costs_more_than_its_reservation_is_logged_at_its_real_cost(install_fake):
    """Reservations are estimates; the ledger must record the truth, and the call must not fail because of it."""
    install_fake(reply(input_tokens=1000, output_tokens=10_000))  # 0.002 + 0.1
    result = run_call(max_tokens=500)
    row = only_row()
    assert result.cost_usd == D("0.102")
    assert row.cost_usd == D("0.102")
    assert row.cost_usd > row.reserved_usd


def test_temperature_is_recorded_and_sent_for_a_model_that_accepts_it(install_fake):
    """Haiku accepts temperature. The SDK's parse() has no temperature keyword, so it travels in extra_body, but the
    stored request JSON shows it as a top-level key."""
    client = install_fake(reply())
    run_call(model=HAIKU, temperature=0.3)
    assert client.calls[0]["extra_body"]["temperature"] == 0.3
    row = only_row()
    assert float(row.temperature) == pytest.approx(0.3)
    assert row.request["temperature"] == pytest.approx(0.3)


def test_temperature_zero_is_a_real_value_not_treated_as_missing(install_fake):
    client = install_fake(reply())
    run_call(model=HAIKU, temperature=0.0)
    assert client.calls[0]["extra_body"]["temperature"] == 0.0
    assert only_row().temperature is not None
    assert float(only_row().temperature) == 0.0


@pytest.mark.parametrize("model", [SONNET, HAIKU])
def test_no_temperature_means_nothing_is_sent_and_the_column_is_null(install_fake, model):
    client = install_fake(reply())
    run_call(model=model)
    sent = client.calls[0]
    assert sent.get("temperature") is None
    assert "temperature" not in (sent.get("extra_body") or {})
    row = only_row()
    assert row.temperature is None
    assert "temperature" not in row.request


@pytest.mark.parametrize("temperature", [0.0, 0.3, 1.0])
def test_a_model_that_rejects_temperature_is_refused_locally_before_anything_happens(install_fake, temperature):
    """Sonnet 5 returns HTTP 400 for a non-default temperature, so call() raises ValueError first: no row, no
    reservation, no client call."""
    client = install_fake(reply())
    with pytest.raises(ValueError):
        run_call(model=SONNET, temperature=temperature)
    assert client.calls == []
    assert rows() == []


def test_accepts_temperature_table():
    from moderation import pricing

    assert pricing.ACCEPTS_TEMPERATURE == {SONNET: False, HAIKU: True}


def test_thinking_is_always_disabled(install_fake):
    """Sonnet 5 thinks by default when `thinking` is omitted, and thinking bills as output tokens."""
    client = install_fake(reply(), reply())
    run_call(model=SONNET)
    run_call(model=HAIKU)
    for sent in client.calls:
        assert sent["thinking"] == {"type": "disabled"}
    for row in rows():
        assert row.request["thinking"] == {"type": "disabled"}


EPHEMERAL = {"type": "ephemeral"}
SYSTEM_TEXT = "You are a careful test moderator."


def _extra(sent):
    return sent.get("extra_body") or {}


def test_caching_is_on_by_default_with_an_explicit_breakpoint_on_the_system_block(install_fake):
    client = install_fake(reply())
    run_call()
    sent = client.calls[0]
    assert sent["system"] == [{"type": "text", "text": SYSTEM_TEXT, "cache_control": EPHEMERAL}]
    assert isinstance(sent["system"], list)  # the fake client receives a list-valued system
    assert "cache_control" not in _extra(sent)  # no automatic caching any more
    assert "cache_control" not in sent


def test_cache_system_true_stored_request_shape(install_fake):
    install_fake(reply())
    run_call(cache_system=True)
    request = only_row().request
    assert request["system"] == SYSTEM_TEXT  # plain text, not the block list
    assert request["cache_system"] is True
    assert request["cache_control"] == EPHEMERAL


def test_cache_system_false_sends_a_plain_string_and_no_cache_control_anywhere(install_fake):
    client = install_fake(reply())
    run_call(cache_system=False)
    sent = client.calls[0]
    assert sent["system"] == SYSTEM_TEXT
    assert isinstance(sent["system"], str)
    assert "cache_control" not in json.dumps(sent, default=str)
    request = only_row().request
    assert request["system"] == SYSTEM_TEXT
    assert request["cache_system"] is False
    assert "cache_control" not in request


@pytest.mark.parametrize("cache_system", [True, False])
def test_temperature_still_goes_in_extra_body_in_both_modes(install_fake, cache_system):
    client = install_fake(reply())
    run_call(model=HAIKU, temperature=0.3, cache_system=cache_system)
    assert client.calls[0]["extra_body"]["temperature"] == 0.3
    assert only_row().request["temperature"] == pytest.approx(0.3)
    assert "cache_control" not in client.calls[0]["extra_body"]


@pytest.mark.parametrize("cache_system", [True, False])
def test_thinking_stays_disabled_in_both_modes(install_fake, cache_system):
    client = install_fake(reply())
    run_call(cache_system=cache_system)
    assert client.calls[0]["thinking"] == {"type": "disabled"}
    assert only_row().request["thinking"] == {"type": "disabled"}


def test_prompt_hash_and_reservation_do_not_depend_on_the_caching_mode(install_fake):
    install_fake(reply(), reply())
    run_call(cache_system=True)
    run_call(cache_system=False)
    on, off = rows()
    assert on.prompt_sha256 == off.prompt_sha256
    assert on.reserved_usd == off.reserved_usd


@pytest.mark.parametrize("cache_system", [True, False])
def test_cache_tokens_in_usage_are_priced_at_the_write_and_read_prices_in_both_modes(install_fake, cache_system):
    """Sonnet: 1000*2 + 500*10 + 2000 cache-write*2.5 + 4000 cache-read*0.2 = 0.0128; Haiku (below) for the second model."""
    install_fake(reply(**SONNET_USAGE))
    result = run_call(cache_system=cache_system)
    row = only_row()
    assert result.cost_usd == row.cost_usd == SONNET_COST
    assert (row.cache_write_tokens, row.cache_read_tokens) == (2000, 4000)
    assert (result.cache_write_tokens, result.cache_read_tokens) == (2000, 4000)


@pytest.mark.parametrize("cache_system", [True, False])
def test_haiku_cache_tokens_are_priced_at_haiku_prices_in_both_modes(install_fake, cache_system):
    """1000*1 + 100*5 + 8000 cache-write*1.25 + 20000 cache-read*0.10 = 1000 + 500 + 10000 + 2000 micro-dollars."""
    install_fake(reply(input_tokens=1000, output_tokens=100, cache_write=8000, cache_read=20_000))
    result = run_call(model=HAIKU, cache_system=cache_system, max_tokens=500)
    assert result.cost_usd == D("0.0135")


def test_a_pure_cache_read_costs_only_the_read_price(install_fake):
    """The point of the breakpoint: a second call reading the cached system prompt. 5000 cache-read tokens on Sonnet
    at $0.20/M = $0.001, plus 50 uncached input at $2/M = $0.0001 and 100 output at $10/M = $0.001."""
    install_fake(reply(input_tokens=50, output_tokens=100, cache_write=0, cache_read=5000))
    assert run_call().cost_usd == D("0.0021")


def test_cache_system_is_keyword_only_and_a_bool_default_true():
    import inspect

    from moderation import llm

    parameter = inspect.signature(llm.call).parameters["cache_system"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is True


def test_the_client_receives_the_model_max_tokens_and_schema_and_the_texts(install_fake):
    client = install_fake(reply())
    kwargs = call_kwargs(model=HAIKU, max_tokens=321)
    run_call(model=HAIKU, max_tokens=321)
    assert len(client.calls) == 1
    sent = client.calls[0]
    assert sent["model"] == HAIKU
    assert sent["max_tokens"] == 321
    assert sent["output_format"] is Verdict
    blob = json.dumps(sent, default=str)
    assert kwargs["system"] in blob
    assert kwargs["messages"][0]["content"] in blob


def test_request_json_records_what_was_sent_with_the_schema_and_never_the_key(install_fake, settings):
    settings.ANTHROPIC_API_KEY = FAKE_KEY
    install_fake(reply())
    kwargs = call_kwargs()
    run_call()
    row = only_row()
    request = row.request
    assert isinstance(request, dict)
    assert request["model"] == SONNET
    assert request["max_tokens"] == 500
    values = list(flatten(request))
    assert kwargs["system"] in values
    assert kwargs["messages"][0]["content"] in values
    assert Verdict.model_json_schema() in [v for v in _all_dicts(request)]  # the schema as JSON schema
    # The API key must not appear anywhere in the row, in any column.
    for value in (
        json.dumps(request),
        row.raw_response,
        row.error,
        row.error_code,
        row.prompt_version,
        row.provider_request_id,
        json.dumps(row.parsed),
    ):
        assert FAKE_KEY not in value


def _all_dicts(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _all_dicts(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _all_dicts(v)


def test_prompt_sha256_is_a_stable_64_hex_digest(install_fake):
    install_fake(reply(), reply())
    run_call()
    run_call()
    first, second = rows()
    assert len(first.prompt_sha256) == 64
    assert set(first.prompt_sha256) <= set("0123456789abcdef")
    assert first.prompt_sha256 == second.prompt_sha256


def test_prompt_sha256_changes_with_the_system_prompt(install_fake):
    install_fake(reply(), reply())
    run_call(system="You are a careful test moderator.")
    run_call(system="You are a careful test moderator!")
    first, second = rows()
    assert first.prompt_sha256 != second.prompt_sha256


def test_prompt_sha256_changes_with_the_output_schema(install_fake):
    install_fake(reply(), reply(parsed=None))
    run_call(output_schema=Verdict)
    from moderation.errors import LLMOutputError

    with pytest.raises(LLMOutputError):
        run_call(output_schema=OtherSchema)
    first, second = rows()
    assert first.prompt_sha256 != second.prompt_sha256


def test_prompt_sha256_ignores_the_conversation_messages_and_ids(install_fake):
    install_fake(reply(), reply())
    run_call(messages=[{"role": "user", "content": "one thing"}], conversation_id=1, run_id=1, attempt=1)
    run_call(messages=[{"role": "user", "content": "something else"}], conversation_id=2, run_id=2, attempt=2)
    first, second = rows()
    assert first.prompt_sha256 == second.prompt_sha256


def test_prompt_sha256_is_the_documented_formula(install_fake):
    """sha256(system + NUL + json.dumps(schema_json, sort_keys=True)), hex digest."""
    install_fake(reply(), reply(parsed=None))
    system = "You are a careful test moderator."
    run_call(system=system, output_schema=Verdict)
    expected = hashlib.sha256((system + "\x00" + json.dumps(Verdict.model_json_schema(), sort_keys=True)).encode("utf-8")).hexdigest()
    assert only_row().prompt_sha256 == expected


def test_prompt_sha256_formula_with_a_unicode_system_prompt_and_another_schema(install_fake):
    from moderation.errors import LLMOutputError

    install_fake(reply(parsed=None))
    system = "Sei un moderatore attento \u2014 \u65e5\u672c\u8a9e"
    with pytest.raises(LLMOutputError):
        run_call(system=system, output_schema=OtherSchema)
    expected = hashlib.sha256((system + "\x00" + json.dumps(OtherSchema.model_json_schema(), sort_keys=True)).encode("utf-8")).hexdigest()
    assert only_row().prompt_sha256 == expected


def test_attempt_number_is_recorded(install_fake):
    install_fake(reply())
    run_call(attempt=2)
    assert only_row().attempt == 2


@pytest.mark.parametrize(
    "purpose, agent", [("moderation", "master"), ("moderation", "intervenor"), ("spike", "master"), ("golden", "intervenor"), ("replay", "master"), ("judge", "judge")]
)
def test_every_purpose_and_agent_is_recorded(install_fake, purpose, agent):
    install_fake(reply())
    run_call(purpose=purpose, agent=agent)
    row = only_row()
    assert (row.purpose, row.agent, row.status) == (purpose, agent, "ok")


def test_context_ids_default_to_null(install_fake):
    install_fake(reply())
    run_call()
    row = only_row()
    assert row.conversation_id is None
    assert row.run_id is None


def test_a_pending_row_exists_with_its_reservation_while_the_call_is_in_flight(install_fake):
    from moderation.models import LLMCall

    seen = {}

    def during(kwargs):
        seen["rows"] = [
            (r.status, r.purpose, r.agent, r.model, r.reserved_usd, r.cost_usd, r.finished_at) for r in LLMCall.objects.all()
        ]
        return make_msg(**SONNET_USAGE)

    install_fake(during)
    run_call()
    assert len(seen["rows"]) == 1
    status, purpose, agent, model, reserved, cost, finished_at = seen["rows"][0]
    assert (status, purpose, agent, model) == ("pending", "moderation", "master", SONNET)
    assert reserved > 0
    assert cost is None
    assert finished_at is None
    row = only_row()
    assert row.status == "ok"
    assert row.reserved_usd == reserved  # the reservation stays on the row


def test_new_rows_are_timestamped_with_the_patched_clock(install_fake, llm_ready):
    """Contract: all step 2 code reads time from moderation.clock.now()."""
    install_fake(reply())
    run_call()
    row = only_row()
    assert row.created_at == llm_ready.now()
    assert row.finished_at == llm_ready.now()


def test_ledger_spend_reflects_the_call(install_fake):
    from moderation import budget

    install_fake(reply(**SONNET_USAGE))
    run_call()
    assert budget.spend() == SONNET_COST


def test_session_budget_is_charged_the_reservation_during_the_call_and_the_real_cost_after(install_fake):
    from moderation.budget import SessionBudget
    from moderation.models import LLMCall

    session = SessionBudget(D("1.00"))
    seen = {}

    def during(kwargs):
        seen["spent"] = session.spent
        seen["reserved"] = LLMCall.objects.get().reserved_usd
        return make_msg(**SONNET_USAGE)

    install_fake(during)
    result = run_call(session=session)
    assert seen["spent"] == seen["reserved"] > 0
    assert session.spent == result.cost_usd == SONNET_COST


def test_arguments_are_keyword_only():
    from moderation import llm

    with pytest.raises(TypeError):
        llm.call("moderation", "master", SONNET, "system", [], Verdict, 100)


def test_the_client_is_called_exactly_once_per_call(install_fake):
    client = install_fake(reply(), reply())
    run_call()
    assert len(client.calls) == 1
    run_call()
    assert len(client.calls) == 2


def test_the_real_key_setting_is_never_passed_to_the_client_call(install_fake, settings):
    settings.ANTHROPIC_API_KEY = FAKE_KEY
    client = install_fake(reply())
    run_call()
    assert FAKE_KEY not in json.dumps(client.calls, default=str)


@pytest.mark.parametrize("bad", [0, -1, -500, 1.5, "500", None])
def test_max_tokens_must_be_a_positive_int_or_nothing_is_written(install_fake, bad):
    client = install_fake(reply())
    with pytest.raises(ValueError):
        run_call(max_tokens=bad)
    assert client.calls == []
    assert rows() == []


def test_temperature_on_a_model_that_rejects_it_is_a_value_error_even_with_the_kill_switch_off(install_fake, settings):
    """The value check comes first: it raises ValueError, not LLMDisabled, and writes no refusal row."""
    settings.LLM_ENABLED = False
    client = install_fake(reply())
    with pytest.raises(ValueError):
        run_call(model=SONNET, temperature=0.5)
    assert client.calls == []
    assert rows() == []


def test_temperature_on_a_model_that_rejects_it_is_a_value_error_even_without_an_api_key(install_fake, settings):
    settings.ANTHROPIC_API_KEY = ""
    install_fake(reply())
    with pytest.raises(ValueError):
        run_call(model=SONNET, temperature=0.5)
    assert rows() == []
