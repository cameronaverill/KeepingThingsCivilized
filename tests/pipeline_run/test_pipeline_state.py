"""Run bookkeeping of run_moderation: which runs it takes, attempts and times, the stale flag, the transcript window, and
"no transaction is open during an LLM call" (brief "5b details" steps 1, 2, 6 and 8)."""
import pipeline_run_kit as kit
import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


def setup():
    world = kit.build()
    return world, kit.new_run(world.last)


class TestWhichRunsAreTaken:
    def test_a_pending_run_is_taken_counts_one_attempt_and_records_its_times(self, fake):
        world, run = setup()
        fake(kit.master_d())
        assert (run.status, run.attempts, run.started_at, run.finished_at) == ("pending", 0, None, None)
        _, stored = kit.go(run)
        assert (stored.status, stored.attempts) == ("done", 1)
        assert stored.started_at is not None and stored.finished_at is not None
        assert stored.started_at <= stored.finished_at

    def test_a_running_run_is_taken_and_counts_another_attempt(self, fake):
        world = kit.build()
        run = kit.new_run(world.last, status="running", attempts=1)
        fake(kit.master_d())
        _, stored = kit.go(run)
        assert (stored.status, stored.attempts) == ("done", 2)

    @pytest.mark.parametrize("status", ["done", "failed", "skipped_budget", "skipped_disabled"])
    def test_a_run_that_is_not_pending_or_running_is_returned_unchanged(self, fake, status):
        from moderation.models import ModerationRun

        world = kit.build()
        run = kit.new_run(world.last, status=status, attempts=2)
        client = fake()
        before_row = ModerationRun.objects.filter(pk=run.pk).values().get()
        before_messages = kit.message_count(world.conv)
        returned, stored = kit.go(run)
        assert returned.status == status
        assert ModerationRun.objects.filter(pk=run.pk).values().get() == before_row
        assert client.calls == []
        assert kit.ledger() == []
        assert kit.message_count(world.conv) == before_messages
        assert kit.issues_of(stored) == []

    def test_running_a_finished_run_a_second_time_does_nothing(self, fake):
        from moderation.models import ModerationRun

        world, run = setup()
        client = fake(*kit.simple_success(world))
        kit.go(run)
        row = ModerationRun.objects.filter(pk=run.pk).values().get()
        calls, ledger_rows, messages = len(client.calls), len(kit.ledger()), kit.message_count(world.conv)
        returned, stored = kit.go(run)
        assert returned.status == "done"
        assert ModerationRun.objects.filter(pk=run.pk).values().get() == row
        assert (len(client.calls), len(kit.ledger()), kit.message_count(world.conv)) == (calls, ledger_rows, messages)
        assert len(kit.issues_of(stored)) == 1
        assert len(kit.acts_of(stored)) == 1

    def test_an_old_python_object_of_a_finished_run_is_not_run_again(self, fake):
        world, run = setup()
        old_copy = kit.reload(run)  # still says pending, and will after the run below finishes
        client = fake(kit.master_d())
        kit.go(run)
        assert old_copy.status == "pending"
        returned, stored = kit.go(old_copy)
        assert stored.status == "done"
        assert stored.attempts == 1
        assert len(client.calls) == 1


class TestStaleFlag:
    def test_a_newer_user_message_at_the_start_makes_the_run_stale_and_it_still_posts_and_excludes_it(self, fake):
        world = kit.build()
        run = kit.new_run(world[2])  # the third message arrived after the snapshot
        trigger = world[2]
        master = kit.master_d(kit.issue_d("i1", trigger, "unsupported_claim", "tenants gain a lot"))
        client = fake(master, kit.interv_d(dispositions=[kit.disp_d("i1")], acts=[kit.act_d(issues=["i1"])]))
        _, stored = kit.go(run)
        assert stored.status == "done"
        assert stored.is_stale is True
        assert stored.posted_message is not None
        assert stored.posted_message.in_reply_to_id == trigger.pk
        assert [pk for pk, _ in kit.rendered_messages(client.calls[0])] == [world[1].pk, world[2].pk]
        assert [pk for pk, _ in kit.rendered_messages(client.calls[1])] == [world[1].pk, world[2].pk]

    def test_a_user_message_arriving_while_the_run_is_working_makes_it_stale(self, fake):
        world, run = setup()
        script = kit.simple_success(world)
        client = fake(
            kit.reply_with(script[0], before=lambda kwargs: world.add_user("B", "A late message during the run.")),
            script[1],
        )
        _, stored = kit.go(run)
        assert stored.status == "done"
        assert stored.is_stale is True
        assert stored.posted_message is not None
        assert [pk for pk, _ in kit.rendered_messages(client.calls[0])] == [m.pk for m in world.msgs[:3]]

    def test_a_moderator_message_after_the_snapshot_does_not_make_the_run_stale(self, fake):
        world = kit.build([("A", "Rents rose sharply, nobody disagrees on that."), ("B", "Not so."), ("mod", "Please cite figures.")])
        run = kit.new_run(world[2])
        fake(kit.master_d())
        _, stored = kit.go(run)
        assert stored.is_stale is False

    def test_a_run_with_nothing_newer_is_not_stale(self, fake):
        world, run = setup()
        fake(kit.master_d())
        _, stored = kit.go(run)
        assert stored.is_stale is False


