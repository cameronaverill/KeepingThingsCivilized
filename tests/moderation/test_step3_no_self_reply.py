"""The moderator never replies to its own messages: a Moderator-authored trigger is refused, Moderator messages may be
context, and an issue on a Moderator message is rejected at item level (the user's rule; brief section 8 addendum).

The transcript folder is redirected to a temp copy (spike.TRANSCRIPTS_DIR); FakeLLM only.
"""
import json
import re
import shutil
from types import SimpleNamespace

import pytest
from step3_testkit import (
    TRANSCRIPT_DIR,
    Scripted,
    find_pair,
    find_report,
    golden,  # noqa: F401
    issue_dict,
    intervenor_output,
    load_transcripts,
    parse_rendered,
    real_results_untouched,  # noqa: F401
    result_json,
    run_spike,
    spike_env,  # noqa: F401
    walk,
)

HOSTILE = "Please stay civil <b>everyone</b> & keep it \"polite\"."


@pytest.fixture
def folder(spike_env, install_fake, tmp_path, monkeypatch):  # noqa: F811
    from moderation.management.commands import spike

    directory = tmp_path / "transcripts"
    shutil.copytree(TRANSCRIPT_DIR, directory)
    monkeypatch.setattr(spike, "TRANSCRIPTS_DIR", directory)
    left, _right = find_pair(load_transcripts(), "factual_accuracy")
    return SimpleNamespace(dir=directory, tid=left["id"], out=str(tmp_path / "res"), install_fake=install_fake, tmp=tmp_path)


def edit(folder, fn):
    path = folder.dir / f"{folder.tid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def transcripts_in(folder):
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.dir.glob("*.json"))}


def go(folder, *extra):
    return run_spike("--max-usd", "5", "--out", folder.out, "--only", folder.tid, *extra)


def ledger_rows():
    from moderation.models import LLMCall

    return LLMCall.objects.count()


# --- (1) a Moderator-authored trigger is refused ----------------------------------------------------------------------

@pytest.mark.parametrize("label", ["Moderator", "moderator", "MODERATOR"])
@pytest.mark.parametrize("dry", [False, True], ids=["run", "dry-run"])
def test_a_moderator_authored_trigger_is_a_clean_error_before_any_call_or_ledger_row(folder, label, dry):
    data = edit(folder, lambda d: d["messages"][-1].update(author=label))
    seq = data["trigger_seq"]
    client = Scripted(load_transcripts()).install(folder.install_fake)
    result = go(folder, *(["--dry-run"] if dry else []))
    assert result.exc is not None, "the command must refuse"
    text = str(result.exc)
    assert f"{folder.tid}.json" in text and str(seq) in text, text
    assert "moderator" in text.lower()
    assert client.calls == []
    assert ledger_rows() == 0


def test_an_unrelated_transcript_is_still_fine_when_another_has_a_moderator_trigger_but_only_selected_ones_are_checked(folder):
    """Validation follows --only (as the other checks do): the bad file is refused when selected."""
    edit(folder, lambda d: d["messages"][-1].update(author="Moderator"))
    other = next(t for t in transcripts_in(folder) if t != folder.tid and t.endswith("_left"))
    scripted = Scripted(load_transcripts())
    client = scripted.install(folder.install_fake)
    good = run_spike("--max-usd", "5", "--out", folder.out, "--only", other)
    assert good.exc is None and len(client.calls) == 2
    bad = go(folder)
    assert bad.exc is not None


# --- (2) Moderator messages as earlier context work -------------------------------------------------------------------

def context_data(folder, label="Moderator"):
    return edit(folder, lambda d: d["messages"][1].update(author=label, text=HOSTILE))


@pytest.mark.parametrize("label", ["Moderator", "moderator"])
def test_moderator_messages_earlier_in_the_conversation_are_context_rendered_with_that_label_and_escaped(folder, label):
    context_data(folder, label)
    scripted = Scripted(transcripts_in(folder))
    client = scripted.install(folder.install_fake)
    result = go(folder)
    assert result.exc is None, result.text
    assert len(client.calls) == 2
    for call in client.calls:
        content = "\n".join(str(m["content"]) for m in call["messages"])
        parsed = parse_rendered(content)
        assert parsed[1][0]["participant"] == label
        assert parsed[1][1].strip() == HOSTILE, "the text survives exactly after unescaping"
        assert "<b>everyone</b>" not in content and "&lt;b&gt;everyone&lt;/b&gt;" in content
        assert content.lower().count("<message ") == len(parsed)


# --- (3) and (4) issues on Moderator messages are rejected; the rest pass -------------------------------------------------

