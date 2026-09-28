"""Step 18 audit, owner-approved bypass-regression tests (docs/test_audit_plan.md).

These do NOT prove a rule holds. They document today's ACCEPTED, KNOWN gap: several invariants in moderation/models.py
are enforced only in Python (save() / validate_rules()), not by the database, and `bulk_create` (or a direct
many-to-many through-table write) skips save() entirely and gets the invalid row into the database with no exception.

Each test below asserts the bypass SUCCEEDS. If one of these ever starts raising, either a database constraint/trigger
was added to close the gap (update this test and its comment) or something else changed; it is not a regression to fix
quietly.
"""
import modmodels_testkit as kit
import pytest
from django.conf import settings

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    conv, parts = kit.make_conversation()
    first = kit.user_msg(conv, parts, "A", "The moon is made of green cheese, everybody knows that.")
    reply = kit.mod_msg(conv, in_reply_to=first, content="Please cite a source for that claim.")
    trigger = kit.user_msg(conv, parts, "B", "That is simply not true.", in_reply_to=first)
    run = kit.make_run(trigger)
    return type("World", (), dict(conv=conv, parts=parts, first=first, reply=reply, trigger=trigger, run=run))


class TestIssueOnModeratorMessageBypass:
    def test_bulk_create_bypasses_the_moderator_message_rejection_rule_known_gap(self, world):
        """Issue.save() raises ValidationError for a valid issue on a moderator message (validate_rules(): "An issue
        on a moderator message must be rejected"). No CheckConstraint or trigger enforces this, so bulk_create, which
        skips save(), lands the row with validity='valid' anyway."""
        from moderation.models import Issue

        bad = kit.unsaved_issue(world.run, world.reply, local_id="modmsg-bypass", validity="valid")

        Issue.objects.bulk_create([bad])

        stored = Issue.objects.get(run=world.run, local_id="modmsg-bypass")
        assert stored.message_id == world.reply.pk
        assert stored.validity == "valid"


class TestIssueDispositionOnInvalidIssueBypass:
    def test_bulk_create_bypasses_the_valid_issue_only_rule_known_gap(self, world):
        """IssueDisposition.save() raises ValidationError when its issue's validity is not 'valid' ("Only a valid
        issue can have a disposition"). No CheckConstraint or trigger enforces this, so bulk_create, which skips
        save(), attaches a disposition to a rejected issue anyway."""
        from moderation.models import Issue, IssueDisposition

        rejected_issue = kit.make_issue(
            world.run, world.first, local_id="rejected-1", validity="rejected", rejection_reason="not credible"
        )
        bad = IssueDisposition(issue=rejected_issue, disposition="declined", reason="known gap")

        IssueDisposition.objects.bulk_create([bad])

        stored = IssueDisposition.objects.get(issue=rejected_issue)
        assert stored.disposition == "declined"
        assert stored.issue.validity == "rejected"


class TestInterventionActCapBypass:
    def test_bulk_create_bypasses_the_max_acts_per_intervention_cap_known_gap(self, world):
        """InterventionAct.save() refuses a valid act once a run already holds settings.MAX_ACTS_PER_INTERVENTION
        valid acts. No CheckConstraint or trigger enforces the cap, so bulk_create, which skips save(), can push a
        run's valid-act count past it."""
        from moderation.models import InterventionAct

        cap = settings.MAX_ACTS_PER_INTERVENTION
        extra_rows = [kit.unsaved_act(world.run, order=order) for order in range(1, cap + 2)]

        InterventionAct.objects.bulk_create(extra_rows)

        valid_count = InterventionAct.objects.filter(run=world.run, validity="valid").count()
        assert valid_count == cap + 1
        assert valid_count > cap


class TestInterventionActCrossRunSourceIssueBypass:
    def test_direct_through_table_write_bypasses_the_cross_run_source_issue_guard_known_gap(self, world):
        """InterventionAct.source_issues is guarded by an m2m_changed receiver (_validate_source_issues) that only
        fires for `.add()` / `.set()`. A direct bulk_create on the auto-generated through table skips the signal
        entirely, so an act can be linked to an issue from a different run with nothing raised."""
        from moderation.models import InterventionAct

        act = kit.make_act(world.run, order=1)
        other_conv, other_parts = kit.make_conversation()
        other_trigger = kit.user_msg(other_conv, other_parts, "A", "A different conversation entirely.")
        other_run = kit.make_run(other_trigger)
        foreign_issue = kit.make_issue(other_run, other_trigger, local_id="foreign-1")
        through = InterventionAct.source_issues.through

        through.objects.bulk_create([through(interventionact_id=act.pk, issue_id=foreign_issue.pk)])

        assert list(act.source_issues.all()) == [foreign_issue]
        assert foreign_issue.run_id != act.run_id
