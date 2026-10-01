"""seeding.research_analyze.summarize / render_markdown on hand-built rows (pure: no database is used by the code)."""
import json
import re

import pytest

import research_kit as kit

row = kit.row


def summ(rows):
    from seeding import research_analyze

    return research_analyze.summarize(rows)


def md(rows):
    from seeding import research_analyze

    return research_analyze.render_markdown(research_analyze.summarize(rows))


def r3(r):
    return r["num"], r["den"], r["rate"]


def cell(s, side, label, measure):
    return r3(s["error"]["by_side_level"][side][label][measure])


def mean(s, side, label, measure):
    m = s["error"]["by_side_level"][side][label][measure]
    return m["mean"], m["den"]


def lmr(s, label, measure):
    return s["error"]["left_minus_right"][label][measure]["diff"]


# left l1: tags 3,3,1,0 ; verdicts disputes, disputes, unclear, confirms ; right l1: tags 3,2 ; verdicts disputes, disputes
STANDARD = [
    row(side="left", arm="l1", tag="3", verdict="disputes_claim", words=10, sources=1, confidence=0.2),
    row(side="left", arm="l1", tag="3", verdict="disputes_claim", words=20, sources=2, confidence=0.4),
    row(side="left", arm="l1", tag="1", verdict="unclear", words=30, sources=3, confidence=0.6),
    row(side="left", arm="l1", tag="0", verdict="confirms_claim", words=40, sources=0, confidence=0.8),
    row(side="right", arm="l1", tag="3", verdict="disputes_claim", words=50, sources=2, confidence=0.9),
    row(side="right", arm="l1", tag="2", verdict="disputes_claim", words=70, sources=4, confidence=0.5),
]


class TestErrorRates:
    @pytest.mark.parametrize("side, measure, expected", [
        ("left", "correct", (2, 4, 0.5)), ("left", "disputes", (2, 4, 0.5)),
        ("right", "correct", (1, 2, 0.5)), ("right", "disputes", (2, 2, 1.0)),
    ])
    def test_rates_carry_numerator_and_denominator(self, side, measure, expected):
        assert cell(summ(STANDARD), side, "1", measure) == expected

    def test_overall_rates(self):
        o = summ(STANDARD)["error"]["overall"]
        assert (r3(o["correct"]), r3(o["disputes"])) == ((3, 6, 0.5), (4, 6, 4 / 6))

    def test_correct_is_tag_three_only_and_is_independent_of_the_verdict(self):
        s = summ([row(arm="l2", tag="3", verdict="unclear"), row(arm="l2", tag="2", verdict="disputes_claim"),
                  row(arm="l2", tag="N/A", verdict="disputes_claim")])
        assert (cell(s, "left", "2", "correct"), cell(s, "left", "2", "disputes")) == ((1, 3, 1 / 3), (2, 3, 2 / 3))

    def test_means_are_over_the_rows_of_the_cell(self):
        s = summ(STANDARD)
        assert (mean(s, "left", "1", "words"), mean(s, "left", "1", "sources"), mean(s, "right", "1", "words"),
                mean(s, "right", "1", "sources")) == ((25.0, 4), (1.5, 4), (60.0, 2), (3.0, 2))
        assert mean(s, "left", "1", "confidence")[0] == pytest.approx(0.5)
        assert mean(s, "right", "1", "confidence")[0] == pytest.approx(0.7)

    def test_missing_confidence_is_left_out_of_the_mean_not_counted_as_zero(self):
        s = summ([row(arm="l1", confidence=0.8), row(arm="l1", confidence=None)])
        assert mean(s, "left", "1", "confidence") == (pytest.approx(0.8), 1)

    def test_all_confidence_missing_gives_no_mean(self):
        s = summ([row(arm="l1", confidence=None)])
        assert mean(s, "left", "1", "confidence")[0] is None

    def test_overall_means(self):
        o = summ(STANDARD)["error"]["overall"]
        assert (o["words"]["mean"], o["sources"]["mean"]) == (pytest.approx(220 / 6), pytest.approx(12 / 6))

    def test_sides_and_levels_are_kept_apart(self):
        s = summ([row(side="left", arm="l1", tag="3"), row(side="left", arm="l3", tag="0"), row(side="right", arm="l2", tag="3")])
        assert (cell(s, "left", "1", "correct"), cell(s, "left", "3", "correct"), cell(s, "right", "2", "correct"),
                cell(s, "right", "1", "correct")) == ((1, 1, 1.0), (0, 1, 0.0), (1, 1, 1.0), (0, 0, None))

    def test_the_non_statistic_arm_is_its_own_row_labelled_err(self):
        s = summ([row(fact_id="law_fact", arm="err", side="left", tag="3"), row(fact_id="law_fact", arm="err", side="right", tag="0"),
                  row(arm="l1", side="left", tag="3")])
        assert (cell(s, "left", "err", "correct"), cell(s, "right", "err", "correct"), cell(s, "left", "1", "correct")) == \
            ((1, 1, 1.0), (0, 1, 0.0), (1, 1, 1.0))

    def test_by_fact(self):
        s = summ([row(fact_id="range_fact", tag="3"), row(fact_id="range_fact", tag="0", side="right"),
                  row(fact_id="law_fact", arm="err", tag="3")])
        f = s["error"]["by_fact"]
        assert (r3(f["range_fact"]["correct"]), r3(f["law_fact"]["correct"])) == ((1, 2, 0.5), (1, 1, 1.0))

    def test_counts(self):
        s = summ(STANDARD + [row(arm="true", side="left", tag="N/A")])
        assert (s["counts"]["rows"], s["counts"]["error_arms"], s["counts"]["true_arms"]) == (7, 6, 1)


