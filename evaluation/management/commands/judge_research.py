"""`manage.py judge_research`: grade the research notes of a replay experiment against the true facts.

    manage.py judge_research --experiment pilot1 --dry-run
    manage.py judge_research --experiment pilot1 --max-usd 1 --live

Results are appended to `generated/judgments/research_<experiment>.jsonl`; cases already judged are skipped unless `--overwrite`.
Without `--live` LLM calls are switched off for this process. The command never prints a key or a note text.
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
    help = "Judge the research notes of a replay experiment. Use --dry-run first (no API call)."

    def add_arguments(self, parser):
        parser.add_argument("--experiment", required=True, help="Name of the replay experiment.")
        parser.add_argument("--max-usd", default=None, help="Most this call may spend (required unless --dry-run).")
        parser.add_argument("--dry-run", action="store_true", help="Make no API call and write nothing; print the plan and worst case.")
        parser.add_argument("--live", action="store_true", help="Make real API calls (needs --max-usd, a key and a typed 'yes' or --yes).")
        parser.add_argument("--yes", action="store_true", help="With --live: skip the typed confirmation.")
        parser.add_argument("--overwrite", action="store_true", help="Judge again cases that already have a judgment.")

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
            cases, unposted = re_.collect(name)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if not cases:
            raise CommandError(f"experiment {name!r} has no finished research note to judge")
        path = re_.judgments_path(name)
        done = set() if options["overwrite"] else re_.load_done(path)
        todo = [c for c in cases if (c.conversation_id, c.assignment, re_.PROMPT_VERSION) not in done]
        calls = [c for c in todo if re_.needs_call(c)]
        try:
            worst = sum((re_.estimate_call_usd(c) for c in calls), Decimal("0"))
        except LLMRefused as exc:
            raise CommandError(str(exc)) from exc
        out = self.stdout.write
        remaining = Decimal(settings.BUDGET_EVAL_USD_TOTAL) - budget.spend(purposes=budget.EVAL_PURPOSES)
        if unposted:
            out(f"Not judged: {len(unposted)} research run(s) without a posted note: {', '.join(unposted)}.")
        out(f"Notes: {len(cases)}; already judged: {len(cases) - len(todo)}; to judge: {len(todo)} ({len(calls)} need an LLM call).")
        out(f"Estimated worst-case cost: {usd(worst)} (real cost is normally well below this).")
        if dry_run:
            out("DRY RUN: nothing is written and no API call is made.")
            out(f"Evaluation budget left (BUDGET_EVAL_USD_TOTAL minus replay and judge spend): {usd(remaining)}.")
            if max_usd is not None and worst > max_usd:
                out(f"WARNING: the worst case ({usd(worst)}) is above --max-usd ({usd(max_usd)}); a real call would stop early.")
            if worst > remaining:
                out(f"WARNING: the worst case is above the evaluation budget left ({usd(remaining)}).")
            return
        if max_usd > remaining:
            raise CommandError(f"--max-usd {usd(max_usd)} is above the evaluation budget left ({usd(remaining)}); lower it")
        if options["live"]:
            if not settings.ANTHROPIC_API_KEY:
                raise CommandError("no ANTHROPIC_API_KEY is configured (it belongs in .env); a live run is refused")
            out(f"LIVE judging: up to {len(calls)} call(s), worst-case cost {usd(worst)}, cap for this call {usd(max_usd)}.")
            if not options["yes"]:
                try:
                    answer = input("Type 'yes' to make real API calls: ")
                except EOFError:
                    answer = ""
                if answer.strip() != "yes":
                    raise CommandError("not confirmed; no call was made")
            switch = override_settings(LLM_ENABLED=True)
        else:
            out("LLM calls are OFF for this call (no --live): nothing will be judged by the model or spent.")
            switch = override_settings(LLM_ENABLED=False)
        with switch:
            report = re_.run_judging(cases, max_usd=max_usd, path=path, overwrite=options["overwrite"],
                                     on_result=self._progress)
        out(report.summary())
        if report.no_note:
            out(f"No call (empty note): {', '.join(report.no_note)}.")
        out(f"Judgments file: {path}.")

    def _progress(self, case, judgment, result):
        self.stdout.write(f"  {case.conversation_id} ({case.assignment}): tag {judgment.tag}, {judgment.verdict}, {usd(result.cost_usd)}")