class TestTranscriptWindow:
    def many(self, count):
        return kit.build([("AB"[i % 2], f"Message number {i + 1} of the long discussion.") for i in range(count)])

    def test_only_the_last_tunable_number_of_messages_up_to_the_snapshot_is_sent_to_both_agents(self, fake, tune):
        tune(TRANSCRIPT_MAX_MESSAGES=4)
        world = self.many(7)
        run = kit.new_run(world[6])  # messages 3 to 6 are the window; message 7 is newer
        trigger = world[6]
        master = kit.master_d(kit.issue_d("i1", trigger, "unsupported_claim", "of the long discussion"))
        client = fake(master, kit.interv_d("no_intervention", "Nothing.", [kit.disp_d("i1", "declined")]))
        kit.go(run)
        expected = [m.pk for m in world.msgs[2:6]]
        assert [pk for pk, _ in kit.rendered_messages(client.calls[0])] == expected
        assert [pk for pk, _ in kit.rendered_messages(client.calls[1])] == expected

    def test_the_default_window_is_the_last_twenty_messages(self, fake):
        world = self.many(25)
        run = kit.new_run(world.last)
        client = fake(kit.master_d())
        kit.go(run)
        assert [pk for pk, _ in kit.rendered_messages(client.calls[0])] == [m.pk for m in world.msgs[5:]]

    def test_messages_are_labelled_with_participant_labels_and_moderator_posts_as_moderator(self, fake):
        world = kit.build(
            [("A", "First."), ("B", "Second."), ("mod", "A moderator note."), ("A", "Fourth, nobody disagrees on that.")]
        )
        run = kit.new_run(world[4])
        client = fake(kit.master_d())
        kit.go(run)
        assert kit.rendered_messages(client.calls[0]) == [
            (world[1].pk, "Participant A"), (world[2].pk, "Participant B"), (world[3].pk, "Moderator"), (world[4].pk, "Participant A"),
        ]  # fmt: skip

    def test_the_intervenor_is_shown_the_same_transcript_as_the_master(self, fake):
        world, run = setup()
        client = fake(*kit.simple_success(world))
        kit.go(run)
        assert kit.rendered_messages(client.calls[0]) == kit.rendered_messages(client.calls[1])


class TestNoTransactionDuringCalls:
    @pytest.mark.django_db(transaction=True)
    def test_no_transaction_is_open_during_any_llm_call_and_progress_is_stored_before_each(self, fake, settings):
        from moderation.models import Issue, ModerationRun

        settings.LLM_FORBID_ATOMIC_CALLS = True  # the production value: the gateway itself refuses a call inside a transaction
        world, run = setup()
        script = kit.simple_success(world)
        seen = []

        def probe(kwargs):
            row = ModerationRun.objects.get(pk=run.pk)
            seen.append(
                (kwargs["output_format"].__name__, connection.in_atomic_block, row.status, row.attempts,
                 row.started_at is not None, Issue.objects.filter(run=run).count())
            )  # fmt: skip

        client = fake(kit.reply_with(kit.BAD_MASTER, probe), kit.reply_with(script[0], probe), kit.reply_with(script[1], probe))
        _, stored = kit.go(run)
        assert stored.status == "done"
        assert len(client.calls) == 3
        assert seen == [
            ("MasterOutput", False, "running", 1, True, 0),
            ("MasterOutput", False, "running", 1, True, 0),
            ("IntervenorOutput", False, "running", 1, True, 1),
        ]  # fmt: skip
        assert connection.in_atomic_block is False