class TestLeftMinusRight:
    def test_the_sign_is_left_minus_right(self):
        s = summ(STANDARD)
        # correct: left 0.5, right 0.5 ; disputes: left 0.5, right 1.0
        assert (lmr(s, "1", "correct"), lmr(s, "1", "disputes")) == (pytest.approx(0.0), pytest.approx(-0.5))

    def test_positive_when_left_is_higher(self):
        s = summ([row(side="left", arm="l2", tag="3"), row(side="right", arm="l2", tag="0")])
        assert lmr(s, "2", "correct") == pytest.approx(1.0)

    def test_each_level_has_its_own_difference(self):
        s = summ([row(side="left", arm="l1", tag="3"), row(side="right", arm="l1", tag="3"),
                  row(side="left", arm="l3", tag="3"), row(side="right", arm="l3", tag="0")])
        assert (lmr(s, "1", "correct"), lmr(s, "3", "correct")) == (pytest.approx(0.0), pytest.approx(1.0))

    def test_the_non_statistic_arm_has_a_difference(self):
        s = summ([row(fact_id="law_fact", arm="err", side="left", tag="0"), row(fact_id="law_fact", arm="err", side="right", tag="3")])
        assert lmr(s, "err", "correct") == pytest.approx(-1.0)

    def test_the_difference_is_between_rates_not_counts(self):
        s = summ([row(side="left", arm="l1", tag="3")] * 1 + [row(side="right", arm="l1", tag="3")] * 2
                 + [row(side="right", arm="l1", tag="0")] * 2)
        assert lmr(s, "1", "correct") == pytest.approx(1.0 - 0.5)

    def test_a_side_with_no_rows_gives_no_difference(self):
        s = summ([row(side="left", arm="l1", tag="3")])
        assert lmr(s, "1", "correct") is None

    def test_the_cell_for_the_empty_side_has_a_zero_denominator_and_no_rate(self):
        s = summ([row(side="left", arm="l1", tag="3")])
        assert cell(s, "right", "1", "correct") == (0, 0, None)


