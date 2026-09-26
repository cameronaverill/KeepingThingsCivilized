"""evaluation.consensus.build_consensus on hand-built cases (docs/step12_brief.md, plan section 9).

Findings are real saved rows (a rater's rating of one message); the result is read only through the ConsensusFinding
attributes, so the tests hold whether the function returns saved or unsaved instances. Readings chosen where the brief is
silent are noted in the tests: the panel's own consensus fields drive the rule; intensity_range is max - min over the
scorable findings; n_raters counts distinct raters; a merged span lies within the merged findings' extent."""
import itertools
import random

import evalmodels_testkit as kit
import pytest

from evaluation.consensus import build_consensus

pytestmark = pytest.mark.django_db

TEXT = "The tax was raised in 2020. It was a disaster for everyone. Nobody liked it at all. " + "x" * 40
FA, AB = "factual_accuracy", "abusiveness"


class Case:
    """A message, n LLM raters in one panel, and a helper to add findings for them."""

    def __init__(self, n_raters=2, text=TEXT, **panel_fields):
        self.message, _ = kit.message_with_text(text)
        self.raters = [kit.make_rater("llm", name=f"cr{kit.n()}") for _ in range(n_raters)]
        self.panel = kit.make_panel(self.raters, **panel_fields)
        self.ratings = {r.pk: kit.make_rating(r, self.message) for r in self.raters}
        self.by_rater = {r: [] for r in self.raters}

    def add(self, rater_index, start, end, dimension=FA, intensity=2, **extra):
        rater = self.raters[rater_index]
        finding = kit.make_finding(self.ratings[rater.pk], start, end, dimension=dimension, intensity=intensity, **extra)
        self.by_rater[rater].append(finding)
        return finding

    def run(self, **kwargs):
        return build_consensus(self.panel, self.message, self.by_rater, **kwargs)


def signature(results):
    return [
        (c.dimension, c.start, c.end, c.n_raters, c.needs_adjudication, c.intensity_mean, c.intensity_range) for c in results
    ]


def only(results):
    assert len(results) == 1, signature(results)
    return results[0]


# --- the basics --------------------------------------------------------------------------------------------------------
def test_no_findings_give_no_consensus_findings():
    assert Case().run() == []


def test_two_raters_with_identical_spans_and_equal_intensity_agree():
    case = Case()
    case.add(0, 4, 26, intensity=3)
    case.add(1, 4, 26, intensity=3)
    result = only(case.run())
    assert (result.dimension, result.start, result.end, result.n_raters) == (FA, 4, 26, 2)
    assert result.needs_adjudication is False
    assert result.intensity_mean == pytest.approx(3.0) and result.intensity_range == 0


def test_the_result_carries_panel_target_and_dimension():
    case = Case()
    case.add(0, 4, 26)
    case.add(1, 4, 26)
    result = only(case.run())
    assert result.panel == case.panel
    assert (result.target_type, result.target_id) == ("message", case.message.pk)


def test_disjoint_findings_stay_separate_and_each_needs_adjudication():
    case = Case()
    case.add(0, 4, 26)
    case.add(1, 30, 58)
    results = case.run()
    assert [(c.start, c.end, c.n_raters, c.needs_adjudication) for c in results] == [(4, 26, 1, True), (30, 58, 1, True)]


def test_a_single_rater_panel_does_not_flag_lonely_findings():
    """'Found by only one rater' counts only when the panel has more than one rater."""
    case = Case(n_raters=1)
    case.add(0, 4, 26, intensity=2)
    result = only(case.run())
    assert result.n_raters == 1 and result.needs_adjudication is False


def test_a_phrase_found_by_one_of_two_raters_needs_adjudication():
    case = Case()
    case.add(0, 4, 26)
    result = only(case.run())
    assert result.n_raters == 1 and result.needs_adjudication is True


def test_a_rater_with_an_empty_list_is_fine():
    case = Case()
    case.add(0, 4, 26)
    case.by_rater[case.raters[1]] = []
    assert only(case.run()).needs_adjudication is True


def test_a_phrase_found_by_one_of_three_raters_needs_adjudication():
    case = Case(n_raters=3)
    case.add(1, 4, 26)
    result = only(case.run())
    assert result.n_raters == 1 and result.needs_adjudication is True


def test_a_phrase_found_by_two_of_three_raters_with_equal_intensity_is_settled():
    case = Case(n_raters=3)
    case.add(0, 4, 26, intensity=2)
    case.add(2, 4, 26, intensity=2)
    result = only(case.run())
    assert result.n_raters == 2 and result.needs_adjudication is False


