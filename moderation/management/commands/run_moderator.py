"""`manage.py run_moderator`: the worker that processes pending moderation runs (docs/plan.md section 11).

Prints one line at start and one line per processed run (run id, conversation id, final status, failure reason). Never
prints keys, usernames, emails or message text. SIGINT and SIGTERM finish the current run and exit cleanly.
"""
import signal
import threading

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from moderation import worker


class Command(BaseCommand):
    help = (
        "Run the moderation worker: claim pending runs, oldest first, and process them one at a time. "
        "Refuses to start when MODERATION_RUN_MODE is 'sync'."
    )

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process every claimable run, then exit.")
        parser.add_argument("--max-runs", type=int, default=None, metavar="N", help="Stop after N processed runs.")
        parser.add_argument(
            "--poll-seconds",
            type=float,
            default=None,
            metavar="S",
            help="Seconds to sleep when nothing is claimable (default: WORKER_POLL_SECONDS in config/tunables.py).",
        )

    def handle(self, *args, **options):
        mode = settings.MODERATION_RUN_MODE
        if mode == "sync":
            raise CommandError(
                "MODERATION_RUN_MODE is 'sync': runs are processed inside the web request, so a worker would be "
                "redundant. Set MODERATION_RUN_MODE = 'worker' in config/tunables.py to use the worker."
            )
        max_runs = options["max_runs"]
        if max_runs is not None and max_runs < 0:
            raise CommandError("--max-runs must not be negative.")
        poll = options["poll_seconds"]
        if poll is not None and poll < 0:
            raise CommandError("--poll-seconds must not be negative.")
        interval = float(settings.WORKER_POLL_SECONDS) if poll is None else poll

        stop = threading.Event()
        restore = self._install_signal_handlers(stop)
        self.stdout.write(f"Worker started: mode={mode}, poll interval={interval:g} s.")
        try:
            worker.run_worker(
                once=options["once"],
                max_runs=max_runs,
                poll_seconds=poll,
                stop=stop,
                on_run=self._report,
            )
        finally:
            restore()

    def _report(self, run):
        line = f"run {run.pk} (conversation {run.conversation_id}): {run.status}"
        if run.failure_reason:
            line += f", {run.failure_reason}"
        self.stdout.write(line)
        self.stdout.flush()

    @staticmethod
    def _install_signal_handlers(stop):
        """SIGINT/SIGTERM set `stop` (the current run finishes first); a second signal interrupts at once. Only possible
        in the main thread; elsewhere (tests, embedding) nothing is installed. Returns a function that undoes it."""
        if threading.current_thread() is not threading.main_thread():
            return lambda: None

        def handler(signum, frame):
            if stop.is_set():
                raise KeyboardInterrupt
            stop.set()

        previous = {}
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, handler)

        def restore():
            for signum, old in previous.items():
                signal.signal(signum, old)

        return restore
