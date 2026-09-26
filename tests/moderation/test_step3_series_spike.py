"""manage.py spike over the mechanical series (brief section 9), FakeLLM only: loading and validation, --only by member ids,
the per-series report section, and the dry-run call count."""
import json
import re
import shutil
from types import SimpleNamespace

import pytest
from step3_serieskit import PROCESS_FACTORS, by_factor, groups, series_members
from step3_testkit import (
    TRANSCRIPT_DIR,
    Scripted,
    find_report,
    golden,  # noqa: F401
    integers_in,
    issue_dict,
    intervenor_output,
    act_dict,
    load_transcripts,
    markdown_tables,
    real_results_untouched,  # noqa: F401
    result_json,
    run_spike,
    spike_env,  # noqa: F401
)


class SeriesScript(Scripted):
    """Like Scripted, but a process-series member gets a Master issue of the process type on its trigger message."""

    ISSUE_FOR = {"flooding": "process_violation", "repetition": "repetition", "unanswered_question": "process_violation"}

    def master_issues(self, tid):
        t = self.transcripts[tid]
        factor = (t.get("series") or {}).get("factor")
        if factor in self.ISSUE_FOR:
            trigger = next(m for m in t["messages"] if m["seq"] == t["trigger_seq"])
            return [issue_dict(1, trigger["seq"], trigger["text"][:15], issue_type=self.ISSUE_FOR[factor], explanation=f"explanation-{tid}-1")]
        return super().master_issues(tid)

    def intervenor(self, tid):
        t = self.transcripts[tid]
        factor = (t.get("series") or {}).get("factor")
        if factor in self.ISSUE_FOR:
            trigger = next(m for m in t["messages"] if m["seq"] == t["trigger_seq"])
            return intervenor_output(
                "intervene", f"rationale-{tid}", dispositions=[{"issue_id": issue_dict(1, 1, "x")["id"], "disposition": "acted", "reason": f"reason-{tid}-1"}],
                acts=[act_dict(type="enforce_process", addressee="all", subject=trigger["author"], issue_ns=(1,), seqs=(trigger["seq"],), tone="neutral", text=self.act_text(tid))],
            )
        return super().intervenor(tid)


@pytest.fixture
def members():
    found = series_members()
    assert found, "no series transcripts in golden/transcripts"
    return found


@pytest.fixture
def env(spike_env, golden, install_fake, tmp_path):  # noqa: F811
    scripted = SeriesScript(golden)
    client = scripted.install(install_fake, copies=200)
    return SimpleNamespace(golden=golden, scripted=scripted, client=client, out=tmp_path / "res", tmp=tmp_path, install_fake=install_fake)


def run_ids(env, ids, max_usd="5"):
    result = run_spike("--max-usd", max_usd, "--out", str(env.out), "--only", *ids)
    assert result.exc is None, result.text
    return result


# --- loading, --only, dry-run ----------------------------------------------------------------------------------------------

def test_a_whole_series_can_be_selected_by_its_member_ids(env, members):
    ids = sorted(t["id"] for t in members.values() if t["series"]["id"] == "flooding")
    assert len(ids) == 2
    run_ids(env, ids)
    assert sorted({tid for tid, _a in env.scripted.seen}) == ids
    assert len(env.client.calls) == 4
    for tid in ids:
        result_json(env.out, tid)


def test_the_full_set_of_series_members_runs_and_every_member_has_a_result(env, members):
    ids = sorted(members)
    run_ids(env, ids)
    assert len(env.client.calls) == 2 * len(ids)
    for tid in ids:
        _p, data = result_json(env.out, tid)
        assert data["status"] == "ok", tid


def test_dry_run_over_everything_counts_two_calls_per_transcript(spike_env, golden, install_fake):  # noqa: F811
    client = install_fake()
    result = run_spike("--max-usd", "50", "--dry-run")
    assert result.exc is None
    assert len(golden) >= 40
    assert 2 * len(golden) in integers_in(result.out), result.out
    assert client.calls == []


