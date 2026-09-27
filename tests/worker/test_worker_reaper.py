"""reap_stuck_runs: live runs `running` since more than RUN_TIMEOUT_SECONDS go back to pending (attempts < RUN_MAX_ATTEMPTS)
or to failed/timeout (attempts at or over it); everything else is left alone; idempotent; returns the counts."""
import pytest
import worker_kit as kit

pytestmark = pytest.mark.django_db

TIMEOUT = 60
MAX_ATTEMPTS = 3
NOW = kit.at(10_000)


@pytest.fixture(autouse=True)
def _small_numbers(tune):
    tune(RUN_TIMEOUT_SECONDS=TIMEOUT, RUN_MAX_ATTEMPTS=MAX_ATTEMPTS)


def reap(**kwargs):
    from moderation import worker

    return worker.reap_stuck_runs(**kwargs)


def stuck(attempts=1, age=TIMEOUT + 1, **fields):
    """A solo running run claimed `age` seconds before NOW."""
    return kit.solo(created_at=kit.at(0), status="running", claimed_at=kit.at(10_000 - age), attempts=attempts, **fields)


class TestTheBoundary:
    def test_a_run_claimed_exactly_the_timeout_ago_is_not_yet_stuck(self):
        run = stuck(age=TIMEOUT)
        before = kit.row(run)
        assert reap(now=NOW) == {"requeued": 0, "failed": 0}
        assert kit.row(run) == before

    def test_a_run_claimed_one_microsecond_over_the_timeout_ago_is_stuck(self):
        from datetime import timedelta

        run = kit.solo(created_at=kit.at(0), status="running", attempts=1,
                       claimed_at=NOW - timedelta(seconds=TIMEOUT, microseconds=1))
        assert reap(now=NOW) == {"requeued": 1, "failed": 0}
        assert kit.status_of(run) == "pending"

    def test_a_run_claimed_one_second_under_the_timeout_ago_is_left_alone(self):
        run = stuck(age=TIMEOUT - 1)
        before = kit.row(run)
        assert reap(now=NOW) == {"requeued": 0, "failed": 0}
        assert kit.row(run) == before

    def test_a_run_claimed_just_now_is_left_alone(self):
        run = stuck(age=0)
        before = kit.row(run)
        assert reap(now=NOW) == {"requeued": 0, "failed": 0}
        assert kit.row(run) == before

    def test_the_boundary_follows_the_tunable(self, tune):
        run = stuck(age=100)
        tune(RUN_TIMEOUT_SECONDS=100)
        assert reap(now=NOW) == {"requeued": 0, "failed": 0}
        tune(RUN_TIMEOUT_SECONDS=99)
        assert reap(now=NOW) == {"requeued": 1, "failed": 0}
        assert kit.status_of(run) == "pending"

    def test_the_shipped_default_timeout_is_used_when_nothing_is_tuned(self, settings, monkeypatch):
        from config import tunables

        monkeypatch.setattr(tunables, "RUN_TIMEOUT_SECONDS", 300)
        settings.RUN_TIMEOUT_SECONDS = 300
        ok = kit.solo(created_at=kit.at(0), status="running", claimed_at=kit.at(10_000 - 300), attempts=1)
        late = kit.solo(created_at=kit.at(0), status="running", claimed_at=kit.at(10_000 - 301), attempts=1)
        assert reap(now=NOW) == {"requeued": 1, "failed": 0}
        assert (kit.status_of(ok), kit.status_of(late)) == ("running", "pending")


class TestRequeueOrFail:
    @pytest.mark.parametrize("attempts", [0, 1, 2])
    def test_below_the_maximum_attempts_the_run_goes_back_to_pending(self, attempts):
        run = stuck(attempts=attempts)
        assert reap(now=NOW) == {"requeued": 1, "failed": 0}
        after = kit.reload(run)
        assert after.status == "pending"
        assert after.attempts == attempts
        assert after.finished_at is None

    @pytest.mark.parametrize("attempts", [3, 4, 10])
    def test_at_or_over_the_maximum_attempts_the_run_fails_with_reason_timeout(self, attempts):
        run = stuck(attempts=attempts)
        assert reap(now=NOW) == {"requeued": 0, "failed": 1}
        after = kit.reload(run)
        assert after.status == "failed"
        assert after.failure_reason == "timeout"
        assert after.finished_at == NOW
        assert after.attempts == attempts

    def test_the_maximum_follows_the_tunable(self, tune):
        run = stuck(attempts=1)
        tune(RUN_MAX_ATTEMPTS=1)
        assert reap(now=NOW) == {"requeued": 0, "failed": 1}
        assert kit.status_of(run) == "failed"

    def test_a_requeued_run_can_be_claimed_again_and_keeps_its_attempts(self):
        from moderation import worker

        run = stuck(attempts=2)
        reap(now=NOW)
        got = worker.claim_next_run(now=NOW)
        assert got.pk == run.pk
        assert kit.reload(run).attempts == 2

    def test_a_failed_run_is_not_claimed_again(self):
        from moderation import worker

        stuck(attempts=MAX_ATTEMPTS)
        reap(now=NOW)
        assert worker.claim_next_run(now=NOW) is None

    def test_a_failed_by_timeout_run_keeps_its_claimed_at_and_started_at(self):
        run = stuck(attempts=MAX_ATTEMPTS, started_at=kit.at(9_000))
        claimed = kit.row(run)["claimed_at"]
        reap(now=NOW)
        after = kit.reload(run)
        assert (after.claimed_at, after.started_at) == (claimed, kit.at(9_000))


