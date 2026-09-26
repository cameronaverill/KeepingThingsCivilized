"""Users never see the A/B labels (owner decision, 2026-09-25): the Intervenor's act text must not name participants, and the
spike report checks it (report-only). Prompt-file tests assert on distinctive rules; report tests use FakeLLM.

Definition used here (the builder's regex is r"\\bParticipants?\\s+[A-Z]\\b" plus a standalone bare or quoted label used as a
name, case-sensitive): "Participant A", "Participant B" and "Participants A and B" are flagged; lower-case "participant a",
ordinary English such as a sentence starting with "A source ..." and "the claim in message 4" are not. Bare or quoted single
letters as names ("B should ...") are NOT tested either way (ambiguous, reported).
"""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from step3_testkit import (
    PROMPT_DIR,
    Scripted,
    act_dict,
    benign_single,
    disposition_dict,
    find_pair,
    find_report,
    golden,  # noqa: F401
    intervenor_output,
    markdown_tables,
    real_results_untouched,  # noqa: F401
    result_json,
    run_spike,
    spike_env,  # noqa: F401
    walk,
)


def prompt(name):
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


# --- (1) prompt files ---------------------------------------------------------------------------------------------------

def test_the_intervenor_prompt_no_longer_tells_the_model_to_name_participants_by_label():
    text = prompt("intervenor_v1")
    assert not re.search(r"Name participants only by their labels", text, re.I)
    assert not re.search(r"name\s+participants[^.\n]*by\s+their\s+labels?", text, re.I)


def lines_with(text, *needles):
    return [l for l in text.splitlines() if all(re.search(n, l, re.I) for n in needles)]


def test_the_intervenor_prompt_forbids_labels_and_names_in_the_act_text():
    text = prompt("intervenor_v1")
    rule = lines_with(text, r"\btext\b", r"Participant\s+[AB]", r"\b(never|must not|do not|may not)\b")
    assert rule, "a rule saying the act `text` never contains 'Participant A/B'"
    assert re.search(r"\bname\b", "\n".join(rule), re.I) or re.search(r"(any|a)\s+name", text, re.I)


def test_the_intervenor_prompt_says_to_refer_to_messages_and_content_with_examples():
    text = prompt("intervenor_v1")
    assert re.search(r"message\s+4", text), "an example such as 'message 4'"
    assert re.search(r"the phrase", text, re.I)
    assert re.search(r"question in message\s+3", text, re.I)


def test_the_intervenor_prompt_forbids_you_your_and_pronouns_about_a_participant():
    text = prompt("intervenor_v1")
    rule = lines_with(text, r"\byou\b", r"\byour\b", r"\b(never|no|not|avoid)\b")
    assert rule, "a rule against 'you' / 'your' about a participant"
    pron = lines_with(text, r"\b(he|she|they)\b", r"\b(never|no|not|avoid)\b")
    assert pron, "a rule against he / she / they about a participant"
    assert re.search(r"impersonal", text, re.I)


def test_the_intervenor_prompt_keeps_the_swap_test_and_the_neutrality_rules():
    text = prompt("intervenor_v1")
    assert re.search(r"swap test", text, re.I)
    assert re.search(r"same (wording|words)", text, re.I) or re.search(r"either side", text, re.I)
    assert "politic" in text.lower() and "infer" in text.lower()


def test_the_intervenor_prompt_still_documents_the_label_values_of_the_structured_fields():
    text = prompt("intervenor_v1")
    addressee = lines_with(text, r"`addressee`")
    subject = lines_with(text, r"`subject`")
    assert addressee and subject
    for value in ("Participant A", "Participant B", "all"):
        assert any(value in l for l in addressee), f"addressee should still list {value!r}"
    for value in ("Participant A", "Participant B", "both", "none"):
        assert any(value in l for l in subject), f"subject should still list {value!r}"


def test_the_intervenor_prompt_says_labels_are_for_the_structured_fields_only():
    text = prompt("intervenor_v1")
    assert re.search(r"labels?[^.\n]*(structured|fields?)[^.\n]*(only)|only[^.\n]*(structured|fields?)", text, re.I) or lines_with(
        text, r"\blabels?\b", r"\bonly\b", r"\b(fields?|addressee|subject)\b"
    )


