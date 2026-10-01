"""Step 21, part 1: clarity issues through the pipeline are stored like the other two dimensions' issues."""
import dim21_kit as dk
import pipeline_run_kit as kit
import pytest

pytestmark = pytest.mark.django_db

PHRASE = "Message number 2 about rent caps"


def setup():
    world, trigger = dk.world_with_users(2)
    return world, kit.new_run(trigger), trigger


def declining(*ids):
    return kit.interv_d("no_intervention", "Nothing to add.", [kit.disp_d(i, "declined") for i in ids])


def clarity_issue(trigger, intensity, id="i1"):
    return kit.issue_d(id, trigger, "unclear_statement", PHRASE, intensity=intensity, explanation="The meaning is vague.")


class TestStored:
    @pytest.mark.parametrize("intensity", [0, 1, 2, 3, 4])
    def test_a_clarity_issue_carries_dimension_clarity_and_its_intensity(self, fake, intensity):
        world, run, trigger = setup()
        fake(kit.master_d(clarity_issue(trigger, intensity)), declining("i1"))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.issue_type, issue.dimension, issue.intensity) == ("unclear_statement", "clarity", intensity)
        assert (issue.validity, issue.rejection_reason) == ("valid", "")

    def test_a_clarity_issue_without_an_intensity_is_valid_with_a_null_intensity(self, fake):
        world, run, trigger = setup()
        fake(kit.master_d(clarity_issue(trigger, None)), declining("i1"))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.validity, issue.dimension, issue.intensity) == ("valid", "clarity", None)

    @pytest.mark.parametrize("bad", [5, -1, 100])
    def test_an_out_of_range_intensity_fails_the_run_structurally_as_for_the_other_dimensions(self, fake, bad):
        world, run, trigger = setup()
        issue = clarity_issue(trigger, bad)
        client = fake(kit.master_d(issue), kit.master_d(issue))
        _, stored = kit.go(run)
        assert (stored.status, stored.failure_reason) == ("failed", "structural")
        assert len(client.calls) == 2
        assert kit.issues_of(stored) == []

    @pytest.mark.parametrize("bad", [5, -1])
    def test_the_other_two_dimensions_behave_the_same_way(self, fake, bad):
        world, run, trigger = setup()
        issue = kit.issue_d("i1", trigger, "abusive_language", PHRASE, intensity=bad)
        fake(kit.master_d(issue), kit.master_d(issue))
        _, stored = kit.go(run)
        assert (stored.status, stored.failure_reason) == ("failed", "structural")

    def test_an_unclear_statement_issue_can_be_acted_on_with_a_clarification_request(self, fake):
        world, run, trigger = setup()
        act = kit.act_d(
            "Could the claim in message 2 be put in other words?", type="request_clarification",
            issues=["i1"], messages=[trigger], addressee="B", subject="B",
        )
        fake(kit.master_d(clarity_issue(trigger, 2)), kit.interv_d(dispositions=[kit.disp_d("i1")], acts=[act]))
        _, stored = kit.go(run)
        assert (stored.status, stored.decision) == ("done", "intervene")
        (stored_issue,) = kit.issues_of(stored)
        assert (stored_issue.dimension, stored_issue.disposition.disposition) == ("clarity", "acted")
        assert kit.act_summary(stored) == [(1, "valid", "")]

    def test_the_intervenor_is_shown_the_clarity_dimension_and_intensity(self, fake):
        world, run, trigger = setup()
        client = fake(kit.master_d(clarity_issue(trigger, 3)), declining("i1"))
        kit.go(run)
        (call,) = dk.intervenor_calls(client)
        text = kit.user_input(call)
        assert "unclear_statement" in text and "<intensity>3</intensity>" in text

    def test_an_intensity_on_a_type_without_a_dimension_is_still_rejected(self, fake):
        world, run, trigger = setup()
        client = fake(kit.master_d(kit.issue_d("i1", trigger, "fallacy", PHRASE, intensity=2)))
        _, stored = kit.go(run)
        assert kit.issue_summary(stored) == [("i1", "rejected", "intensity_without_dimension")]
        assert len(client.calls) == 1

    def test_a_clarity_issue_on_an_older_message_is_rejected_as_not_new(self, fake):
        world, run, trigger = setup()
        client = fake(kit.master_d(kit.issue_d("i1", world[1], "unclear_statement", "Message number 1 about rent caps", intensity=1)))
        _, stored = kit.go(run)
        assert kit.issue_summary(stored) == [("i1", "rejected", "not_new")]

    def test_a_rejected_clarity_issue_keeps_its_dimension_and_intensity_on_the_row(self, fake):
        world, run, trigger = setup()
        fake(kit.master_d(kit.issue_d("i1", trigger, "unclear_statement", "not in the message at all", intensity=2)))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.validity, issue.rejection_reason) == ("rejected", "quote_not_found")
        assert (issue.dimension, issue.intensity) == ("clarity", 2)


class TestModelAndMigration:
    def test_the_issue_dimension_choices_come_from_the_taxonomy_and_include_clarity(self):
        from moderation import taxonomy
        from moderation.models import Issue

        choices = {value for value, _ in Issue._meta.get_field("dimension").choices}
        assert {"clarity", "factual_accuracy", "abusiveness"} <= choices
        assert set(taxonomy.DIMENSIONS) <= choices

    def test_makemigrations_is_clean(self):
        import io

        from django.core.management import call_command

        call_command("makemigrations", "--check", "--dry-run", stdout=io.StringIO(), stderr=io.StringIO())

    def test_an_issue_row_with_dimension_clarity_validates(self):
        from moderation.models import Issue

        world, trigger = dk.world_with_users(2)
        run = kit.new_run(trigger)
        issue = Issue(
            run=run, local_id="i1", message=trigger, issue_type="unclear_statement", quote=PHRASE, quote_match="exact", quote_start=0, quote_end=len(PHRASE),
            explanation="vague", confidence=0.5, intensity=2,
        )
        issue.full_clean()
        issue.save()
        issue.refresh_from_db()
        assert (issue.dimension, issue.intensity) == ("clarity", 2)
