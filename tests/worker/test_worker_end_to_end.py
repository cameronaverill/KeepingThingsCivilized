"""End to end with FakeLLM only: a message posted through forum.services.post_message in worker mode is moderated by the worker
(the moderator message appears and the run is done); with the kill switch off the run ends skipped_disabled and nothing is
posted; several runs and a crash-retry go through the real pipeline."""
import pytest
import worker_kit as kit

pytestmark = pytest.mark.django_db


def post(user, conv, text):
    from forum import services

    return services.post_message(user, conv, text)


def run_worker(**kwargs):
    from moderation import worker

    return worker.run_worker(**kwargs)


def runs_of(conv):
    from moderation.models import ModerationRun

    return list(ModerationRun.objects.filter(conversation=conv).order_by("pk"))


class TestPostThenWork:
    def test_post_message_in_worker_mode_only_enqueues_a_pending_run(self, llm_on, fake):
        client = fake()
        pair = kit.active_pair()
        message = post(pair.ua, pair.conv, kit.TEXT_A)
        (run,) = runs_of(pair.conv)
        assert (run.status, run.kind, run.trigger_message_id) == ("pending", "live", message.pk)
        assert kit.moderator_messages(pair.conv) == []
        assert client.calls == []

    def test_the_worker_moderates_the_message_and_the_run_is_done(self, llm_on, fake):
        pair = kit.active_pair()
        message = post(pair.ua, pair.conv, kit.TEXT_A)
        client = fake(*kit.scripted_pair(message, kit.QUOTE_A, kit.ACT_1))
        assert run_worker(once=True, sleep=kit.Sleeper()) == 1
        (run,) = runs_of(pair.conv)
        assert run.status == "done"
        assert run.decision == "intervene"
        assert run.attempts == 1
        assert run.claimed_at is not None and run.started_at is not None and run.finished_at is not None
        (posted,) = kit.moderator_messages(pair.conv)
        assert posted.content == kit.ACT_1
        assert posted.in_reply_to_id == message.pk
        assert run.posted_message_id == posted.pk
        assert len(client.calls) == 2

    def test_a_second_pass_finds_nothing_to_do(self, llm_on, fake):
        pair = kit.active_pair()
        message = post(pair.ua, pair.conv, kit.TEXT_A)
        client = fake(*kit.scripted_pair(message, kit.QUOTE_A, kit.ACT_1))
        run_worker(once=True, sleep=kit.Sleeper())
        assert run_worker(once=True, sleep=kit.Sleeper()) == 0
        assert len(kit.moderator_messages(pair.conv)) == 1
        assert len(client.calls) == 2

    def test_two_messages_are_moderated_in_order_one_run_at_a_time(self, llm_on, fake, settings):
        settings.MIN_SECONDS_BETWEEN_MESSAGES = 0
        pair = kit.active_pair()
        first = post(pair.ua, pair.conv, kit.TEXT_A)
        second = post(pair.ub, pair.conv, kit.TEXT_B)
        client = fake(
            *kit.scripted_pair(first, kit.QUOTE_A, kit.ACT_1), *kit.scripted_pair(second, kit.QUOTE_B, kit.ACT_2)
        )
        assert run_worker(once=True, sleep=kit.Sleeper()) == 2
        posted = kit.moderator_messages(pair.conv)
        assert [(m.in_reply_to_id, m.content) for m in posted] == [(first.pk, kit.ACT_1), (second.pk, kit.ACT_2)]
        assert [r.status for r in runs_of(pair.conv)] == ["done", "done"]
        assert len(client.calls) == 4

    def test_a_moderator_message_never_becomes_a_run_and_the_worker_stops_by_itself(self, llm_on, fake):
        pair = kit.active_pair()
        message = post(pair.ua, pair.conv, kit.TEXT_A)
        fake(*kit.scripted_pair(message, kit.QUOTE_A, kit.ACT_1))
        run_worker(once=True, sleep=kit.Sleeper())
        assert len(runs_of(pair.conv)) == 1

    def test_runs_of_two_conversations_are_processed_oldest_first_across_conversations(self, llm_on, fake):
        one, two = kit.active_pair(), kit.active_pair()
        first = post(one.ua, one.conv, kit.TEXT_A)
        second = post(two.ua, two.conv, kit.TEXT_B)
        fake(*kit.scripted_pair(first, kit.QUOTE_A, kit.ACT_1), *kit.scripted_pair(second, kit.QUOTE_B, kit.ACT_2))
        assert run_worker(once=True, sleep=kit.Sleeper()) == 2
        assert [m.content for m in kit.moderator_messages(one.conv)] == [kit.ACT_1]
        assert [m.content for m in kit.moderator_messages(two.conv)] == [kit.ACT_2]


