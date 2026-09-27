"""claim_next_run (docs/step8_brief.md, Interface): the oldest claimable live run, one atomic claim, per-conversation
serialization, no attempts bump, no replay runs."""
import pytest
import worker_kit as kit

pytestmark = pytest.mark.django_db


def claim(**kwargs):
    from moderation import worker

    return worker.claim_next_run(**kwargs)


def ids(runs):
    return [r.pk for r in runs]


class TestWhatIsClaimed:
    def test_an_empty_queue_gives_none(self):
        assert claim() is None

    def test_the_only_pending_run_is_claimed_and_becomes_running(self):
        run = kit.solo(created_at=kit.at(0))
        got = claim(now=kit.at(500))
        assert got.pk == run.pk
        assert got.status == "running"
        stored = kit.reload(run)
        assert stored.status == "running"

    def test_claimed_at_is_the_now_argument_on_the_returned_object_and_in_the_database(self):
        run = kit.solo(created_at=kit.at(0))
        got = claim(now=kit.at(777))
        assert got.claimed_at == kit.at(777)
        assert kit.reload(run).claimed_at == kit.at(777)

    def test_without_now_the_time_comes_from_django_timezone_now(self, clock):
        run = kit.solo(created_at=kit.at(0))
        clock.advance(4321)
        claim()
        assert kit.reload(run).claimed_at == kit.at(4321)

    def test_a_second_call_does_not_claim_the_same_run_again(self):
        kit.solo(created_at=kit.at(0))
        assert claim() is not None
        assert claim() is None

    def test_the_claim_changes_nothing_but_status_and_claimed_at(self):
        run = kit.solo(created_at=kit.at(0), failure_reason="", error="earlier note", attempts=1)
        before = kit.row(run)
        claim(now=kit.at(9))
        after = kit.row(run)
        changed = {k for k in before if before[k] != after[k]}
        assert changed == {"status", "claimed_at"}

    @pytest.mark.parametrize("attempts", [0, 1, 2])
    def test_the_claim_does_not_touch_attempts(self, attempts):
        run = kit.solo(created_at=kit.at(0), attempts=attempts)
        claim()
        assert kit.reload(run).attempts == attempts
        assert claim() is None

    def test_the_claim_does_not_set_started_at(self):
        run = kit.solo(created_at=kit.at(0))
        got = claim()
        assert got.started_at is None
        assert kit.reload(run).started_at is None

    def test_an_earlier_started_at_is_left_alone(self):
        run = kit.solo(created_at=kit.at(0), started_at=kit.at(-50), attempts=1)
        claim(now=kit.at(10))
        assert kit.reload(run).started_at == kit.at(-50)

    def test_the_returned_run_has_the_same_pk_and_conversation_as_the_row(self):
        run = kit.solo(created_at=kit.at(0))
        got = claim()
        assert (got.pk, got.conversation_id, got.trigger_message_id) == (run.pk, run.conversation_id, run.trigger_message_id)


class TestOrder:
    def test_the_oldest_created_at_is_claimed_first_not_the_lowest_id(self):
        newest, middle, oldest = kit.solos([kit.at(300), kit.at(200), kit.at(100)])
        assert ids(kit.drain_claims()) == [oldest.pk, middle.pk, newest.pk]

    def test_equal_created_at_is_broken_by_id(self):
        first, second, third = kit.solos([kit.at(50)] * 3)
        assert ids(kit.drain_claims()) == [first.pk, second.pk, third.pk]

    def test_the_order_is_global_across_conversations(self):
        a, b, c, d = kit.solos([kit.at(4), kit.at(1), kit.at(3), kit.at(2)])
        assert ids(kit.drain_claims()) == [b.pk, d.pk, c.pk, a.pk]

    def test_a_newer_run_is_not_taken_while_an_older_one_is_claimable(self):
        old = kit.solo(created_at=kit.at(1))
        kit.solo(created_at=kit.at(2))
        assert claim().pk == old.pk

    def test_within_one_conversation_the_older_run_comes_first_even_with_a_higher_id(self):
        w = kit.world(2)
        newer = kit.run_on(w[1], created_at=kit.at(20))
        older = kit.run_on(w[2], created_at=kit.at(10))
        assert newer.pk < older.pk
        assert claim().pk == older.pk