class SelfReplyScript(Scripted):
    """Master issues: a `repetition` (a cross-message type, so only the Moderator rule can reject it) on the Moderator
    message (seq 2); a `repetition` on participant message 1 (control); a factual issue on the trigger (control)."""

    def master_issues(self, tid):
        t = self.transcripts[tid]
        by_seq = {m["seq"]: m for m in t["messages"]}
        return [
            issue_dict(1, 2, by_seq[2]["text"][:12], issue_type="repetition", explanation=f"explanation-{tid}-on-moderator"),
            issue_dict(2, 1, by_seq[1]["text"][:20], issue_type="repetition", explanation=f"explanation-{tid}-on-participant-old"),
            issue_dict(3, t["trigger_seq"], by_seq[t["trigger_seq"]]["text"][:20], issue_type="unclear_statement", explanation=f"explanation-{tid}-on-trigger"),
        ]

    def intervenor(self, tid):
        return intervenor_output("no_intervention", f"rationale-{tid}")


def checked_issues(folder):
    _p, data = result_json(folder.out, folder.tid)
    found = {}
    for d in walk(data):
        if isinstance(d, dict) and isinstance(d.get("issues_checked"), list):
            for issue in d["issues_checked"]:
                found[issue["id"]] = issue
    assert found, "no issues_checked in the per-transcript JSON"
    return data, found


@pytest.fixture
def selfreply(folder):
    context_data(folder)
    scripted = SelfReplyScript(transcripts_in(folder))
    client = scripted.install(folder.install_fake)
    result = go(folder)
    return SimpleNamespace(folder=folder, scripted=scripted, client=client, result=result)


def test_an_issue_on_a_moderator_message_is_rejected_with_a_reason_and_the_run_continues(selfreply):
    assert selfreply.result.exc is None, selfreply.result.text
    _data, issues = checked_issues(selfreply.folder)
    on_moderator = issues["i1"]
    assert on_moderator["valid"] is False
    assert re.search(r"moderator", on_moderator["rejection_reason"] or "", re.I), on_moderator["rejection_reason"]
    assert selfreply.scripted.calls_for(selfreply.folder.tid) == ["master", "intervenor"], "the Intervenor is still called"


def test_the_other_issues_still_pass_through(selfreply):
    _data, issues = checked_issues(selfreply.folder)
    assert issues["i2"]["valid"] is True, "a cross-message issue on an earlier participant message (control)"
    assert issues["i3"]["valid"] is True, "an issue on the participant trigger message (control)"


def test_the_intervenor_sees_only_the_valid_issues(selfreply):
    shown = selfreply.scripted.contents[(selfreply.folder.tid, "intervenor")]
    tid = selfreply.folder.tid
    assert f"explanation-{tid}-on-moderator" not in shown
    assert f"explanation-{tid}-on-participant-old" in shown and f"explanation-{tid}-on-trigger" in shown


def test_the_rejection_is_in_the_report_too(selfreply):
    report = find_report(selfreply.folder.out).read_text(encoding="utf-8")
    tid = selfreply.folder.tid
    assert f"explanation-{tid}-on-moderator" in report
    block = report[report.index(f"explanation-{tid}-on-moderator") - 600:report.index(f"explanation-{tid}-on-moderator")]
    assert "REJECTED" in block and re.search(r"moderator", block, re.I)
    assert "REJECTED" not in report[report.index(f"explanation-{tid}-on-trigger") - 300:report.index(f"explanation-{tid}-on-trigger")]


def test_an_issue_on_a_participant_message_passes_without_any_moderator_message(folder):
    """Control: with no Moderator message at all, the same kind of issues are all valid."""

    class Plain(SelfReplyScript):
        def master_issues(self, tid):
            t = self.transcripts[tid]
            first = t["messages"][0]
            return [issue_dict(1, first["seq"], first["text"][:12], issue_type="repetition", explanation=f"explanation-{tid}-a"),
                    issue_dict(2, t["trigger_seq"], t["messages"][-1]["text"][:20], issue_type="unclear_statement", explanation=f"explanation-{tid}-b")]

    scripted = Plain(transcripts_in(folder))
    scripted.install(folder.install_fake)
    result = go(folder)
    assert result.exc is None
    _data, issues = checked_issues(folder)
    assert issues["i1"]["valid"] is True and issues["i2"]["valid"] is True


def test_the_moderator_rule_is_case_insensitive_for_issues_too(folder):
    context_data(folder, "moderator")
    scripted = SelfReplyScript(transcripts_in(folder))
    scripted.install(folder.install_fake)
    go(folder)
    _data, issues = checked_issues(folder)
    assert issues["i1"]["valid"] is False


# --- (5) the golden set never contains a self-reply case -----------------------------------------------------------------

def test_no_golden_transcript_has_a_moderator_authored_trigger_message(golden):  # noqa: F811
    assert len(golden) >= 22
    for t in golden.values():
        trigger = next(m for m in t["messages"] if m["seq"] == t["trigger_seq"])
        assert trigger["author"].strip().lower() != "moderator", t["id"]


def test_no_golden_transcript_has_a_moderator_message_at_all_unless_it_is_earlier_context(golden):  # noqa: F811
    for t in golden.values():
        for m in t["messages"]:
            if m["author"].strip().lower() == "moderator":
                assert m["seq"] < t["trigger_seq"] and not m["planted"], t["id"]