# --- span matching -----------------------------------------------------------------------------------------------------
def test_spans_overlapping_by_exactly_the_threshold_merge():
    case = Case()
    case.add(0, 0, 20)
    case.add(1, 0, 10)  # iou = 10 / 20 = 0.5
    assert only(case.run()).n_raters == 2


def test_spans_overlapping_just_below_the_threshold_stay_separate():
    case = Case()
    case.add(0, 0, 20)
    case.add(1, 0, 9)  # iou = 9 / 20 = 0.45
    assert [c.n_raters for c in case.run()] == [1, 1]


def test_a_partial_overlap_above_the_threshold_merges_into_a_span_within_the_findings():
    case = Case()
    case.add(0, 4, 24)
    case.add(1, 6, 26)  # iou = 18 / 22
    result = only(case.run())
    assert result.n_raters == 2 and 4 <= result.start < result.end <= 26


def test_a_partial_overlap_below_the_threshold_does_not_merge():
    case = Case()
    case.add(0, 4, 24)
    case.add(1, 20, 40)  # iou = 4 / 36
    assert len(case.run()) == 2


def test_the_panels_own_threshold_is_used_not_a_global_default():
    """iou 0.7: merged under the default 0.5, separate under a panel that asks for 0.8."""
    loose = Case()
    loose.add(0, 0, 10)
    loose.add(1, 0, 7)
    assert only(loose.run()).n_raters == 2
    strict = Case(span_match_min_iou=0.8)
    strict.add(0, 0, 10)
    strict.add(1, 0, 7)
    assert [c.n_raters for c in strict.run()] == [1, 1]


def test_spans_are_never_merged_across_dimensions():
    case = Case()
    case.add(0, 4, 26, dimension=FA)
    case.add(1, 4, 26, dimension=AB)
    results = case.run()
    assert sorted((c.dimension, c.n_raters) for c in results) == [(AB, 1), (FA, 1)]


def test_findings_of_one_rater_on_different_dimensions_stay_separate():
    case = Case(n_raters=1)
    case.add(0, 4, 26, dimension=FA)
    case.add(0, 4, 26, dimension=AB, intensity=1)
    assert sorted(c.dimension for c in case.run()) == [AB, FA]


def test_several_phrases_in_one_message_merge_independently():
    case = Case()
    case.add(0, 4, 26, intensity=1)
    case.add(1, 4, 26, intensity=1)
    case.add(0, 30, 58, intensity=3)
    case.add(1, 31, 58, intensity=3)
    results = case.run()
    assert [(c.n_raters, c.needs_adjudication) for c in results] == [(2, False), (2, False)]
    assert [c.start for c in results] == sorted(c.start for c in results)


# --- intensities -------------------------------------------------------------------------------------------------------
def test_intensities_differing_by_the_threshold_need_adjudication():
    case = Case()
    case.add(0, 4, 26, intensity=1)
    case.add(1, 4, 26, intensity=3)
    result = only(case.run())
    assert result.needs_adjudication is True
    assert result.intensity_mean == pytest.approx(2.0) and result.intensity_range == 2


def test_intensities_one_below_the_threshold_are_settled():
    case = Case()
    case.add(0, 4, 26, intensity=1)
    case.add(1, 4, 26, intensity=2)
    result = only(case.run())
    assert result.needs_adjudication is False
    assert result.intensity_mean == pytest.approx(1.5) and result.intensity_range == 1


def test_the_widest_possible_disagreement_needs_adjudication():
    case = Case()
    case.add(0, 4, 26, intensity=0)
    case.add(1, 4, 26, intensity=4)
    assert only(case.run()).needs_adjudication is True


def test_the_panels_own_disagreement_threshold_is_used():
    case = Case(intensity_disagreement_threshold=3)
    case.add(0, 4, 26, intensity=1)
    case.add(1, 4, 26, intensity=3)  # differ by 2: below this panel's threshold of 3
    assert only(case.run()).needs_adjudication is False
    case = Case(intensity_disagreement_threshold=3)
    case.add(0, 4, 26, intensity=1)
    case.add(1, 4, 26, intensity=4)
    assert only(case.run()).needs_adjudication is True