def test_the_master_prompt_is_unchanged_in_behavior_it_still_knows_participants_by_label():
    text = prompt("master_v1")
    assert "Participant A" in text and "Participant B" in text
    assert not re.search(r"act\s+`?text`?[^.\n]*never", text, re.I), "the no-label rule belongs to the Intervenor's output only"


# --- (2) the spike report ------------------------------------------------------------------------------------------------

CHECK = "Post text names a participant label"


class LabelScript(Scripted):
    """The Intervenor acts with texts chosen per transcript: `texts[tid]` is a list of act texts (default: one clean act)."""

    def __init__(self, transcripts, texts):
        super().__init__(transcripts)
        self.texts = texts

    def intervenor(self, tid):
        t = self.transcripts[tid]
        issues = self.master_issues(tid)
        texts = self.texts.get(tid)
        if not issues or texts is None and not issues:
            return intervenor_output("no_intervention", f"rationale-{tid}")
        message, planted = next((m, p) for m in t["messages"] for p in m["planted"])
        texts = texts or ["Please point to a source for the claim in message 4."]
        return intervenor_output(
            "intervene", f"rationale-{tid}",
            dispositions=[disposition_dict(1, "acted", f"reason-{tid}-1"), disposition_dict(2, "declined", f"reason-{tid}-2"), disposition_dict(3, "declined", f"reason-{tid}-3")],
            acts=[act_dict(type="correct_factual_error", addressee="all", subject="both", issue_ns=(1,), seqs=(message["seq"],), tone="gentle", text=x) for x in texts],
        )


@pytest.fixture
def env(spike_env, golden, install_fake, tmp_path):  # noqa: F811
    left, right = find_pair(golden, "factual_accuracy")
    return SimpleNamespace(golden=golden, install_fake=install_fake, out=tmp_path / "res", left=left["id"], right=right["id"], tmp=tmp_path)


def go(env, texts, *ids):
    script = LabelScript(env.golden, texts)
    script.install(env.install_fake)
    result = run_spike("--max-usd", "5", "--out", str(env.out), "--only", *ids)
    assert result.exc is None, result.text
    return result, find_report(env.out).read_text(encoding="utf-8")


def flag_of(env, tid):
    """The per-transcript JSON's check: result["label_check"] = {"acts": n, "naming_label": k, "act_numbers": [...]}."""
    _p, data = result_json(env.out, tid)
    check = data["label_check"]
    assert set(check) == {"acts", "naming_label", "act_numbers"}, check
    assert check["naming_label"] == len(check["act_numbers"])
    return check["naming_label"] > 0


NAMING = ["Participant B, please point to a source.", "Participant A raised a fair question.", "Both Participant A and Participant B agree.",
          "Participants A and B should keep to the topic.", "B's message needs a source."]
CLEAN = ["Please point to a source for the claim in message 4.", "A source would help here.", "The question in message 3 is still open.",
         "The quoted phrase in message 4 needs a source.", "Plan B. is one option.", "Option A is fine.", "Vitamin B, and C are cited."]


@pytest.mark.parametrize("text", NAMING)
def test_an_act_text_naming_a_participant_label_is_flagged_in_the_json_the_table_and_the_header(env, text):
    result, report = go(env, {env.left: [text]}, env.left, env.right)
    assert flag_of(env, env.left), text
    assert not flag_of(env, env.right)
    assert CHECK.lower() in report.lower()
    rows = [l for l in report.splitlines() if CHECK.lower() in l.lower() and l.strip().startswith("|")]
    assert rows, "a row in the pair comparison table"
    assert re.search(r"Acts naming a participant label \(moderator posts must not name anyone\):\s*1 of 2\b", report + result.out), (report + result.out)[:600]


@pytest.mark.parametrize("text", CLEAN)
def test_clean_act_texts_are_not_flagged(env, text):
    result, report = go(env, {env.left: [text], env.right: [text]}, env.left, env.right)
    assert not flag_of(env, env.left) and not flag_of(env, env.right)
    assert re.search(r"Acts naming a participant label \(moderator posts must not name anyone\):\s*0 of 2\b", report + result.out)


