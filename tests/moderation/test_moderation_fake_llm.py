"""moderation/fake_llm.py: the scripted stand-in for the Anthropic client (the only client tests may use)."""
import pytest
from moderation_testkit import Verdict


def parse(client, **overrides):
    kwargs = dict(model="claude-sonnet-5", max_tokens=100, system="s", messages=[{"role": "user", "content": "hi"}], output_format=Verdict)
    kwargs.update(overrides)
    return client.messages.parse(**kwargs)


def test_a_dict_item_is_validated_into_the_schema_and_returned():
    from moderation.fake_llm import FakeLLM

    message = parse(FakeLLM([{"label": "ok", "score": 2}]))
    assert isinstance(message.parsed_output, Verdict)
    assert (message.parsed_output.label, message.parsed_output.score) == ("ok", 2)


def test_the_reply_has_the_shape_the_gateway_reads():
    from moderation.fake_llm import FakeLLM

    message = parse(FakeLLM([{"label": "ok", "score": 2}]))
    assert isinstance(message.stop_reason, str) and message.stop_reason
    assert isinstance(message.id, str) and message.id
    for name in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        assert isinstance(getattr(message.usage, name), int), name


def test_items_are_used_in_order():
    from moderation.fake_llm import FakeLLM

    client = FakeLLM([{"label": "first", "score": 1}, {"label": "second", "score": 2}])
    assert parse(client).parsed_output.label == "first"
    assert parse(client).parsed_output.label == "second"


def test_a_provider_error_item_is_raised():
    from moderation.fake_llm import FakeLLM, FakeProviderError

    boom = FakeProviderError(status_code=529, error_type="overloaded_error", message="Overloaded", error_code="busy")
    with pytest.raises(FakeProviderError) as excinfo:
        parse(FakeLLM([boom]))
    assert excinfo.value is boom


def test_provider_error_attributes_and_default_code():
    from moderation.fake_llm import FakeProviderError

    err = FakeProviderError(500, "api_error", "kaboom")
    assert isinstance(err, Exception)
    assert (err.status_code, err.error_type, err.message, err.error_code) == (500, "api_error", "kaboom", None)
    assert "kaboom" in str(err)
    assert FakeProviderError(429, "rate_limit_error", "slow", error_code="x").error_code == "x"


def test_a_callable_item_receives_the_call_kwargs_and_returns_the_message():
    from moderation.fake_llm import FakeLLM
    from moderation_testkit import make_msg

    seen = {}

    def item(kwargs):
        seen.update(kwargs)
        return make_msg({"label": "from callable", "score": 9}, input_tokens=7)

    message = parse(FakeLLM([item]), max_tokens=123)
    assert seen["max_tokens"] == 123
    assert message.parsed_output.label == "from callable"
    assert message.usage.input_tokens == 7


def test_a_callable_item_may_raise():
    from moderation.fake_llm import FakeLLM

    def item(kwargs):
        raise RuntimeError("scripted failure")

    with pytest.raises(RuntimeError, match="scripted failure"):
        parse(FakeLLM([item]))


def test_every_call_is_recorded_in_calls():
    from moderation.fake_llm import FakeLLM

    client = FakeLLM([{"label": "a", "score": 1}, {"label": "b", "score": 2}])
    assert client.calls == []
    parse(client, max_tokens=11)
    parse(client, max_tokens=22)
    assert [c["max_tokens"] for c in client.calls] == [11, 22]
    assert client.calls[0]["model"] == "claude-sonnet-5"


def test_a_call_that_raises_is_still_recorded():
    from moderation.fake_llm import FakeLLM, FakeProviderError

    client = FakeLLM([FakeProviderError(500, "api_error", "x")])
    with pytest.raises(FakeProviderError):
        parse(client)
    assert len(client.calls) == 1


def test_running_out_of_script_is_an_error_not_a_made_up_reply():
    from moderation.fake_llm import FakeLLM

    client = FakeLLM([])
    with pytest.raises(Exception):
        parse(client)


def test_a_dict_that_does_not_fit_the_schema_never_yields_a_parsed_verdict():
    """Contract says only "validated into the schema". Either raising a pydantic ValidationError or returning a
    message with parsed_output None (a billable "invalid output" reply) is acceptable; a Verdict is not."""
    from moderation.fake_llm import FakeLLM
    from pydantic import ValidationError

    try:
        message = parse(FakeLLM([{"label": "missing score"}]))
    except ValidationError:
        return
    assert message.parsed_output is None


def test_make_message_builds_a_reply_with_usage():
    from moderation.fake_llm import make_message

    message = make_message(Verdict(label="x", score=1), input_tokens=123)
    assert message.parsed_output == Verdict(label="x", score=1)
    assert message.usage.input_tokens == 123
    assert isinstance(message.stop_reason, str)
    assert isinstance(message.id, str)
