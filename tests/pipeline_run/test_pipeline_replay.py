"""Replays (kind="replay") never post and never change the conversation (plan sections 2 and 11; brief "5b details" step 5)."""
import pipeline_run_kit as kit
import pytest

pytestmark = pytest.mark.django_db


def replay_setup():
    world = kit.build()
    live = kit.new_run(world.last)
    replay = kit.new_run(world.last, kind="replay", replay_of=live, replicate=1)
    return world, live, replay


def conversation_snapshot(world):
    from forum.models import Message

    return list(Message.objects.filter(conversation=world.conv).order_by("seq_no").values_list("pk", "seq_no", "author_type", "content"))


class TestReplaysNeverPost:
    def test_a_replay_that_would_intervene_records_everything_but_posts_nothing(self, fake):
        world, live, replay = replay_setup()
        fake(*kit.simple_success(world))
        before = conversation_snapshot(world)
        returned, stored = kit.go(replay)
        assert (stored.status, stored.decision) == ("done", "intervene")
        assert stored.posted_message is None
        assert conversation_snapshot(world) == before
        assert kit.issue_summary(stored) == [("i1", "valid", "")]
        assert kit.act_summary(stored) == [(1, "valid", "")]
        assert kit.issues_of(stored)[0].disposition.disposition == "acted"

    def test_a_replay_leaves_the_live_run_untouched_and_creates_no_run(self, fake):
        from moderation.models import ModerationRun

        world, live, replay = replay_setup()
        fake(*kit.simple_success(world))
        kit.go(replay)
        untouched = kit.reload(live)
        assert (untouched.status, untouched.attempts, untouched.posted_message_id, untouched.decision) == ("pending", 0, None, "")
        assert ModerationRun.objects.count() == 2
        assert kit.issues_of(untouched) == []

    def test_replicates_of_one_trigger_each_store_their_own_rows_and_none_posts(self, fake):
        world = kit.build()
        live = kit.new_run(world.last)
        replays = [kit.new_run(world.last, kind="replay", replay_of=live, replicate=n) for n in (1, 2, 3)]
        before = conversation_snapshot(world)
        for replay in replays:
            fake(*kit.simple_success(world))
            _, stored = kit.go(replay)
            assert stored.status == "done"
            assert kit.issue_summary(stored) == [("i1", "valid", "")]
            assert stored.posted_message is None
        assert conversation_snapshot(world) == before

    def test_a_replay_of_a_conversation_the_live_run_already_posted_in_sees_only_up_to_its_snapshot(self, fake):
        world = kit.build()
        live = kit.new_run(world.last)
        fake(*kit.simple_success(world))
        kit.go(live)
        assert len(kit.moderator_messages(world.conv)) == 1
        replay = kit.new_run(world.last, kind="replay", replay_of=kit.reload(live))
        client = fake(*kit.simple_success(world))
        before = conversation_snapshot(world)
        _, stored = kit.go(replay)
        assert stored.posted_message is None
        assert conversation_snapshot(world) == before
        assert stored.is_stale is False
        assert [pk for pk, _ in kit.rendered_messages(client.calls[0])] == [m.pk for m in world.msgs[:3]]

    @pytest.mark.parametrize("setup_failure", ["structural", "api_error"])
    def test_a_failed_replay_posts_nothing_either(self, fake, setup_failure):
        from moderation.fake_llm import FakeProviderError

        world, live, replay = replay_setup()
        script = [kit.BAD_MASTER, kit.BAD_MASTER] if setup_failure == "structural" else [FakeProviderError(500, "api_error", "boom")]
        fake(*script)
        before = conversation_snapshot(world)
        _, stored = kit.go(replay)
        assert stored.status == "failed"
        assert conversation_snapshot(world) == before
        assert stored.posted_message is None

    def test_replay_llm_calls_are_ledgered_against_the_replay_run(self, fake):
        world, live, replay = replay_setup()
        fake(*kit.simple_success(world))
        kit.go(replay)
        assert len(kit.ledger(replay)) == 2
        assert kit.ledger(live) == []
