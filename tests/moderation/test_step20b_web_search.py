"""moderation/llm.py: `call_with_web_search()`, the guarded, real-`web_search`-enabled function added for the Step
20b spike (docs/step20b_spike_brief.md). Written from the brief's contract only, without reading llm.py's diff.

Mirrors the conventions of tests/moderation/test_moderation_gateway_refusals.py, test_moderation_gateway.py and
test_moderation_error_mapping.py: same fixtures (`llm_ready`, `tune`, `install_fake`, `forbid_atomic_calls`), same
`assert_refused`-style row checks, real `FakeProviderError`/SDK-shaped errors, `moderation.fake_llm.make_tool_message`
for a scripted success.
"""
from decimal import Decimal

import pytest
from django.db import connection, transaction
from moderation.fake_llm import FakeProviderError, make_tool_message
from moderation_testkit import FAKE_KEY, SONNET, raiser, seed_call, utc
from types import SimpleNamespace

D = Decimal


def ws_kwargs(**overrides):
    kwargs = dict(
        purpose="moderation",
        agent="master",
        model=SONNET,
        system="You are a careful test moderator.",
        messages=[{"role": "user", "content": "Hello there, this is a test message."}],
        max_tokens=500,
    )
    kwargs.update(overrides)
    return kwargs


def run_ws(**overrides):
    from moderation import llm

    return llm.call_with_web_search(**ws_kwargs(**overrides))


def rows():
    from moderation.models import LLMCall

    return list(LLMCall.objects.order_by("pk"))


def only_row():
    found = rows()
    assert len(found) == 1, found
    return found[0]


def refused_rows():
    return [r for r in rows() if r.status.startswith("refused_")]


@pytest.fixture(autouse=True)
def ready(llm_ready, tune):
    tune(
        BUDGET_PER_CONVERSATION_USD=D("1.25"),
        BUDGET_SITE_USD_PER_DAY=D("1.50"),
        BUDGET_SITE_USD_TOTAL=D("5.00"),
        BUDGET_EVAL_USD_TOTAL=D("10.00"),
        BUDGET_SPIKE_USD_TOTAL=D("0.50"),
    )
    return llm_ready


@pytest.fixture
def fake(install_fake):
    return install_fake(reply_ws(), reply_ws(), reply_ws())  # any call reaching the client consumes one


def reply_ws(**kwargs):
    """A scripted item for `client.messages.create` (mirrors moderation_testkit.reply, for `.parse`)."""
    return lambda _call_kwargs: make_tool_message(**kwargs)


def assert_refused(exc_info, exc_class, status, fake, **expected):
    """One refused_* row of `status`, no client call, exception of the right class and status -- the exact
    convention test_moderation_gateway_refusals.py uses for call()."""
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


# --- purpose validated first, same list as call() ---------------------------------------------------------------------


def test_unknown_purpose_is_a_value_error_with_no_row_and_no_call(fake):
    with pytest.raises(ValueError):
        run_ws(purpose="chitchat")
    assert fake.calls == []
    assert rows() == []


def test_purpose_is_checked_against_the_same_all_purposes_list_call_uses():
    from moderation.budget import EVAL_PURPOSES, SITE_PURPOSES
    from moderation.llm import ALL_PURPOSES

    assert ALL_PURPOSES == SITE_PURPOSES + EVAL_PURPOSES


# --- kill switch --------------------------------------------------------------------------------------------------


def test_kill_switch_off_refuses(fake, settings):
    from moderation.errors import LLMDisabled

    settings.LLM_ENABLED = False
    with pytest.raises(LLMDisabled) as excinfo:
        run_ws(conversation_id=5, run_id=6, attempt=2)
    assert_refused(
        excinfo, LLMDisabled, "refused_disabled", fake,
        purpose="moderation", agent="master", model=SONNET, conversation_id=5, run_id=6, attempt=2,
    )


def test_missing_api_key_refuses_as_disabled(fake, settings):
    from moderation.errors import LLMDisabled

    settings.ANTHROPIC_API_KEY = ""
    with pytest.raises(LLMDisabled) as excinfo:
        run_ws()
    assert_refused(excinfo, LLMDisabled, "refused_disabled", fake)


# --- breaker ----------------------------------------------------------------------------------------------------------


def test_open_breaker_refuses(fake):
    from moderation import breaker
    from moderation.errors import BreakerOpen

    breaker.trip("manual", "test")
    with pytest.raises(BreakerOpen) as excinfo:
        run_ws()
    assert_refused(excinfo, BreakerOpen, "refused_breaker", fake, purpose="moderation", model=SONNET)


# --- model -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["claude-opus-4-1", "gpt-4o", "", "claude-sonnet-5-latest"])
def test_a_model_that_is_not_allowed_is_refused(fake, model):
    from moderation.errors import ModelNotAllowed

    with pytest.raises(ModelNotAllowed) as excinfo:
        run_ws(model=model)
    assert_refused(excinfo, ModelNotAllowed, "refused_model", fake, model=model)


