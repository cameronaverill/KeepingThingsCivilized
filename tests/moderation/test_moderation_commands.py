"""manage.py budget and manage.py reset_breaker (called in-process with call_command)."""
import re
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from moderation_testkit import HAIKU, SONNET, seed_call, utc

D = Decimal


@pytest.fixture(autouse=True)
def caps(tune, frozen_clock):
    tune(
        BUDGET_PER_CONVERSATION_USD=D("1.25"),
        BUDGET_SITE_USD_PER_DAY=D("1.50"),
        BUDGET_SITE_USD_TOTAL=D("5.00"),
        BUDGET_EVAL_USD_TOTAL=D("10.00"),
        PENDING_CALL_STALE_MINUTES=15,
        MASTER_MODEL=SONNET,
        INTERVENOR_MODEL=SONNET,
        SPIKE_MODEL=HAIKU,
        TRANSCRIPT_MAX_MESSAGES=4,
        MAX_MESSAGE_CHARS=1000,
        SYSTEM_PROMPT_TOKENS_ESTIMATE=2000,
        TOKEN_ESTIMATE_CHARS_PER_TOKEN=2.5,
        TOKEN_ESTIMATE_OVERHEAD_TOKENS=0,
        MASTER_MAX_TOKENS=1500,
        INTERVENOR_MAX_TOKENS=1000,
    )
    return frozen_clock


OLD = utc(2026, 9, 10, 9)
TODAY = utc(2026, 9, 25, 9)


def run(name, *args):
    out, err = StringIO(), StringIO()
    call_command(name, *args, stdout=out, stderr=err)
    return out.getvalue()


def numbers(text):
    return {D(m) for m in re.findall(r"\d+(?:\.\d+)?", text)}


def line_with(text, needle):
    matches = [line for line in text.splitlines() if needle.lower() in line.lower()]
    assert matches, f"no line mentioning {needle!r} in:\n{text}"
    return "\n".join(matches)


# --- budget: static content ---------------------------------------------------------------------------------------


def test_budget_prints_the_path_of_the_tunables_file():
    assert "config/tunables.py" in run("budget")


def test_budget_prints_the_four_caps():
    found = numbers(run("budget"))
    for cap in ("1.25", "1.50", "5.00", "10.00"):
        assert D(cap) in found, cap


def test_budget_shows_the_caps_actually_configured(tune):
    tune(
        BUDGET_PER_CONVERSATION_USD=D("0.75"),
        BUDGET_SITE_USD_PER_DAY=D("0.90"),
        BUDGET_SITE_USD_TOTAL=D("3.33"),
        BUDGET_EVAL_USD_TOTAL=D("7.77"),
    )
    found = numbers(run("budget"))
    assert {D("0.75"), D("0.90"), D("3.33"), D("7.77")} <= found


def test_budget_with_an_empty_ledger_shows_full_remaining_amounts():
    found = numbers(run("budget"))
    # Nothing spent, so remaining site total is 5.00, remaining today 1.50, remaining eval 10.00.
    assert {D("5.00"), D("1.50"), D("10.00"), D("0")} <= found


def test_budget_prints_the_worst_case_cost_of_one_run_for_both_models():
    """Sonnet: 3600 input tokens * 2.5 + 1500 * 10 = 0.024 (Master), 0.019 (Intervenor), 0.043 total.
    Haiku: 3600 * 1.25 + 1500 * 5 = 0.012, 0.0095, 0.0215 total."""
    text = run("budget")
    found = numbers(text)
    assert SONNET in text and HAIKU in text
    assert {D("0.043"), D("0.0215")} <= found
    assert {D("0.024"), D("0.019"), D("0.012"), D("0.0095")} <= found


def test_budget_reports_the_kill_switch_state(settings):
    settings.LLM_ENABLED = False
    off = line_with(run("budget"), "kill switch")
    assert re.search(r"\b(off|disabled|false)\b", off, re.I)
    settings.LLM_ENABLED = True
    on = line_with(run("budget"), "kill switch")
    assert re.search(r"\b(on|enabled|true)\b", on, re.I)
    assert on != off


def test_budget_reports_the_breaker_state():
    from moderation import breaker

    closed_text = run("budget")
    closed = line_with(closed_text, "breaker")
    assert re.search(r"closed|not tripped|ok", closed, re.I)
    breaker.trip("spend_limit", "detail-marker-xyz")
    tripped_text = run("budget")
    tripped = line_with(tripped_text, "breaker")
    assert re.search(r"tripped|open", tripped, re.I)
    assert not re.search(r"closed", tripped, re.I)
    assert "spend_limit" in tripped_text
    assert "detail-marker-xyz" in tripped_text


def test_budget_is_read_only_and_exits_normally():
    from moderation.models import LLMCall

    seed_call("ok", cost="0.30", created_at=TODAY)
    seed_call("pending", reserved="0.20", created_at=utc(2026, 9, 25, 8))  # stale
    before = list(LLMCall.objects.order_by("pk").values())
    run("budget")  # would raise SystemExit / CommandError on a non-zero exit
    assert list(LLMCall.objects.order_by("pk").values()) == before


# --- budget: it changes once the ledger has rows ------------------------------------------------------------------


