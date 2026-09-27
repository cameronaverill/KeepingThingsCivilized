"""Idempotency, `save=False` and what a second run may and may not change."""
import dataclasses

import matching14b_kit as kit
import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def w():
    return kit.build_world()


def test_running_twice_adds_no_link(w):
    kit.match(w.panel)
    first = kit.links()
    kit.match(w.panel)
    assert kit.links() == first == kit.world_links(w)
    assert kit.link_count() == 7


def test_running_three_times_still_has_seven_rows(w):
    kit.match(w.panel)
    kit.match(w.panel)
    kit.match(w.panel)
    assert kit.link_count() == 7


def test_created_counts_only_new_links_and_linked_counts_every_pair(w):
    first = kit.match(w.panel)
    second = kit.match(w.panel)
    third = kit.match(w.panel)
    assert (first.linked, first.created) == (7, 7)
    assert (second.linked, second.created) == (7, 0)
    assert (third.linked, third.created) == (7, 0)


def test_the_second_run_reports_the_same_unmatched_lists_and_unrated_messages(w):
    first = kit.match(w.panel)
    second = kit.match(w.panel)
    assert kit.rows(second.issues_unmatched) == kit.rows(first.issues_unmatched) == kit.world_issues_unmatched(w)
    assert kit.rows(second.findings_unmatched) == kit.rows(first.findings_unmatched) == kit.world_findings_unmatched(w)
    assert sorted(second.unrated_messages) == sorted(first.unrated_messages) == [w.m2.pk]


def test_the_second_run_does_not_touch_existing_link_rows(w):
    from evaluation.models import IssueFindingLink

    kit.match(w.panel)
    before = sorted(IssueFindingLink.objects.values_list("pk", "issue_id", "consensus_finding_id", "overlap"))
    kit.match(w.panel)
    after = sorted(IssueFindingLink.objects.values_list("pk", "issue_id", "consensus_finding_id", "overlap"))
    assert after == before


def test_a_link_created_by_hand_is_not_duplicated_and_the_missing_ones_are_added(w):
    kit.make_link(w.i1, w.c1, 1.0)
    report = kit.match(w.panel)
    assert kit.links() == kit.world_links(w)
    assert kit.link_count() == 7
    assert (report.linked, report.created) == (7, 6)


def test_a_new_issue_added_between_runs_gets_only_its_own_new_link(w):
    kit.match(w.panel)
    extra = kit.make_issue(w.run1, w.m1, kit.FA, 20, 30)
    report = kit.match(w.panel)
    assert (report.linked, report.created) == (8, 1)
    assert kit.links() == kit.world_links(w) | {(extra.pk, w.c2.pk, 1.0)}
    assert kit.link_count() == 8


def test_a_new_finding_added_between_runs_is_matched_by_the_second_run(w):
    kit.match(w.panel)
    added = kit.make_cf(w.panel, w.m1, kit.FA, 40, 45, 2.0)  # i3 [40,44) vs [40,45): 4/5 = 0.8
    report = kit.match(w.panel)
    assert (report.linked, report.created) == (8, 1)
    assert kit.links() == kit.world_links(w) | {(w.i3.pk, added.pk, 4 / 5)}
    assert w.i3.pk not in {row[0] for row in report.issues_unmatched}
    assert [row[0] for row in kit.rows(report.issues_unmatched)] == [w.i6.pk]


def test_save_false_creates_no_link(w):
    report = kit.match(w.panel, save=False)
    assert kit.link_count() == 0
    assert (report.linked, report.created, report.saved) == (7, 0, False)


def test_save_false_computes_the_same_reports_as_save_true_except_created_and_saved(w):
    dry = dataclasses.asdict(kit.match(w.panel, save=False))
    wet = dataclasses.asdict(kit.match(w.panel, save=True))
    assert (dry.pop("created"), dry.pop("saved")) == (0, False)
    assert (wet.pop("created"), wet.pop("saved")) == (7, True)
    assert dry == wet


def test_save_false_reports_the_same_after_a_saved_run(w):
    saved = kit.match(w.panel, save=True)
    dry = kit.match(w.panel, save=False)
    assert (dry.linked, dry.created) == (7, 0)
    assert dry.issues_unmatched == saved.issues_unmatched
    assert dry.findings_unmatched == saved.findings_unmatched


def test_save_false_after_a_saved_run_changes_nothing(w):
    kit.match(w.panel)
    kit.match(w.panel, save=False)
    assert kit.links() == kit.world_links(w)


def test_save_false_reports_unmatched_lists_as_if_matched(w):
    report = kit.match(w.panel, save=False)
    assert kit.rows(report.issues_unmatched) == kit.world_issues_unmatched(w)
    assert kit.rows(report.findings_unmatched) == kit.world_findings_unmatched(w)
    assert sorted(report.unrated_messages) == [w.m2.pk]


def test_save_is_true_by_default(w):
    kit.match(w.panel)
    assert kit.link_count() == 7


def test_matching_with_nothing_to_match_returns_an_empty_report():
    panel = kit.make_panel()
    report = kit.match(panel)
    assert report.linked == 0
    assert report.issues_unmatched == []
    assert report.findings_unmatched == []
    assert report.unrated_messages == []
    assert report.not_found_issues == []
    assert (report.linked, report.created) == (0, 0)
    assert kit.link_count() == 0


def test_matching_makes_no_llm_call_and_creates_no_ledger_row(w):
    from moderation.models import LLMCall

    kit.match(w.panel)
    assert LLMCall.objects.count() == 0
