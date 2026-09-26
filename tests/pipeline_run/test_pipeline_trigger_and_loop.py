"""The no-self-reply rule (plan section 2, layers 2 and 3): the database refuses a run with a moderator trigger, the
pipeline re-checks the trigger before any call, and a moderator post never leads to another run."""
import pipeline_run_kit as kit
import pytest
from django.db import IntegrityError, transaction

pytestmark = pytest.mark.django_db

SPECS = [("A", "Rents rose sharply."), ("B", "Not by that much."), ("mod", "Please cite the figures used."), ("A", "Fine, the city report says so.")]


def setup():
    world = kit.build(SPECS)
    return world, kit.new_run(world[4])


class TestTheDatabaseRefusesAModeratorTrigger:
    def test_an_insert_that_skips_save_is_refused_by_the_database(self):
        from moderation.models import ModerationRun

        world = kit.build(SPECS)
        unsaved = ModerationRun(conversation=world.conv, trigger_message=world[3], snapshot_seq=3, kind="live")
        with pytest.raises(IntegrityError), transaction.atomic():
            ModerationRun.objects.bulk_create([unsaved])
        assert ModerationRun.objects.count() == 0

    def test_an_update_that_moves_a_run_onto_a_moderator_message_is_refused_by_the_database(self):
        from moderation.models import ModerationRun

        world, run = setup()
        with pytest.raises(IntegrityError), transaction.atomic():
            ModerationRun.objects.filter(pk=run.pk).update(trigger_message=world[3])
        assert kit.reload(run).trigger_message_id == world[4].pk


class TestThePipelineRechecksTheTrigger:
    """The database rule cannot be bypassed here, so the run object itself is tampered with in memory (the case the pipeline's
    own check exists for: the database rules were bypassed)."""

    @staticmethod
    def swap_the_trigger(run, world):
        run.trigger_message = world[3]

    @staticmethod
    def flip_the_authors_type(run, world):
        run.trigger_message.author_type = "moderator"

    @pytest.mark.parametrize("tamper", ["swap_the_trigger", "flip_the_authors_type"])
    @pytest.mark.parametrize("kind", ["live", "replay"])
    def test_a_moderator_trigger_fails_the_run_with_no_call_no_ledger_row_and_no_post(self, fake, tamper, kind):
        from moderation.models import LLMCall

        world = kit.build(SPECS)
        run = kit.new_run(world[4], kind=kind)
        getattr(self, tamper)(run, world)
        client = fake()
        before = kit.message_count(world.conv)
        returned, stored = kit.go(run)
        assert returned.status == "failed"
        assert (stored.status, stored.failure_reason) == ("failed", "invalid_trigger")
        assert client.calls == []
        assert LLMCall.objects.count() == 0
        assert stored.posted_message is None
        assert kit.message_count(world.conv) == before
        assert kit.issues_of(stored) == []
        assert kit.acts_of(stored) == []

    @pytest.mark.parametrize("status", ["done", "failed", "skipped_budget", "skipped_disabled"])
    def test_a_finished_run_is_left_alone_even_if_its_trigger_looks_wrong(self, fake, status):
        world = kit.build(SPECS)
        run = kit.new_run(world[4], status=status)
        self.swap_the_trigger(run, world)
        fake()
        returned, stored = kit.go(run)
        assert stored.status == status
        assert stored.failure_reason == ""

    def test_a_valid_user_trigger_is_not_failed_by_the_check(self, fake):
        world, run = setup()
        fake(kit.master_d())
        _, stored = kit.go(run)
        assert stored.status == "done"
        assert stored.failure_reason == ""


