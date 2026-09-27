"""Step 20b item 5 (docs/step20b_brief.md): research runs in the worker. Claiming, per-conversation serialization
across kinds, dispatch, and reaping must all treat a `kind="research"` run the same way a `kind="live"` run is
already treated, per moderation/worker.py's own module docstring ("A run is claimable when it is a live or research
run ... this serializes the runs of one conversation even with several workers, across both kinds together"; "Applies
to live and research runs alike" for the reaper).
"""
import pytest
import worker_kit as kit
import worker_research_kit as rkit

pytestmark = pytest.mark.django_db


def claim(**kwargs):
    from moderation import worker

    return worker.claim_next_run(**kwargs)


def process_one(**kwargs):
    from moderation import worker

    return worker.process_one(**kwargs)


def reap(**kwargs):
    from moderation import worker

    return worker.reap_stuck_runs(**kwargs)


# --- Claiming --------------------------------------------------------------------------------------------------

class TestClaiming:
    def test_a_pending_research_run_alone_is_claimed_and_becomes_running(self):
        w = kit.world(1)
        run = rkit.research_run_on(w[1], created_at=kit.at(0))
        got = claim(now=kit.at(500))
        assert got.pk == run.pk
        assert got.status == "running"
        assert kit.reload(run).status == "running"

    def test_a_research_run_and_a_live_run_of_different_conversations_are_both_claimed_oldest_first(self):
        live = kit.solo(created_at=kit.at(1))
        research = rkit.research_run_on(kit.world(1)[1], created_at=kit.at(2))
        claimed = kit.drain_claims()
        assert [r.pk for r in claimed] == [live.pk, research.pk]

    def test_a_research_run_older_than_a_live_run_of_a_different_conversation_is_claimed_first(self):
        research = rkit.research_run_on(kit.world(1)[1], created_at=kit.at(1))
        live = kit.solo(created_at=kit.at(2))
        assert claim().pk == research.pk


class TestSerializationAcrossKinds:
    def test_a_running_live_run_blocks_a_pending_research_run_of_the_same_conversation(self):
        w = kit.world(2)
        running_live = kit.run_on(w[1], status="running", claimed_at=kit.at(0), created_at=kit.at(1))
        blocked_research = rkit.research_run_on(w[2], created_at=kit.at(2))
        other = kit.solo(created_at=kit.at(3))
        got = claim(now=kit.at(5))
        assert got.pk == other.pk
        assert kit.status_of(blocked_research) == "pending"
        assert kit.status_of(running_live) == "running"

    def test_a_running_research_run_blocks_a_pending_live_run_of_the_same_conversation(self):
        w = kit.world(2)
        running_research = rkit.research_run_on(w[1], status="running", claimed_at=kit.at(0), created_at=kit.at(1))
        blocked_live = kit.run_on(w[2], created_at=kit.at(2))
        other = kit.solo(created_at=kit.at(3))
        got = claim(now=kit.at(5))
        assert got.pk == other.pk
        assert kit.status_of(blocked_live) == "pending"
        assert kit.status_of(running_research) == "running"

    def test_a_conversation_is_free_again_once_its_running_research_run_finishes(self):
        from moderation.models import ModerationRun

        w = kit.world(2)
        research = rkit.research_run_on(w[1], created_at=kit.at(1))
        live = kit.run_on(w[2], created_at=kit.at(2))
        assert claim().pk == research.pk
        assert claim() is None
        ModerationRun.objects.filter(pk=research.pk).update(status="done")
        assert claim().pk == live.pk

    def test_two_claims_never_give_two_runs_of_one_conversation_when_the_kinds_are_mixed(self):
        w = kit.world(2)
        rkit.research_run_on(w[1], created_at=kit.at(1))
        kit.run_on(w[2], created_at=kit.at(2))
        assert len(kit.drain_claims()) == 1


# --- Dispatch ----------------------------------------------------------------------------------------------------

