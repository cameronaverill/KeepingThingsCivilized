"""process_one: claim one run, hand it to moderation.pipeline.run_moderation, return the refreshed run; a crash in a run is
contained (failed/internal_error, logged without user text) and never stops the worker."""
import pytest
import worker_kit as kit

pytestmark = pytest.mark.django_db


def process_one(**kwargs):
    from moderation import worker

    return worker.process_one(**kwargs)


class TestNormalRuns:
    def test_an_empty_queue_gives_none_and_the_pipeline_is_not_called(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch)
        assert process_one() is None
        assert stub.calls == []

    def test_a_pending_replay_run_alone_gives_none_and_the_pipeline_is_not_called(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch)
        run = kit.solo(created_at=kit.at(0), kind="replay")
        assert process_one() is None
        assert stub.calls == []
        assert kit.status_of(run) == "pending"

    def test_the_claimed_run_is_passed_to_run_moderation_once(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch)
        run = kit.solo(created_at=kit.at(0))
        process_one()
        assert stub.pks == [run.pk]

    def test_the_claim_is_already_in_the_database_when_the_pipeline_starts_and_counts_nothing(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch)
        kit.solo(created_at=kit.at(0), attempts=0)
        process_one(now=kit.at(321))
        (call,) = stub.calls
        assert call.status_in_db == "running"
        assert call.object_status == "running"
        assert call.claimed_in_db == kit.at(321)
        assert call.attempts_in_db == 0
        assert call.started_in_db is None

    def test_the_returned_run_is_the_refreshed_row(self, monkeypatch):
        kit.install_pipeline(monkeypatch, kit.finish("skipped_budget"))
        run = kit.solo(created_at=kit.at(0))
        got = process_one()
        assert (got.pk, got.status) == (run.pk, "skipped_budget")

    def test_the_row_is_re_read_even_when_the_pipeline_returns_nothing(self, monkeypatch):
        kit.install_pipeline(monkeypatch, kit.finish_quietly("done"))
        run = kit.solo(created_at=kit.at(0))
        got = process_one()
        assert (got.pk, got.status) == (run.pk, "done")

    def test_runs_are_taken_oldest_first_one_per_call(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch)
        newest, oldest, middle = kit.solos([kit.at(30), kit.at(10), kit.at(20)])
        results = [process_one() for _ in range(4)]
        assert stub.pks == [oldest.pk, middle.pk, newest.pk]
        assert [r.pk for r in results[:3]] == [oldest.pk, middle.pk, newest.pk]
        assert results[3] is None

    def test_the_second_run_of_a_conversation_waits_for_the_first_to_finish(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch)
        w = kit.world(2)
        first = kit.run_on(w[1], created_at=kit.at(1))
        second = kit.run_on(w[2], created_at=kit.at(2))
        process_one()
        process_one()
        assert stub.pks == [first.pk, second.pk]
        assert [c.status_in_db for c in stub.calls] == ["running", "running"]

    def test_the_now_argument_is_used_for_the_claim(self, monkeypatch):
        kit.install_pipeline(monkeypatch, kit.finish_quietly("done"))
        run = kit.solo(created_at=kit.at(0))
        process_one(now=kit.at(4000))
        assert kit.reload(run).claimed_at == kit.at(4000)

    def test_the_real_pipeline_with_the_kill_switch_off_ends_the_run_skipped_disabled(self, fake):
        client = fake()
        run = kit.solo(created_at=kit.at(0))
        got = process_one()
        stored = kit.reload(run)
        assert (got.pk, got.status) == (run.pk, "skipped_disabled")
        assert stored.status == "skipped_disabled"
        assert stored.attempts == 1
        assert stored.started_at is not None
        assert stored.claimed_at is not None
        assert client.calls == []

    def test_the_worker_does_not_turn_the_kill_switch_on(self, settings, fake):
        fake()
        kit.solo(created_at=kit.at(0))
        process_one()
        assert settings.LLM_ENABLED is False


class TestNoTransactionAroundTheRun:
    @pytest.mark.django_db(transaction=True)
    def test_the_pipeline_runs_outside_any_transaction(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch)
        kit.solo(created_at=kit.at(0))
        process_one()
        assert [c.in_atomic_block for c in stub.calls] == [False]

    @pytest.mark.django_db(transaction=True)
    def test_the_claim_is_committed_before_the_pipeline_runs(self, monkeypatch):
        """A second connection (a second worker) already sees the run as running while the pipeline is busy."""
        from django.db import connections

        from moderation.models import ModerationRun

        seen = {}

        def behaviour(run):
            other = connections.create_connection("default")
            try:
                with other.cursor() as cursor:
                    cursor.execute("SELECT status FROM moderation_moderationrun WHERE id = %s", [run.pk])
                    seen["status"] = cursor.fetchone()[0]
            finally:
                other.close()
            return kit.finish("done")(run)

        kit.install_pipeline(monkeypatch, behaviour)
        run = kit.solo(created_at=kit.at(0))
        process_one()
        assert seen["status"] == "running"
        assert ModerationRun.objects.get(pk=run.pk).status == "done"


