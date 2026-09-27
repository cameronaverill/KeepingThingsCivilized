"""`rate_target`: findings are located with `locate_quote` and stored with the message's own slice; unlocatable or rule-breaking
findings are dropped and reported, never stored (docs/step14_brief.md, 14a)."""
import logging

import llmr_kit as kit
import pytest

FACT, ABUSE = "factual_accuracy", "abusiveness"
TEXT = "Rent control always lowers rents. Anyone who disagrees is an idiot. The 1990 census counted 5 million people."


def run(fake, text, *findings, dimensions=None, no_issues=()):
    """Rate a one-message conversation whose text is `text`; the model answers `findings`. Returns (result, rating, message)."""
    rater = kit.make_rater(f"rater-{kit.n()}")
    _, message = kit.single(text)
    fake(kit.answer(*findings, no_issues=no_issues))
    result = kit.rate(rater, message, **({} if dimensions is None else {"dimensions": dimensions}))
    return result, kit.rating_of(result), message


def span(text, phrase):
    start = text.index(phrase)
    return start, start + len(phrase)


class TestStoredFindings:
    def test_an_exact_quote_is_stored_with_the_offsets_of_locate_quote(self, fake):
        from moderation.quotes import locate_quote

        _, rating, _ = run(fake, TEXT, kit.finding("f1", FACT, "always lowers rents", 3))
        (stored,) = kit.findings_of(rating)
        located = locate_quote(TEXT, "always lowers rents")
        assert ((stored.start, stored.end), (located.start, located.end)) == (span(TEXT, "always lowers rents"), (13, 32))

    def test_the_stored_quote_is_the_text_slice(self, fake):
        _, rating, _ = run(fake, TEXT, kit.finding("f1", ABUSE, "an idiot", 3))
        (stored,) = kit.findings_of(rating)
        assert (stored.quote, TEXT[stored.start : stored.end]) == ("an idiot", "an idiot")

    def test_every_field_of_the_finding_is_stored(self, fake):
        detail = {"claim": "rent control lowers rents", "correct_fact": "the effect depends on the design"}
        _, rating, _ = run(fake, TEXT, kit.finding("f7", FACT, "always lowers rents", 2, confidence=0.75, detail=detail))
        (stored,) = kit.findings_of(rating)
        assert (stored.local_id, stored.dimension, stored.intensity, stored.not_scorable_reason, stored.confidence, stored.detail) == (
            "f7", FACT, 2, "", 0.75, detail,
        )

    def test_an_intensity_of_zero_is_a_finding_like_any_other(self, fake):
        _, rating, _ = run(fake, TEXT, kit.finding("f1", FACT, "5 million people", 0))
        (stored,) = kit.findings_of(rating)
        assert stored.intensity == 0

    @pytest.mark.parametrize("reason", ["unverifiable", "contested", "needs_context"])
    def test_a_not_scorable_finding_keeps_its_reason_and_has_no_intensity(self, fake, reason):
        _, rating, _ = run(fake, TEXT, kit.finding("f1", FACT, "always lowers rents", None, reason=reason))
        (stored,) = kit.findings_of(rating)
        assert (stored.intensity, stored.not_scorable_reason) == (None, reason)

    def test_a_null_confidence_is_stored_as_null(self, fake):
        _, rating, _ = run(fake, TEXT, kit.finding("f1", FACT, "always lowers rents", 3, confidence=None))
        assert kit.findings_of(rating)[0].confidence is None

    def test_findings_on_two_dimensions_may_share_a_span(self, fake):
        _, rating, _ = run(
            fake, TEXT, kit.finding("f1", FACT, "an idiot", 1), kit.finding("f2", ABUSE, "an idiot", 3),
        )
        assert sorted((f.local_id, f.dimension, f.start, f.end) for f in kit.findings_of(rating)) == [
            ("f1", FACT, *span(TEXT, "an idiot")), ("f2", ABUSE, *span(TEXT, "an idiot")),
        ]

    def test_several_findings_are_all_stored_in_the_order_given(self, fake):
        _, rating, _ = run(
            fake, TEXT, kit.finding("f1", FACT, "always lowers rents", 3), kit.finding("f2", ABUSE, "an idiot", 3),
            kit.finding("f3", FACT, "5 million people", 0),
        )
        assert [f.local_id for f in kit.findings_of(rating)] == ["f1", "f2", "f3"]

    def test_the_whole_message_can_be_the_quote(self, fake):
        _, rating, _ = run(fake, TEXT, kit.finding("f1", ABUSE, TEXT, 2))
        (stored,) = kit.findings_of(rating)
        assert (stored.start, stored.end, stored.quote) == (0, len(TEXT), TEXT)

    def test_a_phrase_that_occurs_twice_is_located_at_its_first_occurrence(self, fake):
        text = "very bad idea. Truly a very bad idea."
        _, rating, _ = run(fake, text, kit.finding("f1", ABUSE, "very bad", 1))
        (stored,) = kit.findings_of(rating)
        assert (stored.start, stored.end) == (0, 8)

    def test_a_clean_answer_stores_no_finding(self, fake):
        _, rating, _ = run(fake, TEXT, no_issues=[FACT, ABUSE])
        assert kit.findings_of(rating) == []


