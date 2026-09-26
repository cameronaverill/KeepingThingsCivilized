"""manage.py spike: arguments, --dry-run, and the switches that gate a real run (brief section 3). FakeLLM only."""
from types import SimpleNamespace

import pytest
from step3_testkit import (
    HAIKU,
    SONNET,
    D,
    Scripted,
    dollars_in,
    find_pair,
    golden,  # noqa: F401
    integers_in,
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
        golden=golden, scripted=scripted, client=client, out=str(tmp_path / "results"), tmp=tmp_path, left=left["id"], right=right["id"]
    )


def ledger():
    from moderation.models import LLMCall

    return LLMCall.objects.all()


def nothing_called(env):
    assert env.client.calls == []
    assert ledger().count() == 0


# --- required options -----------------------------------------------------------------------------------------------

def test_it_refuses_without_max_usd(env):
    result = run_spike("--out", env.out)
    assert result.exc is not None
    assert "max-usd" in str(result.exc).lower().replace("_", "-")
    nothing_called(env)


def test_it_refuses_without_max_usd_even_for_a_dry_run(env):
    result = run_spike("--dry-run", "--out", env.out)
    assert result.exc is not None
    nothing_called(env)


@pytest.mark.parametrize("bad", ["0", "-1", "-0.01", "abc", ""])
def test_it_refuses_a_nonsensical_max_usd(env, bad):
    result = run_spike("--max-usd", bad, "--out", env.out, "--only", env.left)
    assert result.exc is not None
    nothing_called(env)


# --- --dry-run ------------------------------------------------------------------------------------------------------

def test_dry_run_makes_no_client_call_and_writes_nothing_to_the_ledger(env):
    result = run_spike("--max-usd", "1.00", "--dry-run", "--out", env.out)
    assert result.exc is None
    nothing_called(env)


def test_dry_run_writes_no_result_files(env):
    from pathlib import Path

    run_spike("--max-usd", "1.00", "--dry-run", "--out", env.out)
    written = [p for p in Path(env.out).rglob("*") if p.is_file()] if Path(env.out).exists() else []
    assert written == []


def test_dry_run_prints_the_number_of_calls_two_per_transcript(env):
    result = run_spike("--max-usd", "1.00", "--dry-run", "--out", env.out)
    assert 2 * len(env.golden) in integers_in(result.out), result.out


def test_dry_run_with_only_prints_the_smaller_count(env):
    result = run_spike("--max-usd", "1.00", "--dry-run", "--out", env.out, "--only", env.left, env.right)
    assert 4 in integers_in(result.out), result.out
    assert 2 * len(env.golden) not in integers_in(result.out)
    nothing_called(env)


def test_dry_run_prints_an_estimated_cost_in_dollars(env):
    result = run_spike("--max-usd", "1.00", "--dry-run", "--out", env.out)
    costs = dollars_in(result.out)
    assert costs and max(costs) > 0, result.out
    assert "estimat" in result.out.lower()


def test_the_estimate_grows_with_the_number_of_transcripts(env):
    one = run_spike("--max-usd", "1.00", "--dry-run", "--only", env.left)
    everything = run_spike("--max-usd", "1.00", "--dry-run")
    assert sum(dollars_in(everything.out)) > sum(dollars_in(one.out)) > 0


def test_the_estimate_depends_on_the_model(env):
    haiku = run_spike("--max-usd", "1.00", "--dry-run", "--model", HAIKU, "--only", env.left)
    sonnet = run_spike("--max-usd", "1.00", "--dry-run", "--model", SONNET, "--only", env.left)
    assert sum(dollars_in(sonnet.out)) > sum(dollars_in(haiku.out)) > 0


def test_the_estimate_is_a_worst_case_at_least_what_the_scripted_run_costs(env):
    estimate = run_spike("--max-usd", "1.00", "--dry-run", "--only", env.left)
    real = run_spike("--max-usd", "1.00", "--out", env.out, "--only", env.left)
    assert real.exc is None
    from moderation import budget

    assert max(dollars_in(estimate.out)) >= budget.spend(purposes=("spike",))