def test_both_variants_naming_a_label_count_two_and_the_pair_row_says_they_match(env):
    result, report = go(env, {env.left: [NAMING[0]], env.right: [NAMING[1]]}, env.left, env.right)
    assert flag_of(env, env.left) and flag_of(env, env.right)
    assert re.search(r"Acts naming a participant label \(moderator posts must not name anyone\):\s*2 of 2\b", report + result.out)
    row = next(l for l in report.splitlines() if CHECK.lower() in l.lower() and l.strip().startswith("|"))
    assert re.search(r"\bsame\b", row, re.I) and "DIFFERENT" not in row


def test_when_only_one_side_names_a_label_the_pair_row_says_different(env):
    _result, report = go(env, {env.left: [NAMING[0]]}, env.left, env.right)
    row = next(l for l in report.splitlines() if CHECK.lower() in l.lower() and l.strip().startswith("|"))
    assert "DIFFERENT" in row


def test_the_header_counts_acts_not_transcripts(env, golden):  # noqa: F811
    benign = benign_single(golden)["id"]
    _result, report = go(env, {env.left: ["A source would help.", NAMING[0]], env.right: ["A source would help.", "Please cite it."]}, env.left, env.right, benign)
    assert re.search(r"Acts naming a participant label \(moderator posts must not name anyone\):\s*1 of 4\b", report), report[:800]


def test_a_transcript_with_no_acts_is_not_flagged(env, golden):  # noqa: F811
    benign = benign_single(golden)["id"]
    go(env, {}, env.left, env.right, benign)
    assert not flag_of(env, benign)


def test_the_check_is_report_only_the_run_still_succeeds_and_nothing_is_rejected(env):
    result, _report = go(env, {env.left: NAMING, env.right: NAMING}, env.left, env.right)
    assert result.exc is None
    for tid in (env.left, env.right):
        _p, data = result_json(env.out, tid)
        assert data["status"] == "ok"
        acts = [d for d in walk(data) if isinstance(d, dict) and "tone" in d and "type" in d]
        assert len(acts) >= len(NAMING), "every act is kept as written"


def test_the_series_tables_carry_the_row_too(env, golden):  # noqa: F811
    ids = sorted(t["id"] for t in golden.values() if (t.get("series") or {}).get("id") == "length_factual" and t["series"]["level"] == "short")
    assert len(ids) == 2
    _result, report = go(env, {}, *ids)
    blocks = [b for b in markdown_tables(report) if CHECK.lower() in b.lower()]
    assert len(blocks) >= 1, "the series comparison also has the row"


# --- pinned rulings on what counts as naming a label (architect, 2026-09-25) --------------------------------------------------
# Flag ONLY (a) "Participant"/"Participants" + whitespace + an uppercase letter A-Z and (b) an uppercase letter + 's + one of
# message(s), reply/replies, claim(s), point(s), statement(s), argument(s), answer(s), question(s).

FLAGGED = [
    "Participant A", "Participant B, please cite a source.", "Participants A and B should keep to the topic.", "Both Participant B and Participant A agree.",
    "Participant Z", "Participant Alpha", "Participant Ann", "Participants\nB", "Participant  B", "Note for Participant A: keep it short.",
    "B's message needs a source.", "A's claim is unsupported.", "The B's replies were short.", "B's questions are fair.", "A's answer is missing.",
    "See B's point.", "A's statement and B's arguments differ.", "B's messages", "B's message.",
    # (c) viewer-relative references (the insulted reader would read "the other participant" as themselves)
    "This comment is directed at the other participant.", "Please respond to the other person.", "The other side raised a fair point.",
    "Another participant already answered.", "the other party", "Another person asked this.", "Directed at The other participant.",
]
NOT_FLAGGED = [
    "participant b", "participant B", "Participant", "Participants and moderators", "Participant 4",
    "Plan B.", "Plan B is fine.", "Option A is fine.", "Vitamin B, and C.", "Section A: scope.", "A source would help.", "A cap on annual increases",
    "Please give a source for the claim in message 4.", "The question in message 3 has not been answered.",
    'The letter "B" is unclear.', "'A' is a variable here.", "(A) is the first option.", "[B] marks the second.", "\u201cB\u201d",
    "participants in general are welcome", "the other message", "another point", "the other day", "Another message", "the other posts",
    "another participation", "the other personality", "the other sidewalk", "another personal insult",
    "b's message", "B's own view", "A's and B's", "the value of A's", "Bs message", "AB's message", "Type B's", "B'sX message",
]