class TestNormalizedMatches:
    """`locate_quote` also finds a quote that differs in quote marks, dashes, case or spacing; the stored quote is then the
    message's own words, so that `quote == text[start:end]` always holds."""

    def test_straight_quotes_in_the_answer_match_curly_quotes_in_the_message(self, fake):
        text = "He said it\u2019s \u201cfine\u201d now, which is absurd."
        own_words = "it\u2019s \u201cfine\u201d now"
        _, rating, _ = run(fake, text, kit.finding("f1", FACT, 'it\'s "fine" now', 1))
        (stored,) = kit.findings_of(rating)
        assert (stored.quote, (stored.start, stored.end)) == (own_words, span(text, own_words))

    def test_a_different_case_matches(self, fake):
        text = "Water Boils at One Hundred Degrees up on Mount Everest."
        _, rating, _ = run(fake, text, kit.finding("f1", FACT, "water boils at one hundred degrees", 2))
        (stored,) = kit.findings_of(rating)
        assert (stored.quote, stored.start, stored.end) == ("Water Boils at One Hundred Degrees", 0, 34)

    def test_a_different_spacing_matches(self, fake):
        text = "It is   clearly\nnot   true at all."
        _, rating, _ = run(fake, text, kit.finding("f1", FACT, "clearly not true", 2))
        (stored,) = kit.findings_of(rating)
        assert (stored.quote, (stored.start, stored.end)) == ("clearly\nnot   true", (8, 26))

    def test_an_escaped_quote_is_matched_and_stored_as_the_message_has_it(self, fake):
        text = "Tom & Jerry <b>always</b> win."
        _, rating, _ = run(fake, text, kit.finding("f1", FACT, "Tom &amp; Jerry &lt;b&gt;always", 1))
        (stored,) = kit.findings_of(rating)
        assert (stored.quote, stored.start, stored.end) == ("Tom & Jerry <b>always", 0, 21)

    def test_offsets_count_characters_not_bytes_or_utf16_units(self, fake):
        text = "I love \U0001F600 parks and the 1990 census figure."
        _, rating, _ = run(fake, text, kit.finding("f1", FACT, "the 1990 census", 1))
        (stored,) = kit.findings_of(rating)
        assert (stored.start, stored.end, stored.quote) == (19, 34, "the 1990 census")

    def test_accents_are_kept(self, fake):
        text = "Café owners say the résumé rule is unfair."
        _, rating, _ = run(fake, text, kit.finding("f1", FACT, "résumé rule", 1))
        (stored,) = kit.findings_of(rating)
        assert stored.quote == "résumé rule"


