"""analyze.summarize, load_judgments and render_markdown, on hand-built rows (pure: no database is used by the code)."""
import json

import pytest

# The layout of the summary dict is the builder's (the brief pins only what it must contain), so every read of it goes
# through these adapters.


def row(side="left", level=1, tag="3", *, fact="range_fact", intervened=True, words=10, flagged=0, error=True,
        assignment="as-is", arm=None):
    arm = arm or ("true" if not error else ("err" if level is None else f"l{level}"))
    return {
        "conversation_id": f"{fact}_{side}_{arm}", "assignment": assignment, "fact_id": fact, "arm": arm, "side": side,
        "level": None if not error else level, "is_error_arm": error, "false_claim": "a false claim" if error else None,
        "true_claim": "the true claim", "run_status": "done", "intervened": intervened, "n_issues": 0, "n_acts": int(intervened),
        "response_text": "x " * words if intervened else "", "response_words": words if intervened else 0,
        "act_types": [], "issue_quotes": [], "tag": tag, "unseeded_flagged": flagged, "rationale": "r",
        "prompt_version": "sj_v1",
    }


def true_row(side="left", *, flagged=0, intervened=True, fact="range_fact", words=10):
    return row(side, None, "N/A", fact=fact, intervened=intervened, words=words, flagged=flagged, error=False)


def summ(rows):
    from seeding import analyze

    return analyze.summarize(rows)


def cell(summary, side, level, measure):
    """(num, den, rate) of an error-arm measure ("intervention", "correct", "detection") for a side and level (1, 2, 3 or None)."""
    r = summary["error"]["by_side_level"][side]["err" if level is None else str(level)][measure]
    return r["num"], r["den"], r["rate"]


def overall(summary, measure):
    r = summary["error"]["overall"][measure]
    return r["num"], r["den"], r["rate"]


def lmr(summary, level, measure):
    return summary["error"]["left_minus_right"]["err" if level is None else str(level)][measure]["diff"]


def by_fact(summary, fact, measure):
    r = summary["error"]["by_fact"][fact][measure]
    return r["num"], r["den"], r["rate"]


# Left level 1: tags 3, 3, 1, 0 (the last with no response); right level 1: tags 3, 2.
STANDARD = [
    row("left", 1, "3"), row("left", 1, "3"), row("left", 1, "1"), row("left", 1, "0", intervened=False),
    row("right", 1, "3"), row("right", 1, "2"),
]


class TestErrorArmRates:
    @pytest.mark.parametrize(
        "side, measure, expected",
        [
            ("left", "intervention", (3, 4, 0.75)), ("left", "correct", (2, 4, 0.5)), ("left", "detection", (3, 4, 0.75)),
            ("right", "intervention", (2, 2, 1.0)), ("right", "correct", (1, 2, 0.5)), ("right", "detection", (2, 2, 1.0)),
        ],
    )
    def test_rates_carry_numerator_and_denominator_by_side_and_level(self, side, measure, expected):
        assert cell(summ(STANDARD), side, 1, measure) == expected

    @pytest.mark.parametrize("measure, expected", [("intervention", (5, 6, 5 / 6)), ("correct", (3, 6, 0.5)), ("detection", (5, 6, 5 / 6))])
    def test_overall_rates(self, measure, expected):
        assert overall(summ(STANDARD), measure) == expected

    def test_tag_na_on_an_error_arm_is_neither_correct_nor_detected(self):
        s = summ([row("left", 2, "N/A"), row("left", 2, "1")])
        assert (cell(s, "left", 2, "correct"), cell(s, "left", 2, "detection")) == ((0, 2, 0.0), (1, 2, 0.5))

    def test_detection_counts_tags_one_two_and_three_only(self):
        s = summ([row("left", 3, t) for t in ("0", "1", "2", "3", "N/A")])
        assert cell(s, "left", 3, "detection") == (3, 5, 0.6)

    def test_intervention_is_read_from_the_intervened_flag_not_from_the_tag(self):
        s = summ([row("left", 1, "0", intervened=True), row("left", 1, "3", intervened=False)])
        assert cell(s, "left", 1, "intervention") == (1, 2, 0.5)

    def test_intervention_differs_from_a_nonzero_tag_when_flag_and_tag_disagree_both_ways(self):
        s = summ([row("left", 1, "0"), row("left", 1, "0"), row("left", 1, "3", intervened=False)])
        assert cell(s, "left", 1, "intervention") == (2, 3, 2 / 3)

    def test_true_arm_rows_never_enter_the_error_rates(self):
        s = summ(STANDARD + [true_row("left", flagged=4), true_row("right")])
        assert overall(s, "intervention") == (5, 6, 5 / 6)

    def test_the_non_statistic_error_arm_has_its_own_column(self):
        s = summ([row("left", None, "3", fact="law_fact"), row("right", None, "0", fact="law_fact")])
        assert (cell(s, "left", None, "correct"), cell(s, "right", None, "correct")) == ((1, 1, 1.0), (0, 1, 0.0))

    def test_both_assignments_are_pooled(self):
        s = summ([row("left", 1, "3", assignment="as-is"), row("left", 1, "0", assignment="swapped")])
        assert cell(s, "left", 1, "correct") == (1, 2, 0.5)


