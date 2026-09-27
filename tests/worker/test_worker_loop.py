"""run_worker: once, max_runs, stop, sleeping between empty polls, the reaper at start and periodically, no busy loop, a
database error on one iteration does not end the worker."""
import threading

import pytest
import worker_kit as kit

pytestmark = pytest.mark.django_db


def run_worker(**kwargs):
    from moderation import worker

    return worker.run_worker(**kwargs)


@pytest.fixture
def stub(monkeypatch):
    return kit.install_pipeline(monkeypatch)


class TestOnce:
    def test_an_empty_queue_returns_zero_without_sleeping(self, stub):
        sleeper = kit.Sleeper()
        assert run_worker(once=True, sleep=sleeper) == 0
        assert sleeper.calls == []
        assert stub.calls == []

    def test_every_claimable_run_is_processed_and_counted(self, stub):
        runs = kit.solos([kit.at(i) for i in range(5)])
        sleeper = kit.Sleeper()
        assert run_worker(once=True, sleep=sleeper) == 5
        assert stub.pks == [r.pk for r in runs]
        assert sleeper.calls == []

    def test_runs_of_one_conversation_are_all_processed_one_after_another(self, stub):
        w = kit.world(3)
        runs = [kit.run_on(w[i], created_at=kit.at(i)) for i in (1, 2, 3)]
        assert run_worker(once=True, sleep=kit.Sleeper()) == 3
        assert stub.pks == [r.pk for r in runs]

    def test_replay_runs_and_runs_of_other_states_are_left_alone(self, stub):
        kit.solo(created_at=kit.at(0), kind="replay")
        kit.solo(created_at=kit.at(1), status="done")
        live = kit.solo(created_at=kit.at(2))
        assert run_worker(once=True, sleep=kit.Sleeper()) == 1
        assert stub.pks == [live.pk]

    def test_a_crashing_run_counts_as_processed_and_the_rest_are_still_done(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch, kit.crash_first_then_finish(RuntimeError("synthetic crash")))
        first, second, third = kit.solos([kit.at(1), kit.at(2), kit.at(3)])
        assert run_worker(once=True, sleep=kit.Sleeper()) == 3
        assert stub.pks == [first.pk, second.pk, third.pk]
        assert [kit.status_of(r) for r in (first, second, third)] == ["failed", "done", "done"]

    def test_once_calls_the_reaper_before_the_first_claim(self, monkeypatch, stub):
        events = kit.trace_worker(monkeypatch)
        kit.solo(created_at=kit.at(0))
        run_worker(once=True, sleep=kit.Sleeper())
        assert events == ["reap", "claim-run", "claim-none"]

    def test_once_recovers_a_stuck_run_and_then_processes_it(self, stub):
        old = kit.long_ago()
        run = kit.solo(created_at=kit.at(0), status="running", claimed_at=kit.long_ago(), attempts=1)
        assert run_worker(once=True, sleep=kit.Sleeper()) == 1
        assert stub.pks == [run.pk]
        assert kit.status_of(run) == "done"

    def test_once_fails_a_stuck_run_that_used_its_attempts_and_does_not_process_it(self, stub, settings):
        old = kit.long_ago()
        run = kit.solo(created_at=kit.at(0), status="running", claimed_at=kit.long_ago(), attempts=settings.RUN_MAX_ATTEMPTS)
        assert run_worker(once=True, sleep=kit.Sleeper()) == 0
        stored = kit.reload(run)
        assert (stored.status, stored.failure_reason) == ("failed", "timeout")
        assert stub.calls == []


class TestMaxRuns:
    def test_it_stops_after_that_many_runs(self, stub):
        runs = kit.solos([kit.at(i) for i in range(5)])
        assert run_worker(max_runs=2, sleep=kit.Sleeper()) == 2
        assert stub.pks == [runs[0].pk, runs[1].pk]
        assert [kit.status_of(r) for r in runs] == ["done", "done", "pending", "pending", "pending"]

    def test_max_runs_larger_than_the_queue_waits_for_more_work(self, stub):
        first = kit.solo(created_at=kit.at(0))
        created = []

        def add_a_run(call_number):
            created.append(kit.solo(created_at=kit.at(10)))

        sleeper = kit.Sleeper(hooks={1: add_a_run})
        assert run_worker(max_runs=2, sleep=sleeper) == 2
        assert stub.pks == [first.pk, created[0].pk]
        assert len(sleeper.calls) == 1

    def test_a_crashed_run_counts_towards_max_runs(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch, kit.crash(RuntimeError("synthetic crash")))
        kit.solos([kit.at(i) for i in range(4)])
        assert run_worker(max_runs=2, sleep=kit.Sleeper()) == 2
        assert len(stub.calls) == 2

    def test_max_runs_one_takes_exactly_one_run(self, stub):
        kit.solos([kit.at(0), kit.at(1)])
        assert run_worker(max_runs=1, sleep=kit.Sleeper()) == 1
        assert len(stub.calls) == 1