class TestWhatIsNeverClaimed:
    @pytest.mark.parametrize("status", kit.NON_CLAIMABLE)
    def test_a_run_that_is_not_pending_is_never_claimed(self, status):
        run = kit.solo(created_at=kit.at(0), status=status, claimed_at=kit.at(-100000))
        before = kit.row(run)
        assert claim(now=kit.at(10)) is None
        assert kit.row(run) == before

    def test_a_running_run_with_a_very_old_claimed_at_is_not_taken_over(self):
        run = kit.solo(created_at=kit.at(0), status="running", claimed_at=kit.at(-10**7), attempts=1)
        assert claim(now=kit.at(0)) is None
        assert kit.reload(run).claimed_at == kit.at(-10**7)

    def test_a_pending_replay_run_is_never_claimed(self):
        run = kit.solo(created_at=kit.at(0), kind="replay")
        before = kit.row(run)
        assert claim() is None
        assert kit.row(run) == before

    def test_an_older_replay_run_does_not_get_in_the_way_of_a_live_run(self):
        replay = kit.solo(created_at=kit.at(1), kind="replay")
        live = kit.solo(created_at=kit.at(2))
        got = claim()
        assert got.pk == live.pk
        assert kit.status_of(replay) == "pending"

    def test_a_replay_run_next_to_a_live_run_of_the_same_conversation_stays_pending(self):
        w = kit.world(1)
        replay = kit.run_on(w[1], kind="replay", created_at=kit.at(1))
        live = kit.run_on(w[1], created_at=kit.at(2))
        assert claim().pk == live.pk
        assert kit.status_of(replay) == "pending"
        assert claim() is None

    def test_many_replay_runs_alone_give_none(self):
        kit.solos([kit.at(i) for i in range(5)], kind="replay")
        assert claim() is None


class TestOneRunAtATimePerConversation:
    def test_a_conversation_with_a_running_run_is_skipped_and_others_go_ahead(self):
        w = kit.world(2)
        running = kit.run_on(w[1], status="running", claimed_at=kit.at(0), created_at=kit.at(1))
        blocked = kit.run_on(w[2], created_at=kit.at(2))
        other = kit.solo(created_at=kit.at(3))
        got = claim(now=kit.at(5))
        assert got.pk == other.pk
        assert kit.status_of(blocked) == "pending"
        assert kit.status_of(running) == "running"

    def test_nothing_is_claimable_when_every_pending_run_sits_behind_a_running_one(self):
        w = kit.world(2)
        kit.run_on(w[1], status="running", claimed_at=kit.at(0), created_at=kit.at(1))
        pending = kit.run_on(w[2], created_at=kit.at(2))
        assert claim() is None
        assert kit.status_of(pending) == "pending"

    @pytest.mark.parametrize("finished", ["done", "failed", "skipped_budget", "skipped_disabled"])
    def test_a_conversation_is_free_again_when_its_running_run_finishes(self, finished):
        from moderation.models import ModerationRun

        w = kit.world(2)
        first = kit.run_on(w[1], created_at=kit.at(1))
        second = kit.run_on(w[2], created_at=kit.at(2))
        assert claim().pk == first.pk
        assert claim() is None
        ModerationRun.objects.filter(pk=first.pk).update(status=finished)
        assert claim().pk == second.pk

    def test_two_claims_in_a_row_never_give_two_runs_of_one_conversation(self):
        w = kit.world(3)
        for seq in (1, 2, 3):
            kit.run_on(w[seq], created_at=kit.at(seq))
        assert len(kit.drain_claims()) == 1

    def test_three_conversations_with_two_runs_each_give_one_claim_per_conversation(self):
        worlds = [kit.world(2) for _ in range(3)]
        for index, w in enumerate(worlds):
            kit.run_on(w[1], created_at=kit.at(index * 10 + 1))
            kit.run_on(w[2], created_at=kit.at(index * 10 + 2))
        got = kit.drain_claims()
        assert sorted(r.conversation_id for r in got) == sorted(w.conv.pk for w in worlds)

    def test_a_stuck_running_run_blocks_its_conversation_until_the_reaper_frees_it(self):
        from moderation import worker

        w = kit.world(2)
        stuck = kit.run_on(w[1], status="running", claimed_at=kit.at(0), attempts=1, created_at=kit.at(1))
        later = kit.run_on(w[2], created_at=kit.at(2))
        now = kit.at(10**6)
        assert claim(now=now) is None
        worker.reap_stuck_runs(now=now)
        assert kit.status_of(stuck) == "pending"
        assert claim(now=now).pk == stuck.pk
        assert kit.status_of(later) == "pending"

    def test_a_running_replay_of_a_conversation_does_not_stop_a_different_conversation(self):
        w = kit.world(1)
        kit.run_on(w[1], kind="replay", status="running", claimed_at=kit.at(0), created_at=kit.at(1))
        other = kit.solo(created_at=kit.at(2))
        assert claim().pk == other.pk