# --- budget --------------------------------------------------------------------------------------------------------

OLD = utc(2026, 9, 10, 9)


def test_site_total_cap_refuses(fake):
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="4.995", created_at=OLD)
    with pytest.raises(BudgetExceeded) as excinfo:
        run_ws(purpose="spike", agent="master")
    err = excinfo.value
    assert (err.limit, err.spent) == (D("5.00"), D("4.995"))
    found = refused_rows()
    assert len(found) == 1
    assert found[0].status == "refused_budget"
    assert fake.calls == []


def test_conversation_cap_refuses_only_for_that_conversation(fake):
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="1.245", conversation_id=9, created_at=OLD)
    with pytest.raises(BudgetExceeded):
        run_ws(conversation_id=9)
    assert len(refused_rows()) == 1 and fake.calls == []
    run_ws(conversation_id=10)  # another conversation is unaffected
    assert len(fake.calls) == 1


def test_session_budget_refuses(fake):
    from moderation.budget import SessionBudget
    from moderation.errors import BudgetExceeded

    with pytest.raises(BudgetExceeded) as excinfo:
        run_ws(session=SessionBudget(D("0.005")))
    assert excinfo.value.limit == D("0.005")
    assert len(refused_rows()) == 1 and fake.calls == []


def test_refused_rows_do_not_use_up_the_budget(fake):
    from moderation import budget
    from moderation.errors import BudgetExceeded

    seed_call("ok", cost="1.495", created_at=utc(2026, 9, 25, 2))
    for _ in range(3):
        with pytest.raises(BudgetExceeded):
            run_ws()
    assert len(refused_rows()) == 3
    assert budget.spend() == D("1.495")


# --- inside a transaction -------------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_call_inside_atomic_raises_and_touches_nothing(install_fake, forbid_atomic_calls, settings):
    from moderation.models import GuardState

    assert settings.LLM_FORBID_ATOMIC_CALLS is True
    client = install_fake(reply_ws())
    with pytest.raises(RuntimeError, match=r"(?i)rolled|rollback|roll back|erase"):
        with transaction.atomic():
            assert connection.in_atomic_block
            run_ws()
    assert client.calls == []
    assert rows() == []
    assert GuardState.load().consecutive_errors == 0 and GuardState.load().breaker_tripped is False


# --- success: a scripted web_search_tool_result reply ------------------------------------------------------------------


def make_tool_result_block(**kwargs):
    """A fake web_search_tool_result-shaped content block (SimpleNamespace, per the brief)."""
    defaults = dict(type="web_search_tool_result", tool_use_id="srvtoolu_test_1", content=[
        {"type": "web_search_result", "title": "Example", "url": "https://example.test", "encrypted_content": "abc"},
    ])
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_a_scripted_success_returns_a_web_search_result_with_text_and_untouched_tool_blocks(install_fake):
    from moderation.llm import WebSearchResult

    block = make_tool_result_block()
    install_fake(reply_ws(
        text="Here is what I found.", tool_results=[block],
        input_tokens=1000, output_tokens=500, cache_creation_input_tokens=2000, cache_read_input_tokens=4000,
        stop_reason="end_turn", id="msg_abc",
    ))
    result = run_ws()
    assert isinstance(result, WebSearchResult)
    assert result.text == "Here is what I found."
    assert result.tool_blocks == [block]
    assert result.tool_blocks[0] is block  # untouched, not copied or rebuilt
    assert result.stop_reason == "end_turn"
    assert result.input_tokens == 1000
    assert result.output_tokens == 500
    assert result.cache_write_tokens == 2000
    assert result.cache_read_tokens == 4000
    assert result.call_id == only_row().pk
    assert isinstance(result.latency_ms, int) and result.latency_ms >= 0
    # priced from the real usage, the same formula call() uses (Sonnet: 1000*2 + 500*10 + 2000*2.5 + 4000*0.2 micro-$)
    assert result.cost_usd == D("0.0128")


def test_a_scripted_success_finishes_the_ledger_row_ok(install_fake):
    block = make_tool_result_block()
    install_fake(reply_ws(text="Found it.", tool_results=[block], input_tokens=1000, output_tokens=500,
                           cache_creation_input_tokens=2000, cache_read_input_tokens=4000, id="msg_abc"))
    result = run_ws(prompt_version="spike-v1", conversation_id=41, run_id=17)
    row = only_row()
    assert row.status == "ok"
    assert row.purpose == "moderation"
    assert row.agent == "master"
    assert row.model == SONNET
    assert row.prompt_version == "spike-v1"
    assert row.conversation_id == 41
    assert row.run_id == 17
    assert (row.tokens_in, row.tokens_out, row.cache_write_tokens, row.cache_read_tokens) == (1000, 500, 2000, 4000)
    assert row.cost_usd == D("0.0128") == result.cost_usd
    assert row.finished_at is not None
    assert row.error == "" and not row.error_code