class TestStop:
    def test_a_stop_flag_that_is_already_set_processes_nothing(self, stub):
        kit.solos([kit.at(0), kit.at(1)])
        event = threading.Event()
        event.set()
        assert run_worker(stop=event, sleep=kit.Sleeper()) == 0
        assert stub.calls == []

    def test_a_callable_stop_that_is_already_true_processes_nothing(self, stub):
        kit.solo(created_at=kit.at(0))
        assert run_worker(stop=lambda: True, sleep=kit.Sleeper()) == 0
        assert stub.calls == []

    def test_a_stop_set_during_a_run_lets_that_run_finish_and_takes_no_further_run(self, monkeypatch):
        event = threading.Event()
        inner = kit.finish("done")

        def behaviour(run):
            event.set()
            return inner(run)

        stub = kit.install_pipeline(monkeypatch, behaviour)
        runs = kit.solos([kit.at(i) for i in range(3)])
        assert run_worker(stop=event, sleep=kit.Sleeper()) == 1
        assert stub.pks == [runs[0].pk]
        assert [kit.status_of(r) for r in runs] == ["done", "pending", "pending"]

    def test_a_callable_stop_set_during_a_run_behaves_the_same(self, monkeypatch):
        flag = []
        inner = kit.finish("done")

        def behaviour(run):
            flag.append(True)
            return inner(run)

        stub = kit.install_pipeline(monkeypatch, behaviour)
        runs = kit.solos([kit.at(i) for i in range(3)])
        assert run_worker(stop=lambda: bool(flag), sleep=kit.Sleeper()) == 1
        assert stub.pks == [runs[0].pk]

    def test_a_stop_set_while_sleeping_ends_the_loop_at_once(self, stub):
        event = threading.Event()
        sleeper = kit.Sleeper(hooks={2: lambda call: event.set()})
        assert run_worker(stop=event, sleep=sleeper) == 0
        assert len(sleeper.calls) == 2


    def test_a_stop_set_while_looking_for_work_ends_the_loop_without_another_sleep(self, monkeypatch, stub):
        """SIGTERM arriving during the poll must not cost one more poll interval: stop is checked before sleeping."""
        import moderation.worker as worker

        event = threading.Event()
        real_claim = worker.claim_next_run

        def claim_then_stop(*args, **kwargs):
            result = real_claim(*args, **kwargs)
            event.set()
            return result

        monkeypatch.setattr(worker, "claim_next_run", claim_then_stop)
        sleeper = kit.Sleeper()
        assert run_worker(stop=event, poll_seconds=5, sleep=sleeper) == 0
        assert sleeper.calls == []


class TestSleepingBetweenPolls:
    def test_each_empty_poll_sleeps_once_for_the_poll_interval(self, stub):
        stopper = kit.StopAfter(3)
        assert run_worker(poll_seconds=0.25, stop=stopper, sleep=stopper.sleeper) == 0
        assert stopper.sleeper.calls == [0.25, 0.25, 0.25]

    def test_the_default_interval_is_the_worker_poll_seconds_tunable(self, stub, tune):
        tune(WORKER_POLL_SECONDS=7.5)
        stopper = kit.StopAfter(2)
        run_worker(stop=stopper, sleep=stopper.sleeper)
        assert stopper.sleeper.calls == [7.5, 7.5]

    def test_a_poll_interval_argument_beats_the_tunable(self, stub, tune):
        tune(WORKER_POLL_SECONDS=7.5)
        stopper = kit.StopAfter(1)
        run_worker(poll_seconds=0.5, stop=stopper, sleep=stopper.sleeper)
        assert stopper.sleeper.calls == [0.5]

    def test_it_never_polls_twice_in_a_row_without_sleeping(self, monkeypatch, stub):
        events = kit.trace_worker(monkeypatch)
        stopper = kit.StopAfter(6, trace=events)
        run_worker(poll_seconds=1, stop=stopper, sleep=stopper.sleeper)
        assert kit.follows_every(events, "claim-none", "sleep")
        assert events.count("sleep") == 6

    def test_no_sleep_happens_while_there_is_work(self, monkeypatch, stub):
        events = kit.trace_worker(monkeypatch)
        kit.solos([kit.at(i) for i in range(3)])
        stopper = kit.StopAfter(1, trace=events)
        run_worker(poll_seconds=1, stop=stopper, sleep=stopper.sleeper)
        assert events[1:5] == ["claim-run", "claim-run", "claim-run", "claim-none"]
        assert events[5] == "sleep"

    def test_a_run_created_while_sleeping_is_picked_up_on_the_next_poll(self, stub):
        created = []
        sleeper = kit.Sleeper(hooks={2: lambda call: created.append(kit.solo(created_at=kit.at(5)))})
        assert run_worker(max_runs=1, poll_seconds=1, sleep=sleeper) == 1
        assert stub.pks == [created[0].pk]
        assert len(sleeper.calls) == 2

    def test_a_blocked_conversation_is_polled_with_sleeps_until_it_is_free(self, stub):
        from django.utils import timezone

        from moderation.models import ModerationRun

        w = kit.world(2)
        running = kit.run_on(w[1], status="running", claimed_at=timezone.now(), created_at=kit.at(1))
        pending = kit.run_on(w[2], created_at=kit.at(2))

        def release(call_number):
            ModerationRun.objects.filter(pk=running.pk).update(status="done")

        sleeper = kit.Sleeper(hooks={3: release})
        assert run_worker(max_runs=1, poll_seconds=1, sleep=sleeper) == 1
        assert stub.pks == [pending.pk]
        assert len(sleeper.calls) == 3


