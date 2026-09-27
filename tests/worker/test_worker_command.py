"""`manage.py run_moderator` through call_command: options, the start line, one line per run, the refusal in sync mode, no user
text in the output, signals that finish the current run and exit cleanly."""
import os
import re
import signal
from io import StringIO

import pytest
import worker_kit as kit
from django.core.management import CommandError, call_command

pytestmark = pytest.mark.django_db


def command(*args):
    """Run the command; returns (stdout lines, stderr text)."""
    out, err = StringIO(), StringIO()
    call_command("run_moderator", *args, stdout=out, stderr=err)
    return [line for line in out.getvalue().splitlines() if line.strip()], err.getvalue()


def has_number(line, number):
    return re.search(rf"(?<![\w.]){number}(?![\w.])", line) is not None


class TestOutput:
    def test_nothing_to_do_prints_only_the_start_line(self):
        lines, err = command("--once")
        assert len(lines) == 1
        assert err == ""

    def test_the_start_line_names_the_worker_mode_and_the_default_poll_interval(self, tune):
        tune(WORKER_POLL_SECONDS=7.25)
        (start,) = command("--once")[0]
        assert "worker" in start.lower()
        assert "7.25" in start

    def test_the_start_line_shows_the_poll_seconds_option(self):
        (start,) = command("--once", "--poll-seconds", "0.5")[0]
        assert "0.5" in start
        assert "worker" in start.lower()

    def test_one_line_per_processed_run_after_the_start_line(self, monkeypatch):
        kit.install_pipeline(monkeypatch)
        kit.world(1), kit.world(1)  # so that no conversation id equals a run id
        runs = kit.solos([kit.at(i) for i in range(3)])
        assert all(r.conversation_id != r.pk for r in runs)
        lines, _ = command("--once")
        assert len(lines) == 1 + 3
        for run, line in zip(runs, lines[1:]):
            assert has_number(line, run.pk)
            assert has_number(line, run.conversation_id)
            assert "done" in line

    def test_run_lines_follow_the_order_the_runs_were_processed_in(self, monkeypatch):
        kit.install_pipeline(monkeypatch)
        newest, oldest = kit.solos([kit.at(20), kit.at(10)])
        lines, _ = command("--once")
        assert has_number(lines[1], oldest.pk)
        assert has_number(lines[2], newest.pk)

    def test_a_failed_run_line_shows_the_status_and_the_failure_reason(self, monkeypatch):
        kit.install_pipeline(monkeypatch, kit.finish("failed", "structural"))
        run = kit.solo(created_at=kit.at(0))
        lines, _ = command("--once")
        assert len(lines) == 2
        assert has_number(lines[1], run.pk)
        assert "failed" in lines[1]
        assert "structural" in lines[1]

    def test_a_crashed_run_is_reported_as_failed_internal_error(self, monkeypatch):
        kit.install_pipeline(monkeypatch, kit.crash(RuntimeError("synthetic crash")))
        kit.solo(created_at=kit.at(0))
        lines, _ = command("--once")
        assert len(lines) == 2
        assert "failed" in lines[1]
        assert "internal_error" in lines[1]

    def test_a_run_without_a_failure_reason_shows_no_reason(self, monkeypatch):
        kit.install_pipeline(monkeypatch, kit.finish("skipped_budget"))
        kit.solo(created_at=kit.at(0))
        lines, _ = command("--once")
        assert "skipped_budget" in lines[1]
        assert "internal_error" not in lines[1]

    def test_replay_runs_are_not_processed_and_not_reported(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch)
        kit.solo(created_at=kit.at(0), kind="replay")
        lines, _ = command("--once")
        assert len(lines) == 1
        assert stub.calls == []


