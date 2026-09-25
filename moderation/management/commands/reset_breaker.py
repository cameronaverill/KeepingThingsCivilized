"""`manage.py reset_breaker`: close the circuit breaker by hand."""
from django.core.management.base import BaseCommand

from moderation import breaker
from moderation.models import GuardState


class Command(BaseCommand):
    help = "Close the circuit breaker (it never closes by itself) and say what had tripped it."

    def handle(self, *args, **options):
        out = self.stdout.write
        state = GuardState.load()
        if state.breaker_tripped:
            when = f"{state.tripped_at:%Y-%m-%d %H:%M:%S} UTC" if state.tripped_at else "an unknown time"
            kind = state.trip_kind or "hard"
            out(f"The breaker was tripped ({kind}) at {when}. Reason: {state.trip_reason or 'unknown'}.")
            if kind == "soft":
                cleared = f"cooldown {state.cooldown_seconds} s"
                if state.probe_in_flight:
                    cleared += ", a probe call in flight"
                out(f"Clearing: {cleared}, {state.consecutive_errors} counted errors.")
            if state.trip_detail:
                out(f"Detail: {state.trip_detail}")
        else:
            out("The breaker was not tripped.")
        was_spend_limit = state.breaker_tripped and state.trip_reason == "spend_limit"
        breaker.reset()
        out("The breaker is now closed.")
        if was_spend_limit:
            out(
                "It tripped on a SPEND LIMIT error. Before you switch LLM_ENABLED back on, confirm in the Anthropic "
                "Console (Settings > Billing and the workspace's Spend limits) that the limit is raised or the new "
                "month has begun; otherwise the next call will fail and trip the breaker again."
            )