class TestDroppedFindings:
    """Each is reported (local_id and a reason) and never stored; its siblings are stored; the rating is done."""

    GOOD = kit.finding("good", FACT, "always lowers rents", 3)

    def dropped(self, fake, bad, **kwargs):
        result, rating, _ = run(fake, TEXT, self.GOOD, bad, **kwargs)
        return result, rating

    def check(self, result, rating, local_id):
        (only,) = kit.findings_of(rating)
        (dropped_id, reason) = kit.rejected_of(result)[0]
        assert (only.local_id, len(kit.rejected_of(result)), dropped_id, isinstance(reason, str) and reason != "", rating.status) == (
            "good", 1, local_id, True, "done",
        )

    def test_a_quote_that_is_not_in_the_message(self, fake):
        result, rating = self.dropped(fake, kit.finding("bad", FACT, "a phrase nobody wrote", 3))
        self.check(result, rating, "bad")

    @pytest.mark.parametrize("quote", ["", "   ", "\n"])
    def test_an_empty_quote(self, fake, quote):
        result, rating = self.dropped(fake, kit.finding("bad", FACT, quote, 3))
        self.check(result, rating, "bad")

    def test_a_dimension_the_rating_does_not_cover(self, fake):
        result, rating, _ = run(
            fake, TEXT, kit.finding("good", ABUSE, "an idiot", 3), kit.finding("bad", FACT, "always lowers rents", 3),
            dimensions=[ABUSE],
        )
        self.check(result, rating, "bad")

    def test_a_dimension_that_does_not_exist(self, fake):
        result, rating = self.dropped(fake, kit.finding("bad", "fallacy", "an idiot", 3))
        self.check(result, rating, "bad")

    def test_no_intensity_and_no_reason(self, fake):
        result, rating = self.dropped(fake, kit.finding("bad", FACT, "an idiot", None, reason=None))
        self.check(result, rating, "bad")

    def test_an_intensity_together_with_a_reason(self, fake):
        result, rating = self.dropped(fake, kit.finding("bad", FACT, "an idiot", 2, reason="contested"))
        self.check(result, rating, "bad")

    @pytest.mark.parametrize("intensity", [5, 9, -1])
    def test_an_intensity_outside_the_scale(self, fake, intensity):
        result, rating, _ = run(fake, TEXT, self.GOOD, kit.finding("bad", FACT, "an idiot", intensity))
        assert [f.local_id for f in kit.findings_of(rating)] == ["good"]

    def test_a_repeated_local_id_drops_the_later_one_only(self, fake):
        result, rating, _ = run(fake, TEXT, self.GOOD, kit.finding("good", ABUSE, "an idiot", 3))
        (only,) = kit.findings_of(rating)
        assert (only.dimension, kit.rejected_of(result)[0][0], len(kit.rejected_of(result))) == (FACT, "good", 1)

    def test_every_finding_dropped_still_leaves_a_done_rating(self, fake):
        result, rating, _ = run(
            fake, TEXT, kit.finding("b1", FACT, "not in the text", 2), kit.finding("b2", "fallacy", "an idiot", 2),
        )
        assert (rating.status, kit.findings_of(rating), [local for local, _ in kit.rejected_of(result)]) == ("done", [], ["b1", "b2"])

    def test_the_reports_are_in_the_order_of_the_answer(self, fake):
        result, _, _ = run(
            fake, TEXT, kit.finding("z9", FACT, "no such words", 2), self.GOOD, kit.finding("a1", FACT, "none of these", 2),
        )
        assert [local for local, _ in kit.rejected_of(result)] == ["z9", "a1"]

    def test_a_clean_answer_reports_nothing_dropped(self, fake):
        result, _, _ = run(fake, TEXT, self.GOOD)
        assert kit.rejected_of(result) == []

    def test_the_ledger_row_still_holds_the_whole_answer_including_the_dropped_finding(self, fake):
        run(fake, TEXT, self.GOOD, kit.finding("bad", FACT, "a phrase nobody wrote", 3))
        (row,) = kit.ledger()
        assert ([f["local_id"] for f in row.parsed["findings"]], row.status, row.error) == (["good", "bad"], "ok", "")

    def test_the_ledger_row_is_not_changed_by_storing_the_findings(self, fake):
        rater = kit.make_rater("the-rater")
        _, message = kit.single(TEXT)
        fake(kit.priced(kit.answer(self.GOOD, kit.finding("bad", FACT, "nothing like it", 3)), input_tokens=800, output_tokens=60))
        kit.rate(rater, message)
        (row,) = kit.ledger()
        assert (row.tokens_in, row.tokens_out, row.status, row.attempt) == (800, 60, "ok", 1)