def names_label(text):
    from moderation.management.commands.spike import names_a_label

    return names_a_label(text)


@pytest.mark.parametrize("text", FLAGGED)
def test_these_texts_are_flagged_as_naming_a_label(text):
    assert names_label(text) is True, text


@pytest.mark.parametrize("text", NOT_FLAGGED)
def test_these_texts_are_not_flagged(text):
    assert names_label(text) is False, text


def test_the_check_is_case_sensitive_on_the_letter_and_the_word_participant_must_be_capitalised_form_or_plural():
    assert names_label("Participant B") and not names_label("Participant b") and not names_label("participant B")
    assert names_label("B's reply") and not names_label("b's reply")


def test_a_label_inside_a_longer_text_is_found_anywhere_in_it():
    text = "Possible factual error in message 4: the figure is far above published estimates. Participant B may wish to cite a source."
    assert names_label(text)
    assert not names_label(text.replace("Participant B may wish", "The author may wish"))


@pytest.mark.parametrize("text", ["B's message needs a source.", "Plan B. is a fallback."])
def test_end_to_end_only_the_ruled_cases_reach_the_report(env, text):
    result, report = go(env, {env.left: [text]}, env.left, env.right)
    assert flag_of(env, env.left) is names_label(text)
    expected = "1 of 2" if names_label(text) else "0 of 2"
    assert re.search(rf"Acts naming a participant label \(moderator posts must not name anyone\):\s*{expected}\b", report + result.out)


# --- (c) "the other participant" style references and the new prompt rule ---------------------------------------------------------

@pytest.mark.parametrize("text", ["the other participant", "The other person", "The other side", "the other party", "Another participant", "another person"])
def test_viewer_relative_references_flag_in_either_capitalisation_of_the_first_letter(text):
    assert names_label(text) is True, text


@pytest.mark.parametrize("text", ["THE OTHER PARTICIPANT", "The Other Participant", "ANOTHER PARTICIPANT", "another party", "another side", "a participant"])
def test_only_the_ruled_forms_of_the_viewer_relative_pattern_flag(text):
    """Case-sensitive except for the first letter of "The"/"Another"; "another party/side" and a bare "a participant" are not in
    the report-only regex (the prompt still forbids them)."""
    assert names_label(text) is False, text


def test_a_viewer_relative_act_reaches_the_report_as_naming(env):
    text = "Message 4 is directed at the other participant."
    result, report = go(env, {env.left: [text]}, env.left, env.right)
    assert flag_of(env, env.left) is True and flag_of(env, env.right) is False
    assert re.search(r"Acts naming a participant label \(moderator posts must not name anyone\):\s*1 of 2\b", report + result.out)


def test_the_impersonal_example_is_clean():
    assert names_label("Message 4 contains a personal insult.") is False


def test_the_intervenor_prompt_forbids_the_other_participant_style_references():
    text = prompt("intervenor_v1")
    for phrase in ("the other participant", "the other person", "the other side", "the other party", "another participant", "a participant"):
        assert phrase in text, f"the prompt must name {phrase!r} as forbidden"
    forbid = lines_with(text, r"the other participant", r"\b(never|must not|do not|not|no)\b")
    assert forbid, "the forbidding rule"


def test_the_intervenor_prompt_forbids_saying_who_a_comment_is_directed_at_or_written_by():
    text = prompt("intervenor_v1")
    assert re.search(r"directed at", text, re.I)
    assert re.search(r"written by", text, re.I)
    rule = lines_with(text, r"directed at", r"written by")
    assert rule and re.search(r"\b(never|must not|do not|not|no)\b", rule[0], re.I), rule


def test_the_intervenor_prompt_gives_the_impersonal_insult_example():
    assert "Message 4 contains a personal insult." in prompt("intervenor_v1")


def test_the_master_prompt_does_not_carry_the_post_wording_rules():
    text = prompt("master_v1")
    assert "Message 4 contains a personal insult." not in text
    assert not re.search(r"directed at", text, re.I)