class TestBookkeepingOfTheTwoOutcomes:
    """Architect rulings (2026-09-26): a requeue clears claimed_at; a failure leaves a short error text with no user text; a
    running run without claimed_at is ignored."""

    def test_a_requeued_run_has_no_claimed_at_any_more(self):
        run = stuck(attempts=1)
        reap(now=NOW)
        assert kit.reload(run).claimed_at is None

    def test_a_failed_by_timeout_run_carries_a_short_error_text(self):
        pair = kit.active_pair()
        from forum.models import Message

        message = Message.objects.create(
            conversation=pair.conv, author_type="user", participant=pair.pa, content="a distinctive sentence about walruses"
        )
        run = kit.run_on(message, created_at=kit.at(0), status="running", attempts=MAX_ATTEMPTS,
                         claimed_at=kit.at(10_000 - TIMEOUT - 1))
        reap(now=NOW)
        error = kit.reload(run).error
        assert error != ""
        assert len(error) <= 300
        for forbidden in ["walruses", *pair.names, *pair.emails]:
            assert forbidden not in error

    def test_a_running_run_without_claimed_at_is_left_alone(self):
        run = kit.solo(created_at=kit.at(0), status="running", claimed_at=None, attempts=MAX_ATTEMPTS)
        before = kit.row(run)
        assert reap(now=NOW) == {"requeued": 0, "failed": 0}
        assert kit.row(run) == before


class TestCounts:
    def test_a_mixed_batch_returns_exact_counts_and_touches_only_the_stuck_runs(self):
        requeue_a, requeue_b = stuck(attempts=0), stuck(attempts=2, age=5_000)
        fail_a = stuck(attempts=3)
        fresh = stuck(age=TIMEOUT)
        assert reap(now=NOW) == {"requeued": 2, "failed": 1}
        assert [kit.status_of(r) for r in (requeue_a, requeue_b, fail_a, fresh)] == ["pending", "pending", "failed", "running"]

    def test_nothing_to_do_returns_zeros(self):
        assert reap(now=NOW) == {"requeued": 0, "failed": 0}

    def test_the_result_is_a_plain_dict_with_exactly_two_keys(self):
        stuck()
        result = reap(now=NOW)
        assert type(result) is dict
        assert set(result) == {"requeued", "failed"}

    def test_without_now_the_time_comes_from_django_timezone_now(self, clock):
        run = kit.solo(created_at=kit.at(0), status="running", attempts=MAX_ATTEMPTS, claimed_at=kit.at(0))
        clock.advance(TIMEOUT)
        assert reap() == {"requeued": 0, "failed": 0}
        clock.advance(1)
        assert reap() == {"requeued": 0, "failed": 1}
        assert kit.reload(run).finished_at == clock()


class TestWhatIsLeftAlone:
    @pytest.mark.parametrize("status", ["pending", "done", "failed", "skipped_budget", "skipped_disabled"])
    def test_a_run_that_is_not_running_is_never_touched_however_old_its_claimed_at(self, status):
        run = kit.solo(created_at=kit.at(0), status=status, claimed_at=kit.at(-10**7), attempts=MAX_ATTEMPTS + 2)
        before = kit.row(run)
        assert reap(now=NOW) == {"requeued": 0, "failed": 0}
        assert kit.row(run) == before

    def test_a_stuck_replay_run_is_not_touched(self):
        run = stuck(kind="replay")
        before = kit.row(run)
        assert reap(now=NOW) == {"requeued": 0, "failed": 0}
        assert kit.row(run) == before

    def test_a_stuck_replay_run_with_too_many_attempts_is_not_failed(self):
        run = stuck(kind="replay", attempts=MAX_ATTEMPTS + 1)
        before = kit.row(run)
        assert reap(now=NOW) == {"requeued": 0, "failed": 0}
        assert kit.row(run) == before

    def test_a_done_run_keeps_its_posted_message_and_decision(self):
        from forum.models import Message

        w = kit.world(1)
        note = Message.objects.create(
            conversation=w.conv, author_type="moderator", content="A calm note.", in_reply_to=w[1]
        )
        run = kit.run_on(w[1], status="done", decision="intervene", posted_message=note, claimed_at=kit.at(-10**7),
                         finished_at=kit.at(-10**7 + 5))
        before = kit.row(run)
        reap(now=NOW)
        assert kit.row(run) == before

    def test_a_pending_live_run_is_not_counted_as_requeued(self):
        kit.solo(created_at=kit.at(0))
        assert reap(now=NOW) == {"requeued": 0, "failed": 0}


class TestIdempotent:
    def test_a_second_call_changes_nothing_and_returns_zeros(self):
        runs = [stuck(attempts=0), stuck(attempts=MAX_ATTEMPTS)]
        assert reap(now=NOW) == {"requeued": 1, "failed": 1}
        after_first = kit.rows(runs)
        assert reap(now=NOW) == {"requeued": 0, "failed": 0}
        assert kit.rows(runs) == after_first

    def test_a_requeued_run_is_not_requeued_again_by_the_next_reap(self):
        run = stuck(attempts=1)
        reap(now=NOW)
        reap(now=NOW)
        reap(now=NOW)
        after = kit.reload(run)
        assert (after.status, after.attempts) == ("pending", 1)

    def test_a_run_that_is_stuck_again_after_a_new_claim_is_reaped_again(self):
        from moderation import worker
        from moderation.models import ModerationRun

        run = stuck(attempts=1)
        reap(now=NOW)
        worker.claim_next_run(now=kit.at(20_000))
        ModerationRun.objects.filter(pk=run.pk).update(attempts=2)
        assert reap(now=kit.at(20_000 + TIMEOUT + 1)) == {"requeued": 1, "failed": 0}
        ModerationRun.objects.filter(pk=run.pk).update(status="running", attempts=3, claimed_at=kit.at(30_000))
        assert reap(now=kit.at(30_000 + TIMEOUT + 1)) == {"requeued": 0, "failed": 1}