class TestACrashInsideARun:
    def test_a_crash_does_not_escape_and_the_run_ends_failed_with_internal_error(self, monkeypatch):
        kit.install_pipeline(monkeypatch, kit.crash(RuntimeError("synthetic crash")))
        run = kit.solo(created_at=kit.at(0))
        got = process_one(now=kit.at(50))
        stored = kit.reload(run)
        assert (stored.status, stored.failure_reason) == ("failed", "internal_error")
        assert stored.finished_at is not None
        assert (got.pk, got.status, got.failure_reason) == (run.pk, "failed", "internal_error")

    def test_the_error_text_names_only_the_exception_class(self, monkeypatch):
        pair = kit.active_pair()
        from forum.models import Message

        message = Message.objects.create(
            conversation=pair.conv, author_type="user", participant=pair.pa, content="a distinctive sentence about walruses"
        )
        run = kit.run_on(message, created_at=kit.at(0))
        kit.install_pipeline(monkeypatch, kit.crash(RuntimeError(f"boom {pair.names[0]} walruses")))
        process_one()
        assert kit.reload(run).error == "the worker caught a crash: RuntimeError"

    def test_finished_at_is_the_current_time_when_the_worker_marks_the_crash(self, monkeypatch, clock):
        kit.install_pipeline(monkeypatch, kit.crash(RuntimeError("synthetic crash")))
        run = kit.solo(created_at=kit.at(0))
        clock.advance(90)
        process_one()
        assert kit.reload(run).finished_at == kit.at(90)

    @pytest.mark.parametrize("exc", [ValueError, KeyError, ZeroDivisionError, AssertionError, OSError])
    def test_any_exception_type_is_contained(self, monkeypatch, exc):
        kit.install_pipeline(monkeypatch, kit.crash(exc))
        run = kit.solo(created_at=kit.at(0))
        process_one()
        assert kit.status_of(run) == "failed"

    def test_a_database_error_inside_the_run_is_contained_too(self, monkeypatch):
        from django.db import OperationalError

        kit.install_pipeline(monkeypatch, kit.crash(OperationalError("synthetic database is locked")))
        run = kit.solo(created_at=kit.at(0))
        process_one()
        assert kit.reload(run).failure_reason == "internal_error"

    def test_a_run_the_pipeline_already_failed_keeps_its_own_reason(self, monkeypatch):
        kit.install_pipeline(monkeypatch, kit.crash_after("failed", "api_error", RuntimeError("synthetic after failing")))
        run = kit.solo(created_at=kit.at(0))
        process_one()
        stored = kit.reload(run)
        assert (stored.status, stored.failure_reason) == ("failed", "api_error")

    def test_a_run_the_pipeline_already_failed_keeps_its_finished_at(self, monkeypatch, clock):
        kit.install_pipeline(monkeypatch, kit.crash_after("failed", "structural", RuntimeError("synthetic after failing")))
        run = kit.solo(created_at=kit.at(0))
        clock.advance(10)
        process_one()
        clock.advance(1000)
        assert kit.reload(run).finished_at == kit.at(10)

    def test_the_crash_is_logged_with_logger_exception(self, monkeypatch, caplog):
        kit.install_pipeline(monkeypatch, kit.crash(RuntimeError("synthetic crash")))
        kit.solo(created_at=kit.at(0))
        process_one()
        records = kit.error_records(caplog)
        assert len(records) >= 1
        assert "RuntimeError" in caplog.text

    def test_the_log_has_no_message_text_username_or_email(self, monkeypatch, caplog):
        pair = kit.active_pair()
        from forum.models import Message

        user_sentence = "quokka-zeppelin-verbatim-user-sentence"
        message = Message.objects.create(conversation=pair.conv, author_type="user", participant=pair.pa, content=user_sentence)
        kit.run_on(message, created_at=kit.at(0))
        kit.install_pipeline(monkeypatch, kit.crash(RuntimeError("synthetic crash")))
        process_one()
        assert kit.error_records(caplog)
        for forbidden in [user_sentence, *pair.names, *pair.emails]:
            assert forbidden not in caplog.text

    def test_a_crashed_run_is_not_picked_up_again(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch, kit.crash(RuntimeError("synthetic crash")))
        kit.solo(created_at=kit.at(0))
        process_one()
        assert process_one() is None
        assert len(stub.calls) == 1

    def test_the_next_run_is_still_processed_after_a_crash(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch, kit.crash_first_then_finish(RuntimeError("synthetic crash")))
        first, second = kit.solos([kit.at(1), kit.at(2)])
        one, two = process_one(), process_one()
        assert stub.pks == [first.pk, second.pk]
        assert [(one.pk, one.status), (two.pk, two.status)] == [(first.pk, "failed"), (second.pk, "done")]

    def test_a_crash_in_one_conversation_does_not_block_the_next_run_of_that_conversation(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch, kit.crash_first_then_finish(RuntimeError("synthetic crash")))
        w = kit.world(2)
        first = kit.run_on(w[1], created_at=kit.at(1))
        second = kit.run_on(w[2], created_at=kit.at(2))
        process_one()
        got = process_one()
        assert stub.pks == [first.pk, second.pk]
        assert got.status == "done"

    def test_the_real_pipelines_programming_error_path_is_contained(self, monkeypatch, caplog):
        """The real run_moderation marks the run failed/internal_error and re-raises; the worker swallows the re-raise."""
        import moderation.pipeline as pipeline

        def broken(run):
            raise ValueError("synthetic transcript failure")

        monkeypatch.setattr(pipeline, "build_transcript", broken)
        run = kit.solo(created_at=kit.at(0))
        got = process_one()
        stored = kit.reload(run)
        assert (stored.status, stored.failure_reason) == ("failed", "internal_error")
        assert (got.pk, got.status) == (run.pk, "failed")
        assert kit.error_records(caplog)

    @pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
    def test_keyboard_interrupt_and_system_exit_are_not_swallowed(self, monkeypatch, exc):
        kit.install_pipeline(monkeypatch, kit.crash(exc))
        kit.solo(created_at=kit.at(0))
        with pytest.raises(exc):
            process_one()
