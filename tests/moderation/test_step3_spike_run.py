"""manage.py spike, a real (fake-client) run: what is sent, in what order, and how failures and limits are handled."""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from step3_testkit import (
    HAIKU,
    D,
    Scripted,
    call_agent,
    call_text,
    find_pair,
    flatten,
    benign_single,
    golden,  # noqa: F401
    parse_rendered,
    real_results_untouched,  # noqa: F401
    run_spike,
    spike_env,  # noqa: F401
)


@pytest.fixture
def env(spike_env, golden, install_fake, tmp_path):  # noqa: F811
    scripted = Scripted(golden)
    client = scripted.install(install_fake)
    left, right = find_pair(golden, "factual_accuracy")
    return SimpleNamespace(
        golden=golden, scripted=scripted, client=client, out=str(tmp_path / "results"), tmp=tmp_path,
        left=left["id"], right=right["id"], benign=benign_single(golden)["id"], install_fake=install_fake,
    )


def spike_rows():
    from moderation.models import LLMCall

    return LLMCall.objects.filter(purpose="spike")


def go(env, *extra, ids=None, max_usd="1.00"):
    args = ["--max-usd", max_usd, "--out", env.out]
    if ids:
        args += ["--only", *ids]
    return run_spike(*args, *extra)


def clean(result):
    """A run that ends normally or with a CommandError (a non-zero exit) but never with a stray exception."""
    assert result.exc is None or "Unknown command" not in str(result.exc)


# --- what is sent ---------------------------------------------------------------------------------------------------

def test_master_then_intervenor_for_each_transcript(env):
    result = go(env, ids=[env.left, env.right, env.benign])
    assert result.exc is None
    for tid in (env.left, env.right, env.benign):
        assert env.scripted.calls_for(tid) == ["master", "intervenor"], tid
    assert len(env.client.calls) == 6


def test_a_transcript_with_no_issues_still_gets_an_intervenor_call(env):
    """The spike wants to see whether the Intervenor abstains, so it is called even when the Master found nothing."""
    go(env, ids=[env.benign])
    assert env.scripted.calls_for(env.benign) == ["master", "intervenor"]


def test_a_full_run_covers_every_transcript_once(env):
    result = go(env)
    assert result.exc is None
    assert len(env.client.calls) == 2 * len(env.golden)
    assert {tid for tid, _a in env.scripted.seen} == set(env.golden)
    for tid in env.golden:
        assert env.scripted.calls_for(tid) == ["master", "intervenor"], tid


def test_ledger_rows_are_agent_labelled_versioned_and_use_the_configured_max_tokens(env, settings):
    from moderation.prompting import load_prompt

    go(env, ids=[env.left])
    master_row, intervenor_row = spike_rows().order_by("pk")
    assert (master_row.agent, intervenor_row.agent) == ("master", "intervenor")
    assert master_row.status == intervenor_row.status == "ok"
    master_prompt, intervenor_prompt = load_prompt("master_v1"), load_prompt("intervenor_v1")
    # the recorded version is the prompt's version ("v1") or its full name ("master_v1"): either identifies the file
    assert master_row.prompt_version in (master_prompt.version, master_prompt.name)
    assert intervenor_row.prompt_version in (intervenor_prompt.version, intervenor_prompt.name)
    assert master_row.prompt_version != "" and intervenor_row.prompt_version != ""
    assert master_row.max_tokens == settings.MASTER_MAX_TOKENS
    assert intervenor_row.max_tokens == settings.INTERVENOR_MAX_TOKENS
    assert len(master_row.prompt_sha256) == 64 and len(intervenor_row.prompt_sha256) == 64
    assert master_row.prompt_sha256 != intervenor_row.prompt_sha256


def test_the_output_schemas_and_system_prompts_are_the_step3_ones(env):
    from moderation.prompting import load_prompt
    from moderation.schemas import IntervenorOutput, MasterOutput

    go(env, ids=[env.left])
    master_call, intervenor_call = env.client.calls
    assert master_call["output_format"] is MasterOutput
    assert intervenor_call["output_format"] is IntervenorOutput
    assert load_prompt("master_v1").text in flatten(master_call["system"])
    assert load_prompt("intervenor_v1").text in flatten(intervenor_call["system"])
    assert load_prompt("intervenor_v1").text not in flatten(master_call["system"])


def test_the_transcript_reaches_both_agents_as_rendered_escaped_data(env):
    t = env.golden[env.left]
    go(env, ids=[env.left])
    expected = [(str(m["seq"]), m["author"], m["text"].strip()) for m in t["messages"] if m["seq"] <= t["trigger_seq"]]
    for call in env.client.calls:
        parsed = parse_rendered(call_text(call))
        assert [(a["id"], a["participant"], text.strip()) for a, text in parsed] == expected, call_agent(call)


