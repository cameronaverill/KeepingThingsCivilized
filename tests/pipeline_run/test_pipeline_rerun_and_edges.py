"""A run that is taken again after an earlier attempt died half way ("running" runs are accepted; the reaper that resets them
is step 8), and text edge cases of quote location."""
import pipeline_run_kit as kit
import pytest

pytestmark = pytest.mark.django_db


def partial_attempt(world):
    """What an attempt that crashed after storing the Master's issues (and maybe more) leaves behind on a `running` run."""
    from moderation.models import InterventionAct, Issue, IssueDisposition

    run = kit.new_run(world.last, status="running", attempts=1)
    text = world.last.content
    start = text.index(kit.QUOTE_1)
    kept = Issue.objects.create(
        run=run, local_id="i1", message=world.last, issue_type="unsupported_claim", quote=kit.QUOTE_1,
        quote_start=start, quote_end=start + len(kit.QUOTE_1), quote_match="exact", explanation="From the dead attempt.",
        confidence=0.5,
    )  # fmt: skip
    Issue.objects.create(
        run=run, local_id="old_only", message=world.last, issue_type="fallacy", quote="not there", quote_match="not_found",
        explanation="Only the dead attempt had this.", confidence=0.5, validity="rejected", rejection_reason="quote_not_found",
    )  # fmt: skip
    IssueDisposition.objects.create(issue=kept, disposition="declined", reason="From the dead attempt.")
    InterventionAct.objects.create(run=run, order=1, act_type="request_information", tone="neutral", text="Dead attempt text.", addressee="all", subject="none")
    return run


class TestRetryingAnAttemptThatDiedHalfWay:
    def test_the_second_attempt_completes_and_leaves_only_its_own_rows(self, fake):
        world = kit.build()
        run = partial_attempt(world)
        fake(*kit.simple_success(world))
        returned, stored = kit.go(run)
        assert (stored.status, stored.attempts, stored.decision) == ("done", 2, "intervene")
        assert stored.failure_reason == ""
        assert kit.issue_summary(stored) == [("i1", "valid", "")]
        assert kit.issues_of(stored)[0].explanation == "The claim is stated without support."
        assert [(a.order, a.text) for a in kit.acts_of(stored)] == [(1, kit.CLEAN_TEXT)]
        assert stored.posted_message.content == kit.CLEAN_TEXT

    def test_a_second_attempt_with_no_valid_issue_clears_the_dead_attempts_rows(self, fake):
        world = kit.build()
        run = partial_attempt(world)
        fake(kit.master_d())
        _, stored = kit.go(run)
        assert (stored.status, stored.decision, stored.rationale) == ("done", "no_intervention", "no valid issues")
        assert kit.issues_of(stored) == []
        assert kit.acts_of(stored) == []


class TestQuoteLocationEdges:
    def test_an_html_escaped_quote_is_found_in_the_original_text(self, fake):
        text = "Tom & Jerry say rents are 50% higher than anywhere."
        world = kit.build([("A", "First."), ("B", text)])
        run = kit.new_run(world.last)
        fake(kit.master_d(kit.issue_d("i1", world.last, "unsupported_claim", "Tom &amp; Jerry say")), kit.interv_d("no_intervention", "Nothing.", [kit.disp_d("i1", "declined")]))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.validity, issue.quote_match) == ("valid", "normalized")
        assert text[issue.quote_start:issue.quote_end] == "Tom & Jerry say"

    def test_offsets_count_characters_not_bytes_after_emoji_and_accents(self, fake):
        text = "\U0001f600\U0001f600 Café owners say rents doubled, nobody disagrees on that."
        world = kit.build([("A", "First."), ("B", text)])
        run = kit.new_run(world.last)
        fake(kit.master_d(kit.issue_d("i1", world.last, "unsupported_claim", kit.QUOTE_1)), kit.interv_d("no_intervention", "Nothing.", [kit.disp_d("i1", "declined")]))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert text[issue.quote_start:issue.quote_end] == kit.QUOTE_1
        assert issue.quote_start == text.index(kit.QUOTE_1)

    def test_a_quote_split_across_a_line_break_matches_with_normalized_whitespace(self, fake):
        text = "Landlords always leave the market\nwhen rent is capped, and nobody disagrees on that."
        world = kit.build([("A", "First."), ("B", text)])
        run = kit.new_run(world.last)
        fake(kit.master_d(kit.issue_d("i1", world.last, "unsupported_claim", "leave the market when rent")), kit.interv_d("no_intervention", "Nothing.", [kit.disp_d("i1", "declined")]))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.validity, issue.quote_match) == ("valid", "normalized")
        assert text[issue.quote_start:issue.quote_end] == "leave the market\nwhen rent"


class TestAtMostOnePostPerRun:
    def test_a_running_run_that_already_has_its_post_never_posts_a_second_message(self, fake):
        from moderation.models import ModerationRun

        world = kit.build()
        earlier_post = world.add_moderator("The earlier post of this same run.", in_reply_to=world.last)
        run = kit.new_run(world[3], status="running", attempts=1)
        ModerationRun.objects.filter(pk=run.pk).update(posted_message=earlier_post)
        fake(
            kit.master_d(kit.issue_d("i1", world[3], "unsupported_claim", kit.QUOTE_1)),
            kit.interv_d(dispositions=[kit.disp_d("i1")], acts=[kit.act_d(issues=["i1"])]),
        )
        _, stored = kit.go(kit.reload(run))
        assert (stored.status, stored.decision) == ("done", "intervene")
        assert stored.posted_message_id == earlier_post.pk
        assert [m.pk for m in kit.moderator_messages(world.conv)] == [earlier_post.pk]