def test_a_zero_and_a_one_are_settled_but_zero_and_two_are_not():
    settled = Case()
    settled.add(0, 4, 26, intensity=0)
    settled.add(1, 4, 26, intensity=1)
    assert only(settled.run()).needs_adjudication is False
    split = Case()
    split.add(0, 4, 26, intensity=0)
    split.add(1, 4, 26, intensity=2)
    assert only(split.run()).needs_adjudication is True


def test_three_raters_spread_by_the_threshold_need_adjudication():
    case = Case(n_raters=3)
    for index, value in enumerate((1, 2, 3)):
        case.add(index, 4, 26, intensity=value)
    result = only(case.run())
    assert result.n_raters == 3 and result.needs_adjudication is True
    assert result.intensity_mean == pytest.approx(2.0) and result.intensity_range == 2


def test_three_raters_within_the_threshold_are_settled():
    case = Case(n_raters=3)
    for index, value in enumerate((2, 2, 3)):
        case.add(index, 4, 26, intensity=value)
    result = only(case.run())
    assert result.n_raters == 3 and result.needs_adjudication is False
    assert result.intensity_mean == pytest.approx(7 / 3) and result.intensity_range == 1


def test_three_raters_in_full_agreement():
    case = Case(n_raters=3)
    for index in range(3):
        case.add(index, 4, 26, intensity=4)
    result = only(case.run())
    assert result.needs_adjudication is False and result.intensity_mean == pytest.approx(4.0) and result.intensity_range == 0


# --- not scorable ------------------------------------------------------------------------------------------------------
def test_disagreement_on_whether_a_phrase_is_scorable_needs_adjudication():
    case = Case()
    case.add(0, 4, 26, intensity=3)
    case.add(1, 4, 26, intensity=None, not_scorable_reason="contested")
    result = only(case.run())
    assert result.n_raters == 2 and result.needs_adjudication is True


def test_the_mean_and_range_cover_only_scorable_findings():
    case = Case(n_raters=3)
    case.add(0, 4, 26, intensity=3)
    case.add(1, 4, 26, intensity=None, not_scorable_reason="unverifiable")
    case.add(2, 4, 26, intensity=2)
    result = only(case.run())
    assert result.intensity_mean == pytest.approx(2.5) and result.intensity_range == 1
    assert result.needs_adjudication is True


def test_one_scorable_finding_among_not_scorable_ones_has_that_intensity_and_zero_range():
    case = Case()
    case.add(0, 4, 26, intensity=3)
    case.add(1, 4, 26, intensity=None, not_scorable_reason="needs_context")
    result = only(case.run())
    assert result.intensity_mean == pytest.approx(3.0) and result.intensity_range == 0


def test_agreement_that_a_phrase_is_not_scorable_is_settled_with_no_mean():
    """Both raters decline (even for different reasons): they agree on scorability, so no adjudication; nothing to average."""
    case = Case()
    case.add(0, 4, 26, intensity=None, not_scorable_reason="contested")
    case.add(1, 4, 26, intensity=None, not_scorable_reason="unverifiable")
    result = only(case.run())
    assert result.n_raters == 2 and result.needs_adjudication is False
    assert result.intensity_mean is None


def test_a_lone_not_scorable_finding_from_one_rater_still_counts_as_found_by_one():
    case = Case()
    case.add(0, 4, 26, intensity=None, not_scorable_reason="contested")
    result = only(case.run())
    assert result.n_raters == 1 and result.needs_adjudication is True and result.intensity_mean is None


# --- determinism -------------------------------------------------------------------------------------------------------
def build_busy_case():
    case = Case(n_raters=3)
    case.add(0, 4, 26, intensity=1)
    case.add(1, 5, 26, intensity=3)
    case.add(2, 4, 27, intensity=2)
    case.add(0, 30, 58, dimension=AB, intensity=4)
    case.add(2, 30, 57, dimension=AB, intensity=None, not_scorable_reason="contested")
    case.add(1, 60, 82, intensity=0)
    case.add(1, 60, 70, dimension=AB, intensity=1)
    return case


def test_the_result_is_the_same_when_the_input_is_reordered():
    case = build_busy_case()
    expected = signature(case.run())
    assert len(expected) >= 4
    rng = random.Random(20260926)
    for _ in range(25):
        raters = list(case.by_rater)
        rng.shuffle(raters)
        shuffled = {}
        for rater in raters:
            findings = list(case.by_rater[rater])
            rng.shuffle(findings)
            shuffled[rater] = findings
        assert signature(build_consensus(case.panel, case.message, shuffled)) == expected