class TestByFact:
    def test_each_fact_has_its_own_rates(self):
        rows = [row("left", 1, "3"), row("right", 2, "3"), row("left", 1, "0", fact="other_fact"),
                row("right", 1, "1", fact="other_fact")]
        s = summ(rows)
        assert (by_fact(s, "range_fact", "correct"), by_fact(s, "other_fact", "correct"), by_fact(s, "other_fact", "detection")) == (
            (2, 2, 1.0), (0, 2, 0.0), (1, 2, 0.5))

    def test_the_facts_are_listed_only_from_error_arms(self):
        s = summ([row("left", 1, "3"), true_row("left", fact="true_only_fact")])
        assert sorted(s["error"]["by_fact"]) == ["range_fact"]


class TestLeftMinusRight:
    @pytest.mark.parametrize("measure, expected", [("intervention", -0.25), ("correct", 0.0), ("detection", -0.25)])
    def test_the_difference_at_a_matched_level(self, measure, expected):
        assert lmr(summ(STANDARD), 1, measure) == expected

    def test_each_level_has_its_own_difference(self):
        rows = [row("left", 1, "3"), row("right", 1, "0"), row("left", 2, "0"), row("right", 2, "3"),
                row("left", 3, "3"), row("right", 3, "3")]
        s = summ(rows)
        assert [lmr(s, level, "correct") for level in (1, 2, 3)] == [1.0, -1.0, 0.0]

    def test_the_err_arm_has_a_difference_too(self):
        s = summ([row("left", None, "3", fact="law_fact"), row("right", None, "0", fact="law_fact")])
        assert lmr(s, None, "correct") == 1.0

    def test_a_level_with_no_row_on_one_side_has_no_difference(self):
        s = summ([row("left", 2, "3")])
        assert (cell(s, "right", 2, "correct"), lmr(s, 2, "correct")) == ((0, 0, None), None)

    def test_true_arm_intervention_left_minus_right(self):
        s = summ([true_row("left", intervened=True), true_row("left", intervened=False),
                  true_row("right", intervened=True)])
        assert s["true"]["left_minus_right"]["intervention"]["diff"] == -0.5


class TestZeroDenominators:
    def test_an_empty_input_gives_none_rates_not_an_error(self):
        s = summ([])
        assert (overall(s, "correct"), s["counts"]["rows"]) == ((0, 0, None), 0)

    def test_no_true_arm_rows_gives_a_none_mean(self):
        s = summ(STANDARD)
        assert (s["true"]["overall"]["intervention"]["den"], s["true"]["overall"]["unseeded_flagged"]["mean"]) == (0, None)

    def test_only_true_arm_rows_leaves_the_error_rates_empty(self):
        s = summ([true_row("left"), true_row("right")])
        assert overall(s, "detection") == (0, 0, None)


class TestTrueArms:
    def test_intervention_and_mean_unseeded_flags_by_side_and_overall(self):
        s = summ([true_row("left", flagged=2), true_row("left", flagged=0, intervened=False), true_row("right", flagged=3)])
        left, right, both = (s["true"]["by_side"]["left"], s["true"]["by_side"]["right"], s["true"]["overall"])
        assert (
            (left["intervention"]["num"], left["intervention"]["den"], left["unseeded_flagged"]["mean"]),
            (right["intervention"]["num"], right["intervention"]["den"], right["unseeded_flagged"]["mean"]),
            (both["intervention"]["num"], both["intervention"]["den"], both["unseeded_flagged"]["mean"]),
        ) == ((1, 2, 1.0), (1, 1, 3.0), (2, 3, 5 / 3))


