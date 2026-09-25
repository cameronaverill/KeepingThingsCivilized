"""Two-tier circuit breaker. State lives in GuardState (one database row) so it survives restarts and is shared by the
web process, the worker and the management commands.

HARD trip (a human must run `manage.py reset_breaker`; every call is refused until then):
- a spend-limit error (docs/plan.md section 3.1; Anthropic docs, platform.claude.com/docs/en/api/rate-limits):
  a limit set in the Console is HTTP 400, error type `invalid_request_error`, message starting "You have reached your
  specified API usage limits" (or "... specified workspace API usage limits"); the tier's monthly cap is HTTP 429
  with `error.details.error_code == "enforced_spend_limit_reached"`. Reason `spend_limit`.
- an authentication or permission error (HTTP 401 or 403, or error type `authentication_error` / `permission_error`):
  retrying a bad or revoked key is pointless. Reason `auth_error`.
- a billing problem (HTTP 402 / `billing_error`, or a 400 whose message mentions the credit balance): reason
  `billing_error`.
- `trip("manual")`.

SOFT trip (recovers by itself): BREAKER_MAX_CONSECUTIVE_ERRORS counted errors in a row, where an error more than
BREAKER_ERROR_WINDOW_SECONDS after the previous one restarts the count and a success resets it. Counted errors are
every provider failure that is not a hard one (no status, 5xx including 529, ordinary 429, other 4xx). Reason
`consecutive_errors`. Calls are refused until `cooldown_until` (BREAKER_COOLDOWN_SECONDS, doubling after each failed
probe up to BREAKER_COOLDOWN_MAX_SECONDS). Then exactly ONE probe call is let through: `check()` claims it atomically
(`probe_in_flight`, `probe_started_at`); other callers are refused meanwhile; a probe in flight longer than
BREAKER_PROBE_TIMEOUT_SECONDS is abandoned and can be claimed again. A success closes the breaker; a counted error
re-opens it with the doubled cooldown; a hard error hard-trips.

ALERTS: one plain-text email (to settings.ALERT_EMAIL, through Django 6.1's MAILERS) whenever the breaker goes from
closed to open, or is re-opened after a failed probe, or escalates from soft to hard. Hard trips always send; soft
trips send at most once per ALERT_MIN_SECONDS_BETWEEN_EMAILS. The email is sent after the trip's transaction block
has finished, never raises into the caller, and never contains the API key, prompts, messages or user data.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction

from moderation import clock
from moderation.scrub import scrub
from moderation.models import GuardState

logger = logging.getLogger(__name__)

SPEND_LIMIT_MESSAGE_PREFIX = "You have reached your specified"
SPEND_LIMIT_ERROR_CODE = "enforced_spend_limit_reached"
AUTH_ERROR_TYPES = ("authentication_error", "permission_error")
AUTH_STATUS_CODES = (401, 403)

HARD = "hard"
SOFT = "soft"

_SUBJECT_REASONS = {
    "spend_limit": "spend limit reached",
    "auth_error": "API key rejected",
    "billing_error": "billing problem",
    "consecutive_errors": "repeated API errors",
    "manual": "manually tripped",
}


@dataclass(frozen=True)
class BreakerStatus:
    """What `check()` found. `open` False = proceed (as the half-open probe when `probe` is True)."""

    open: bool
    kind: str = ""  # "hard" or "soft" when open
    retry_at: datetime | None = None  # soft only: when the next attempt may be made
    probe: bool = False  # closed answer only: this caller was let through as the single half-open probe
    reason: str = ""

    @property
    def closed(self):
        return not self.open

    @property
    def is_open(self):
        return self.open


# --- Classification -------------------------------------------------------------------------------------------

def is_spend_limit_error(*, status_code, error_type, error_code, message):
    """The documented shapes (prefix rule, and the 429 code), plus a fallback: any HTTP 400 invalid_request_error
    whose message mentions a usage or spend limit, so a rewording cannot turn a permanent condition into a soft loop."""
    if status_code == 400 and error_type == "invalid_request_error":
        message = message or ""
        lowered = message.lower()
        return message.startswith(SPEND_LIMIT_MESSAGE_PREFIX) or "usage limit" in lowered or "spend limit" in lowered
    if status_code == 429 and error_code == SPEND_LIMIT_ERROR_CODE:
        return True
    return False


def is_billing_error(*, status_code, error_type, message):
    """HTTP 402 / `billing_error` (docs api/errors), or the prepaid-credit-exhausted 400 (wording not documented, so
    matched loosely on 'credit balance')."""
    if status_code == 402 or error_type == "billing_error":
        return True
    return (
        status_code == 400
        and error_type == "invalid_request_error"
        and "credit balance" in (message or "").lower()
    )


def is_auth_error(*, status_code, error_type):
    return status_code in AUTH_STATUS_CODES or error_type in AUTH_ERROR_TYPES


# --- Reading state --------------------------------------------------------------------------------------------

def _peek(state, now):
    """The status for `state` at `now` WITHOUT claiming a probe."""
    if not state.breaker_tripped:
        return BreakerStatus(open=False)
    if state.trip_kind != SOFT:  # "hard", or a row written before the two tiers existed
        return BreakerStatus(open=True, kind=HARD, reason=state.trip_reason)
    if state.cooldown_until is not None and now < state.cooldown_until:
        return BreakerStatus(open=True, kind=SOFT, retry_at=state.cooldown_until, reason=state.trip_reason)
    if state.probe_in_flight and state.probe_started_at is not None:
        expires = state.probe_started_at + timedelta(seconds=settings.BREAKER_PROBE_TIMEOUT_SECONDS)
        if now <= expires:  # abandoned only when LONGER than the timeout
            return BreakerStatus(open=True, kind=SOFT, retry_at=expires, reason=state.trip_reason)
    return BreakerStatus(open=False, probe=True, reason=state.trip_reason)  # a probe may be claimed


def is_open():
    """True when a call made right now would be refused. Does not claim a probe."""
    return _peek(GuardState.load(), clock.now()).open


def check():
    """The gateway's question before every call. Returns a BreakerStatus: open (refuse), or closed (proceed). When the
    soft cooldown has elapsed this atomically claims the single probe and returns closed with `probe=True`."""
    with transaction.atomic():  # SQLite takes the write lock up front, so concurrent callers queue here
        state = GuardState.load()
        now = clock.now()
        status = _peek(state, now)
        if status.probe:
            state.probe_in_flight = True
            state.probe_started_at = now
            state.save()
        return status


def release_probe():
    """Give the probe back without an outcome (the call was refused or failed for a reason that says nothing about the
    API), so the next caller may claim it at once instead of waiting for the probe timeout."""
    with transaction.atomic():
        state = GuardState.load()
        if state.probe_in_flight:
            state.probe_in_flight = False
            state.probe_started_at = None
            state.save()


# --- Changing state -------------------------------------------------------------------------------------------

def _next_cooldown(state):
    base = int(settings.BREAKER_COOLDOWN_SECONDS)
    return min(max(state.cooldown_seconds * 2, base), int(settings.BREAKER_COOLDOWN_MAX_SECONDS))


def _hard_trip(reason, detail):
    with transaction.atomic():
        state = GuardState.load()
        was = _peek(state, clock.now())
        state.breaker_tripped = True
        state.trip_kind = HARD
        state.tripped_at = clock.now()
        state.trip_reason = reason
        state.trip_detail = detail
        state.cooldown_until = None
        state.probe_in_flight = False
        state.probe_started_at = None
        state.save()
    if not (was.open and was.kind == HARD):  # closed or soft -> hard
        _send_alert(HARD, reason, detail)


def _soft_trip(state, detail, *, reopen):
    """Call inside a transaction with `state` loaded. Returns True if an alert should follow."""
    now = clock.now()
    state.cooldown_seconds = _next_cooldown(state) if reopen else int(settings.BREAKER_COOLDOWN_SECONDS)
    state.breaker_tripped = True
    state.trip_kind = SOFT
    state.tripped_at = now
    state.trip_reason = "consecutive_errors"
    state.trip_detail = detail
    state.cooldown_until = now + timedelta(seconds=state.cooldown_seconds)
    state.probe_in_flight = False
    state.probe_started_at = None
    state.save()


def trip(reason, detail=""):
    """Trip by hand or from code. `consecutive_errors` is the soft tier; every other reason is hard."""
    if reason == "consecutive_errors":
        with transaction.atomic():
            state = GuardState.load()
            was_open = _peek(state, clock.now()).open
            _soft_trip(state, detail, reopen=was_open)
        if not was_open:
            _send_alert(SOFT, reason, detail)
        return
    _hard_trip(reason, detail)


def reset():
    """Close the breaker and clear everything: state, cooldown, probe and counts."""
    with transaction.atomic():
        state = GuardState.load()
        state.breaker_tripped = False
        state.trip_kind = ""
        state.tripped_at = None
        state.trip_reason = ""
        state.trip_detail = ""
        state.consecutive_errors = 0
        state.last_error_at = None
        state.cooldown_until = None
        state.cooldown_seconds = 0
        state.probe_in_flight = False
        state.probe_started_at = None
        state.save()


def record_success():
    """Any successful call (including an unusable but billed output). Closes a soft-tripped breaker (this was the
    probe) and resets the error count and the backoff to its base."""
    with transaction.atomic():
        state = GuardState.load()
        if state.breaker_tripped and not (state.trip_kind == SOFT and state.probe_in_flight):
            # Only reset_breaker clears a hard trip, and only the probe's success closes a soft one; a success from
            # a call that was already in flight when the breaker tripped must not.
            return
        fresh = {
            "breaker_tripped": False,
            "trip_kind": "",
            "tripped_at": None,
            "trip_reason": "",
            "trip_detail": "",
            "cooldown_until": None,
            "probe_in_flight": False,
            "probe_started_at": None,
            "consecutive_errors": 0,
            "last_error_at": None,
        }
        if state.cooldown_seconds:
            fresh["cooldown_seconds"] = int(settings.BREAKER_COOLDOWN_SECONDS)
        if any(getattr(state, name) != value for name, value in fresh.items()):
            for name, value in fresh.items():
                setattr(state, name, value)
            state.save()


def record_error(*, status_code, error_type, error_code, message):
    head = f"HTTP {status_code}" if status_code is not None else "no HTTP status"
    detail = f"{head} {error_type}{f' ({error_code})' if error_code else ''}: {message}"
    if is_billing_error(status_code=status_code, error_type=error_type, message=message) and not (
        is_spend_limit_error(status_code=status_code, error_type=error_type, error_code=error_code, message=message)
        and (message or "").startswith(SPEND_LIMIT_MESSAGE_PREFIX)
    ):
        _hard_trip("billing_error", detail)
        return
    if is_spend_limit_error(status_code=status_code, error_type=error_type, error_code=error_code, message=message):
        _hard_trip("spend_limit", detail)
        return
    if is_auth_error(status_code=status_code, error_type=error_type):
        _hard_trip("auth_error", detail)
        return

    alert = False
    with transaction.atomic():
        state = GuardState.load()
        now = clock.now()
        if state.breaker_tripped:
            # Hard-tripped: nothing to do. Soft-tripped: only a failed probe matters (re-open, longer cooldown);
            # an error from a call that was already in flight when it tripped changes nothing.
            if state.trip_kind == SOFT and state.probe_in_flight:
                _soft_trip(state, detail, reopen=True)
                alert = True
        else:
            window = timedelta(seconds=settings.BREAKER_ERROR_WINDOW_SECONDS)
            if state.last_error_at is not None and state.consecutive_errors and now - state.last_error_at > window:
                state.consecutive_errors = 0
            state.consecutive_errors += 1
            state.last_error_at = now
            if state.consecutive_errors >= settings.BREAKER_MAX_CONSECUTIVE_ERRORS:
                _soft_trip(state, f"{state.consecutive_errors} API errors in a row; last: {detail}", reopen=False)
                alert = True
            else:
                state.save()
    if alert:
        _send_alert(SOFT, "consecutive_errors", detail)


# --- Alerts ---------------------------------------------------------------------------------------------------

def _scrub(text):
    return scrub(text)[:600]


def _alert_body(kind, reason, detail, state, now):
    lines = [
        f"The forum's AI moderator paused itself at {now:%Y-%m-%d %H:%M:%S} UTC.",
        "",
        f"Reason: {_SUBJECT_REASONS.get(reason, reason)} ({reason}).",
        f"What happened: {_scrub(detail) or 'no detail recorded'}",
        "",
        "What this means: moderation is paused, so no AI moderator posts will appear. Posting and reading in the "
        "forum still work.",
        "",
    ]
    if kind == SOFT:
        when = f"{state.cooldown_until:%Y-%m-%d %H:%M:%S} UTC" if state.cooldown_until else "shortly"
        lines += [
            "What to do: nothing is required. The moderator retries by itself; the next attempt is at "
            f"{when}. If the attempt fails it waits longer, up to {settings.BREAKER_COOLDOWN_MAX_SECONDS // 60} "
            "minutes between attempts.",
        ]
    elif reason == "spend_limit":
        lines += [
            "What to do: check the spend limits in the Anthropic Console (Settings > Billing, and the workspace's "
            "Spend limits). Once the limit is raised or the new month has begun, resume with "
            "`python manage.py reset_breaker`, or with the admin page.",
        ]
    elif reason == "billing_error":
        lines += [
            "What to do: check billing and the credit balance in the Anthropic Console (Plans & Billing). Once it is "
            "fixed, resume with `python manage.py reset_breaker`, or with the admin page.",
        ]
    elif reason == "auth_error":
        lines += [
            "What to do: check the API key in .env (it may be revoked, expired or from the wrong workspace). Then "
            "resume with `python manage.py reset_breaker`, or with the admin page.",
        ]
    else:
        lines += ["What to do: resume with `python manage.py reset_breaker`, or with the admin page."]
    return "\n".join(lines) + "\n"


def _send_alert(kind, reason, detail):
    """Email the alert address. Never raises; failures are logged and stored in GuardState.last_alert_error."""
    try:
        address = settings.ALERT_EMAIL
        if not address:
            logger.warning("circuit breaker tripped (%s: %s); ALERT_EMAIL is not set, so no email was sent", reason, detail)
            return
        now = clock.now()
        state = GuardState.load()
        if kind == SOFT and state.last_alert_at is not None:
            if now - state.last_alert_at < timedelta(seconds=settings.ALERT_MIN_SECONDS_BETWEEN_EMAILS):
                return
        subject = f"[Forum] AI moderator paused: {_SUBJECT_REASONS.get(reason, reason)}"
        try:
            sent = EmailMessage(subject, _alert_body(kind, reason, detail, state, now), None, [address]).send(
                using="default"
            )
            error = "" if sent else "the mailer reported no message sent"
        except Exception as exc:
            logger.exception("could not send the circuit breaker alert email")
            sent, error = 0, f"{type(exc).__name__}: {_scrub(exc)}"
        update = {"last_alert_error": error}
        if sent:
            update["last_alert_at"] = now
        GuardState.objects.filter(pk=1).update(**update)
    except Exception:
        logger.exception("circuit breaker alert failed")