def test_raw_usage_is_the_real_usage_object_not_just_the_four_priced_fields(install_fake):
    """The brief: raw_usage carries whatever field(s) report web_search's own fee, unparsed, so the spike can find it."""
    install_fake(reply_ws(extra_usage={"server_tool_use": {"web_search_requests": 2}}))
    result = run_ws()
    assert getattr(result.raw_usage, "server_tool_use") == {"web_search_requests": 2}
    assert result.raw_usage.input_tokens == 100  # the ordinary fields are still there too


def test_no_output_schema_or_parse_is_used(install_fake):
    """The brief: this call is NOT `.parse()` and has no `output_schema` -- it must reach `.create()` on the fake,
    never `.parse()`. `client.call_methods` (moderation/fake_llm.py) records which method was actually invoked, in
    the same order/length as `client.calls`, so this is a direct check of the method used, not just of its kwargs
    (kwargs alone cannot discriminate `.create()` from `.parse()` here: `create_kwargs` never has `output_format`
    either way, and a `make_tool_message` reply has `parsed_output=None`, so `.parse()`'s dict-validation branch
    never triggers and it would behave identically to `.create()` for this scripted reply -- confirmed by mutating
    `call_with_web_search` locally to call `.parse()` instead and seeing this assertion, and only this assertion,
    catch it)."""
    client = install_fake(reply_ws())
    run_ws()
    assert client.call_methods == ["create"]
    assert len(client.calls) == 1
    sent = client.calls[0]
    assert "output_format" not in sent
    assert "tools" in sent
    assert sent["tools"][0]["type"] == "web_search_20260209"
    assert sent["tools"][0]["name"] == "web_search"


def test_max_uses_is_sent_and_defaults_to_three(install_fake):
    client = install_fake(reply_ws(), reply_ws())
    run_ws()
    run_ws(max_uses=7)
    assert client.calls[0]["tools"][0]["max_uses"] == 3
    assert client.calls[1]["tools"][0]["max_uses"] == 7


# --- provider error --------------------------------------------------------------------------------------------------


def test_a_provider_error_finishes_the_row_error_records_the_breaker_and_raises_llmapierror(install_fake):
    from moderation import breaker
    from moderation.errors import LLMAPIError
    from moderation_testkit import sdk_status_error

    exc = sdk_status_error("InternalServerError", 500, "api_error", "An unexpected error has occurred.")
    install_fake(raiser(exc))
    with pytest.raises(LLMAPIError) as excinfo:
        run_ws()
    err = excinfo.value
    assert err.status_code == 500
    assert err.error_type == "api_error"
    row = only_row()
    assert err.call_id == row.pk
    assert row.status == "error"
    assert "An unexpected error has occurred." in row.error
    assert breaker.is_open() is False  # one counted (soft) error, same as call()'s equivalent path
    from moderation.models import GuardState

    assert GuardState.load().consecutive_errors == 1


def test_a_fake_provider_error_also_finishes_the_row_error(install_fake):
    """FakeProviderError (moderation.fake_llm), not just a real SDK exception class."""
    from moderation.errors import LLMAPIError

    install_fake(raiser(FakeProviderError(500, "api_error", "boom")))
    with pytest.raises(LLMAPIError) as excinfo:
        run_ws()
    assert excinfo.value.status_code == 500
    row = only_row()
    assert row.status == "error"
    assert "boom" in row.error


# --- ledger auditability: tools list logged, prompt hash reacts to it ------------------------------------------------


def test_the_ledger_request_field_includes_the_tools_that_were_actually_sent(install_fake):
    client = install_fake(reply_ws())
    run_ws(max_uses=5)
    row = only_row()
    assert row.request["tools"] == client.calls[0]["tools"]
    assert row.request["tools"][0] == {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}


def test_prompt_sha256_changes_with_max_uses_because_the_tools_shape_differs(install_fake):
    install_fake(reply_ws(), reply_ws())
    row1 = only_row_after(run_ws(max_uses=3))
    row2 = only_row_after(run_ws(max_uses=5))
    assert row1.prompt_sha256 != row2.prompt_sha256


def only_row_after(_result):
    return rows()[-1]


def test_prompt_sha256_is_the_same_for_two_calls_with_identical_system_and_tools(install_fake):
    install_fake(reply_ws(), reply_ws())
    run_ws()
    run_ws()
    hashes = {r.prompt_sha256 for r in rows()}
    assert len(hashes) == 1


def test_the_api_key_is_never_written_to_the_row(install_fake, settings):
    from moderation.errors import LLMDisabled

    settings.LLM_ENABLED = False
    settings.ANTHROPIC_API_KEY = FAKE_KEY
    install_fake(reply_ws())
    with pytest.raises(LLMDisabled):
        run_ws()
    row = only_row()
    assert FAKE_KEY not in f"{row.error}{row.request}{row.raw_response}"
