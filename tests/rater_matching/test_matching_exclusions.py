"""What must never link: rejected issues, not_found issues, issues without a dimension, other dimensions, other panels,
other targets, moderator messages. Each case also checks how the reports treat the row."""
import matching14b_kit as kit
import pytest

pytestmark = pytest.mark.django_db


def test_a_rejected_issue_with_exact_overlap_is_never_linked_and_its_finding_stays_a_candidate_miss():
    s = kit.simple_world()
    issue = kit.make_issue(s.run, s.message, kit.FA, 0, 10, validity="rejected")
    finding = kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 2.0)
    report = kit.match(s.panel)
    assert kit.links() == set()
    assert report.linked == 0
    assert report.issues_unmatched == []
    assert kit.rows(report.findings_unmatched) == [(finding.pk, s.message.pk, kit.FA, 2.0)]
    assert report.not_found_issues == []


def test_a_rejected_issue_on_an_unrated_message_is_not_reported_unrated():
    s = kit.simple_world()
    kit.make_issue(s.run, s.message, kit.FA, 0, 10, validity="rejected")
    report = kit.match(s.panel)
    assert report.unrated_messages == []


def test_a_valid_issue_next_to_a_rejected_one_still_links():
    s = kit.simple_world()
    rejected = kit.make_issue(s.run, s.message, kit.FA, 0, 10, validity="rejected")
    valid = kit.make_issue(s.run, s.message, kit.FA, 0, 10)
    finding = kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 2.0)
    kit.match(s.panel)
    assert kit.links() == {(valid.pk, finding.pk, 1.0)}
    assert rejected.pk not in {i for (i, _c, _o) in kit.links()}


def test_a_not_found_issue_is_skipped_reported_and_not_a_false_positive():
    s = kit.simple_world()
    issue = kit.make_issue(s.run, s.message, kit.FA)  # valid, quote_match not_found, no offsets
    finding = kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 2.0)
    report = kit.match(s.panel)
    assert kit.links() == set()
    assert report.issues_unmatched == []
    assert kit.rows(report.findings_unmatched) == [(finding.pk, s.message.pk, kit.FA, 2.0)]
    assert report.not_found_issues == [(issue.pk, s.message.pk, kit.FA)]
    assert report.unrated_messages == []


def test_a_not_found_issue_is_reported_even_when_its_message_is_unrated_and_the_message_still_counts_unrated():
    s = kit.simple_world()
    issue = kit.make_issue(s.run, s.message, kit.FA)
    report = kit.match(s.panel)
    assert report.not_found_issues == [(issue.pk, s.message.pk, kit.FA)]
    assert report.unrated_messages == [s.message.pk]
    assert report.issues_unmatched == []


def test_not_found_issues_reported_with_save_false_too():
    s = kit.simple_world()
    issue = kit.make_issue(s.run, s.message, kit.FA)
    kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 2.0)
    report = kit.match(s.panel, save=False)
    assert report.not_found_issues == [(issue.pk, s.message.pk, kit.FA)]


def test_two_not_found_issues_are_both_reported():
    s = kit.simple_world()
    a = kit.make_issue(s.run, s.message, kit.FA)
    b = kit.make_issue(s.run, s.message, kit.AB)
    kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 2.0)
    report = kit.match(s.panel)
    assert report.not_found_issues == [(a.pk, s.message.pk, kit.FA), (b.pk, s.message.pk, kit.AB)]


def test_an_issue_without_a_dimension_is_never_linked():
    s = kit.simple_world()
    issue = kit.make_issue(s.run, s.message, "", 0, 10)
    kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 2.0)
    kit.make_cf(s.panel, s.message, kit.AB, 0, 10, 2.0)
    report = kit.match(s.panel)
    assert kit.links() == set()
    assert report.linked == 0
    assert issue.dimension == ""
    assert report.issues_unmatched == []
    assert report.not_found_issues == []
    assert report.unrated_messages == []


def test_issues_of_a_different_dimension_never_link_even_with_identical_spans():
    s = kit.simple_world()
    issue = kit.make_issue(s.run, s.message, kit.FA, 0, 10)
    finding = kit.make_cf(s.panel, s.message, kit.AB, 0, 10, 3.0)
    report = kit.match(s.panel)
    assert kit.links() == set()
    assert kit.rows(report.issues_unmatched) == [(issue.pk, s.message.pk, kit.FA)]
    assert kit.rows(report.findings_unmatched) == [(finding.pk, s.message.pk, kit.AB, 3.0)]
    assert report.unrated_messages == []


