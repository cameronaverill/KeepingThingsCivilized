"""IoU rule: a link needs IoU >= the threshold (panel default, or `min_iou`), spans on the message text, several-to-one and
one-to-several links, exact known IoU values. Every span pair below has its IoU written next to it."""
import matching14b_kit as kit
import pytest

pytestmark = pytest.mark.django_db


def one(threshold, issue_span, finding_span, **kwargs):
    """A message with one FA issue at issue_span and one FA consensus finding at finding_span. Returns (world, issue, finding)."""
    s = kit.simple_world(threshold)
    issue = kit.make_issue(s.run, s.message, kit.FA, *issue_span)
    finding = kit.make_cf(s.panel, s.message, kit.FA, *finding_span, 2.0)
    return s, issue, finding


def test_identical_spans_link_with_overlap_one():
    s, issue, finding = one(0.5, (10, 20), (10, 20))
    report = kit.match(s.panel)
    assert kit.links() == {(issue.pk, finding.pk, 1.0)}
    assert report.linked == 1


def test_iou_exactly_at_the_panel_threshold_links():
    # issue [0,10) vs finding [0,5): intersection 5, union 10, IoU 0.5 = threshold 0.5
    s, issue, finding = one(0.5, (0, 10), (0, 5))
    kit.match(s.panel)
    assert kit.links() == {(issue.pk, finding.pk, 0.5)}


def test_iou_just_below_the_threshold_does_not_link():
    # issue [0,10) vs finding [0,4): 4/10 = 0.4 < 0.5
    s, issue, finding = one(0.5, (0, 10), (0, 4))
    report = kit.match(s.panel)
    assert kit.links() == set()
    assert report.linked == 0
    assert kit.rows(report.issues_unmatched) == [(issue.pk, s.message.pk, kit.FA)]
    assert kit.rows(report.findings_unmatched) == [(finding.pk, s.message.pk, kit.FA, 2.0)]


def test_one_more_character_of_overlap_crosses_the_threshold():
    # issue [0,10) vs finding [0,6): 6/10 = 0.6 >= 0.5
    s, issue, finding = one(0.5, (0, 10), (0, 6))
    kit.match(s.panel)
    assert kit.links() == {(issue.pk, finding.pk, 6 / 10)}


def test_the_threshold_is_the_panels_not_the_global_default():
    # issue [0,10) vs finding [0,3): 0.3. Panel threshold 0.3 links; the global default 0.5 would not.
    s, issue, finding = one(0.3, (0, 10), (0, 3))
    kit.match(s.panel)
    assert kit.links() == {(issue.pk, finding.pk, 3 / 10)}


def test_a_stricter_panel_threshold_refuses_what_the_default_would_link():
    # issue [0,10) vs finding [0,6): 0.6; panel threshold 0.7
    s, issue, finding = one(0.7, (0, 10), (0, 6))
    kit.match(s.panel)
    assert kit.links() == set()


def test_min_iou_argument_overrides_the_panel_threshold_downwards():
    # 0.4 with panel threshold 0.5: refused by default, linked with min_iou=0.4 (4/10 == 0.4 exactly)
    s, issue, finding = one(0.5, (0, 10), (0, 4))
    kit.match(s.panel, min_iou=0.4)
    assert kit.links() == {(issue.pk, finding.pk, 4 / 10)}


def test_min_iou_argument_overrides_the_panel_threshold_upwards():
    # 0.5 links under the panel's 0.5 but not under min_iou=0.6
    s, issue, finding = one(0.5, (0, 10), (0, 5))
    kit.match(s.panel, min_iou=0.6)
    assert kit.links() == set()


def test_min_iou_equal_to_the_iou_links():
    s, issue, finding = one(0.9, (0, 10), (0, 5))
    kit.match(s.panel, min_iou=0.5)
    assert kit.links() == {(issue.pk, finding.pk, 0.5)}


def test_the_second_positional_argument_is_the_conversation_ids_and_min_iou_is_a_keyword():
    s, issue, finding = one(0.5, (0, 10), (0, 10))
    report = kit.match(s.panel, [s.conv.pk], min_iou=0.5)
    assert report.linked == 1


def test_touching_spans_never_link_even_with_a_tiny_threshold():
    # issue [0,5) vs finding [5,10): intersection 0
    s, issue, finding = one(0.5, (0, 5), (5, 10))
    kit.match(s.panel, min_iou=0.01)
    assert kit.links() == set()


def test_disjoint_spans_never_link():
    s, issue, finding = one(0.5, (0, 5), (50, 60))
    kit.match(s.panel, min_iou=0.01)
    assert kit.links() == set()


def test_a_short_issue_inside_a_long_finding_links_only_when_the_ratio_reaches_the_threshold():
    # issue [10,15) inside finding [0,20): 5/20 = 0.25 (below 0.5)
    s, issue, finding = one(0.5, (10, 15), (0, 20))
    kit.match(s.panel)
    assert kit.links() == set()
    kit.match(s.panel, min_iou=0.25)
    assert kit.links() == {(issue.pk, finding.pk, 5 / 20)}


