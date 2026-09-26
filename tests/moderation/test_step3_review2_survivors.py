"""Tests added after the independent mutation pass: each one kills a mutation that the first-round tests let survive."""
import json
import re
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
    issue_dict,
    load_transcripts,
    parse_rendered,
    real_results_untouched,  # noqa: F401
    result_json,
    run_spike,
    spike_env,  # noqa: F401
    walk,
)


# --- taxonomy ------------------------------------------------------------------------------------------------------

def test_dispositions_disagreement_kinds_reasons_and_dimensions_all_have_a_definition():
    from moderation import taxonomy

    for group in (taxonomy.DISPOSITIONS, taxonomy.DISAGREEMENT_KINDS, taxonomy.NOT_SCORABLE_REASONS, tuple(taxonomy.DIMENSIONS)):
        for value in group:
            text = taxonomy.DEFINITIONS.get(value)
            assert isinstance(text, str) and len(text.strip()) >= 15 and text.endswith("."), value


def test_every_definition_key_is_a_known_value():
    """A stale or misspelled DEFINITIONS key would silently document nothing."""
    from moderation import taxonomy

    known = set()
    for name in ("ISSUE_TYPES", "ACT_TYPES", "DECISIONS", "TONES", "NOT_SCORABLE_REASONS", "DISPOSITIONS", "DISAGREEMENT_KINDS"):
        known |= set(getattr(taxonomy, name))
    known |= set(taxonomy.DIMENSIONS)
    assert set(taxonomy.DEFINITIONS) == known


# --- schemas -------------------------------------------------------------------------------------------------------

def test_discussion_map_and_issues_may_not_be_null():
    from pydantic import ValidationError

    from moderation.schemas import MasterOutput

    good = {"issues": [], "discussion_map": {"agreements": [], "disagreements": []}}
    assert MasterOutput.model_validate(good)
    for bad in ({**good, "discussion_map": None}, {**good, "issues": None}):
        with pytest.raises(ValidationError):
            MasterOutput.model_validate(bad)


# --- quotes --------------------------------------------------------------------------------------------------------

def test_occurrences_are_counted_without_overlap():
    """Pinned decision: 'aaaa' contains 'aa' twice (non-overlapping), the same as str.count."""
    from moderation.quotes import locate_quote

    assert locate_quote("aaaa", "aa").occurrences == 2
    assert locate_quote("AAAA", "aa").occurrences == 2


def test_case_variants_count_together_and_first_wins_on_the_normalized_path():
    from moderation.quotes import locate_quote

    text = "x Rent   Control, y rent control, z RENT CONTROL"
    result = locate_quote(text, "Rent control")
    assert (result.match, result.start, result.occurrences) == ("normalized", 2, 3)
    assert text[result.start:result.end] == "Rent   Control"


# --- prompting -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad_id", [None, 1.5, object(), ["1"], b"1", True])
def test_message_id_must_be_an_int_or_a_str(bad_id):
    from moderation.prompting import render_transcript

    with pytest.raises(TypeError):
        render_transcript([(bad_id, "A", "hi")])


def test_a_user_object_is_not_accepted_as_a_message_id():
    from django.contrib.auth import get_user_model

    from moderation.prompting import render_transcript

    user = get_user_model().objects.create_user(username="idleak_user", email="idleak@example.org", password="pw" * 12)
    with pytest.raises(TypeError):
        render_transcript([(user, "A", "hi")])


def test_the_moderator_label_and_the_newest_message_marker_use_the_last_message():
    from moderation.prompting import render_intervenor_input, render_master_input

    messages = [(3, "A", "one"), (7, "Moderator", "two"), (9, "B", "three")]
    master = render_master_input(messages)
    inter = render_intervenor_input(messages, [])
    for rendered in (master, inter):
        assert re.search(r'<newest_message id="9"\s*/>', rendered)
        assert [a["participant"] for a, _t in parse_rendered(rendered)] == ["A", "Moderator", "B"]


# --- spike command -------------------------------------------------------------------------------------------------

class OlderMessageScript(Scripted):
    """A Master that raises: a fallacy on an OLDER message (rejected by only-new-issues), a repetition on the older
    message (cross-message, so accepted), a factual error on the newest message (accepted)."""

    def master_issues(self, tid):
        t = self.transcripts[tid]
        older, newest = t["messages"][0]["seq"], t["trigger_seq"]
        assert older != newest
        return [
            issue_dict(1, older, t["messages"][0]["text"][:20], issue_type="fallacy", explanation=f"explanation-{tid}-old-fallacy"),
            issue_dict(2, older, t["messages"][0]["text"][:20], issue_type="repetition", explanation=f"explanation-{tid}-old-repetition"),
            issue_dict(3, newest, t["messages"][-1]["text"][:20], issue_type="unclear_statement", explanation=f"explanation-{tid}-new-unclear"),
        ]

    def intervenor(self, tid):
        from step3_testkit import intervenor_output

        return intervenor_output("no_intervention", f"rationale-{tid}")


