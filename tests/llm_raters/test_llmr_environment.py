"""The guarantees the fixtures of this folder give (see conftest.py): no real client, no network, a scripted FakeLLM as the only
client. Non-vacuity of every "no call was made" assertion elsewhere in this folder."""
import socket

import llmr_kit as kit
import pytest


def test_the_real_client_builder_fails_the_test_when_called():
    from moderation import llm

    with pytest.raises(AssertionError, match="real Anthropic client"):
        llm._build_real_client()


def test_a_client_that_was_never_installed_cannot_be_built_by_a_rating_call():
    rater = kit.make_rater("no-client")
    _, message = kit.single("Rent control always lowers rents.")
    with pytest.raises(AssertionError, match="real Anthropic client"):
        kit.rate(rater, message)


def test_a_socket_cannot_connect():
    with pytest.raises(AssertionError, match="network connection"):
        socket.socket().connect(("127.0.0.1", 9))


def test_an_unscripted_call_to_the_fake_fails_loudly(fake):
    rater = kit.make_rater("unscripted")
    _, message = kit.single("Rent control always lowers rents.")
    fake()
    with pytest.raises(AssertionError, match="script is exhausted"):
        kit.rate(rater, message)


def test_the_fake_is_the_client_a_call_uses(fake):
    rater = kit.make_rater("scripted")
    _, message = kit.single("Rent control always lowers rents.")
    client = fake(kit.nothing())
    kit.rate(rater, message)
    assert len(client.calls) == 1


def test_the_evaluation_budget_is_roomy_and_the_kill_switch_is_on_with_a_dummy_key(settings):
    assert (settings.LLM_ENABLED, settings.ANTHROPIC_API_KEY, settings.BUDGET_EVAL_USD_TOTAL > 10) == (True, kit.DUMMY_KEY, True)