class TestTrueArms:
    TRUE = [
        row(arm="true", side="left", tag="N/A", verdict="confirms_claim"), row(arm="true", side="left", tag="N/A", verdict="confirms_claim"),
        row(arm="true", side="left", tag="N/A", verdict="disputes_claim"), row(arm="true", side="left", tag="N/A", verdict="unclear"),
        row(arm="true", side="right", tag="N/A", verdict="confirms_claim"), row(arm="true", side="right", tag="N/A", verdict="unclear"),
    ]

    def t(self, s, side, verdict):
        return r3(s["true"]["by_side"][side][verdict])

    def test_verdict_rates_by_side(self):
        s = summ(self.TRUE)
        assert (self.t(s, "left", "confirms_claim"), self.t(s, "left", "disputes_claim"), self.t(s, "left", "unclear"),
                self.t(s, "right", "confirms_claim"), self.t(s, "right", "disputes_claim"), self.t(s, "right", "unclear")) == \
            ((2, 4, 0.5), (1, 4, 0.25), (1, 4, 0.25), (1, 2, 0.5), (0, 2, 0.0), (1, 2, 0.5))

    def test_overall(self):
        s = summ(self.TRUE)
        assert r3(s["true"]["overall"]["confirms_claim"]) == (3, 6, 0.5)

    def test_left_minus_right(self):
        s = summ(self.TRUE)
        d = s["true"]["left_minus_right"]
        assert (d["confirms_claim"]["diff"], d["disputes_claim"]["diff"], d["unclear"]["diff"]) == \
            (pytest.approx(0.0), pytest.approx(0.25), pytest.approx(-0.25))

    def test_error_rows_do_not_leak_into_the_true_arm_rates(self):
        s = summ(self.TRUE + [row(arm="l1", side="left", tag="3", verdict="disputes_claim")] * 5)
        assert self.t(s, "left", "disputes_claim") == (1, 4, 0.25)

    def test_true_rows_do_not_leak_into_the_error_arm_rates(self):
        s = summ(self.TRUE + [row(arm="l1", side="left", tag="3", verdict="disputes_claim")])
        assert (cell(s, "left", "1", "disputes"), s["error"]["overall"]["disputes"]["den"]) == ((1, 1, 1.0), 1)

    def test_is_error_arm_decides_not_the_arm_name(self):
        s = summ([row(arm="true", is_error=True, tag="3"), row(arm="l1", is_error=False, tag="N/A", verdict="confirms_claim")])
        assert (s["counts"]["error_arms"], s["counts"]["true_arms"]) == (1, 1)


class TestZeroDenominators:
    def test_no_rows_at_all(self):
        s = summ([])
        assert (s["counts"]["rows"], r3(s["error"]["overall"]["correct"]), r3(s["true"]["overall"]["confirms_claim"])) == (0, (0, 0, None), (0, 0, None))

    def test_no_true_arms(self):
        s = summ([row(arm="l1")])
        assert r3(s["true"]["by_side"]["left"]["confirms_claim"]) == (0, 0, None)
        assert s["true"]["left_minus_right"]["confirms_claim"]["diff"] is None

    def test_no_error_arms(self):
        s = summ([row(arm="true", tag="N/A", verdict="unclear")])
        assert (r3(s["error"]["overall"]["disputes"]), s["error"]["by_fact"]) == ((0, 0, None), {})

    def test_markdown_survives_empty_input(self):
        text = md([])
        assert "PRELIMINARY" in text

    def test_markdown_shows_na_for_an_empty_cell(self):
        text = md([row(side="left", arm="l1", tag="3")])
        assert "n/a" in text


class TestMarkdown:
    def test_header_and_caveat(self):
        text = md(STANDARD)
        assert text.splitlines()[0].startswith("# PRELIMINARY")
        for phrase in ("unaudited", "single run", "varies", "no controls", "significance"):
            assert phrase in text.lower()

    def test_rates_show_numerator_and_denominator(self):
        text = md(STANDARD)
        assert "(2/4)" in text and "(1/2)" in text and "(2/2)" in text

    def test_left_minus_right_is_signed_in_percentage_points(self):
        text = md(STANDARD)
        assert "-50 pts" in text

    def test_it_names_the_levels_and_the_non_statistic_arm(self):
        text = md(STANDARD + [row(fact_id="law_fact", arm="err", tag="3")])
        assert "level 1" in text and "err" in text

    def test_the_facts_are_listed(self):
        assert "range_fact" in md(STANDARD)

    def test_it_never_mentions_a_note_text_or_a_false_claim(self):
        text = md([row(note_text="SECRETNOTE", false_claim="SECRETCLAIM")])
        assert "SECRETNOTE" not in text and "SECRETCLAIM" not in text

    def test_it_has_no_p_values(self):
        assert not re.search(r"p\s*[<=]\s*0?\.\d", md(STANDARD))


class TestLoading:
    def test_load_judgments_reads_jsonl(self, tmp_path):
        from seeding import research_analyze

        path = tmp_path / "x.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in STANDARD[:2]) + "\n\n", encoding="utf-8")
        assert research_analyze.load_judgments(path) == STANDARD[:2]

    def test_summary_is_json_serialisable(self):
        json.dumps(summ(STANDARD))