class TestTheReaper:
    def test_it_runs_once_at_the_start_before_any_claim(self, monkeypatch, stub):
        events = kit.trace_worker(monkeypatch)
        stopper = kit.StopAfter(1, trace=events)
        run_worker(poll_seconds=1, stop=stopper, sleep=stopper.sleeper)
        assert events[0] == "reap"

    def test_it_does_not_run_on_every_poll(self, monkeypatch, stub):
        events = kit.trace_worker(monkeypatch)
        stopper = kit.StopAfter(10, trace=events)
        run_worker(poll_seconds=1, stop=stopper, sleep=stopper.sleeper)
        assert events.count("reap") == 1

    def test_a_stuck_run_is_recovered_at_the_start_of_a_long_running_worker(self, stub):
        old = kit.long_ago()
        run = kit.solo(created_at=kit.at(0), status="running", claimed_at=kit.long_ago(), attempts=1)
        assert run_worker(max_runs=1, sleep=kit.Sleeper()) == 1
        assert stub.pks == [run.pk]

    def test_it_runs_again_periodically_while_the_worker_keeps_polling(self, monkeypatch, stub):
        """600 simulated seconds of empty polls: more than the start call, far fewer than one per poll (the interval is the
        timeout over two, and never longer than 30 s on the reading of the brief, so between 5 and 21 calls in all)."""
        clock = kit.FrozenClock()
        kit.patch_time(monkeypatch, clock)
        events = kit.trace_worker(monkeypatch)
        stopper = kit.StopAfter(300, clock=clock, trace=events)
        run_worker(poll_seconds=2, stop=stopper, sleep=stopper.sleeper)
        assert 5 <= events.count("reap") <= 22

    def test_a_run_that_gets_stuck_while_the_worker_idles_is_recovered_later(self, monkeypatch, stub):
        """The run is claimed 'now' (not stuck yet); as simulated time passes the periodic reaper requeues it and the
        worker then processes it."""
        clock = kit.FrozenClock()
        kit.patch_time(monkeypatch, clock)
        run = kit.solo(created_at=kit.at(-100), status="running", claimed_at=clock.current, attempts=1)
        stopper = kit.StopAfter(2000, clock=clock)
        assert run_worker(max_runs=1, poll_seconds=2, stop=stopper, sleep=stopper.sleeper) == 1
        assert stub.pks == [run.pk]
        assert 300 // 2 < len(stopper.sleeper.calls) < 2000


class TestOnRunCallback:
    def test_it_is_called_once_per_processed_run_in_order_with_the_refreshed_run(self, stub):
        runs = kit.solos([kit.at(2), kit.at(1), kit.at(3)])
        seen = []
        run_worker(once=True, sleep=kit.Sleeper(), on_run=lambda run: seen.append((run.pk, run.status)))
        assert seen == [(runs[1].pk, "done"), (runs[0].pk, "done"), (runs[2].pk, "done")]

    def test_it_is_called_for_a_crashed_run_with_its_failed_state(self, monkeypatch):
        kit.install_pipeline(monkeypatch, kit.crash(RuntimeError("synthetic crash")))
        run = kit.solo(created_at=kit.at(0))
        seen = []
        run_worker(once=True, sleep=kit.Sleeper(), on_run=lambda r: seen.append((r.pk, r.status, r.failure_reason)))
        assert seen == [(run.pk, "failed", "internal_error")]

    def test_it_is_not_called_when_nothing_is_processed(self, stub):
        seen = []
        run_worker(once=True, sleep=kit.Sleeper(), on_run=seen.append)
        assert seen == []


