"""The hand-built world of matching14b_kit (see its docstring for every row and the IoU worked out by hand): the exact
links, the four reports and the two helper querysets."""
import dataclasses
import json

import matching14b_kit as kit
import pytest
from django.db.models import QuerySet

pytestmark = pytest.mark.django_db


@pytest.fixture
def w():
    return kit.build_world()


def test_the_stored_links_are_exactly_the_seven_worked_out_by_hand(w):
    kit.match(w.panel)
    assert kit.links() == kit.world_links(w)


def test_the_report_counts_seven_links(w):
    report = kit.match(w.panel)
    assert report.linked == 7


def test_issues_unmatched_lists_the_two_candidate_false_positives(w):
    report = kit.match(w.panel)
    assert kit.rows(report.issues_unmatched) == kit.world_issues_unmatched(w)


def test_findings_unmatched_lists_the_three_candidate_misses_with_their_mean_and_not_the_adjudicated_value(w):
    report = kit.match(w.panel)
    assert kit.rows(report.findings_unmatched) == kit.world_findings_unmatched(w)


def test_unrated_messages_is_the_message_without_any_consensus_finding_once(w):
    report = kit.match(w.panel)
    assert sorted(report.unrated_messages) == [w.m2.pk]


def test_the_not_found_issue_is_listed_in_not_found_issues_and_nowhere_else(w):
    report = kit.match(w.panel)
    assert kit.rows(report.not_found_issues) == kit.world_not_found(w)
    assert w.i8.pk not in {row[0] for row in report.issues_unmatched}
    assert w.m1.pk not in report.unrated_messages


def test_the_rejected_issue_is_in_no_report_list(w):
    report = kit.match(w.panel)
    assert w.i7.pk not in {row[0] for row in report.issues_unmatched}
    assert w.i7.pk not in {row[0] for row in report.not_found_issues}


def test_the_first_run_created_all_seven_links_and_recorded_its_parameters(w):
    report = kit.match(w.panel)
    assert (report.created, report.saved, report.min_iou) == (7, True, 0.5)


def test_the_report_is_a_plain_dataclass_with_a_json_friendly_as_dict(w):
    report = kit.match(w.panel)
    assert dataclasses.is_dataclass(report) and not isinstance(report, type)
    decoded = json.loads(json.dumps(report.as_dict()))
    assert decoded == {
        "min_iou": 0.5, "saved": True, "linked": 7, "created": 7,
        "issues_unmatched": [[w.i3.pk, w.m1.pk, kit.FA], [w.i6.pk, w.m1.pk, kit.FA]],
        "findings_unmatched": [
            [w.c3.pk, w.m1.pk, kit.FA, 1.0], [w.c4.pk, w.m1.pk, kit.FA, None], [w.c8.pk, w.m3.pk, kit.AB, 2.0],
        ],
        "unrated_messages": [w.m2.pk],
        "not_found_issues": [[w.i8.pk, w.m1.pk, kit.FA]],
    }  # fmt: skip


def test_the_report_has_exactly_the_agreed_fields(w):
    report = kit.match(w.panel)
    assert [f.name for f in dataclasses.fields(report)] == list(kit.REPORT_FIELDS)


def test_report_lists_are_sorted_by_id_and_entries_are_tuples(w):
    report = kit.match(w.panel)
    assert report.issues_unmatched == [(w.i3.pk, w.m1.pk, kit.FA), (w.i6.pk, w.m1.pk, kit.FA)]
    assert report.findings_unmatched == [
        (w.c3.pk, w.m1.pk, kit.FA, 1.0), (w.c4.pk, w.m1.pk, kit.FA, None), (w.c8.pk, w.m3.pk, kit.AB, 2.0),
    ]  # fmt: skip
    assert report.unrated_messages == [w.m2.pk]
    assert report.not_found_issues == [(w.i8.pk, w.m1.pk, kit.FA)]