class TestKillSwitchOff:
    def test_the_run_ends_skipped_disabled_and_nothing_is_posted(self, fake, settings):
        client = fake()
        pair = kit.active_pair()
        post(pair.ua, pair.conv, kit.TEXT_A)
        assert run_worker(once=True, sleep=kit.Sleeper()) == 1
        (run,) = runs_of(pair.conv)
        assert run.status == "skipped_disabled"
        assert kit.moderator_messages(pair.conv) == []
        assert client.calls == []
        assert settings.LLM_ENABLED is False

    def test_the_command_does_the_same(self, fake):
        from io import StringIO

        from django.core.management import call_command

        fake()
        pair = kit.active_pair()
        post(pair.ua, pair.conv, kit.TEXT_A)
        out = StringIO()
        call_command("run_moderator", "--once", stdout=out)
        assert [r.status for r in runs_of(pair.conv)] == ["skipped_disabled"]
        assert "skipped_disabled" in out.getvalue()


class TestRecovery:
    def test_a_stuck_run_is_retried_by_the_worker_and_completes_with_one_moderator_message(self, llm_on, fake):
        from moderation.models import ModerationRun

        pair = kit.active_pair()
        message = post(pair.ua, pair.conv, kit.TEXT_A)
        (run,) = runs_of(pair.conv)
        ModerationRun.objects.filter(pk=run.pk).update(
            status="running", attempts=1, claimed_at=kit.long_ago()
        )
        client = fake(*kit.scripted_pair(message, kit.QUOTE_A, kit.ACT_1))
        assert run_worker(once=True, sleep=kit.Sleeper()) == 1
        stored = kit.reload(run)
        assert (stored.status, stored.attempts) == ("done", 2)
        assert len(kit.moderator_messages(pair.conv)) == 1
        assert len(client.calls) == 2

    def test_a_stuck_run_that_used_all_its_attempts_is_failed_with_timeout_and_no_call_is_made(self, llm_on, fake, settings):
        from moderation.models import ModerationRun

        pair = kit.active_pair()
        post(pair.ua, pair.conv, kit.TEXT_A)
        (run,) = runs_of(pair.conv)
        ModerationRun.objects.filter(pk=run.pk).update(
            status="running", attempts=settings.RUN_MAX_ATTEMPTS, claimed_at=kit.long_ago()
        )
        client = fake()
        assert run_worker(once=True, sleep=kit.Sleeper()) == 0
        stored = kit.reload(run)
        assert (stored.status, stored.failure_reason) == ("failed", "timeout")
        assert kit.moderator_messages(pair.conv) == []
        assert client.calls == []

    def test_a_stuck_run_that_used_its_attempts_does_not_block_a_later_message(self, llm_on, fake, settings):
        from moderation.models import ModerationRun

        settings.MIN_SECONDS_BETWEEN_MESSAGES = 0
        pair = kit.active_pair()
        post(pair.ua, pair.conv, kit.TEXT_A)
        second = post(pair.ub, pair.conv, kit.TEXT_B)
        first_run = runs_of(pair.conv)[0]
        ModerationRun.objects.filter(pk=first_run.pk).update(
            status="running", attempts=settings.RUN_MAX_ATTEMPTS, claimed_at=kit.long_ago()
        )
        fake(*kit.scripted_pair(second, kit.QUOTE_B, kit.ACT_2))
        assert run_worker(once=True, sleep=kit.Sleeper()) == 1
        assert [r.status for r in runs_of(pair.conv)] == ["failed", "done"]
        assert [m.content for m in kit.moderator_messages(pair.conv)] == [kit.ACT_2]


class TestNoTransactionWhileTheModelIsCalled:
    @pytest.mark.django_db(transaction=True)
    def test_the_gateway_guard_stays_quiet_because_the_worker_holds_no_transaction(self, llm_on, settings, fake):
        settings.LLM_FORBID_ATOMIC_CALLS = True
        pair = kit.active_pair()
        message = post(pair.ua, pair.conv, kit.TEXT_A)
        fake(*kit.scripted_pair(message, kit.QUOTE_A, kit.ACT_1))
        assert run_worker(once=True, sleep=kit.Sleeper()) == 1
        (run,) = runs_of(pair.conv)
        assert run.status == "done"
