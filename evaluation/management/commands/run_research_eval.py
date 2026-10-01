"""`manage.py run_research_eval`: run the web-search research step on the seeded replay conversations.

    manage.py run_research_eval --experiment pilot1 --dry-run                # no call: acts to run and worst-case cost
    manage.py run_research_eval --experiment pilot1 --max-usd 3 --live       # real calls (needs a key and a typed 'yes')

Without `--live` the command runs with LLM calls switched off for this process only. It never prints a key or a message text.
"""
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from moderation import budget
from seeding import research_eval as re_
from seeding.research_eval import LLMRefused  # re-exported so commands never import moderation.errors


def usd(value):
    return f"${Decimal(value):.4f}"


class Command(BaseCommand):
    help = "Run the research step on every replayed conversation with an eligible act. Use --dry-run first (no API call)."

    def add_arguments(self, parser):
        parser.add_argument("--experiment", required=True, help="Name of the replay experiment.")
        parser.add_argument("--max-usd", default=None, help="Most this call may spend (required unless --dry-run).")
        parser.add_argument("--dry-run", action="store_true", help="Make no API call and write nothing; print the plan and worst case.")
        parser.add_argument("--live", action="store_true", help="Make real API calls (needs --max-usd, a key and a typed 'yes' or --yes).")
        parser.add_argument("--yes", action="store_true", help="With --live: skip the typed confirmation.")
        parser.add_argument("--set", action="append", default=[], dest="sets", metavar="GLOB",
                            help="Only transcript ids matching this glob (repeatable).")
        parser.add_argument("--retry-failed", action="store_true", help="Run again acts whose research run failed.")

    def _parse_max_usd(self, raw, *, required):
        if raw is None:
            if required:
                raise CommandError("--max-usd is required (unless --dry-run): the most this call may spend, in US dollars")
            return None
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            raise CommandError(f"--max-usd must be a number of dollars, not {raw!r}") from None
        if not value.is_finite() or value <= 0:
            raise CommandError("--max-usd must be greater than zero")
        return value

    def handle(self, *args, **options):
        name = options["experiment"]
        dry_run = options["dry_run"]
        max_usd = self._parse_max_usd(options["max_usd"], required=not dry_run)
        try:
            acts, extra = re_.eligible(name)
            todo = re_.plan_research(name, retry_failed=options["retry_failed"], sets=tuple(options["sets"]))
            if not acts:
                raise CommandError(f"experiment {name!r} has no conversation with an eligible moderator act")
            worst = sum((re_.estimate_research_usd(a) for a in todo), Decimal("0"))
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        except LLMRefused as exc:
            raise CommandError(str(exc)) from exc
        out = self.stdout.write
        remaining = Decimal(settings.BUDGET_EVAL_USD_TOTAL) - budget.spend(purposes=budget.EVAL_PURPOSES)
        out(f"Eligible conversations: {len(acts)} ({extra} further eligible act(s) left out: one per conversation); "
            f"to run: {len(todo)}; already done or held back: {len(acts) - len(todo)}.")
        out(f"Estimated worst-case cost: {usd(worst)} (tokens plus up to {settings.RESEARCH_MAX_USES} searches at "
            f"{usd(re_.SEARCH_FEE_USD)} each per call; real cost is normally well below this).")
        if dry_run:
            out("DRY RUN: nothing is written and no API call is made.")
            out(f"Evaluation budget left (BUDGET_EVAL_USD_TOTAL minus replay, research and judge spend): {usd(remaining)}.")
            if max_usd is not None and worst > max_usd:
                out(f"WARNING: the worst case ({usd(worst)}) is above --max-usd ({usd(max_usd)}); a real run would stop early.")
            if worst > remaining:
                out(f"WARNING: the worst case is above the evaluation budget left ({usd(remaining)}).")
            return
        if max_usd > remaining:
            raise CommandError(f"--max-usd {usd(max_usd)} is above the evaluation budget left ({usd(remaining)}); lower it")
        if options["live"]:
            if not settings.ANTHROPIC_API_KEY:
                raise CommandError("no ANTHROPIC_API_KEY is configured (it belongs in .env); a live run is refused")
            out(f"LIVE research: up to {len(todo)} call(s), worst-case cost {usd(worst)}, cap for this call {usd(max_usd)}.")
            if not options["yes"]:
                try:
                    answer = input("Type 'yes' to make real API calls: ")
                except EOFError:
                    answer = ""
                if answer.strip() != "yes":
                    raise CommandError("not confirmed; no call was made")
            switch = override_settings(LLM_ENABLED=True)
        else:
            out("LLM calls are OFF for this call (no --live): nothing will be run or spent.")
            switch = override_settings(LLM_ENABLED=False)
        with switch:
            report = re_.run_research_eval(todo, max_usd=max_usd, on_result=self._progress)
        out(report.summary())

    def _progress(self, act, run, cost):
        self.stdout.write(f"  {act.conversation_id}: {run.status}, {usd(cost)}")