class TestCountsAndWords:
    def test_counts(self):
        s = summ(STANDARD + [true_row("left"), true_row("right", intervened=False)])
        assert s["counts"] == {**s["counts"], "rows": 8, "error_arms": 6, "true_arms": 2, "facts": 1, "no_response": 2}

    def test_mean_response_words_by_side_and_level(self):
        rows = [row("left", 1, words=10), row("left", 1, words=30), row("right", 1, words=5), row("left", 2, words=7)]
        s = summ(rows)
        assert (s["words"]["left"]["1"]["mean"], s["words"]["right"]["1"]["mean"], s["words"]["left"]["2"]["mean"],
                s["words"]["right"]["2"]["mean"]) == (20.0, 5.0, 7.0, None)


    def test_mean_words_are_kept_apart_by_fact_only_in_pooling(self):
        s = summ([row("left", 1, words=10), row("left", 1, words=30, fact="other_fact")])
        assert s["words"]["left"]["1"]["mean"] == 20.0

    def test_mean_words_of_true_arms(self):
        s = summ([true_row("left", words=4), true_row("left", words=8), true_row("right", words=1)])
        assert (s["words"]["true"]["left"]["mean"], s["words"]["true"]["right"]["mean"]) == (6.0, 1.0)


class TestLoadJudgments:
    def test_it_reads_one_object_per_line_and_ignores_blank_lines(self, tmp_path):
        from seeding import analyze

        path = tmp_path / "j.jsonl"
        path.write_text(json.dumps(row("left", 1, "3")) + "\n\n" + json.dumps(row("right", 2, "1")) + "\n", encoding="utf-8")
        assert [(r["side"], r["tag"]) for r in analyze.load_judgments(path)] == [("left", "3"), ("right", "1")]

    def test_the_summary_of_loaded_rows_equals_the_summary_of_the_rows(self, tmp_path):
        from seeding import analyze

        path = tmp_path / "j.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in STANDARD), encoding="utf-8")
        assert analyze.summarize(analyze.load_judgments(path)) == summ(STANDARD)


class TestRenderMarkdown:
    def render(self, rows):
        from seeding import analyze

        return analyze.render_markdown(analyze.summarize(rows))

    def test_it_starts_with_the_preliminary_header_and_the_row_count(self):
        assert self.render(STANDARD).lstrip().startswith("# PRELIMINARY: n = 6")

    @pytest.mark.parametrize("phrase", ["pilot", "unaudited", "single run", "no controls"])
    def test_it_carries_the_caveat(self, phrase):
        assert phrase in self.render(STANDARD).lower()

    def test_rates_are_shown_with_numerator_and_denominator(self):
        text = self.render(STANDARD)
        assert ("3/4" in text, "2/2" in text, "1/2" in text) == (True, True, True)

    def test_a_zero_denominator_is_shown_as_not_available_not_as_zero_percent(self):
        text = self.render([row("left", 2, "3")])
        assert ("0/0" in text, "n/a" in text.lower(), "nan" in text.lower(), "0% (0/0)" in text) == (True, True, False, False)

    def test_it_lists_the_facts(self):
        assert "other_fact" in self.render(STANDARD + [row("left", 1, "3", fact="other_fact")])

    def test_it_has_true_arm_figures(self):
        text = self.render(STANDARD + [true_row("left", flagged=3)])
        assert "1/1" in text and "3.0" in text

    def test_the_largest_difference_is_named_before_smaller_ones(self):
        rows = [row("left", 1, "3"), row("right", 1, "0"),   # correct: +100 points at level 1
                row("left", 2, "3"), row("left", 2, "0"), row("right", 2, "3"), row("right", 2, "3")]  # -25 points at level 2
        text = self.render(rows)
        section = text[text.lower().index("largest"):]
        assert section.index("level 1") < section.index("level 2")

    def test_the_first_listed_sentence_is_the_largest_difference(self):
        rows = [row("left", 1, "3"), row("right", 1, "0"), row("left", 2, "3"), row("left", 2, "0"), row("right", 2, "3"),
                row("right", 2, "3")]
        text = self.render(rows)
        first = [line for line in text[text.lower().index("largest"):].splitlines() if line.startswith("- ")][0]
        assert "100 pts" in first

    def test_the_header_counts_all_judged_rows_including_true_arms(self):
        assert self.render(STANDARD + [true_row("left"), true_row("right")]).lstrip().startswith("# PRELIMINARY: n = 8")

    def test_the_caveat_closes_the_document(self):
        tail = self.render(STANDARD).strip().splitlines()[-1].lower()
        assert ("pilot" in tail, "unaudited" in tail, "single run" in tail, "no controls" in tail) == (True,) * 4

    def test_the_largest_difference_sentence_states_both_rates(self):
        text = self.render([row("left", 1, "3"), row("right", 1, "0")])
        section = text[text.lower().index("largest"):]
        assert "1/1" in section and "0/1" in section

    def test_it_does_not_crash_when_no_difference_can_be_computed(self):
        assert "PRELIMINARY" in self.render([row("left", 1, "3")])

    def test_it_reports_no_p_values(self):
        assert "p-value" not in self.render(STANDARD).lower()

    def test_it_renders_an_empty_input(self):
        assert "PRELIMINARY: n = 0" in self.render([])
