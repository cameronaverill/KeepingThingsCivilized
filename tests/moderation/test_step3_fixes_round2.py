"""Tests for the fixes that followed the independent review: corrections and evidence in the golden set, spike validation of
seq order, and the dry-run warnings."""
import json
import re
import shutil
from types import SimpleNamespace

import pytest
from step3_testkit import (
    TRANSCRIPT_DIR,
    Scripted,
    find_pair,
    golden,  # noqa: F401
    integers_in,
    load_transcripts,
    real_results_untouched,  # noqa: F401
    run_spike,
    spike_env,  # noqa: F401
)


def planted_of(transcripts, tid):
    return [p for m in transcripts[tid]["messages"] for p in m["planted"]]


@pytest.mark.parametrize("tid", ["rent_factual_left", "rent_factual_right"])
def test_the_oregon_correction_names_sb_611_2023_the_ten_percent_cap_and_the_2026_figure(golden, tid):  # noqa: F811
    (planted,) = planted_of(golden, tid)
    text = planted["correction"]
    assert re.search(r"SB\s*611", text), "the 10 percent cap comes from SB 611"
    assert "2023" in text
    assert re.search(r"10\s*(percent|%)", text)
    assert re.search(r"9\.5\s*(percent|%)", text)
    assert re.search(r"SB\s*608", text) and re.search(r"7\s*(percent|%)", text)
    assert "3 percent" in text or "3%" in text or "about 3" in text, "says the claimed figure is wrong"


@pytest.mark.parametrize("tid", ["rent_factual_left", "rent_factual_right"])
def test_the_oregon_evidence_cites_the_bill_or_nlihc_and_an_official_or_press_source_for_the_2026_figure(golden, tid):  # noqa: F811
    (planted,) = planted_of(golden, tid)
    evidence = planted["evidence"]
    assert re.search(r"olis\.oregonlegislature\.gov/[^\s;]*SB608|nlihc\.org", evidence), evidence
    assert re.search(r"apps\.oregon\.gov|oregon\.gov/das|oregoncapitalchronicle\.com", evidence), evidence
    assert len(re.findall(r"https?://", evidence)) >= 2


def test_the_oregon_evidence_also_cites_sb_611_or_says_where_the_cap_comes_from(golden):  # noqa: F811
    for tid in ("rent_factual_left", "rent_factual_right"):
        (planted,) = planted_of(golden, tid)
        blob = planted["evidence"] + " " + planted["correction"]
        assert re.search(r"SB611|SB\s*611|Senate Bill 611", blob)


def test_the_portugal_population_correction_gives_the_current_range(golden):  # noqa: F811
    hits = [
        p for t in golden.values() for m in t["messages"] for p in m["planted"]
        if "thirty million" in p["phrase"]
    ]
    assert len(hits) == 1
    text = hits[0]["correction"]
    assert re.search(r"10\.4", text) and re.search(r"11\.4", text) and "million" in text, text
    assert re.search(r"10\.4\s*(to|-|and)\s*11\.4\s*million", text), "the range reads '10.4 to 11.4 million'"
    assert "thirty" in text or "30" in text


# --- spike load: seq order --------------------------------------------------------------------------------------------

@pytest.fixture
def folder(spike_env, install_fake, tmp_path, monkeypatch):  # noqa: F811
    from moderation.management.commands import spike

    directory = tmp_path / "transcripts"
    shutil.copytree(TRANSCRIPT_DIR, directory)
    monkeypatch.setattr(spike, "TRANSCRIPTS_DIR", directory)
    transcripts = load_transcripts()
    scripted = Scripted(transcripts)
    client = scripted.install(install_fake)
    left, _r = find_pair(transcripts, "factual_accuracy")
    return SimpleNamespace(dir=directory, client=client, tid=left["id"], out=str(tmp_path / "res"))


def edit(folder, fn):
    path = folder.dir / f"{folder.tid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def go(folder, *extra):
    return run_spike("--max-usd", "5", "--out", folder.out, "--only", folder.tid, *extra)


def swap_first_two(data):
    data["messages"][0], data["messages"][1] = data["messages"][1], data["messages"][0]


def descending(data):
    data["messages"].reverse()


def duplicate(data):
    data["messages"][1]["seq"] = data["messages"][0]["seq"]


@pytest.mark.parametrize("breaker", [swap_first_two, descending, duplicate], ids=["swapped", "descending", "duplicate"])
@pytest.mark.parametrize("dry", [False, True], ids=["run", "dry-run"])
def test_non_ascending_or_duplicate_seq_is_a_clean_error_before_any_call(folder, breaker, dry):
    edit(folder, breaker)
    result = go(folder, *(["--dry-run"] if dry else []))
    assert result.exc is not None
    assert f"{folder.tid}.json" in str(result.exc) and "seq" in str(result.exc)
    assert folder.client.calls == []


# --- dry-run warnings ---------------------------------------------------------------------------------------------------

def dry(*extra):
    return run_spike("--dry-run", *extra)


def lines_with(text, *needles):
    return [l for l in text.splitlines() if any(n.lower() in l.lower() for n in needles)]


def test_dry_run_warns_when_the_estimate_exceeds_the_max_usd_limit(spike_env, golden):  # noqa: F811
    result = dry("--max-usd", "0.01")
    assert result.exc is None
    warned = [l for l in lines_with(result.out, "max-usd", "limit") if re.search(r"warn|exceed|above|more than|over|stop early", l, re.I)]
    assert warned, result.out


def test_dry_run_warns_when_the_estimate_exceeds_the_spike_cap(spike_env, golden, settings):  # noqa: F811
    settings.BUDGET_SPIKE_USD_TOTAL = __import__("decimal").Decimal("0.05")
    result = dry("--max-usd", "50")
    assert result.exc is None
    warned = [l for l in lines_with(result.out, "BUDGET_SPIKE_USD_TOTAL", "spike cap") if re.search(r"warn|exceed|above|more than|over|stop early", l, re.I)]
    assert warned, result.out
    assert not [l for l in lines_with(result.out, "max-usd") if re.search(r"warn|exceed|stop early", l, re.I)], "the --max-usd limit is fine here"


def test_dry_run_does_not_warn_when_both_limits_are_large(spike_env, golden, settings):  # noqa: F811
    settings.BUDGET_SPIKE_USD_TOTAL = __import__("decimal").Decimal("50")
    result = dry("--max-usd", "50")
    assert result.exc is None
    assert not re.search(r"warn|exceed|stop early|above the", result.out, re.I), result.out


def test_dry_run_warning_does_not_change_the_call_count_line(spike_env, golden):  # noqa: F811
    result = dry("--max-usd", "0.01")
    assert 2 * len(golden) in integers_in(result.out)