@pytest.fixture
def env(spike_env, golden, install_fake, tmp_path):  # noqa: F811
    left, right = find_pair(golden, "factual_accuracy")
    return SimpleNamespace(golden=golden, install_fake=install_fake, out=tmp_path / "res", left=left["id"], right=right["id"], tmp=tmp_path)


def go(env, *ids, max_usd="5"):
    result = run_spike("--max-usd", max_usd, "--out", str(env.out), "--only", *ids)
    assert result.exc is None, result.text
    return result


def test_the_intervenor_is_shown_only_the_valid_issues(env):
    scripted = Scripted(env.golden)
    scripted.install(env.install_fake)
    go(env, env.left)
    shown = scripted.contents[(env.left, "intervenor")]
    assert f"explanation-{env.left}-1" in shown and f"explanation-{env.left}-2" in shown
    assert f"explanation-{env.left}-3" not in shown, "issue 3 quotes text that is not in the message, so it is invalid"


def test_only_new_issues_rule_and_cross_message_exception(env):
    scripted = OlderMessageScript(env.golden)
    scripted.install(env.install_fake)
    go(env, env.left)
    _p, data = result_json(env.out, env.left)
    checked = {i["id"]: i for d in walk(data) if isinstance(d, dict) and isinstance(d.get("issues_checked"), list) for i in d["issues_checked"]}
    old_fallacy, old_repetition, new_unclear = (checked[k] for k in ("i1", "i2", "i3"))
    assert old_fallacy["valid"] is False and "older message" in (old_fallacy["rejection_reason"] or "")
    assert old_repetition["valid"] is True
    assert new_unclear["valid"] is True
    shown = scripted.contents[(env.left, "intervenor")]
    assert "old-fallacy" not in shown
    assert "old-repetition" in shown and "new-unclear" in shown


def test_after_a_guard_refusal_the_rest_are_not_run_and_marked_so(env):
    scripted = Scripted(env.golden)
    client = scripted.install(env.install_fake)
    ids = [env.left, env.right, benign_single(env.golden)["id"]]
    result = run_spike("--max-usd", "0.0001", "--out", str(env.out), "--only", *ids)
    assert result.exc is None or type(result.exc).__name__ == "CommandError"
    from moderation.models import LLMCall

    assert client.calls == []
    rows = list(LLMCall.objects.all())
    assert len(rows) == 1 and rows[0].status == "refused_budget", "exactly one attempt: the run stops at the first refusal"
    for tid in ids:
        _path, data = result_json(env.out, tid)
        assert data["status"] == "not_run", tid
        assert data["total_cost_usd"] in ("0", "0.0", "0.000000")
    report = find_report(env.out).read_text(encoding="utf-8")
    assert report.count("NOT RUN") == 3
    assert "STOPPED EARLY" in result.out


def test_a_failed_master_that_was_billed_is_counted_in_the_printed_total(env):
    """A dropped connection is billed at the whole reservation: the printed total must include it."""
    from step3_testkit import decimals_in

    from moderation import budget

    scripted = Scripted(env.golden)
    scripted.install(env.install_fake)
    scripted.fail[(env.left, "master")] = "connection"
    result = go(env, env.left, env.right)
    total = budget.spend(purposes=("spike",))
    assert total > D("0.0073") * 1  # the reservation is far bigger than a scripted call
    assert total in decimals_in(result.out)
    _p, data = result_json(env.out, env.left)
    assert D(data["total_cost_usd"]) > 0