# --- the report ------------------------------------------------------------------------------------------------------------

def sections(report):
    """[(heading line, body)] split at markdown headings."""
    parts, heading, body = [], None, []
    for line in report.splitlines():
        if line.startswith("#"):
            if heading is not None:
                parts.append((heading, "\n".join(body)))
            heading, body = line, []
        else:
            body.append(line)
    if heading is not None:
        parts.append((heading, "\n".join(body)))
    return parts


@pytest.fixture
def report(env, members):
    run_ids(env, sorted(members))
    return find_report(env.out).read_text(encoding="utf-8")


def series_ids(members):
    return sorted({t["series"]["id"] for t in members.values()})


def test_the_report_has_exactly_one_section_per_series(report, members):
    heads = [h for h, _b in sections(report)]
    for sid in series_ids(members):
        matching = [h for h in heads if re.search(rf"(?<![a-z0-9_]){re.escape(sid)}(?![a-z0-9_])", h)]
        assert len(matching) == 1, f"series {sid}: headings {matching}"


def series_table(report, sid, members):
    levels = sorted({t["series"]["level"] for t in members.values() if t["series"]["id"] == sid})
    for heading, body in sections(report):
        if re.search(rf"(?<![a-z0-9_]){re.escape(sid)}(?![a-z0-9_])", heading):
            for block in markdown_tables(body):
                lowered = block.lower()
                if "left" in lowered.splitlines()[0] and "right" in lowered.splitlines()[0]:
                    return heading, body, block, levels
    raise AssertionError(f"no table with left and right columns in the section of series {sid}")


def test_each_series_table_has_a_row_per_level_and_left_and_right_columns_and_a_same_across_sides_marker(report, members):
    for sid in series_ids(members):
        _heading, body, block, levels = series_table(report, sid, members)
        for level in levels:
            assert any(line.strip().startswith("|") and level in line for line in block.splitlines()[2:]), (sid, level)
        assert re.search(r"same across sides", block + body, re.I), sid


def test_the_series_table_shows_the_documented_features(report, members):
    for sid in series_ids(members):
        _h, body, block, _levels = series_table(report, sid, members)
        lowered = (block + body).lower()
        for word in ("intervene", "planted", "tone", "flagged"):
            assert word in lowered, (sid, word)


def test_length_series_show_the_computed_trigger_length(report, members):
    for sid in ("length_factual", "length_abusive"):
        _h, body, block, _levels = series_table(report, sid, members)
        for t in members.values():
            if t["series"]["id"] == sid:
                chars = [v for k, v in t["computed"].items() if "char" in k][0]
                assert str(chars) in block + body, (sid, t["id"], chars)


def test_process_series_list_the_flagged_issue_types(report, members):
    expected = {"flooding": "process_violation", "repetition": "repetition", "unanswered_question": "process_violation"}
    for sid, issue_type in expected.items():
        _h, body, block, _levels = series_table(report, sid, members)
        assert issue_type in block + body, (sid, issue_type)


def test_the_process_series_show_the_computed_process_features(report, members):
    _h, body, block, _l = series_table(report, "flooding", members)
    assert re.search(r"run", block + body, re.I) and re.search(r"\b4\b", block + body)
    _h, body, block, _l = series_table(report, "repetition", members)
    assert re.search(r"repeat", block + body, re.I)
    _h, body, block, _l = series_table(report, "unanswered_question", members)
    assert re.search(r"unanswered|question", block + body, re.I)


# --- validation against the base ---------------------------------------------------------------------------------------------

