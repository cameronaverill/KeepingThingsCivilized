"""manage.py spike: the files it writes (one JSON per transcript, and report.md), always under a temp --out directory."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from step3_testkit import (
    D,
    Scripted,
    benign_single,
    find_pair,
    find_report,
    golden,  # noqa: F401
    markdown_tables,
    phrase_of,
    real_results_untouched,  # noqa: F401
    result_json,
    run_spike,
    spike_env,  # noqa: F401
    strings_in,
    values_under_key,
    walk,
)


@pytest.fixture
def env(spike_env, golden, install_fake, tmp_path):  # noqa: F811
    scripted = Scripted(golden)
    scripted.install(install_fake)
    fl, fr = find_pair(golden, "factual_accuracy")
    al, ar = find_pair(golden, "abusiveness")
    out = tmp_path / "results"
    return SimpleNamespace(
        golden=golden, scripted=scripted, out=out, fl=fl, fr=fr, al=al, ar=ar, benign=benign_single(golden),
    )


def go(env, *ids):
    result = run_spike("--max-usd", "1.00", "--out", str(env.out), "--only", *[i["id"] for i in ids])
    assert result.exc is None, result.text
    return result


def results_dir(env):
    return find_report(env.out).parent


# --- layout --------------------------------------------------------------------------------------------------------

def test_one_json_per_transcript_and_a_report_in_the_same_directory(env):
    go(env, env.fl, env.fr, env.benign)
    directory = results_dir(env)
    assert (directory / "report.md").is_file()
    for t in (env.fl, env.fr, env.benign):
        path, _data = result_json(directory, t["id"])
        assert path.suffix == ".json"


def test_a_full_run_writes_one_json_for_each_of_the_sixteen_transcripts(env):
    result = run_spike("--max-usd", "5.00", "--out", str(env.out))
    assert result.exc is None, result.text
    directory = results_dir(env)
    for tid in env.golden:
        result_json(directory, tid)
    assert (directory / "report.md").is_file()


def test_results_go_under_the_out_directory(env):
    go(env, env.fl)
    assert Path(env.out) in find_report(env.out).parents or find_report(env.out).parent == Path(env.out)


# --- the JSON ------------------------------------------------------------------------------------------------------

def test_json_holds_the_input(env):
    go(env, env.fl)
    _p, data = result_json(results_dir(env), env.fl["id"])
    assert env.fl["id"] in json.dumps(data)
    fragment = env.scripted.fragments[env.fl["id"]]
    assert any(fragment in s for s in strings_in(data)), "the messages that were sent are in the file"


def test_json_holds_both_raw_outputs(env):
    go(env, env.fl)
    _p, data = result_json(results_dir(env), env.fl["id"])
    text = json.dumps(data)
    assert f"explanation-{env.fl['id']}-1" in text and f"rationale-{env.fl['id']}" in text
    assert f"actxt-{env.fl['id']}" in text and f"reason-{env.fl['id']}-1" in text
    lowered = text.lower()
    assert "master" in lowered and "intervenor" in lowered


def test_json_holds_located_quotes_with_start_end_and_match(env):
    go(env, env.fl)
    _p, data = result_json(results_dir(env), env.fl["id"])
    message, planted = phrase_of(env.fl)
    text = message["text"]
    located = [d for d in walk(data) if isinstance(d, dict) and {"start", "end", "match"} <= set(d)]
    assert len(located) >= 3, "one located quote for each of the Master's three issues"
    by_match = {}
    for d in located:
        by_match.setdefault(d["match"], []).append(d)
    assert set(by_match) == {"exact", "normalized", "not_found"}, sorted(by_match)
    exact = by_match["exact"][0]
    assert text[exact["start"]:exact["end"]] == planted["phrase"]
    normalized = by_match["normalized"][0]
    assert text[normalized["start"]:normalized["end"]] == planted["phrase"]
    missing = by_match["not_found"][0]
    assert missing["start"] is None and missing["end"] is None


def test_json_holds_tokens_and_cost_of_both_calls(env):
    go(env, env.fl)
    _p, data = result_json(results_dir(env), env.fl["id"])
    tokens = {v for v in values_under_key(data, "token") if isinstance(v, int)}
    assert {1234, 321, 2345, 432} <= tokens, tokens
    costs = {D(str(v)) for v in values_under_key(data, "cost") if isinstance(v, (int, float, str)) and str(v).replace(".", "", 1).isdigit()}
    assert D("0.002839") in costs and D("0.004505") in costs, costs


def test_json_of_a_transcript_with_no_issues(env):
    go(env, env.benign)
    _p, data = result_json(results_dir(env), env.benign["id"])
    located = [d for d in walk(data) if isinstance(d, dict) and {"start", "end", "match"} <= set(d)]
    assert located == []
    assert f"rationale-{env.benign['id']}" in json.dumps(data)


# --- report.md -----------------------------------------------------------------------------------------------------

def report_text(env):
    return find_report(env.out).read_text(encoding="utf-8")


def test_report_lists_each_transcript_with_its_issues_dispositions_and_acts(env):
    go(env, env.fl, env.fr, env.benign)
    text = report_text(env)
    for t in (env.fl, env.fr, env.benign):
        assert t["id"] in text
    for t in (env.fl, env.fr):
        tid = t["id"]
        assert f"explanation-{tid}-1" in text, "issues"
        assert f"reason-{tid}-1" in text and f"reason-{tid}-2" in text, "dispositions"
        assert "acted" in text and "declined" in text
        assert f"actxt-{tid}" in text, "acts"
        assert "correct_factual_error" in text
        assert planted_phrase(t) in text, "the quoted phrase is shown"
    assert f"rationale-{env.benign['id']}" in text or "no_intervention" in text


def planted_phrase(t):
    return phrase_of(t)[1]["phrase"]


def test_report_shows_issue_types_intensity_tone_and_decision(env):
    go(env, env.fl, env.fr)
    text = report_text(env)
    for word in ("possible_factual_error", "intervene", "gentle"):
        assert word in text, word
    intensity = phrase_of(env.fl)[1]["intensity"]
    assert str(intensity) in text


def test_report_has_a_side_by_side_table_for_each_pair(env):
    go(env, env.fl, env.fr)
    text = report_text(env)
    tables = [b for b in markdown_tables(text) if "left" in b.lower() and "right" in b.lower()]
    assert tables, "a table that puts the left and right variants next to each other"
    block = tables[0]
    lowered = block.lower()
    for word in ("intervene", "correct_factual_error", "gentle"):
        assert word in lowered, f"the pair table lacks {word}"
    assert str(len(env.scripted.act_text(env.fl["id"]))) in block, "the length of the act text"
    assert str(phrase_of(env.fl)[1]["intensity"]) in block, "intensity"
    assert "possible_factual_error" in lowered, "the issues found"


def test_report_has_one_table_per_pair_run(env):
    go(env, env.fl, env.fr, env.al, env.ar)
    text = report_text(env)
    assert env.fl["pair_id"] in text and env.al["pair_id"] in text
    tables = [b for b in markdown_tables(text) if "left" in b.lower() and "right" in b.lower()]
    assert len(tables) >= 2


def test_report_for_a_single_transcript_run_does_not_invent_a_pair_comparison(env):
    go(env, env.benign)
    text = report_text(env)
    assert env.benign["id"] in text
    assert env.fl["id"] not in text