def test_a_message_of_multibyte_characters_under_the_limit_is_accepted(env, settings, monkeypatch):
    """The limit counts characters (code points), not bytes: 2,900 emoji is 11,600 bytes and must still pass."""
    from moderation.management.commands import spike

    directory = env.tmp / "t"
    directory.mkdir()
    data = json.loads((Path(__file__).resolve().parents[2] / "golden" / "transcripts" / f"{env.left}.json").read_text(encoding="utf-8"))
    data["messages"][0]["text"] = "\U0001F600" * (settings.MAX_MESSAGE_CHARS - 100)
    for m in data["messages"][0]["planted"]:
        m.clear()
    data["messages"][0]["planted"] = []
    (directory / f"{env.left}.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(spike, "TRANSCRIPTS_DIR", directory)
    spike.validate_transcript(directory / f"{env.left}.json", data)  # must not raise
    data["messages"][0]["text"] = "\U0001F600" * (settings.MAX_MESSAGE_CHARS + 1)
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        spike.validate_transcript(directory / f"{env.left}.json", data)


def test_the_length_rule_strips_and_uses_nfc(settings):
    from django.core.management.base import CommandError

    from moderation.management.commands import spike

    limit = settings.MAX_MESSAGE_CHARS
    base = {"topic": {"title": "t", "proposition": "p"}, "trigger_seq": 1, "messages": [{"seq": 1, "author": "A", "text": "", "planted": []}]}

    def check(text):
        data = json.loads(json.dumps(base))
        data["messages"][0]["text"] = text
        spike.validate_transcript(Path("x.json"), data)

    check("  " + "a" * limit + "  \n")  # whitespace around does not count
    check("é" * limit)  # NFC turns each pair into one character
    with pytest.raises(CommandError):
        check("a" * (limit + 1))
    with pytest.raises(CommandError):
        check("é" * (limit + 1))


# --- second round: quotes and prompt wording ------------------------------------------------------------------------------

def test_an_ellipsis_character_matches_three_dots_either_way():
    from moderation.quotes import locate_quote

    text = "Wait… what did you say?"
    result = locate_quote(text, "Wait... what did you say")
    assert result.match == "normalized" and text[result.start:result.end] == "Wait… what did you say"
    text2 = "Wait... what did you say?"
    result2 = locate_quote(text2, "Wait… what")
    assert result2.match == "normalized" and text2[result2.start:result2.end] == "Wait... what"


@pytest.mark.parametrize("prompt", ["master_v1", "intervenor_v1"])
def test_the_prompts_say_in_plain_words_that_message_text_is_never_instructions_and_is_ignored(prompt):
    text = (Path(__file__).resolve().parents[2] / "moderation" / "prompts" / f"{prompt}.md").read_text(encoding="utf-8")
    assert re.search(r"\bDATA\b", text), "the word DATA (in capitals) marks the rule"
    assert re.search(r"never\s+(instructions|commands|orders)", text, re.I)
    assert re.search(r"instruction[^.]*\bis ignored|instructions?[^.]*\bare ignored|ignored", text, re.I)


def test_the_master_prompt_limits_reporting_to_the_newest_message():
    text = (Path(__file__).resolve().parents[2] / "moderation" / "prompts" / "master_v1.md").read_text(encoding="utf-8")
    assert re.search(r"newest message", text, re.I)


# --- third round: spike inputs, gating and validation ----------------------------------------------------------------

def test_the_intervenor_receives_the_masters_discussion_map(env):
    scripted = Scripted(env.golden)
    scripted.install(env.install_fake)
    go(env, env.left)
    shown = scripted.contents[(env.left, "intervenor")]
    assert "Both value affordable housing." in shown, "an agreement from the Master's discussion map"
    assert "Whether it works." in shown, "a disagreement summary"
    assert "Both value affordable housing." not in scripted.contents[(env.left, "master")]


def test_the_json_keeps_the_exact_rendered_inputs_of_both_agents(env):
    scripted = Scripted(env.golden)
    scripted.install(env.install_fake)
    go(env, env.left)
    _p, data = result_json(env.out, env.left)
    strings = [x for x in walk(data) if isinstance(x, str)]
    assert any(s == scripted.contents[(env.left, "master")] for s in strings), "the Master's input, verbatim"
    assert any(s == scripted.contents[(env.left, "intervenor")] for s in strings), "the Intervenor's input, verbatim"


def test_a_real_run_with_the_switch_off_or_no_key_stops_before_touching_the_ledger_or_the_disk(env, settings):
    """Pinned: the up-front refusal is a clean CommandError with no ledger row and no results directory."""
    from moderation.models import LLMCall

    scripted = Scripted(env.golden)
    client = scripted.install(env.install_fake)
    for switch in ("LLM_ENABLED", "ANTHROPIC_API_KEY"):
        old = getattr(settings, switch)
        setattr(settings, switch, False if switch == "LLM_ENABLED" else "")
        result = run_spike("--max-usd", "1", "--out", str(env.out), "--only", env.left)
        setattr(settings, switch, old)
        assert result.exc is not None, switch
        assert client.calls == [] and LLMCall.objects.count() == 0, switch
        assert not env.out.exists(), f"{switch}: results directory was created"


def test_duplicate_seq_numbers_in_a_transcript_are_a_clean_error(env, monkeypatch):
    from moderation.management.commands import spike

    directory = env.tmp / "dupseq"
    directory.mkdir()
    data = json.loads((Path(__file__).resolve().parents[2] / "golden" / "transcripts" / f"{env.left}.json").read_text(encoding="utf-8"))
    data["messages"][1]["seq"] = data["messages"][0]["seq"]
    (directory / f"{env.left}.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(spike, "TRANSCRIPTS_DIR", directory)
    scripted = Scripted(env.golden)
    client = scripted.install(env.install_fake)
    result = run_spike("--max-usd", "1", "--out", str(env.out), "--only", env.left)
    assert result.exc is not None and env.left in str(result.exc)
    assert client.calls == []