def test_budget_shows_spend_and_remaining_after_rows_exist():
    seed_call("ok", purpose="moderation", cost="0.30", conversation_id=42, created_at=TODAY)
    seed_call("ok", purpose="spike", cost="0.40", created_at=OLD)
    seed_call("ok", purpose="judge", cost="2.00", created_at=OLD)
    seed_call("refused_budget", reserved="9.99", created_at=TODAY)  # refused rows are not spend
    before_text = run("budget")
    found = numbers(before_text)
    assert D("0.70") in found  # site total: 0.30 + 0.40
    assert D("0.30") in found  # today
    assert D("2.00") in found  # eval
    assert D("4.30") in found  # site remaining: 5.00 - 0.70
    assert D("1.20") in found  # today remaining: 1.50 - 0.30
    assert D("8.00") in found  # eval remaining: 10.00 - 2.00
    assert D("9.99") not in found


def test_budget_output_differs_before_and_after_spending():
    empty = run("budget")
    seed_call("ok", cost="0.33", created_at=TODAY)
    assert run("budget") != empty


def test_budget_counts_pending_rows_as_spend():
    seed_call("pending", reserved="0.37", created_at=utc(2026, 9, 25, 11, 55))  # fresh, not stale
    assert D("0.37") in numbers(run("budget"))


def test_budget_lists_the_top_five_conversations_by_spend():
    for conversation_id, cost in [(1, "0.11"), (2, "0.22"), (3, "0.33"), (4, "0.44"), (5, "0.55"), (6, "0.66")]:
        seed_call("ok", cost=cost, conversation_id=conversation_id, created_at=OLD)
    found = numbers(run("budget"))
    for cost in ("0.22", "0.33", "0.44", "0.55", "0.66"):
        assert D(cost) in found, cost
    assert D("0.11") not in found  # the sixth-biggest is left out


def test_budget_shows_remaining_room_for_a_conversation():
    seed_call("ok", cost="0.30", conversation_id=42, created_at=OLD)
    assert D("0.95") in numbers(run("budget"))  # 1.25 - 0.30


def test_budget_reports_stale_pending_calls_only():
    seed_call("ok", cost="0.30", created_at=TODAY)
    seed_call("pending", reserved="0.13", created_at=utc(2026, 9, 25, 11, 55))  # 5 minutes old: still in flight
    fresh_only = numbers(run("budget"))
    seed_call("pending", reserved="0.37", created_at=utc(2026, 9, 25, 11, 40))  # 20 minutes old: stale
    with_stale = numbers(run("budget"))
    assert D("0.37") in with_stale
    assert D("0.37") not in fresh_only
    assert D("0.13") not in with_stale  # only the stale one is listed on its own


def test_budget_stale_threshold_is_the_tunable(tune):
    seed_call("pending", reserved="0.37", created_at=utc(2026, 9, 25, 11, 40))  # 20 minutes old
    assert D("0.37") in numbers(run("budget"))
    tune(PENDING_CALL_STALE_MINUTES=30)
    seed_call("ok", cost="0.30", created_at=TODAY)
    text = run("budget")
    assert D("0.37") not in numbers(text)


def test_budget_reports_calls_that_cost_more_than_their_reservation():
    seed_call("ok", cost="0.30", created_at=TODAY)
    seed_call("ok", cost="0.0123", reserved="0.0789", created_at=TODAY)  # under its reservation: not reported
    quiet = numbers(run("budget"))
    assert D("0.0123") not in quiet and D("0.0789") not in quiet
    seed_call("ok", cost="0.0456", reserved="0.0321", created_at=TODAY)  # over its reservation: reported
    loud = numbers(run("budget"))
    assert D("0.0456") in loud
    assert D("0.0321") in loud


# --- reset_breaker ------------------------------------------------------------------------------------------------


def test_reset_breaker_clears_a_tripped_breaker_and_says_what_was_tripped():
    from moderation import breaker
    from moderation.models import GuardState

    breaker.trip("consecutive_errors", "five 500s in a row")
    text = run("reset_breaker")
    assert breaker.is_open() is False
    state = GuardState.objects.get(pk=1)
    assert state.breaker_tripped is False and state.consecutive_errors == 0
    assert "consecutive_errors" in text
    assert "five 500s in a row" in text


def test_reset_breaker_reminds_you_to_confirm_the_console_limit_after_a_spend_limit_trip():
    from moderation import breaker

    breaker.trip("spend_limit", "You have reached your specified API usage limits")
    text = run("reset_breaker").lower()
    assert "spend_limit" in text
    assert "console" in text
    assert "spend limit" in text
    assert breaker.is_open() is False


@pytest.mark.parametrize("reason", ["manual", "consecutive_errors"])
def test_reset_breaker_does_not_give_the_console_warning_for_other_reasons(reason):
    from moderation import breaker

    breaker.trip(reason, "x")
    assert "console" not in run("reset_breaker").lower()


def test_reset_breaker_when_nothing_is_tripped_is_harmless_and_clears_the_error_count():
    from moderation import breaker
    from moderation.models import GuardState

    breaker.record_error(status_code=500, error_type="api_error", error_code=None, message="x")
    breaker.record_error(status_code=500, error_type="api_error", error_code=None, message="x")
    text = run("reset_breaker")
    assert text.strip()
    assert breaker.is_open() is False
    assert GuardState.objects.get(pk=1).consecutive_errors == 0


def test_after_reset_breaker_the_gateway_works_again(install_fake, settings):
    from moderation import breaker
    from moderation.errors import BreakerOpen
    from moderation_testkit import reply, run_call

    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = "test-key"
    client = install_fake(reply())
    breaker.trip("manual")
    with pytest.raises(BreakerOpen):
        run_call()
    run("reset_breaker")
    run_call()
    assert len(client.calls) == 1


def test_the_commands_live_in_the_moderation_app():
    from django.core.management import get_commands

    commands = get_commands()
    assert commands.get("budget") == "moderation"
    assert commands.get("reset_breaker") == "moderation"