class TestOptions:
    def test_max_runs_stops_after_that_many_runs(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch)
        runs = kit.solos([kit.at(i) for i in range(4)])
        lines, _ = command("--max-runs", "2", "--poll-seconds", "0.01")
        assert len(lines) == 1 + 2
        assert stub.pks == [runs[0].pk, runs[1].pk]
        assert [kit.status_of(r) for r in runs] == ["done", "done", "pending", "pending"]

    def test_once_processes_every_claimable_run(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch)
        runs = kit.solos([kit.at(i) for i in range(4)])
        command("--once")
        assert stub.pks == [r.pk for r in runs]

    def test_once_with_max_runs_stops_at_the_smaller(self, monkeypatch):
        stub = kit.install_pipeline(monkeypatch)
        kit.solos([kit.at(i) for i in range(4)])
        command("--once", "--max-runs", "3")
        assert len(stub.calls) == 3

    @pytest.mark.parametrize("option,value", [("--max-runs", "abc"), ("--max-runs", "1.5"), ("--poll-seconds", "soon")])
    def test_a_value_that_is_not_a_number_is_a_command_error(self, option, value):
        with pytest.raises(CommandError):
            command("--once", option, value)

    @pytest.mark.parametrize("option", ["--max-runs", "--poll-seconds"])
    def test_a_negative_number_is_refused_and_nothing_is_processed(self, monkeypatch, option):
        stub = kit.install_pipeline(monkeypatch)
        kit.solo(created_at=kit.at(0))
        with pytest.raises(CommandError):
            command("--once", f"{option}=-1")
        assert stub.calls == []

    def test_an_unknown_option_is_a_command_error(self):
        with pytest.raises(CommandError):
            command("--forever")


class TestWiringToTheWorker:
    """What the options turn into: the keyword arguments of run_worker."""

    @pytest.fixture
    def seen(self, monkeypatch):
        import moderation.worker as worker

        captured = []

        def fake_run_worker(**kwargs):
            captured.append(kwargs)
            return 0

        monkeypatch.setattr(worker, "run_worker", fake_run_worker)
        return captured

    def test_no_options_means_a_forever_loop_with_the_default_poll(self, seen):
        command()
        (kwargs,) = seen
        assert kwargs["once"] is False
        assert kwargs["max_runs"] is None
        assert kwargs["poll_seconds"] is None
        assert kwargs["stop"] is not None

    def test_every_option_reaches_run_worker(self, seen):
        command("--once", "--max-runs", "7", "--poll-seconds", "0.75")
        (kwargs,) = seen
        assert (kwargs["once"], kwargs["max_runs"], kwargs["poll_seconds"]) == (True, 7, 0.75)

    def test_the_stop_argument_is_something_run_worker_can_poll(self, seen):
        command()
        (kwargs,) = seen
        assert kwargs["stop"].is_set() is False

    def test_the_start_line_is_printed_before_run_worker_is_called(self, monkeypatch):
        import moderation.worker as worker

        out = StringIO()
        printed_before = []

        def fake_run_worker(**kwargs):
            printed_before.append(out.getvalue())
            return 0

        monkeypatch.setattr(worker, "run_worker", fake_run_worker)
        call_command("run_moderator", stdout=out)
        assert len(printed_before[0].splitlines()) == 1

    def test_nothing_reaches_run_worker_in_sync_mode(self, seen, settings):
        settings.MODERATION_RUN_MODE = "sync"
        with pytest.raises((CommandError, SystemExit)):
            command()
        assert seen == []


class TestSyncModeRefusal:
    def test_the_command_refuses_to_start_in_sync_mode_with_a_clear_message(self, settings, monkeypatch):
        settings.MODERATION_RUN_MODE = "sync"
        stub = kit.install_pipeline(monkeypatch)
        run = kit.solo(created_at=kit.at(0))
        out, err = StringIO(), StringIO()
        with pytest.raises((CommandError, SystemExit)) as raised:
            call_command("run_moderator", "--once", stdout=out, stderr=err)
        message = f"{raised.value} {out.getvalue()} {err.getvalue()}".lower()
        assert "sync" in message
        assert stub.calls == []
        assert kit.status_of(run) == "pending"

    def test_the_refusal_is_a_non_zero_exit(self, settings):
        settings.MODERATION_RUN_MODE = "sync"
        with pytest.raises((CommandError, SystemExit)) as raised:
            command("--once")
        code = getattr(raised.value, "returncode", getattr(raised.value, "code", None))
        assert code not in (0, None)

    def test_worker_mode_is_accepted(self, settings):
        settings.MODERATION_RUN_MODE = "worker"
        assert len(command("--once")[0]) == 1