class TestNoReplyLoop:
    def scripts_for(self, kind, message):
        issue = kit.issue_d("i1", message, "unsupported_claim", "nobody disagrees on")
        clean = kit.act_d(f"Could a source be given for the claim in message {message.seq_no}?", issues=["i1"])
        if kind == "intervene":
            return [kit.master_d(issue), kit.interv_d(dispositions=[kit.disp_d("i1")], acts=[clean])]
        if kind == "none":
            return [kit.master_d()]
        if kind == "structural":
            return [kit.BAD_MASTER, kit.BAD_MASTER]
        if kind == "decline":
            return [kit.master_d(issue), kit.interv_d("no_intervention", "Not needed.", [kit.disp_d("i1", "declined")])]
        assert kind == "all_rejected"
        bad = kit.act_d("Participant A should cite a source.", issues=["i1"])
        return [kit.master_d(issue), kit.interv_d("intervene", "A note is due.", [kit.disp_d("i1")], [bad])]

    def test_after_n_user_messages_there_are_at_most_n_moderator_posts_and_no_run_has_a_moderator_trigger(self, fake):
        from forum.models import Message
        from moderation.models import ModerationRun

        kinds = ["intervene", "none", "structural", "intervene", "decline", "all_rejected", "intervene"]
        world = kit.build([])
        runs = []
        seen_moderator_label = []
        authors = ["A", "B", "A", "A", "B", "A", "B"]
        for index, (kind, author) in enumerate(zip(kinds, authors), start=1):
            message = world.add_user(author, f"Claim {index} is one that nobody disagrees on, honestly.")
            run = kit.new_run(message)
            runs.append(run)
            client = fake(*self.scripts_for(kind, message))
            runs_before = ModerationRun.objects.count()
            posted_before = len(kit.moderator_messages(world.conv))
            _, stored = kit.go(run)
            assert ModerationRun.objects.count() == runs_before
            assert len(kit.moderator_messages(world.conv)) - posted_before == (1 if kind == "intervene" else 0)
            assert stored.status == ("failed" if kind == "structural" else "done")
            seen_moderator_label.append(any(label == "Moderator" for _, label in kit.rendered_messages(client.calls[0])))
        n_users = len(kinds)
        posts = kit.moderator_messages(world.conv)
        assert len(posts) == 3
        assert len(posts) <= n_users
        assert ModerationRun.objects.count() == n_users
        assert ModerationRun.objects.filter(trigger_message__author_type="moderator").count() == 0
        user_ids = set(Message.objects.filter(conversation=world.conv, author_type="user").values_list("pk", flat=True))
        assert {r.trigger_message_id for r in ModerationRun.objects.all()} == user_ids
        assert {r.posted_message_id for r in ModerationRun.objects.exclude(posted_message=None)} == {p.pk for p in posts}
        for post in posts:
            assert post.in_reply_to_id in user_ids
        assert seen_moderator_label == [False, True, True, True, True, True, True]  # a post shows up in later transcripts, as "Moderator"

    def test_a_master_that_keeps_reporting_on_the_moderators_own_posts_never_gets_them_through(self, fake):
        from moderation.models import Issue

        world = kit.build([])
        for index in range(1, 5):
            message = world.add_user("AB"[index % 2], f"Claim {index} is one that nobody disagrees on, honestly.")
            run = kit.new_run(message)
            issues = [kit.issue_d("i1", message, "unsupported_claim", "nobody disagrees on")]
            for post in kit.moderator_messages(world.conv)[-1:]:
                issues.append(kit.issue_d("i2", post, "repetition", "Could a source"))
            fake(
                kit.master_d(*issues),
                kit.interv_d(dispositions=[kit.disp_d("i1")], acts=[kit.act_d(issues=["i1"])]),
            )
            _, stored = kit.go(run)
            assert stored.status == "done"
        assert Issue.objects.filter(message__author_type="moderator").exclude(validity="rejected").count() == 0
        assert Issue.objects.filter(message__author_type="moderator", rejection_reason="moderator_message").count() == 3
        assert len(kit.moderator_messages(world.conv)) == 4

    def test_running_the_same_run_again_after_it_posted_never_posts_a_second_time(self, fake):
        world = kit.build()
        run = kit.new_run(world.last)
        fake(*kit.simple_success(world))
        kit.go(run)
        kit.go(run)
        assert len(kit.moderator_messages(world.conv)) == 1