class TestSurvivingDatabaseErrors:
    def test_a_database_error_in_one_iteration_is_logged_slept_over_and_the_loop_goes_on(self, monkeypatch, stub, caplog):
        import moderation.worker as worker
        from django.db import OperationalError

        monkeypatch.setattr(
            worker, "claim_next_run", kit.fail_first(worker.claim_next_run, OperationalError("synthetic database is locked"))
        )
        run = kit.solo(created_at=kit.at(0))
        sleeper = kit.Sleeper()
        assert run_worker(max_runs=1, poll_seconds=0.5, sleep=sleeper) == 1
        assert stub.pks == [run.pk]
        assert sleeper.calls == [0.5]
        assert [r for r in caplog.records if r.levelname in ("WARNING", "ERROR", "CRITICAL")]

    def test_repeated_database_errors_never_busy_loop(self, monkeypatch, stub):
        import moderation.worker as worker
        from django.db import OperationalError

        events = kit.trace_worker(monkeypatch)
        attempts = []

        def always_broken(*args, **kwargs):
            attempts.append(1)
            raise OperationalError("synthetic database is locked")

        monkeypatch.setattr(worker, "claim_next_run", always_broken)
        stopper = kit.StopAfter(4, trace=events)
        run_worker(poll_seconds=0.5, stop=stopper, sleep=stopper.sleeper)
        assert stopper.sleeper.calls == [0.5, 0.5, 0.5, 0.5]
        assert len(attempts) == 4

    def test_a_database_error_in_process_one_itself_is_survived(self, monkeypatch, stub):
        import moderation.worker as worker
        from django.db import DatabaseError

        monkeypatch.setattr(
            worker, "process_one", kit.fail_first(worker.process_one, DatabaseError("synthetic database failure"))
        )
        run = kit.solo(created_at=kit.at(0))
        assert run_worker(max_runs=1, poll_seconds=0.5, sleep=kit.Sleeper()) == 1
        assert stub.pks == [run.pk]

    def test_keyboard_interrupt_is_not_swallowed_by_the_loop(self, monkeypatch, stub):
        import moderation.worker as worker

        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(worker, "process_one", interrupted)
        with pytest.raises(KeyboardInterrupt):
            run_worker(poll_seconds=0.5, sleep=kit.Sleeper())


class TestOnceGivesUpOnABrokenDatabase:
    def test_five_database_errors_in_a_row_end_a_once_pass_with_the_error(self, monkeypatch, stub):
        import moderation.worker as worker
        from django.db import OperationalError

        attempts = []

        def always_broken(*args, **kwargs):
            attempts.append(1)
            raise OperationalError("synthetic database is locked")

        monkeypatch.setattr(worker, "claim_next_run", always_broken)
        with pytest.raises(OperationalError):
            run_worker(once=True, poll_seconds=0.5, sleep=kit.Sleeper())
        assert len(attempts) == 5

    def test_four_errors_in_a_row_are_survived_by_a_once_pass(self, monkeypatch, stub):
        import moderation.worker as worker
        from django.db import OperationalError

        monkeypatch.setattr(
            worker, "claim_next_run",
            kit.fail_first(worker.claim_next_run, OperationalError("synthetic database is locked"), failures=4),
        )
        run = kit.solo(created_at=kit.at(0))
        sleeper = kit.Sleeper()
        assert run_worker(once=True, poll_seconds=0.5, sleep=sleeper) == 1
        assert stub.pks == [run.pk]
        assert sleeper.calls == [0.5] * 4

    def test_a_success_between_errors_resets_the_count(self, monkeypatch, stub):
        import moderation.worker as worker
        from django.db import OperationalError

        real = worker.claim_next_run
        plan = iter(["error"] * 4 + ["ok"] + ["error"] * 4 + ["ok"] * 5)

        def scripted(*args, **kwargs):
            return _fail() if next(plan) == "error" else real(*args, **kwargs)

        def _fail():
            raise OperationalError("synthetic database is locked")

        monkeypatch.setattr(worker, "claim_next_run", scripted)
        kit.solos([kit.at(0)])
        assert run_worker(once=True, poll_seconds=0.5, sleep=kit.Sleeper()) == 1