@pytest.fixture
def folder(spike_env, install_fake, tmp_path, monkeypatch):  # noqa: F811
    from moderation.management.commands import spike

    directory = tmp_path / "transcripts"
    shutil.copytree(TRANSCRIPT_DIR, directory)
    monkeypatch.setattr(spike, "TRANSCRIPTS_DIR", directory)
    scripted = SeriesScript(load_transcripts())
    client = scripted.install(install_fake, copies=50)
    return SimpleNamespace(dir=directory, client=client, out=str(tmp_path / "res"))


def member_id(factor, side="left"):
    for t in series_members().values():
        if t["series"]["factor"] == factor and t["series"]["side"] == side:
            return t["id"]
    raise AssertionError(factor)


def edit(folder, tid, fn):
    path = folder.dir / f"{tid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def go(folder, tid):
    try:
        return run_spike("--max-usd", "5", "--out", folder.out, "--only", tid)
    except AssertionError as exc:  # the scripted client could not answer: the command went on to call the model
        pytest.fail(f"the command called the model instead of refusing the transcript: {exc}")


@pytest.mark.parametrize("factor", ["message_length", "label_swap", "flooding", "repetition", "unanswered_question"])
def test_a_series_member_with_an_unknown_base_is_a_clean_error(folder, factor):
    tid = member_id(factor)
    edit(folder, tid, lambda d: d["series"].update(base="no_such_transcript"))
    result = go(folder, tid)
    assert result.exc is not None and f"{tid}.json" in str(result.exc) and "no_such_transcript" in str(result.exc)
    assert folder.client.calls == []


def test_a_series_member_whose_base_file_is_missing_is_a_clean_error(folder):
    tid = member_id("message_length")
    base = next(t for t in series_members().values() if t["id"] == tid)["series"]["base"]
    (folder.dir / f"{base}.json").unlink()
    result = go(folder, tid)
    assert result.exc is not None and base in str(result.exc)
    assert folder.client.calls == []


@pytest.mark.parametrize("index", [0, 1, 2])
def test_a_length_member_whose_first_three_messages_differ_from_the_base_is_a_clean_error(folder, index):
    tid = member_id("message_length")
    edit(folder, tid, lambda d: d["messages"][index].update(text=d["messages"][index]["text"] + " Extra words."))
    result = go(folder, tid)
    assert result.exc is not None
    text = str(result.exc)
    assert f"{tid}.json" in text and re.search(rf"message\s*(seq\s*)?{index + 1}|messages? 1 to 3|seq {index + 1}", text, re.I), text
    assert folder.client.calls == []


def test_a_length_member_with_a_different_planted_phrase_is_a_clean_error(folder):
    tid = member_id("message_length")
    edit(folder, tid, lambda d: d["messages"][3]["planted"][0].update(phrase="a phrase that is not the base's"))
    result = go(folder, tid)
    assert result.exc is not None
    assert folder.client.calls == []


def test_a_series_member_without_a_computed_block_is_a_clean_error(folder):
    tid = member_id("flooding")
    edit(folder, tid, lambda d: d.pop("computed"))
    result = go(folder, tid)
    assert result.exc is not None and "computed" in str(result.exc)
    assert folder.client.calls == []


@pytest.mark.parametrize("bad", ["bogus_factor"])
def test_a_series_member_with_an_unknown_factor_or_side_is_a_clean_error(folder, bad):
    tid = member_id("flooding")
    edit(folder, tid, lambda d: d["series"].update(factor=bad))
    assert go(folder, tid).exc is not None
    tid2 = member_id("repetition")
    edit(folder, tid2, lambda d: d["series"].update(side="middle"))
    assert go(folder, tid2).exc is not None
    assert folder.client.calls == []


def test_a_computed_block_that_disagrees_with_the_messages_is_a_clean_error(folder):
    """The ground truth is computed by code and checked at load time: a hand-edited value must not slip through."""
    tid = member_id("flooding")
    edit(folder, tid, lambda d: d["computed"].update(longest_consecutive_run=3))
    result = go(folder, tid)
    assert result.exc is not None and tid in str(result.exc)
    assert folder.client.calls == []
