"""Direct unit tests of moderation.series.has_repeated_sentence (no pipeline, no LLM, no golden transcripts).

The rest of series.py is exercised transitively through the golden-transcript series tests (test_step3_series_format.py);
this file targets the one function a mutation audit found undertested: the "three words or more" threshold."""
from moderation.series import has_repeated_sentence


class TestHasRepeatedSentenceThreshold:

    def test_a_three_word_sentence_repeated_by_the_same_author_is_repeated(self):
        messages = [
            {"seq": 1, "author": "A", "text": "Cats chase mice."},
            {"seq": 2, "author": "B", "text": "That is fine."},
            {"seq": 3, "author": "A", "text": "Cats chase mice."},
        ]

        result = has_repeated_sentence(messages)

        assert result is True

    def test_a_three_word_sentence_repeated_by_a_different_author_is_not_repeated(self):
        messages = [
            {"seq": 1, "author": "A", "text": "Cats chase mice."},
            {"seq": 2, "author": "B", "text": "Cats chase mice."},
        ]

        result = has_repeated_sentence(messages)

        assert result is False

    def test_a_two_word_sentence_repeated_by_the_same_author_is_below_the_threshold(self):
        messages = [
            {"seq": 1, "author": "A", "text": "Cats run."},
            {"seq": 2, "author": "A", "text": "Cats run."},
        ]

        result = has_repeated_sentence(messages)

        assert result is False


class TestHasRepeatedSentenceVerbatimMatch:

    def test_extra_internal_whitespace_still_counts_as_the_same_sentence(self):
        messages = [
            {"seq": 1, "author": "A", "text": "Cats chase mice."},
            {"seq": 2, "author": "A", "text": "Cats   chase\nmice."},
        ]

        result = has_repeated_sentence(messages)

        assert result is True

    def test_a_different_case_second_occurrence_is_not_a_verbatim_repeat(self):
        messages = [
            {"seq": 1, "author": "A", "text": "Cats chase mice."},
            {"seq": 2, "author": "A", "text": "cats chase mice."},
        ]

        result = has_repeated_sentence(messages)

        assert result is False
