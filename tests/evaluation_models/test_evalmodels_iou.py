"""evaluation.consensus.iou on hand-built spans (half-open [start, end) offsets)."""
import pytest

from evaluation.consensus import iou


def test_identical_spans_have_iou_one():
    assert iou((0, 10), (0, 10)) == 1.0


def test_disjoint_spans_have_iou_zero():
    assert iou((0, 5), (10, 15)) == 0.0


def test_touching_spans_have_iou_zero():
    """[0, 5) and [5, 10) share no character."""
    assert iou((0, 5), (5, 10)) == 0.0
    assert iou((5, 10), (0, 5)) == 0.0


def test_a_partial_overlap_is_intersection_over_union():
    assert iou((0, 10), (5, 15)) == pytest.approx(5 / 15)


def test_a_span_inside_another_is_its_share_of_the_larger():
    assert iou((0, 10), (2, 6)) == pytest.approx(4 / 10)


def test_iou_is_symmetric():
    for a, b in [((0, 10), (5, 15)), ((3, 9), (0, 4)), ((0, 10), (2, 6)), ((1, 2), (8, 9))]:
        assert iou(a, b) == iou(b, a)


def test_a_one_character_overlap():
    assert iou((0, 5), (4, 9)) == pytest.approx(1 / 9)


def test_exactly_half():
    assert iou((0, 10), (0, 5)) == pytest.approx(0.5)
    assert iou((0, 10), (5, 10)) == pytest.approx(0.5)


def test_the_result_is_always_between_zero_and_one():
    spans = [(0, 1), (0, 10), (3, 4), (5, 50), (7, 8), (0, 100)]
    for a in spans:
        for b in spans:
            assert 0.0 <= iou(a, b) <= 1.0


def test_shifting_both_spans_together_changes_nothing():
    assert iou((0, 10), (5, 15)) == iou((100, 110), (105, 115))


def test_a_larger_overlap_never_scores_lower():
    assert iou((0, 10), (0, 9)) > iou((0, 10), (0, 7)) > iou((0, 10), (0, 3))


def test_lists_are_accepted_as_spans():
    assert iou([0, 10], [5, 15]) == pytest.approx(5 / 15)