def test_every_ordering_of_a_two_rater_input_gives_the_same_result():
    case = Case()
    case.add(0, 4, 26, intensity=1)
    case.add(0, 30, 58, intensity=2)
    case.add(1, 5, 26, intensity=2)
    case.add(1, 30, 57, intensity=2)
    expected = signature(case.run())
    first_rater, second_rater = case.raters
    for first, second in itertools.product(
        itertools.permutations(case.by_rater[first_rater]), itertools.permutations(case.by_rater[second_rater])
    ):
        forward = {first_rater: list(first), second_rater: list(second)}
        backward = {second_rater: list(second), first_rater: list(first)}
        assert signature(build_consensus(case.panel, case.message, forward)) == expected
        assert signature(build_consensus(case.panel, case.message, backward)) == expected


def test_calling_twice_gives_the_same_signature():
    case = build_busy_case()
    assert signature(case.run()) == signature(case.run())


def test_the_output_is_ordered_by_position():
    case = build_busy_case()
    keys = [(c.start, c.end) for c in case.run() if c.dimension == FA]
    assert keys == sorted(keys)


# --- sentence snapping -------------------------------------------------------------------------------------------------
def test_without_snapping_two_phrases_in_one_sentence_do_not_merge():
    case = Case()
    case.add(0, 4, 26, intensity=1)  # "tax was raised in 2020"
    case.add(1, 0, 7, intensity=1)  # "The tax"
    assert [c.n_raters for c in case.run()] == [1, 1]
    assert [c.n_raters for c in case.run(snap_to_sentences=False)] == [1, 1]


def test_with_snapping_phrases_in_the_same_sentence_merge():
    case = Case()
    case.add(0, 4, 26, intensity=1)
    case.add(1, 0, 7, intensity=1)
    result = only(case.run(snap_to_sentences=True))
    assert result.n_raters == 2 and result.needs_adjudication is False
    assert result.start == 0 and 26 <= result.end <= 28  # the first sentence, "The tax was raised in 2020."


def test_snapped_spans_lie_on_sentence_boundaries():
    case = Case()
    case.add(0, 36, 48, intensity=2)  # "a disaster for" inside the second sentence
    case.add(1, 30, 40, intensity=2)
    result = only(case.run(snap_to_sentences=True))
    second_start = TEXT.index("It was")
    second_end = TEXT.index("everyone.") + len("everyone.")
    assert result.start == second_start
    assert second_end <= result.end <= second_end + 1


def test_snapping_does_not_merge_findings_in_different_sentences():
    case = Case()
    case.add(0, 4, 26)
    case.add(1, 30, 58)
    assert [c.n_raters for c in case.run(snap_to_sentences=True)] == [1, 1]


def test_snapping_keeps_dimensions_apart():
    case = Case()
    case.add(0, 4, 26, dimension=FA)
    case.add(1, 0, 7, dimension=AB)
    assert len(case.run(snap_to_sentences=True)) == 2


def test_snapping_does_not_change_the_intensity_rules():
    case = Case()
    case.add(0, 4, 26, intensity=0)
    case.add(1, 0, 7, intensity=3)
    result = only(case.run(snap_to_sentences=True))
    assert result.needs_adjudication is True and result.intensity_range == 3


# --- documented by the builder, not fixed by the brief: the merged findings and saving ------------------------------------------
def test_each_result_lists_the_findings_it_merges():
    case = Case()
    a = case.add(0, 4, 26, intensity=1)
    b = case.add(1, 4, 26, intensity=1)
    lone = case.add(0, 30, 58, intensity=2)
    first, second = case.run()
    assert set(first.merged_findings) == {a, b}
    assert set(second.merged_findings) == {lone}


def test_results_are_unsaved_by_default_and_save_writes_them_with_their_links():
    from evaluation.models import ConsensusFinding

    case = Case()
    a = case.add(0, 4, 26, intensity=1)
    b = case.add(1, 4, 26, intensity=3)
    case.add(0, 30, 58, intensity=2)
    unsaved = case.run()
    assert ConsensusFinding.objects.count() == 0 and all(c.pk is None for c in unsaved)
    saved = case.run(save=True)
    assert ConsensusFinding.objects.count() == 2 and all(c.pk for c in saved)
    assert set(kit.merged_findings(saved[0]).all()) == {a, b}
    assert saved[0].needs_adjudication is True