class TestTheClaimIsAConditionalUpdate:
    """A rival worker takes the candidate between the read and the write: the loser must see zero rows updated, must not
    return the run, must not overwrite the rival's claim, and must carry on with the next candidate (re-checking the
    one-run-per-conversation rule). The rival is injected by wrapping QuerySet.update."""

    @pytest.fixture
    def rival(self, monkeypatch):
        from django.db.models.query import QuerySet

        from moderation.models import ModerationRun

        real_update = QuerySet.update
        state = {"fired": False, "taken": None}

        def update(queryset, **kwargs):
            if queryset.model is ModerationRun and not state["fired"] and kwargs.get("status") == "running":
                state["fired"] = True
                candidate = ModerationRun.objects.filter(status="pending", kind="live").order_by("created_at", "pk").first()
                state["taken"] = candidate.pk
                real_update(
                    ModerationRun.objects.filter(pk=candidate.pk, status="pending"),
                    status="running", claimed_at=kit.at(-5),
                )
            return real_update(queryset, **kwargs)

        monkeypatch.setattr(QuerySet, "update", update)
        return state

    def test_the_loser_gets_the_next_candidate_and_leaves_the_rivals_claim_alone(self, rival):
        first = kit.solo(created_at=kit.at(1))
        second = kit.solo(created_at=kit.at(2))
        got = claim(now=kit.at(100))
        assert rival["taken"] == first.pk
        assert got.pk == second.pk
        assert kit.reload(first).claimed_at == kit.at(-5)
        assert kit.reload(second).claimed_at == kit.at(100)

    def test_the_loser_returns_none_when_the_rival_took_the_only_candidate(self, rival):
        only = kit.solo(created_at=kit.at(1))
        assert claim(now=kit.at(100)) is None
        assert rival["taken"] == only.pk
        assert kit.reload(only).claimed_at == kit.at(-5)

    def test_after_losing_a_run_the_loser_does_not_take_a_second_run_of_the_rivals_conversation(self, rival):
        w = kit.world(2)
        first = kit.run_on(w[1], created_at=kit.at(1))
        sibling = kit.run_on(w[2], created_at=kit.at(2))
        elsewhere = kit.solo(created_at=kit.at(3))
        got = claim(now=kit.at(100))
        assert rival["taken"] == first.pk
        assert got.pk == elsewhere.pk
        assert kit.status_of(sibling) == "pending"


class TestOldestFirstSurvivesALostRace:
    """A claimer that loses a race must not go on down a stale list and take a later run of a conversation while an older
    pending run of it exists (the older one only failed because a rival run of that conversation was `running` for a
    moment). The rival is injected by wrapping QuerySet.update: it takes the first candidate and finishes just before the
    third update attempt."""

    def test_a_later_run_is_not_taken_over_an_older_pending_one_of_the_same_conversation(self, monkeypatch):
        from django.db.models.query import QuerySet

        from moderation.models import ModerationRun

        w = kit.world(3)
        rival_run = kit.run_on(w[1], created_at=kit.at(1))
        older = kit.run_on(w[2], created_at=kit.at(2))
        newer = kit.run_on(w[3], created_at=kit.at(3))
        real_update = QuerySet.update
        state = {"calls": 0}

        def update(queryset, **kwargs):
            if queryset.model is ModerationRun and kwargs.get("status") == "running":
                state["calls"] += 1
                if state["calls"] == 1:
                    real_update(ModerationRun.objects.filter(pk=rival_run.pk), status="running", claimed_at=kit.at(-5))
                if state["calls"] == 3:
                    real_update(ModerationRun.objects.filter(pk=rival_run.pk), status="done")
            return real_update(queryset, **kwargs)

        monkeypatch.setattr(QuerySet, "update", update)
        got = claim(now=kit.at(100))
        assert getattr(got, "pk", None) != newer.pk
        assert kit.status_of(newer) == "pending"