def test_the_iou_is_intersection_over_union_not_over_the_smaller_span():
    # issue [10,20) vs finding [15,25): intersection 5, union 15, IoU 1/3 (overlap coefficient would be 0.5)
    s, issue, finding = one(0.4, (10, 20), (15, 25))
    kit.match(s.panel)
    assert kit.links() == set()
    kit.match(s.panel, min_iou=1 / 3)
    assert kit.links() == {(issue.pk, finding.pk, 5 / 15)}


def test_containment_uses_the_union_so_a_partial_cover_scores_the_ratio():
    # issue [0,20) vs finding [5,15): intersection 10, union 20, IoU 0.5
    s, issue, finding = one(0.5, (0, 20), (5, 15))
    kit.match(s.panel)
    assert kit.links() == {(issue.pk, finding.pk, 10 / 20)}


def test_several_issues_link_to_one_finding():
    s = kit.simple_world(0.5)
    finding = kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 2.0)
    a = kit.make_issue(s.run, s.message, kit.FA, 0, 10)
    b = kit.make_issue(s.run, s.message, kit.FA, 0, 5)
    c = kit.make_issue(s.run, s.message, kit.FA, 0, 4)  # 0.4: not linked
    report = kit.match(s.panel)
    assert kit.links() == {(a.pk, finding.pk, 1.0), (b.pk, finding.pk, 0.5)}
    assert report.linked == 2
    assert kit.rows(report.issues_unmatched) == [(c.pk, s.message.pk, kit.FA)]
    assert report.findings_unmatched == []


def test_one_issue_links_to_several_findings():
    s = kit.simple_world(0.5)
    issue = kit.make_issue(s.run, s.message, kit.FA, 100, 120)
    f1 = kit.make_cf(s.panel, s.message, kit.FA, 100, 110, 1.0)
    f2 = kit.make_cf(s.panel, s.message, kit.FA, 110, 120, 3.0)
    f3 = kit.make_cf(s.panel, s.message, kit.FA, 118, 130, 2.0)  # [100,120) and [118,130): intersection 2, union 30, IoU 2/30
    report = kit.match(s.panel)
    assert kit.links() == {(issue.pk, f1.pk, 0.5), (issue.pk, f2.pk, 0.5)}
    assert report.linked == 2
    assert kit.rows(report.findings_unmatched) == [(f3.pk, s.message.pk, kit.FA, 2.0)]
    assert report.issues_unmatched == []


def test_many_to_many_links_all_qualifying_pairs():
    s = kit.simple_world(0.5)
    i1 = kit.make_issue(s.run, s.message, kit.FA, 0, 10)
    i2 = kit.make_issue(s.run, s.message, kit.FA, 0, 20)
    f1 = kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 1.0)
    f2 = kit.make_cf(s.panel, s.message, kit.FA, 10, 20, 1.0)
    # i1-f1 1.0; i1-f2 0; i2-f1 10/20; i2-f2 10/20
    kit.match(s.panel)
    assert kit.links() == {(i1.pk, f1.pk, 1.0), (i2.pk, f1.pk, 0.5), (i2.pk, f2.pk, 0.5)}


def test_spans_are_compared_on_the_same_message_only():
    s = kit.simple_world(0.5)
    other = kit.user_msg(s.conv, s.parts, "B")
    run = kit.make_run(other)
    kit.make_issue(run, s.message, kit.FA, 0, 10)
    finding_elsewhere = kit.make_cf(s.panel, other, kit.FA, 0, 10, 2.0)
    report = kit.match(s.panel)
    assert kit.links() == set()
    assert kit.rows(report.findings_unmatched) == [(finding_elsewhere.pk, other.pk, kit.FA, 2.0)]
    assert sorted(report.unrated_messages) == [s.message.pk]
    assert report.issues_unmatched == []


def test_a_normalized_quote_match_with_offsets_is_used_like_an_exact_one():
    s = kit.simple_world(0.5)
    issue = kit.make_issue(s.run, s.message, kit.FA, 10, 20, quote_match="normalized", quote="a  slightly  different quote")
    finding = kit.make_cf(s.panel, s.message, kit.FA, 10, 20, 2.0)
    kit.match(s.panel)
    assert kit.links() == {(issue.pk, finding.pk, 1.0)}


def test_the_issue_span_is_the_stored_offsets_not_the_position_of_the_quote_text():
    # the message repeats "abcdefghij"; the issue's offsets (10,20) are the second repetition, so a finding at (0,10) is disjoint
    s = kit.simple_world(0.5)
    issue = kit.make_issue(s.run, s.message, kit.FA, 10, 20)
    finding = kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 2.0)
    kit.match(s.panel)
    assert kit.links() == set()
    assert issue.quote == s.message.content[0:10]


def test_intensity_is_not_part_of_matching():
    s = kit.simple_world(0.5)
    issue = kit.make_issue(s.run, s.message, kit.FA, 0, 10, intensity=0)
    finding = kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 4.0)
    kit.match(s.panel)
    assert kit.links() == {(issue.pk, finding.pk, 1.0)}