# --- more consensus cases ----------------------------------------------------------------------------------------------------
def test_one_raters_two_overlapping_findings_never_count_as_two_raters():
    case = Case()
    case.add(0, 0, 10, intensity=1)
    case.add(0, 0, 9, intensity=1)
    results = case.run()
    assert results and all(c.n_raters == 1 for c in results)


def test_the_panel_decides_how_many_raters_there_are_even_if_one_rater_is_missing_from_the_input():
    case = Case()
    finding = case.add(0, 4, 26)
    result = only(build_consensus(case.panel, case.message, {case.raters[0]: [finding]}))
    assert result.n_raters == 1 and result.needs_adjudication is True


def test_snapping_uses_question_and_exclamation_marks_as_sentence_ends():
    text = "Is the tax rising? The tax rose in 2020! Nobody liked it at all. " + "x" * 30
    case = Case(text=text)
    second = text.index("The tax rose")
    case.add(0, second + 4, second + 12, intensity=2)  # inside the second sentence
    case.add(1, second, second + 7, intensity=2)
    assert [c.n_raters for c in case.run()] == [1, 1]
    result = only(case.run(snap_to_sentences=True))
    assert result.n_raters == 2
    assert result.start == second and result.end == text.index("2020!") + len("2020!")


def test_a_span_that_ends_where_the_next_sentence_begins_is_not_stretched_into_it():
    case = Case()
    case.add(0, 0, 28, intensity=1)  # the first sentence plus the space after it
    case.add(1, 4, 26, intensity=1)
    result = only(case.run(snap_to_sentences=True))
    assert result.start == 0 and result.end <= 28


def test_text_without_sentence_punctuation_is_one_sentence():
    case = Case(text="no punctuation here at all just words " + "y" * 40)
    case.add(0, 3, 12, intensity=1)
    case.add(1, 20, 30, intensity=1)
    result = only(case.run(snap_to_sentences=True))
    assert result.start == 0 and result.n_raters == 2


# --- reading pinned by the tester: the merged span is the union; greedy order is (start, end, rater name) ---------------------
def test_a_merged_span_is_the_union_of_the_merged_spans():
    """The brief says only that the consensus finding has a start and end. Reading chosen: the phrase both raters
    pointed at, at its widest, i.e. the union of the merged spans."""
    case = Case()
    case.add(0, 4, 24)
    case.add(1, 6, 26)
    result = only(case.run())
    assert (result.start, result.end) == (4, 26)


def test_the_merge_order_is_by_start_then_end_then_rater_not_by_rater_first():
    """Three raters whose names sort in the opposite order to their spans: 'zeta' (0,10), 'alpha' (4,14), 'mid' (8,18),
    threshold 0.4. Processed by start, zeta and alpha merge (iou 0.43) and mid stays out (iou 0.11 with zeta's span,
    0.33 with their union); processed rater name first, alpha (4,14) would take both. The brief fixes the sort."""
    message, _ = kit.message_with_text(TEXT)
    raters = {name: kit.make_rater("llm", name=name) for name in ("zeta", "alpha", "mid")}
    panel = kit.make_panel(list(raters.values()), span_match_min_iou=0.4)
    spans = {"zeta": (0, 10), "alpha": (4, 14), "mid": (8, 18)}
    by_rater = {}
    for name, rater in raters.items():
        rating = kit.make_rating(rater, message)
        by_rater[rater] = [kit.make_finding(rating, *spans[name], intensity=2)]
    results = build_consensus(panel, message, by_rater)
    assert [(c.n_raters, c.start, c.end) for c in results] == [(2, 0, 14), (1, 8, 18)]


def test_snapping_stretches_a_span_over_every_sentence_it_touches():
    case = Case()
    case.add(0, 20, 40, intensity=2)  # the end of sentence one and the start of sentence two
    case.add(1, 0, 58, intensity=2)  # both whole sentences
    assert [c.n_raters for c in case.run()] == [1, 1]
    result = only(case.run(snap_to_sentences=True))
    assert result.n_raters == 2
    assert result.start == 0 and result.end == TEXT.index("everyone.") + len("everyone.")


def test_a_decimal_point_does_not_end_a_sentence():
    text = "Prices rose 3.5 percent last year. Nobody noticed at all. " + "z" * 40
    case = Case(text=text)
    case.add(0, 0, 10, intensity=1)  # "Prices ros"
    case.add(1, 16, 30, intensity=1)  # after the decimal point, same sentence
    result = only(case.run(snap_to_sentences=True))
    assert result.n_raters == 2 and result.start == 0 and result.end == text.index("last year.") + len("last year.")