def test_the_intervenor_input_includes_the_masters_output(env):
    go(env, ids=[env.left])
    intervenor_input = env.scripted.contents[(env.left, "intervenor")]
    assert f"explanation-{env.left}-1" in intervenor_input
    # (issue 3 quotes text that is not in the message, so it is not a "valid issue"; whether the Intervenor is shown it
    # anyway is left open, see the report)
    assert "possible_factual_error" in intervenor_input
    master_input = env.scripted.contents[(env.left, "master")]
    assert f"explanation-{env.left}-1" not in master_input


def test_nothing_that_identifies_a_user_reaches_the_model_the_ledger_or_the_results(env):
    import json
    from pathlib import Path

    from django.contrib.auth import get_user_model

    get_user_model().objects.create_user(username="zed_unique_name", email="zed.unique@example.org", password="pw" * 12)
    go(env, ids=[env.left, env.right])
    blob = json.dumps([[c["model"], flatten(c["system"]), call_text(c)] for c in env.client.calls])
    blob += json.dumps([r.request for r in spike_rows()], default=str)
    blob += "".join(p.read_text(encoding="utf-8") for p in Path(env.out).rglob("*") if p.is_file())
    assert "zed_unique_name" not in blob and "zed.unique@example.org" not in blob


def test_no_temperature_or_thinking_options_are_invented(env):
    go(env, ids=[env.left])
    for call in env.client.calls:
        assert call.get("thinking") == {"type": "disabled"}
        assert "temperature" not in call and "temperature" not in call.get("extra_body", {})


# --- total cost ----------------------------------------------------------------------------------------------------

def test_the_printed_total_cost_equals_the_ledger_sum_for_spike(env):
    from step3_testkit import decimals_in

    from moderation import budget

    result = go(env, ids=[env.left, env.right, env.benign])
    total = budget.spend(purposes=("spike",))
    assert total > 0
    printed = decimals_in(result.out)
    assert total in printed, f"the ledger says {total}; the output shows {printed}"
    expected_per_call = {D("0.002839"), D("0.004505")}
    assert {r.cost_usd for r in spike_rows()} == expected_per_call
    assert total == 3 * sum(expected_per_call)


def test_the_printed_total_ignores_other_purposes(env):
    from step3_testkit import decimals_in

    from moderation import budget
    from moderation_testkit import seed_call

    seed_call(purpose="moderation", cost="0.123456")
    seed_call(purpose="golden", cost="0.111111")
    result = go(env, ids=[env.left])
    printed = decimals_in(result.out)
    assert budget.spend(purposes=("spike",)) in printed
    assert D("0.123456") not in printed and D("0.111111") not in printed


# --- per-transcript failures do not stop the run --------------------------------------------------------------------

FAILURES = [
    ("master", "provider"),
    ("master", "invalid"),
    ("master", "truncated"),
    ("intervenor", "provider"),
    ("intervenor", "invalid"),
    ("intervenor", "truncated"),
]


@pytest.mark.parametrize("agent, mode", FAILURES)
def test_a_failure_on_one_transcript_does_not_stop_the_others_and_is_reported(env, agent, mode):
    env.scripted.fail[(env.left, agent)] = mode
    result = go(env, ids=[env.left, env.right, env.benign])
    assert result.exc is None or isinstance(result.exc, Exception)
    for tid in (env.right, env.benign):
        assert env.scripted.calls_for(tid) == ["master", "intervenor"], f"{tid} was not fully processed"
    expected = ["master"] if agent == "master" else ["master", "intervenor"]
    assert env.scripted.calls_for(env.left) == expected
    report = (list(__import__("pathlib").Path(env.out).rglob("report.md")) or [None])[0]
    assert report is not None, "a report is still written"
    text = result.text + "\n" + report.read_text(encoding="utf-8")
    line = "\n".join(l for l in text.splitlines() if env.left in l).lower()
    assert any(word in line for word in ("fail", "error", "skipped", "refus", "invalid", "truncat")), line


def test_failed_calls_are_logged_in_the_ledger_as_spike_calls(env):
    env.scripted.fail[(env.left, "master")] = "provider"
    go(env, ids=[env.left, env.right])
    statuses = [(r.agent, r.status) for r in spike_rows().order_by("pk")]
    assert ("master", "error") in statuses
    assert statuses.count(("master", "ok")) == 1 and statuses.count(("intervenor", "ok")) == 1