class TestDispatch:
    def test_a_research_run_is_handed_to_research_run_research_not_the_pipeline(self, monkeypatch):
        pipeline_stub = kit.install_pipeline(monkeypatch)
        research_stub = rkit.install_research(monkeypatch)
        w = kit.world(1)
        run = rkit.research_run_on(w[1], created_at=kit.at(0))
        process_one()
        assert research_stub.pks == [run.pk]
        assert pipeline_stub.calls == []

    def test_a_live_run_is_still_handed_to_pipeline_run_moderation_unchanged(self, monkeypatch):
        pipeline_stub = kit.install_pipeline(monkeypatch)
        research_stub = rkit.install_research(monkeypatch)
        run = kit.solo(created_at=kit.at(0))
        process_one()
        assert pipeline_stub.pks == [run.pk]
        assert research_stub.calls == []

    def test_live_and_research_runs_in_different_conversations_are_each_routed_correctly(self, monkeypatch):
        pipeline_stub = kit.install_pipeline(monkeypatch)
        research_stub = rkit.install_research(monkeypatch)
        live = kit.solo(created_at=kit.at(1))
        w = kit.world(1)
        research = rkit.research_run_on(w[1], created_at=kit.at(2))
        process_one()
        process_one()
        assert pipeline_stub.pks == [live.pk]
        assert research_stub.pks == [research.pk]

    def test_the_returned_run_from_a_research_dispatch_is_the_refreshed_row(self, monkeypatch):
        rkit.install_research(monkeypatch, kit.finish("skipped_budget"))
        w = kit.world(1)
        run = rkit.research_run_on(w[1], created_at=kit.at(0))
        got = process_one()
        assert (got.pk, got.status) == (run.pk, "skipped_budget")

    def test_a_crash_inside_a_research_run_is_contained_like_a_live_crash(self, monkeypatch):
        rkit.install_research(monkeypatch, kit.crash(RuntimeError("synthetic research crash")))
        w = kit.world(1)
        run = rkit.research_run_on(w[1], created_at=kit.at(0))
        got = process_one()
        stored = kit.reload(run)
        assert (stored.status, stored.failure_reason) == ("failed", "internal_error")
        assert (got.pk, got.status) == (run.pk, "failed")


# --- Reaping -------------------------------------------------------------------------------------------------------

class TestReaping:
    TIMEOUT = 60
    MAX_ATTEMPTS = 3

    @pytest.fixture(autouse=True)
    def _small_numbers(self, tune):
        tune(RUN_TIMEOUT_SECONDS=self.TIMEOUT, RUN_MAX_ATTEMPTS=self.MAX_ATTEMPTS)

    def _stuck_research(self, attempts=1, age=None):
        age = self.TIMEOUT + 1 if age is None else age
        w = kit.world(1)
        return rkit.research_run_on(
            w[1], created_at=kit.at(0), status="running", claimed_at=kit.at(10_000 - age), attempts=attempts,
        )

    def test_a_stuck_research_run_below_max_attempts_goes_back_to_pending(self):
        run = self._stuck_research(attempts=1)
        assert reap(now=kit.at(10_000)) == {"requeued": 1, "failed": 0}
        after = kit.reload(run)
        assert after.status == "pending"
        assert after.claimed_at is None

    def test_a_stuck_research_run_at_max_attempts_fails_with_reason_timeout(self):
        run = self._stuck_research(attempts=self.MAX_ATTEMPTS)
        assert reap(now=kit.at(10_000)) == {"requeued": 0, "failed": 1}
        after = kit.reload(run)
        assert (after.status, after.failure_reason) == ("failed", "timeout")

    def test_a_requeued_research_run_can_be_claimed_again(self):
        run = self._stuck_research(attempts=1)
        reap(now=kit.at(10_000))
        got = claim(now=kit.at(10_000))
        assert got.pk == run.pk

    def test_a_research_run_not_yet_past_the_timeout_is_left_alone(self):
        run = self._stuck_research(attempts=1, age=self.TIMEOUT - 1)
        before = kit.row(run)
        assert reap(now=kit.at(10_000)) == {"requeued": 0, "failed": 0}
        assert kit.row(run) == before

    def test_a_mixed_batch_of_stuck_live_and_research_runs_is_reaped_together(self):
        live = kit.solo(created_at=kit.at(0), status="running", claimed_at=kit.at(10_000 - self.TIMEOUT - 1), attempts=1)
        research = self._stuck_research(attempts=1)
        assert reap(now=kit.at(10_000)) == {"requeued": 2, "failed": 0}
        assert kit.status_of(live) == "pending"
        assert kit.status_of(research) == "pending"
