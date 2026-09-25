"""`manage.py budget`: read-only report of the caps, the spend so far and the worst-case cost of a moderation run."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import F

from config import tunables
from moderation import budget, clock
from moderation.models import GuardState, LLMCall


def usd(value):
    return f"${Decimal(value):.6f}"


class Command(BaseCommand):
    help = "Show the budget caps, the spend so far, and the worst-case cost of one moderation run. Changes nothing."

    def handle(self, *args, **options):
        out = self.stdout.write
        out(f"Tunables file (edit caps here): {tunables.__file__}")
        out("")
        out(f"Kill switch (LLM_ENABLED): {'ON' if settings.LLM_ENABLED else 'OFF (no LLM calls are made)'}")
        out(f"API key configured: {'yes' if settings.ANTHROPIC_API_KEY else 'no'}")
        state = GuardState.load()
        if state.breaker_tripped:
            kind = state.trip_kind or "hard"
            tripped_at = f"{state.tripped_at:%Y-%m-%d %H:%M:%S} UTC" if state.tripped_at else "unknown"
            out(f"Circuit breaker: TRIPPED ({kind}), reason {state.trip_reason}, tripped at {tripped_at}.")
            if state.trip_detail:
                out(f"  Detail: {state.trip_detail}")
            if kind == "soft":
                until = f"{state.cooldown_until:%Y-%m-%d %H:%M:%S} UTC" if state.cooldown_until else "unknown"
                out(f"  Cooldown until: {until} ({state.cooldown_seconds} s); retries by itself.")
                out(f"  Probe in flight: {'yes' if state.probe_in_flight else 'no'}")
            else:
                out("  Needs a human: run `manage.py reset_breaker` after checking why it tripped.")
        else:
            out(f"Circuit breaker: closed ({state.consecutive_errors} consecutive errors recorded)")
        out(f"Alert email: {'configured' if settings.ALERT_EMAIL else 'not configured'}")
        if state.last_alert_at:
            out(f"  Last alert sent: {state.last_alert_at:%Y-%m-%d %H:%M:%S} UTC")
        else:
            out("  Last alert sent: never")
        if state.last_alert_error:
            out(f"  Last alert error: {state.last_alert_error}")
        out("")

        start, end = budget.utc_day_bounds()
        site_total = budget.spend(purposes=budget.SITE_PURPOSES)
        site_today = budget.spend(purposes=budget.SITE_PURPOSES, since=start, until=end)
        eval_total = budget.spend(purposes=budget.EVAL_PURPOSES)

        out("Caps, spent, remaining (USD):")
        rows = [
            ("Site total, all time", settings.BUDGET_SITE_USD_TOTAL, site_total),
            (f"Site per day (UTC day {start:%Y-%m-%d})", settings.BUDGET_SITE_USD_PER_DAY, site_today),
            ("Evaluation total (replay, judge)", settings.BUDGET_EVAL_USD_TOTAL, eval_total),
        ]
        for label, cap, spent in rows:
            out(f"  {label}: cap {usd(cap)}, spent {usd(spent)}, remaining {usd(max(cap - spent, 0))}")
        out(f"  Per conversation (moderation): cap {usd(settings.BUDGET_PER_CONVERSATION_USD)} each")

        out("")
        out("Spend per conversation (moderation), top 5:")
        per_conversation = []
        conversation_ids = (
            LLMCall.objects.filter(purpose="moderation")
            .exclude(conversation_id__isnull=True)
            .values_list("conversation_id", flat=True)
            .distinct()
        )
        for conversation_id in conversation_ids:
            per_conversation.append(
                (budget.spend(purposes=("moderation",), conversation_id=conversation_id), conversation_id)
            )
        per_conversation.sort(reverse=True)
        if per_conversation:
            for spent, conversation_id in per_conversation[:5]:
                remaining = max(settings.BUDGET_PER_CONVERSATION_USD - spent, 0)
                out(f"  conversation {conversation_id}: spent {usd(spent)}, remaining {usd(remaining)}")
        else:
            out("  (none)")

        out("")
        cutoff = clock.now() - timedelta(minutes=settings.PENDING_CALL_STALE_MINUTES)
        stale = LLMCall.objects.filter(status="pending", created_at__lt=cutoff).order_by("created_at")
        out(f"Pending calls older than {settings.PENDING_CALL_STALE_MINUTES} minutes (a crash may have left them): {stale.count()}")
        for call in stale[:20]:
            out(f"  call {call.pk}: {call.purpose}/{call.agent} {call.model}, reserved {usd(call.reserved_usd)}, since {call.created_at:%Y-%m-%d %H:%M:%S} UTC")

        over = LLMCall.objects.filter(status__in=("ok", "error"), cost_usd__gt=F("reserved_usd")).order_by("pk")
        out(f"Calls whose real cost exceeded their reservation (the estimate was too low): {over.count()}")
        for call in over[:20]:
            out(f"  call {call.pk}: {call.purpose}/{call.agent} {call.model}, reserved {usd(call.reserved_usd)}, cost {usd(call.cost_usd)}")

        out("")
        out("Worst-case cost of one moderation run (one Master call plus one Intervenor call), by model:")
        for model, costs in budget.worst_case_run_cost().items():
            out(
                f"  {model}: Master {usd(costs['master'])} + Intervenor {usd(costs['intervenor'])} = {usd(costs['total'])}"
            )
        out(
            f"  (assumes {settings.TRANSCRIPT_MAX_MESSAGES} messages of {settings.MAX_MESSAGE_CHARS} characters, "
            f"a {settings.SYSTEM_PROMPT_TOKENS_ESTIMATE}-token system prompt, and no cache hits)"
        )