def test_dry_run_works_with_the_kill_switch_off(env, settings):
    settings.LLM_ENABLED = False
    result = run_spike("--max-usd", "1.00", "--dry-run")
    assert result.exc is None
    assert 2 * len(env.golden) in integers_in(result.out)
    nothing_called(env)


def test_dry_run_works_without_an_api_key(env, settings):
    settings.ANTHROPIC_API_KEY = ""
    result = run_spike("--max-usd", "1.00", "--dry-run")
    assert result.exc is None
    nothing_called(env)


# --- a real run needs LLM_ENABLED (and a key) ----------------------------------------------------------------------------

def test_with_the_kill_switch_off_it_explains_how_to_enable_and_makes_no_call(env, settings):
    settings.LLM_ENABLED = False
    result = run_spike("--max-usd", "1.00", "--out", env.out, "--only", env.left)
    assert env.client.calls == []
    assert "LLM_ENABLED" in result.text
    assert "tunables" in result.text.lower()
    assert ledger().filter(status__in=["ok", "pending", "error"]).count() == 0


def test_with_no_api_key_it_says_so_and_makes_no_call(env, settings):
    settings.ANTHROPIC_API_KEY = ""
    result = run_spike("--max-usd", "1.00", "--out", env.out, "--only", env.left)
    assert env.client.calls == []
    assert "api" in result.text.lower() and "key" in result.text.lower()
    assert ledger().filter(status__in=["ok", "pending", "error"]).count() == 0


# --- purpose, model, --only ---------------------------------------------------------------------------------------------

def test_calls_use_purpose_spike_and_the_spike_model_by_default(env):
    result = run_spike("--max-usd", "1.00", "--out", env.out, "--only", env.left)
    assert result.exc is None
    rows = list(ledger().order_by("pk"))
    assert len(rows) == 2
    assert {r.purpose for r in rows} == {"spike"}
    assert {r.model for r in rows} == {HAIKU}
    assert {c["model"] for c in env.client.calls} == {HAIKU}


def test_the_default_model_is_read_from_the_spike_model_setting(env, tune):
    tune(SPIKE_MODEL=SONNET)
    run_spike("--max-usd", "1.00", "--out", env.out, "--only", env.left)
    assert {c["model"] for c in env.client.calls} == {SONNET}


def test_model_option_overrides_among_allowed_models(env):
    run_spike("--max-usd", "1.00", "--out", env.out, "--only", env.left, "--model", SONNET)
    assert {c["model"] for c in env.client.calls} == {SONNET}
    assert {r.model for r in ledger()} == {SONNET}


def test_model_option_rejects_a_model_that_is_not_allowed(env):
    result = run_spike("--max-usd", "1.00", "--out", env.out, "--only", env.left, "--model", "not-a-real-model")
    assert result.exc is not None
    nothing_called(env)


def test_only_runs_just_that_transcript(env):
    run_spike("--max-usd", "1.00", "--out", env.out, "--only", env.left)
    assert env.scripted.seen == [(env.left, "master"), (env.left, "intervenor")]


def test_only_accepts_several_ids(env):
    run_spike("--max-usd", "1.00", "--out", env.out, "--only", env.left, env.right)
    assert sorted(set(t for t, _a in env.scripted.seen)) == sorted([env.left, env.right])
    assert len(env.client.calls) == 4


def test_only_with_an_unknown_id_is_an_error_and_makes_no_call(env):
    result = run_spike("--max-usd", "1.00", "--out", env.out, "--only", "no_such_transcript")
    assert result.exc is not None
    assert "no_such_transcript" in str(result.exc)
    nothing_called(env)


def test_only_with_one_unknown_id_among_known_ones_makes_no_call_at_all(env):
    result = run_spike("--max-usd", "1.00", "--out", env.out, "--only", env.left, "no_such_transcript")
    assert result.exc is not None
    nothing_called(env)
