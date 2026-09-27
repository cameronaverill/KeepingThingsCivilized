"""The worker: claims pending live moderation runs and processes them one at a time (docs/plan.md section 11;
docs/step8_brief.md).

- `claim_next_run` takes the oldest claimable run with ONE conditional UPDATE, so two workers can never take the same run.
  A run is claimable when it is a live run, `pending`, and no other run of its conversation is `running` (this serializes
  the runs of one conversation even with several workers). Replay runs (step 13) are never claimed here.
- `reap_stuck_runs` gives up on runs that have been `running` for longer than `RUN_TIMEOUT_SECONDS`: back to `pending`
  while `attempts < RUN_MAX_ATTEMPTS`, otherwise `failed` with reason `timeout`.
- `process_one` claims one run and hands it to `moderation.pipeline.run_moderation`. A crash inside the run is caught and
  logged (never with user text in the log message), the run is left `failed`, and the worker carries on.
- `run_worker` is the loop; `manage.py run_moderator` wires it to the command line and to SIGINT/SIGTERM.

The worker never turns `LLM_ENABLED` on and never reads `.env`; every number comes from `config/tunables.py`.
"""
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import Error as DatabaseError
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from moderation import pipeline
from moderation.models import ModerationRun

logger = logging.getLogger(__name__)

# The reaper runs at least this often (seconds), whatever RUN_TIMEOUT_SECONDS is.
MAX_SECONDS_BETWEEN_REAPS = 30

# Most times one claim re-runs its candidate query after losing a race (a bound so it can never spin).
MAX_CLAIM_ATTEMPTS = 100

# In `once` mode, give up after this many database errors in a row (other modes keep trying, sleeping between tries).
MAX_ONCE_ERRORS = 5

# Statuses in which a run can still be picked up or is being worked on; after a crash anything still in one of them
# is forced to `failed`.
_UNFINISHED = ("pending", "running")


def _now(now):
    return timezone.now() if now is None else now


# --- Claiming ---------------------------------------------------------------------------------------------------------

def _running_sibling():
    """Is another run of the same conversation `running`? (Correlated with the run row being updated or selected.)"""
    return Exists(
        ModerationRun.objects.filter(conversation_id=OuterRef("conversation_id"), status="running")
    )


def claim_next_run(*, now=None):
    """Atomically claim the oldest claimable run and return it (status `running`, `claimed_at` set), or None.

    Oldest = `created_at`, then `id`. The claim is a single conditional UPDATE (pending -> running); a worker that loses
    the race sees zero rows updated and re-runs the candidate query. It does not touch `attempts` or `started_at`
    (`run_moderation` sets those)."""
    claimed_at = _now(now)
    # After a lost race the candidate query is run again (never a stale list), so a run that was blocked by a rival and is
    # claimable now is taken before any later run of its conversation: oldest-first holds within a conversation.
    for _ in range(MAX_CLAIM_ATTEMPTS):
        pk = (
            ModerationRun.objects.filter(kind="live", status="pending")
            .filter(~_running_sibling())
            .order_by("created_at", "id")
            .values_list("pk", flat=True)
            .first()
        )
        if pk is None:
            return None
        updated = (
            ModerationRun.objects.filter(pk=pk, kind="live", status="pending")
            .filter(~_running_sibling())
            .update(status="running", claimed_at=claimed_at)
        )
        if updated:
            return ModerationRun.objects.get(pk=pk)
    return None


# --- The reaper -------------------------------------------------------------------------------------------------------