def test_every_transcript_failing_still_ends_cleanly(env):
    for tid in (env.left, env.right):
        env.scripted.fail[(tid, "master")] = "provider"
    result = go(env, ids=[env.left, env.right])
    assert result.exc is None or type(result.exc).__name__ == "CommandError"
    assert env.scripted.calls_for(env.left) == ["master"] and env.scripted.calls_for(env.right) == ["master"]


@pytest.mark.parametrize("agent", ["master", "intervenor"])
@pytest.mark.parametrize("mode", ["provider", "connection", "validation", "truncated", "invalid"])
def test_the_printed_total_still_equals_the_ledger_when_a_transcript_fails(env, agent, mode):
    """Failed calls are billed differently (nothing for an HTTP error, the whole reservation for a dropped connection or
    unparseable JSON, the real usage for a truncated answer); the printed total must follow the ledger in every case."""
    from step3_testkit import decimals_in

    from moderation import budget

    env.scripted.fail[(env.left, agent)] = mode
    result = go(env, ids=[env.left, env.right])
    total = budget.spend(purposes=("spike",))
    assert total in decimals_in(result.out), f"ledger {total}; printed {decimals_in(result.out)}"


def test_the_cost_of_a_failed_output_is_still_counted_in_the_total(env):
    """An unusable answer (truncated) was billed by the provider: the ledger row is ok with a cost, and it is in the total."""
    from moderation import budget

    env.scripted.fail[(env.left, "master")] = "truncated"
    go(env, ids=[env.left])
    assert budget.spend(purposes=("spike",)) >= D("0.002839")


# --- the session limit (--max-usd) -----------------------------------------------------------------------------------

def test_a_tiny_max_usd_refuses_before_any_call_and_ends_cleanly(env):
    result = go(env, ids=[env.left, env.right], max_usd="0.0001")
    assert result.exc is None or type(result.exc).__name__ == "CommandError"
    assert env.client.calls == []
    assert spike_rows().exclude(status="refused_budget").count() == 0
    assert spike_rows().filter(status="refused_budget").count() >= 1
    assert "session" in spike_rows().filter(status="refused_budget").first().error.lower()
    text = result.text.lower()
    assert "budget" in text or "limit" in text or "max-usd" in text


def test_the_session_limit_stops_the_run_once_it_is_used_up(env):
    """Calibrate: one transcript at a large scripted cost, read the reservations the guard made; then run again with a
    limit that covers exactly that transcript's real cost plus half a reservation, so the next call cannot be reserved."""
    from moderation.models import LLMCall

    big = Scripted(env.golden, master_usage=(20000, 2000), intervenor_usage=(20000, 2000))
    env.install_fake(*([big] * 50))
    first = go(env, ids=[env.left], max_usd="1.00")
    assert first.exc is None
    rows = list(spike_rows())
    assert len(rows) == 2
    cost_per_call = D("0.03")
    assert {r.cost_usd for r in rows} == {cost_per_call}
    smallest_reservation = min(r.reserved_usd for r in rows)
    LLMCall.objects.all().delete()
    big.seen.clear()

    limit = 2 * cost_per_call + smallest_reservation / 2
    result = go(env, ids=[env.left, env.right], max_usd=str(limit))
    assert result.exc is None or type(result.exc).__name__ == "CommandError"
    assert len(spike_rows().filter(status="ok")) == 2, "exactly one transcript (two calls) fits"
    done = {t for t, _a in big.seen}
    assert len(done) == 1 and big.calls_for(next(iter(done))) == ["master", "intervenor"]
    refused = spike_rows().filter(status="refused_budget")
    assert refused.count() >= 1
    assert all("session" in r.error.lower() for r in refused)
    other = ({env.left, env.right} - done).pop()
    assert other in result.text, "the transcript that could not run is reported by id"


def test_max_usd_larger_than_the_need_does_not_stop_anything(env):
    result = go(env, ids=[env.left, env.right], max_usd="50")
    assert result.exc is None
    assert spike_rows().filter(status="refused_budget").count() == 0


def test_issues_that_cite_a_missing_message_or_an_empty_quote_do_not_crash_the_run(env):
    env.scripted.weird.add(env.left)
    result = go(env, ids=[env.left, env.right])
    assert result.exc is None, result.text
    assert env.scripted.calls_for(env.right) == ["master", "intervenor"]
    assert env.scripted.calls_for(env.left)[:1] == ["master"]
    from step3_testkit import find_report

    text = find_report(env.out).read_text(encoding="utf-8")
    assert f"explanation-{env.left}-1" in text, "the bad issue is still shown in the report, not silently dropped"
