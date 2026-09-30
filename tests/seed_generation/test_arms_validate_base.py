"""seeding.arms.validate_base: each rule of the contract (docs/step12_generator_brief.md)."""
import gen_kit as kit
import pytest

from config import tunables


def check(base):
    from seeding import arms

    return arms.validate_base(base)


def base_error():
    from seeding import arms

    return arms.BaseError


class TestAValidBase:
    def test_exactly_four_messages_pass(self):
        assert check(kit.base(count=4)) is None

    def test_the_shipped_range_is_exactly_four(self):
        from config import tunables

        assert (tunables.GENERATOR_MIN_MESSAGES, tunables.GENERATOR_MAX_MESSAGES) == (4, 4)

    @pytest.mark.parametrize("count", [4, 6])
    def test_the_limits_of_a_widened_range_pass(self, count, tune):
        tune(GENERATOR_MIN_MESSAGES=4, GENERATOR_MAX_MESSAGES=6)
        assert check(kit.base(count=count)) is None

    @pytest.mark.parametrize("side", ["left", "right"])
    def test_either_side_passes(self, side):
        assert check(kit.base(side)) is None

    def test_the_error_is_a_value_error(self):
        assert issubclass(base_error(), ValueError)

    def test_the_marker_constant(self):
        from seeding import arms

        assert arms.MARKER == "[[CLAIM]]"

    def test_the_input_is_not_changed(self):
        source = kit.base()
        before = kit.edited(source)
        check(source)
        assert source == before


class TestMessageCount:
    @pytest.mark.parametrize("count", [2, 3])
    def test_below_the_minimum_is_refused(self, count):
        with pytest.raises(base_error()):
            check(kit.base(count=count))

    @pytest.mark.parametrize("count", [5, 6, 7, 8])
    def test_above_the_maximum_is_refused(self, count):
        with pytest.raises(base_error()):
            check(kit.base(count=count))

    def test_the_range_is_read_from_the_tunables_at_call_time(self, tune):
        tune(GENERATOR_MIN_MESSAGES=2, GENERATOR_MAX_MESSAGES=2)
        assert check(kit.base(count=2)) is None
        with pytest.raises(base_error()):
            check(kit.base(count=4))

    def test_no_messages_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.edited(kit.base(), messages=[]))


class TestAuthors:
    def test_it_must_start_with_participant_a(self):
        source = kit.base()
        swapped = kit.edited(source, messages=[dict(m, author="Participant B" if m["author"] == "Participant A" else "Participant A") for m in source["messages"]])
        with pytest.raises(base_error()):
            check(swapped)

    def test_it_must_alternate(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 1, author="Participant A"))

    def test_it_must_end_with_participant_b(self):
        with pytest.raises(base_error()):
            check(kit.base(count=5))

    def test_an_unknown_author_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 0, author="Moderator"))


class TestSeq:
    @pytest.mark.parametrize("seqs", [[0, 1, 2, 3], [2, 3, 4, 5], [1, 2, 4, 5], [1, 1, 2, 3], [1, 3, 2, 4]])
    def test_seq_must_be_1_to_n_in_order(self, seqs):
        source = kit.base()
        broken = kit.edited(source, messages=[dict(m, seq=s) for m, s in zip(source["messages"], seqs)])
        with pytest.raises(base_error()):
            check(broken)


class TestMarker:
    def test_no_marker_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 3, text="I have no marker here."))

    def test_two_markers_in_the_last_message_are_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 3, text="One. [[CLAIM]]. Two. [[CLAIM]]. Done."))

    def test_a_marker_in_another_message_as_well_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 0, text="Early. [[CLAIM]]. Words."))

    def test_the_marker_only_in_an_earlier_message_is_refused(self):
        source = kit.with_message(kit.base(), 3, text="No marker in the last one.")
        with pytest.raises(base_error()):
            check(kit.with_message(source, 1, text="Middle. [[CLAIM]]. Words."))

    def test_the_marker_in_the_last_message_alone_passes(self):
        assert check(kit.with_message(kit.base(), 3, text="[[CLAIM]].")) is None


class TestTexts:
    @pytest.mark.parametrize("text", ["", "   ", "\n\t"])
    def test_an_empty_text_is_refused(self, text):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 1, text=text))

    def test_a_message_of_exactly_the_maximum_passes(self):
        assert check(kit.with_message(kit.base(), 0, text="x" * tunables.MAX_MESSAGE_CHARS)) is None

    def test_a_message_over_the_maximum_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 0, text="x" * (tunables.MAX_MESSAGE_CHARS + 1)))

    def test_a_last_message_over_the_maximum_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 3, text="x" * tunables.MAX_MESSAGE_CHARS + " [[CLAIM]]."))


class TestSide:
    def test_an_unknown_side_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.edited(kit.base(), side="centre"))


class TestMarkerStartsASentence:
    """Ruling of 2026-09-30: the marker is the very start of the message, or preceded (ignoring spaces) by . ! or ?."""

    @pytest.mark.parametrize("text", [
        "[[CLAIM]].",
        "Words here. [[CLAIM]].",
        "Words here.[[CLAIM]].",
        "Words here.    [[CLAIM]].",
        "Is that so? [[CLAIM]].",
        "Look at this! [[CLAIM]].",
        "Two sentences. Then more! [[CLAIM]].",
        "Two sentences. Then more! [[CLAIM]]",
    ])
    def test_a_marker_at_a_sentence_start_passes(self, text):
        assert check(kit.with_message(kit.base(), 3, text=text)) is None

    @pytest.mark.parametrize("text", [
        "I think that [[CLAIM]].",
        "As they said: [[CLAIM]].",
        "As they said, [[CLAIM]].",
        'She said "[[CLAIM]]".',
        "He said “[[CLAIM]].”",
        "Consider this; [[CLAIM]].",
        "It is true that[[CLAIM]].",
        "Words here - [[CLAIM]].",
        "Words here (see [[CLAIM]]).",
        "Words here, and [[CLAIM]].",
    ])
    def test_a_marker_in_any_other_position_is_refused(self, text):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 3, text=text))

    def test_a_marker_after_a_lowercase_word_at_the_start_of_the_message_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 3, text="but [[CLAIM]]."))