class TestTheReasonCodes:
    """The codes of `RateResult.rejected` are pinned by the architect (a code never holds message text)."""

    GOOD = kit.finding("good", FACT, "always lowers rents", 3)

    def reason_of(self, fake, bad, **kwargs):
        result, _, _ = run(fake, TEXT, self.GOOD, bad, **kwargs)
        return kit.rejected_of(result)

    def test_a_quote_that_is_not_in_the_message(self, fake):
        assert self.reason_of(fake, kit.finding("bad", FACT, "nobody wrote this", 3)) == [("bad", "quote_not_found")]

    def test_an_empty_quote(self, fake):
        assert self.reason_of(fake, kit.finding("bad", FACT, "", 3)) == [("bad", "quote_not_found")]

    def test_a_dimension_the_rating_does_not_cover(self, fake):
        result, _, _ = run(
            fake, TEXT, kit.finding("good", ABUSE, "an idiot", 3), kit.finding("bad", FACT, "always lowers rents", 3), dimensions=[ABUSE],
        )
        assert kit.rejected_of(result) == [("bad", "dimension_not_requested")]

    def test_a_dimension_that_does_not_exist(self, fake):
        assert self.reason_of(fake, kit.finding("bad", "fallacy", "an idiot", 3)) == [("bad", "dimension_not_requested")]

    def test_no_intensity_and_no_reason(self, fake):
        assert self.reason_of(fake, kit.finding("bad", FACT, "an idiot", None)) == [("bad", "missing_reason")]

    def test_an_intensity_together_with_a_reason(self, fake):
        assert self.reason_of(fake, kit.finding("bad", FACT, "an idiot", 2, reason="contested")) == [("bad", "intensity_and_reason")]

    @pytest.mark.parametrize("intensity", [5, -1, 40])
    def test_an_intensity_outside_the_scale(self, fake, intensity):
        assert self.reason_of(fake, kit.finding("bad", FACT, "an idiot", intensity)) == [("bad", "intensity_out_of_range")]

    def test_a_repeated_local_id(self, fake):
        assert self.reason_of(fake, kit.finding("good", ABUSE, "an idiot", 3)) == [("good", "duplicate_local_id")]

    def test_an_empty_local_id(self, fake):
        assert self.reason_of(fake, kit.finding("", ABUSE, "an idiot", 3)) == [("", "invalid_local_id")]

    def test_a_local_id_too_long_for_the_table(self, fake):
        too_long = "f" * 51
        assert self.reason_of(fake, kit.finding(too_long, ABUSE, "an idiot", 3)) == [(too_long, "invalid_local_id")]


class TestConfidence:
    @pytest.mark.parametrize("confidence", [1.5, -0.1, 7.0])
    def test_a_confidence_outside_zero_to_one_is_stored_as_null_and_the_finding_is_kept(self, fake, confidence):
        result, rating, _ = run(fake, TEXT, kit.finding("f1", FACT, "always lowers rents", 3, confidence=confidence))
        (stored,) = kit.findings_of(rating)
        assert (stored.confidence, kit.rejected_of(result)) == (None, [])

    @pytest.mark.parametrize("confidence", [0.0, 1.0, 0.5])
    def test_a_confidence_from_zero_to_one_is_kept(self, fake, confidence):
        _, rating, _ = run(fake, TEXT, kit.finding("f1", FACT, "always lowers rents", 3, confidence=confidence))
        assert kit.findings_of(rating)[0].confidence == confidence


class TestNoMessageTextIsLogged:
    SECRET = "SECRETPHRASE-zx91"

    def test_no_log_line_and_no_output_contains_the_message_or_a_quote(self, fake, caplog, capsys):
        text = f"Everyone knows that {self.SECRET} is the truth, and {self.SECRET} is never wrong."
        with caplog.at_level(logging.DEBUG):
            run(
                fake, text, kit.finding("f1", FACT, self.SECRET, 3), kit.finding("f2", FACT, f"{self.SECRET} but not here", 2),
                kit.finding("f3", "bogus", f"is never wrong {self.SECRET}", 2),
            )
        captured = capsys.readouterr()
        assert (self.SECRET in caplog.text, self.SECRET in captured.out, self.SECRET in captured.err) == (False, False, False)

    def test_the_reasons_of_the_dropped_findings_hold_no_message_text(self, fake):
        text = f"Everyone knows that {self.SECRET} is the truth."
        result, _, _ = run(fake, text, kit.finding("f1", FACT, f"{self.SECRET} lies", 2), kit.finding("f2", "bogus", self.SECRET, 2))
        assert [self.SECRET in reason for _, reason in kit.rejected_of(result)] == [False, False]