def test_a_message_rated_only_on_another_dimension_is_rated_so_its_issue_is_a_candidate_false_positive_not_unrated():
    s = kit.simple_world()
    issue = kit.make_issue(s.run, s.message, kit.FA, 200, 210)
    kit.make_cf(s.panel, s.message, kit.AB, 0, 10, 3.0)
    report = kit.match(s.panel)
    assert kit.rows(report.issues_unmatched) == [(issue.pk, s.message.pk, kit.FA)]
    assert report.unrated_messages == []


def test_findings_of_another_panel_are_ignored_and_the_message_is_unrated_for_this_panel():
    s = kit.simple_world()
    other_panel = kit.make_panel()
    issue = kit.make_issue(s.run, s.message, kit.FA, 0, 10)
    foreign = kit.make_cf(other_panel, s.message, kit.FA, 0, 10, 2.0)
    report = kit.match(s.panel)
    assert kit.links() == set()
    assert report.issues_unmatched == []
    assert report.findings_unmatched == []
    assert sorted(report.unrated_messages) == [s.message.pk]
    assert kit.pks(kit.unmatched_findings(s.panel)) == []
    assert kit.pks(kit.unmatched_findings(other_panel)) == [foreign.pk]
    assert issue.pk not in {row[0] for row in report.issues_unmatched}


def test_two_panels_are_matched_independently_and_a_link_only_goes_to_the_asked_panels_finding():
    s = kit.simple_world()
    other_panel = kit.make_panel()
    issue = kit.make_issue(s.run, s.message, kit.FA, 0, 10)
    mine = kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 2.0)
    theirs = kit.make_cf(other_panel, s.message, kit.FA, 0, 10, 4.0)
    kit.match(s.panel)
    assert kit.links() == {(issue.pk, mine.pk, 1.0)}
    report = kit.match(other_panel)
    assert kit.links() == {(issue.pk, mine.pk, 1.0), (issue.pk, theirs.pk, 1.0)}
    assert report.linked == 1
    assert kit.links(other_panel) == {(issue.pk, theirs.pk, 1.0)}


def test_another_panels_existing_link_does_not_make_this_panels_finding_matched():
    s = kit.simple_world()
    other_panel = kit.make_panel()
    issue = kit.make_issue(s.run, s.message, kit.FA, 0, 10)
    mine = kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 2.0)
    theirs = kit.make_cf(other_panel, s.message, kit.FA, 0, 10, 2.0)
    kit.make_link(issue, theirs)
    report = kit.match(s.panel, save=False)
    assert kit.rows(report.issues_unmatched) == []
    assert report.linked == 1
    assert kit.pks(kit.unmatched_findings(s.panel)) == [mine.pk]
    assert kit.pks(kit.unmatched_issues(s.panel)) == [issue.pk]


def test_a_consensus_finding_on_an_intervention_act_with_the_same_numeric_id_never_matches():
    from evaluation.models import ConsensusFinding

    conv, parts = kit.make_conversation()
    trigger = kit.user_msg(conv, parts, "A")
    run0 = kit.make_run(trigger)
    from moderation.models import InterventionAct

    act = InterventionAct.objects.create(
        id=7777, run=run0, order=1, act_type="request_information", tone="neutral", text=kit.TEXT, addressee="A",
        subject="none",
    )
    message = kit.user_msg(conv, parts, "B", id=7777)
    run1 = kit.make_run(message)
    panel = kit.make_panel()
    issue = kit.make_issue(run1, message, kit.FA, 0, 10)
    on_act = ConsensusFinding.objects.create(
        panel=panel, target_type="intervention_act", target_id=act.pk, dimension=kit.FA, start=0, end=10, n_raters=1,
        intensity_mean=1.0,
    )
    assert act.pk == message.pk
    report = kit.match(panel)
    assert kit.links() == set()
    assert report.unrated_messages == [message.pk]
    assert report.findings_unmatched == []
    assert issue.pk not in {row[0] for row in report.issues_unmatched}
    assert on_act.pk not in {row[0] for row in report.issues_unmatched}


