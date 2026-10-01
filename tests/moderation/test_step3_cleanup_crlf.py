"""Step 3 cleanup, job 1 (docs/step3_cleanup_brief.md): transcript validation counts message length exactly like real users.

`transcripts.validate_transcript` must use `forum.limits.count_message_chars` (CRLF and lone CR become one newline, NFC, strip,
count code points), not its own copy and not a raw `len`. The replay command's CRLF workaround is redundant once that is
true, so it is gone; the replay behaviour itself is pinned by tests/replay/.
"""
import inspect
from pathlib import Path

import pytest

LIMIT = 80

# (text, hand-computed count under the project rule). One row per behaviour of the rule.
CASES = [
    ("hello", 5),
    ("hello world", 11),
    ("  padded  ", 6),
    ("\n\nline\n\n", 4),
    ("a\r\nb", 3),
    ("a\rb", 3),
    ("a\nb", 3),
    ("a\r\n\r\nb", 4),
    ("a\r\r\nb", 4),
    ("a\n\rb", 4),
    ("\r\nabc\r\n", 3),
    ("cafe\u0301", 4),
    ("caf\u00e9", 4),
    ("e\u0301" * 5, 5),
    ("\U0001F600", 1),
    ("\U0001F600" * 3, 3),
    ("\U0001F468\u200d\U0001F469\u200d\U0001F467", 5),
    ("\U0001F44D\U0001F3FD", 2),
    ("\u1100\u1161\u11a8", 1),
    ("A\u030a", 1),
    ("\u212b", 1),
    ("\ufb01", 1),
    ("a\u00a0", 1),
    ("\u3000\u5168\u89d2\u3000", 2),
    ("\t tab \t", 3),
    ("a b", 3),
    ("a  b", 4),
    ("\u200bhi", 3),
    ("\x0bvt\x0c", 2),
    ("\x1cfs\x1d", 2),
    ("\u2028ls\u2029", 2),
    ("a\u2028b", 3),
    ("\x85nel\x85", 3),
    ("\u00fcn\u00ef", 3),
    ("u\u0308n\u0308i\u0308", 4),  # n + diaeresis has no precomposed form
    ("a" * 80, 80),
    ("a" * 39 + "\r\n" + "b" * 40, 80),
    ("a" * 40 + "\r\n" + "b" * 40, 81),
    ("line\r\n" * 20, 99),
    ("line\r" * 10, 49),
    ("\r\n".join(["x"] * 50), 99),
    ("\U0001F600\r\n\U0001F600", 3),
    ("e\u0301\r\ne\u0301", 3),
    ("a\x00b", 3),
    ("\u0301", 1),
]
IDS = [f"case{n:02d}" for n in range(len(CASES))]


def transcript(*texts):
    """A minimal valid transcript: one participant message per text, the last one the trigger."""
    messages = [{"seq": n, "author": "Participant A" if n % 2 else "Participant B", "text": text, "planted": []} for n, text in enumerate(texts, start=1)]
    return {"topic": {"title": "t", "proposition": "p"}, "trigger_seq": len(messages), "messages": messages}


def validate(data):
    from moderation import transcripts

    transcripts.validate_transcript(Path("crlf_probe.json"), data)


def rejected(data):
    """The CommandError text that validating `data` raises."""
    from django.core.management.base import CommandError

    with pytest.raises(CommandError) as caught:
        validate(data)
    return str(caught.value)


# --- the exact boundary from the brief -----------------------------------------------------------------------------------

def test_eighty_characters_with_a_crlf_pass_at_limit_eighty(settings):
    settings.MAX_MESSAGE_CHARS = LIMIT
    validate(transcript("a" * 39 + "\r\n" + "b" * 40))  # 80 by the project counter, 81 raw


def test_eighty_one_characters_with_a_crlf_fail_at_limit_eighty(settings):
    settings.MAX_MESSAGE_CHARS = LIMIT
    message = rejected(transcript("a" * 40 + "\r\n" + "b" * 40))  # 81 by the project counter, 82 raw
    assert ("over MAX_MESSAGE_CHARS" in message, "81 characters" in message, "seq 1" in message) == (True, True, True)


def test_a_lone_cr_counts_as_one_character(settings):
    settings.MAX_MESSAGE_CHARS = LIMIT
    validate(transcript("a" * 39 + "\r" + "b" * 40))


def test_many_crlfs_that_only_push_the_raw_length_over_pass(settings):
    settings.MAX_MESSAGE_CHARS = LIMIT
    validate(transcript("\r\n".join(["x"] * 40)))  # 79 by the project counter, 118 raw


def test_many_crlfs_are_still_counted_once_each_when_over_the_limit(settings):
    settings.MAX_MESSAGE_CHARS = LIMIT
    message = rejected(transcript("\r\n".join(["x"] * 41)))  # 81 by the project counter
    assert "81 characters" in message


def test_the_crlf_rule_applies_to_every_message_not_only_the_first(settings):
    settings.MAX_MESSAGE_CHARS = LIMIT
    validate(transcript("short", "short", "a" * 39 + "\r\n" + "b" * 40, "short"))