class TestNothingSensitiveIsPrinted:
    def test_output_has_no_username_email_message_text_or_key(self, monkeypatch, settings, fake, llm_on):
        pair = kit.active_pair()
        from forum.models import Message

        user_sentence = "quokka-zeppelin-verbatim-user-sentence " + kit.TEXT_A
        message = Message.objects.create(conversation=pair.conv, author_type="user", participant=pair.pa, content=user_sentence)
        kit.run_on(message, created_at=kit.at(0))
        fake(*kit.scripted_pair(message, kit.QUOTE_A, kit.ACT_1))
        out, err = StringIO(), StringIO()
        call_command("run_moderator", "--once", stdout=out, stderr=err)
        printed = out.getvalue() + err.getvalue()
        assert "done" in printed
        for forbidden in [user_sentence, kit.TEXT_A, kit.ACT_1, *pair.names, *pair.emails, settings.ANTHROPIC_API_KEY]:
            assert forbidden not in printed

    def test_a_crash_prints_no_exception_text_or_user_text(self, monkeypatch):
        pair = kit.active_pair()
        from forum.models import Message

        message = Message.objects.create(
            conversation=pair.conv, author_type="user", participant=pair.pa, content="a distinctive sentence about walruses"
        )
        kit.run_on(message, created_at=kit.at(0))
        kit.install_pipeline(monkeypatch, kit.crash(RuntimeError(f"boom {pair.names[0]} walruses")))
        out, err = StringIO(), StringIO()
        call_command("run_moderator", "--once", stdout=out, stderr=err)
        printed = out.getvalue() + err.getvalue()
        for forbidden in ["walruses", *pair.names, *pair.emails]:
            assert forbidden not in printed


class TestTheCommandDoesNotSwitchTheModelOn:
    def test_kill_switch_off_runs_end_skipped_disabled_and_the_switch_stays_off(self, settings, fake):
        client = fake()
        run = kit.solo(created_at=kit.at(0))
        lines, _ = command("--once")
        assert "skipped_disabled" in lines[1]
        assert kit.status_of(run) == "skipped_disabled"
        assert settings.LLM_ENABLED is False
        assert client.calls == []


class TestSignals:
    """A signal during a run lets that run finish, then the command returns normally (no exception, exit code 0). The
    sentinel fixture keeps the test process alive if the command fails to install its own handlers."""

    @pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
    def test_a_signal_during_a_run_finishes_that_run_and_stops_the_worker(self, monkeypatch, signal_sentinels, sig):
        inner = kit.finish("done")

        def behaviour(run):
            os.kill(os.getpid(), sig)
            return inner(run)

        stub = kit.install_pipeline(monkeypatch, behaviour)
        runs = kit.solos([kit.at(i) for i in range(3)])
        lines, _ = command("--poll-seconds", "0.01")
        assert signal_sentinels == []
        assert stub.pks == [runs[0].pk]
        assert [kit.status_of(r) for r in runs] == ["done", "pending", "pending"]
        assert len(lines) == 1 + 1
        assert "done" in lines[1]

    @pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
    def test_the_previous_signal_handlers_are_put_back_afterwards(self, monkeypatch, signal_sentinels, sig):
        before = signal.getsignal(sig)
        kit.install_pipeline(monkeypatch)
        kit.solo(created_at=kit.at(0))
        command("--once")
        assert signal.getsignal(sig) is before

    def test_a_signal_while_idle_ends_the_worker(self, monkeypatch, signal_sentinels):
        """No run is claimable; the first poll sleep is interrupted by the signal and the loop ends."""
        import moderation.worker as worker

        def sleep_then_signal(seconds):
            os.kill(os.getpid(), signal.SIGTERM)

        real = worker.run_worker

        def with_signalling_sleep(**kwargs):
            return real(**{**kwargs, "sleep": sleep_then_signal})

        monkeypatch.setattr(worker, "run_worker", with_signalling_sleep)
        lines, _ = command("--poll-seconds", "0.01")
        assert signal_sentinels == []
        assert len(lines) == 1