class TestNoDigitsInABase:
    """Ruling of 2026-09-30: any ASCII digit 0-9 in any message text is refused."""

    @pytest.mark.parametrize("digit", list("0123456789"))
    def test_every_digit_is_refused_in_an_early_message(self, digit):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 0, text=f"There were {digit} of them, I think."))

    def test_a_digit_in_the_last_message_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 3, text="I recall 7 cases. [[CLAIM]]."))

    def test_a_digit_after_the_marker_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 3, text="Yes. [[CLAIM]]. I say 3 things follow."))

    def test_a_digit_inside_a_word_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 1, text="The covid19 years were hard."))

    def test_numbers_written_in_words_pass(self):
        assert check(kit.with_message(kit.base(), 1, text="I think three or four cases come to mind, maybe a hundred.")) is None

    def test_non_ascii_digits_are_not_the_rule(self):
        assert check(kit.with_message(kit.base(), 1, text="Fractions like ½ and superscripts like ² are not ASCII digits here.")) is None

    def test_the_pair_check_refuses_a_digit_too(self):
        from seeding import arms

        with pytest.raises(arms.BaseError):
            arms.check_pair(kit.base("left"), kit.with_message(kit.base("right"), 0, text="The first turn of the right conversation has 2 things " + "thing " * 20))

    def test_build_transcripts_refuses_a_digit_too(self):
        from seeding import arms

        with pytest.raises(arms.BaseError):
            kit.transcripts(kit.range_fact(), kit.base("left"), kit.with_message(kit.base("right"), 0, text="The first turn of the right conversation has 2 things " + "thing " * 20))


class TestDigitAtTheEdges:
    def test_a_digit_as_the_last_character_of_a_message_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 1, text="This message ends with a digit 7"))

    def test_a_digit_as_the_first_character_of_a_message_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 0, text="7 was the number that came to mind."))


class TestMarkerEndsTheMessage:
    """Ruling after run 3: nothing follows the marker except, optionally, one full stop (trailing whitespace is ignored)."""

    @pytest.mark.parametrize("text", ["Words. [[CLAIM]]", "Words. [[CLAIM]].", "Words. [[CLAIM]].  ", "Words. [[CLAIM]]\n", "[[CLAIM]]"])
    def test_a_message_ending_with_the_marker_passes(self, text):
        assert check(kit.with_message(kit.base(), 3, text=text)) is None

    @pytest.mark.parametrize("text", [
        "Words. [[CLAIM]]. More words.",
        "Words. [[CLAIM]] and more",
        "Words. [[CLAIM]]..",
        "Words. [[CLAIM]]!",
        "Words. [[CLAIM]]?",
        "Words. [[CLAIM]]. .",
        "Words. [[CLAIM]].\nNext line.",
        "Words. [[CLAIM]],",
    ])
    def test_anything_after_the_marker_is_refused(self, text):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 3, text=text))


BANNED = ["widespread", "growing", "handful", "majority", "minority", "surge", "mainstream", "fringe", "spreading"]


class TestBannedWordsInTheLastMessage:
    def test_the_constant_lists_the_nine_words(self):
        from seeding import arms

        assert sorted(arms.BANNED_WORDS) == sorted(BANNED)

    @pytest.mark.parametrize("word", BANNED)
    def test_each_word_is_refused(self, word):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 3, text=f"I hold that it is {word} here. [[CLAIM]]."))

    @pytest.mark.parametrize("word", BANNED)
    def test_each_word_is_refused_in_capitals(self, word):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 3, text=f"I hold that it is {word.upper()} here. [[CLAIM]]."))

    @pytest.mark.parametrize("word", BANNED)
    def test_each_word_is_refused_at_the_start_with_a_capital_and_punctuation(self, word):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 3, text=f"{word.capitalize()}, I hold this view. [[CLAIM]]."))

    @pytest.mark.parametrize("word", BANNED)
    def test_a_word_that_merely_contains_it_passes(self, word):
        assert check(kit.with_message(kit.base(), 3, text=f"I hold that {word}ish and un{word} things differ. [[CLAIM]].")) is None

    @pytest.mark.parametrize("word", BANNED)
    def test_the_words_are_allowed_in_earlier_messages(self, word):
        assert check(kit.with_message(kit.base(), 1, text=f"Some say that {word} people agree, and I disagree with them.")) is None

    def test_a_banned_word_hyphenated_still_counts_as_a_word(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base(), 3, text="This is a widespread-ish view. [[CLAIM]]."))

    @pytest.mark.parametrize("word", ["most", "many", "only", "few"])
    def test_removed_words_are_no_longer_banned(self, word):
        assert check(kit.with_message(kit.base(), 3, text=f"I think {word} people would weigh this carefully. [[CLAIM]].")) is None

    @pytest.mark.parametrize("word", ["most", "many", "only", "few", "Only", "FEW"])
    def test_removed_words_are_not_in_the_constant(self, word):
        from seeding import arms

        assert word.lower() not in arms.BANNED_WORDS