def test_an_over_limit_message_is_still_named_when_another_message_only_has_crlfs(settings):
    settings.MAX_MESSAGE_CHARS = LIMIT
    message = rejected(transcript("a" * 39 + "\r\n" + "b" * 40, "c" * 81))
    assert ("seq 2" in message, "81 characters" in message) == (True, True)


def test_the_limit_is_read_from_the_setting_at_call_time(settings):
    settings.MAX_MESSAGE_CHARS = 3000
    validate(transcript("a" * 1499 + "\r\n" + "b" * 1500))
    settings.MAX_MESSAGE_CHARS = 2999
    assert "3000 characters" in rejected(transcript("a" * 1499 + "\r\n" + "b" * 1500))


# --- same answer as the rule real users are held to ----------------------------------------------------------------------

def test_the_table_is_about_forty_strings_with_distinct_behaviours():
    assert (len(CASES), len({text for text, _n in CASES})) == (45, 45)


@pytest.mark.parametrize(("text", "expected"), CASES, ids=IDS)
def test_the_hand_computed_counts_match_the_project_counter(text, expected):
    """Guards the table itself: a wrong expectation here would make the two tests below prove nothing."""
    from forum.limits import count_message_chars

    assert count_message_chars(text) == expected


@pytest.mark.parametrize(("text", "expected"), CASES, ids=IDS)
def test_a_message_of_exactly_the_counted_length_is_accepted(settings, text, expected):
    settings.MAX_MESSAGE_CHARS = expected
    validate(transcript(text))


@pytest.mark.parametrize(("text", "expected"), CASES, ids=IDS)
def test_a_message_one_over_the_counted_length_is_rejected_reporting_the_count(settings, text, expected):
    settings.MAX_MESSAGE_CHARS = expected - 1
    assert f"{expected} characters" in rejected(transcript(text))


@pytest.mark.parametrize("text", ["", " ", "\r\n", "\r", "\n\n\t \u00a0"], ids=["empty", "space", "crlf", "cr", "whitespace"])
def test_a_message_of_only_whitespace_counts_zero_and_passes_the_length_check(settings, text):
    settings.MAX_MESSAGE_CHARS = 1
    validate(transcript(text))


def test_no_other_rule_changed_a_message_of_the_wrong_type_is_still_refused(settings):
    settings.MAX_MESSAGE_CHARS = LIMIT
    data = transcript("fine")
    data["messages"][0]["text"] = 5
    assert "needs a string 'author' and a string 'text'" in rejected(data)


def test_no_other_rule_changed_a_missing_text_key_is_still_refused(settings):
    settings.MAX_MESSAGE_CHARS = LIMIT
    data = transcript("fine")
    del data["messages"][0]["text"]
    assert "missing required key 'messages[1].text'" in rejected(data)


# --- the validator uses the shared counter, not a copy ---------------------------------------------------------------------

def test_the_transcripts_module_imports_the_project_counter_itself():
    from forum import limits
    from moderation import transcripts

    assert transcripts.count_message_chars is limits.count_message_chars


def test_the_transcripts_module_does_not_carry_its_own_copy_of_the_normalisation():
    from moderation import transcripts

    source = inspect.getsource(transcripts)
    assert ('.replace("\\r\\n"' in source, ".replace('\\r\\n'" in source, "_counted_chars" in source) == (False, False, False)


def test_the_validator_uses_whatever_the_shared_counter_says(monkeypatch, settings):
    """Behavioural proof that validate_transcript calls the imported counter: a stub that always says 1 accepts anything."""
    from moderation import transcripts

    settings.MAX_MESSAGE_CHARS = LIMIT
    monkeypatch.setattr(transcripts, "count_message_chars", lambda text: 1)
    validate(transcript("z" * 5000))


# --- the replay workaround is gone and replay behaviour is unchanged -----------------------------------------------------

def test_the_replay_crlf_workaround_is_removed():
    from moderation import replay

    assert hasattr(replay, "_newlines_normalized") is False


def replay_pairs(*texts):
    data = transcript(*texts)
    data["id"] = "crlf_probe"
    return [(Path("crlf_probe.json"), data)]


def test_replay_hands_the_original_transcript_to_the_validator_not_a_copy(monkeypatch, settings):
    from moderation import replay, transcripts

    settings.MAX_MESSAGE_CHARS = LIMIT
    seen = []
    real = transcripts.validate_transcript

    def recording(path, data, known_ids=None):
        seen.append(data)
        return real(path, data, known_ids=known_ids)

    monkeypatch.setattr(transcripts, "validate_transcript", recording)
    pairs = replay_pairs("a" * 39 + "\r\n" + "b" * 40)
    replay.validate_transcripts(pairs)
    assert (len(seen), seen[0] is pairs[0][1]) == (1, True)


def test_replay_validation_still_accepts_the_crlf_boundary(settings):
    from moderation import replay

    settings.MAX_MESSAGE_CHARS = LIMIT
    replay.validate_transcripts(replay_pairs("a" * 39 + "\r\n" + "b" * 40))


def test_replay_validation_still_rejects_one_over_with_a_crlf(settings):
    from django.core.management.base import CommandError

    from moderation import replay

    settings.MAX_MESSAGE_CHARS = LIMIT
    with pytest.raises(CommandError):
        replay.validate_transcripts(replay_pairs("a" * 40 + "\r\n" + "b" * 40))
