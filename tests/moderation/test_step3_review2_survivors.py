"""Tests added after the independent mutation pass: each one kills a mutation that the first-round tests let survive."""
import json
import re
from pathlib import Path

import pytest
from step3_testkit import parse_rendered


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


def test_a_message_of_multibyte_characters_under_the_limit_is_accepted(settings):
    """The limit counts characters (code points), not bytes: 2,900 emoji is 11,600 bytes and must still pass."""
    from django.core.management.base import CommandError

    from moderation import transcripts

    def data_with(text):
        return {"topic": {"title": "t", "proposition": "p"}, "trigger_seq": 1, "messages": [{"seq": 1, "author": "A", "text": text, "planted": []}]}

    transcripts.validate_transcript(Path("x.json"), data_with("\U0001F600" * (settings.MAX_MESSAGE_CHARS - 100)))  # must not raise
    with pytest.raises(CommandError):
        transcripts.validate_transcript(Path("x.json"), data_with("\U0001F600" * (settings.MAX_MESSAGE_CHARS + 1)))


def test_the_length_rule_strips_and_uses_nfc(settings):
    from django.core.management.base import CommandError

    from moderation import transcripts

    limit = settings.MAX_MESSAGE_CHARS
    base = {"topic": {"title": "t", "proposition": "p"}, "trigger_seq": 1, "messages": [{"seq": 1, "author": "A", "text": "", "planted": []}]}

    def check(text):
        data = json.loads(json.dumps(base))
        data["messages"][0]["text"] = text
        transcripts.validate_transcript(Path("x.json"), data)

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