def test_the_overlap_stored_is_the_iou(w):
    kit.match(w.panel)
    from evaluation.models import IssueFindingLink

    stored = {(link.issue_id, link.consensus_finding_id): link.overlap for link in IssueFindingLink.objects.all()}
    assert stored[(w.i2.pk, w.c2.pk)] == 5 / 10
    assert stored[(w.i9.pk, w.c2.pk)] == 10 / 20
    assert stored[(w.i1.pk, w.c1.pk)] == 1.0


def test_unmatched_issues_helper_returns_a_queryset_of_issues_equal_to_the_report(w):
    kit.match(w.panel)
    result = kit.unmatched_issues(w.panel)
    assert isinstance(result, QuerySet)
    assert result.model._meta.label == "moderation.Issue"
    assert kit.pks(result) == sorted([w.i3.pk, w.i6.pk])


def test_unmatched_findings_helper_returns_a_queryset_of_consensus_findings_equal_to_the_report(w):
    kit.match(w.panel)
    result = kit.unmatched_findings(w.panel)
    assert isinstance(result, QuerySet)
    assert result.model._meta.label == "evaluation.ConsensusFinding"
    assert kit.pks(result) == sorted([w.c3.pk, w.c4.pk, w.c8.pk])


def test_before_matching_every_eligible_issue_and_every_finding_is_unmatched(w):
    eligible = [w.i1, w.i2, w.i3, w.i4, w.i5, w.i6, w.i9, w.i10]
    assert kit.pks(kit.unmatched_issues(w.panel)) == sorted(i.pk for i in eligible)
    assert kit.pks(kit.unmatched_findings(w.panel)) == sorted(c.pk for c in (w.c1, w.c2, w.c3, w.c4, w.c5, w.c6, w.c7, w.c8))


def test_conversation_scope_one_only_reports_that_conversation(w):
    report = kit.match(w.panel, [w.conv1.pk])
    assert report.linked == 7
    assert kit.rows(report.issues_unmatched) == kit.world_issues_unmatched(w)
    assert kit.rows(report.findings_unmatched) == [(w.c3.pk, w.m1.pk, kit.FA, 1.0), (w.c4.pk, w.m1.pk, kit.FA, None)]
    assert sorted(report.unrated_messages) == [w.m2.pk]


def test_conversation_scope_two_only_reports_that_conversation(w):
    report = kit.match(w.panel, [w.conv2.pk])
    assert report.linked == 0
    assert kit.links() == set()
    assert kit.rows(report.issues_unmatched) == []
    assert kit.rows(report.findings_unmatched) == [(w.c8.pk, w.m3.pk, kit.AB, 2.0)]
    assert report.unrated_messages == []
    assert report.not_found_issues == []


def test_conversation_scope_both_equals_no_scope(w):
    report = kit.match(w.panel, [w.conv1.pk, w.conv2.pk])
    assert report.linked == 7
    assert kit.links() == kit.world_links(w)
    assert kit.rows(report.findings_unmatched) == kit.world_findings_unmatched(w)


def test_scoped_unmatched_helpers_follow_the_scope(w):
    kit.match(w.panel)
    assert kit.pks(kit.unmatched_findings(w.panel, [w.conv2.pk])) == [w.c8.pk]
    assert kit.pks(kit.unmatched_findings(w.panel, [w.conv1.pk])) == sorted([w.c3.pk, w.c4.pk])
    assert kit.pks(kit.unmatched_issues(w.panel, [w.conv2.pk])) == []
    assert kit.pks(kit.unmatched_issues(w.panel, [w.conv1.pk])) == sorted([w.i3.pk, w.i6.pk])


def test_matching_writes_nothing_but_links(w):
    from evaluation.models import ConsensusFinding, Finding, Panel, Rating
    from forum.models import Message
    from moderation.models import Issue, LLMCall

    before = [m.objects.count() for m in (ConsensusFinding, Finding, Panel, Rating, Message, Issue, LLMCall)]
    kit.match(w.panel)
    after = [m.objects.count() for m in (ConsensusFinding, Finding, Panel, Rating, Message, Issue, LLMCall)]
    assert after == before