def reap_stuck_runs(*, now=None):
    """Requeue or fail live runs that have been `running` since before `now - RUN_TIMEOUT_SECONDS`.

    A run is stuck when its `claimed_at` is strictly older than the timeout (exactly the timeout is not yet stuck). A
    running run without `claimed_at` is left alone (only the worker sets `running` together with `claimed_at`).
    Returns {"requeued": n, "failed": m}. Idempotent; touches nothing that is not `running`."""
    now = _now(now)
    cutoff = now - timedelta(seconds=int(settings.RUN_TIMEOUT_SECONDS))
    max_attempts = int(settings.RUN_MAX_ATTEMPTS)
    stuck = ModerationRun.objects.filter(kind="live", status="running", claimed_at__lt=cutoff)

    failed = stuck.filter(attempts__gte=max_attempts).update(
        status="failed",
        failure_reason="timeout",
        error=f"abandoned: still running {int(settings.RUN_TIMEOUT_SECONDS)} s after it was claimed, "
        f"after {max_attempts} attempts",
        finished_at=now,
    )
    requeued = stuck.filter(attempts__lt=max_attempts).update(status="pending", claimed_at=None)
    if requeued or failed:
        logger.warning("reaper: %s stuck run(s) requeued, %s failed", requeued, failed)
    return {"requeued": requeued, "failed": failed}


# --- Processing -------------------------------------------------------------------------------------------------------

def process_one(*, now=None):
    """Claim one run, run the pipeline on it and return the refreshed run; None when nothing is claimable.

    Any exception from `run_moderation` (including its programming-error re-raise) is logged and swallowed; the run is
    left `failed` (`internal_error`) unless the pipeline already gave it a terminal status. `KeyboardInterrupt` and
    `SystemExit` are not caught."""
    run = claim_next_run(now=now)
    if run is None:
        return None
    try:
        pipeline.run_moderation(run)
    except Exception as exc:
        # The log message names the run and the exception class only: exception text may quote user text.
        logger.exception("moderation run %s crashed in the worker (%s)", run.pk, type(exc).__name__)
        try:
            ModerationRun.objects.filter(pk=run.pk, status__in=_UNFINISHED).update(
                status="failed",
                failure_reason="internal_error",
                error=f"the worker caught a crash: {type(exc).__name__}",
                finished_at=_now(now),
            )
        except DatabaseError:
            logger.exception("could not mark moderation run %s failed after a crash", run.pk)
    try:
        run.refresh_from_db()
    except ModerationRun.DoesNotExist:  # pragma: no cover - runs are never deleted
        pass
    return run


# --- The loop ---------------------------------------------------------------------------------------------------------

def _stopped(stop):
    if stop is None:
        return False
    is_set = getattr(stop, "is_set", None)
    return bool(is_set() if callable(is_set) else stop())


def _reap_interval_seconds():
    return min(int(settings.RUN_TIMEOUT_SECONDS) / 2, MAX_SECONDS_BETWEEN_REAPS)


def run_worker(*, once=False, max_runs=None, poll_seconds=None, stop=None, sleep=time.sleep, on_run=None):
    """Process runs until told to stop; returns the number of runs processed.

    The reaper runs once at the start and then every `min(RUN_TIMEOUT_SECONDS / 2, 30)` seconds. The loop processes runs
    until nothing is claimable, then sleeps `poll_seconds` (default `WORKER_POLL_SECONDS`) and repeats. `once`: reap,
    process every claimable run, return. `max_runs`: stop after that many runs. `stop`: a callable or an Event; the loop
    ends when it is true/set (after the current run). `on_run(run)`, if given, is called after each processed run (the
    command uses it to print one line per run). A database error in one iteration is logged and the loop sleeps and
    carries on."""
    poll = float(settings.WORKER_POLL_SECONDS) if poll_seconds is None else poll_seconds
    interval = timedelta(seconds=_reap_interval_seconds())
    processed = 0
    last_reap = None
    errors = 0

    while True:
        if _stopped(stop) or (max_runs is not None and processed >= max_runs):
            break
        try:
            if last_reap is None or timezone.now() - last_reap >= interval:
                last_reap = timezone.now()
                reap_stuck_runs()
            run = process_one()
        except DatabaseError:
            logger.exception("worker: database error in one iteration; sleeping before the next")
            errors += 1
            if once and errors >= MAX_ONCE_ERRORS:
                raise  # a one-shot pass gives up on a database that stays broken
            sleep(poll)
            continue
        errors = 0
        if run is not None:
            processed += 1
            if on_run is not None:
                on_run(run)
            continue
        if once or _stopped(stop):
            break
        sleep(poll)
    return processed