def test_a_rejected_issue_on_a_moderator_message_never_matches():
    s = kit.simple_world()
    moderator = kit.mod_msg(s.conv)
    run = kit.make_run(kit.user_msg(s.conv, s.parts, "B"))  # a run is triggered by a user message, here one after the moderator's
    issue = kit.make_issue(run, moderator, kit.FA, 0, 10, validity="rejected")
    finding = kit.make_cf(s.panel, moderator, kit.FA, 0, 10, 2.0)
    report = kit.match(s.panel)
    assert kit.links() == set()
    assert report.linked == 0
    assert report.issues_unmatched == []
    assert report.findings_unmatched == []
    assert report.unrated_messages == []
    assert issue.message.author_type == "moderator"
    assert finding.target_id == moderator.pk


def test_even_a_valid_issue_forced_onto_a_moderator_message_never_matches():
    from moderation.models import Issue

    s = kit.simple_world()
    moderator = kit.mod_msg(s.conv)
    run = kit.make_run(kit.user_msg(s.conv, s.parts, "B"))  # a run is triggered by a user message, here one after the moderator's
    issue = kit.make_issue(run, moderator, kit.FA, 0, 10, validity="rejected")
    Issue.objects.filter(pk=issue.pk).update(validity="valid")  # bypasses the model rule on purpose
    kit.make_cf(s.panel, moderator, kit.FA, 0, 10, 2.0)
    user_issue = kit.make_issue(run, s.message, kit.FA, 0, 10)
    user_finding = kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 2.0)
    report = kit.match(s.panel)
    assert kit.links() == {(user_issue.pk, user_finding.pk, 1.0)}
    assert report.linked == 1
    assert issue.pk not in {row[0] for row in report.issues_unmatched}
    assert moderator.pk not in report.unrated_messages
    assert issue.pk not in {row[0] for row in report.not_found_issues}
    assert report.findings_unmatched == []


def test_a_moderator_message_finding_is_not_linked_to_the_user_messages_issue():
    s = kit.simple_world()
    moderator = kit.mod_msg(s.conv)
    kit.make_issue(s.run, s.message, kit.FA, 0, 10)
    kit.make_cf(s.panel, moderator, kit.FA, 0, 10, 2.0)
    report = kit.match(s.panel)
    assert kit.links() == set()
    assert report.unrated_messages == [s.message.pk]
    assert report.findings_unmatched == []


def test_an_existing_link_from_a_rejected_issue_does_not_count_as_a_valid_issue_for_the_finding():
    s = kit.simple_world()
    issue = kit.make_issue(s.run, s.message, kit.FA, 0, 10, validity="rejected")
    finding = kit.make_cf(s.panel, s.message, kit.FA, 0, 10, 2.0)
    kit.make_link(issue, finding)
    report = kit.match(s.panel)
    assert kit.rows(report.findings_unmatched) == [(finding.pk, s.message.pk, kit.FA, 2.0)]
    assert kit.pks(kit.unmatched_findings(s.panel)) == [finding.pk]


def test_a_moderator_message_finding_never_appears_in_the_unmatched_helper():
    s = kit.simple_world()
    moderator = kit.mod_msg(s.conv)
    kit.make_cf(s.panel, moderator, kit.FA, 0, 10, 2.0)
    assert kit.pks(kit.unmatched_findings(s.panel)) == []


def test_unrated_messages_lists_each_message_once_sorted_by_id_whatever_the_order_of_the_issues():
    s = kit.simple_world()
    m2 = kit.user_msg(s.conv, s.parts, "B")
    m3 = kit.user_msg(s.conv, s.parts, "A")
    run = kit.make_run(m3)
    kit.make_issue(run, m3, kit.FA, 0, 10)
    kit.make_issue(run, m2, kit.AB, 0, 10)
    kit.make_issue(run, m3, kit.AB, 20, 30)
    kit.make_issue(run, s.message, kit.FA, 0, 10)
    kit.make_issue(run, m2, kit.FA)  # a not_found issue on an unrated message still counts the message
    report = kit.match(s.panel)
    assert report.unrated_messages == [s.message.pk, m2.pk, m3.pk]
    assert report.issues_unmatched == []
