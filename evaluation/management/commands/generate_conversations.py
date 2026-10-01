"""`manage.py generate_conversations`: write the base conversations of the seeded-error experiment and build their transcripts.

    manage.py generate_conversations --dry-run                       # no write, no API call: the plan and a worst case
    manage.py generate_conversations --max-usd 2                     # LLM calls OFF: nothing is generated or written
    manage.py generate_conversations --max-usd 2 --live              # real calls (needs a key and a typed 'yes')

Bases are saved to `generated/bases/` and reused on later runs (unless `--overwrite`); transcripts go to
`generated/transcripts/` (or `--output-dir`). Without `--live` the command runs with LLM calls switched off for this process
only; `--live` turns them on for this process only (a process-local `override_settings(LLM_ENABLED=True)`), the same guard as
`replay --live`. Spend is counted in the ledger as `purpose="replay"`. The command never prints a key or a message text.
"""
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from moderation import budget
from seeding.generate import LLMRefused  # re-exported so commands never import moderation.errors
from seeding import arms, generate
from seeding.facts import load_facts


def usd(value):
    return f"${Decimal(value):.4f}"


class Command(BaseCommand):
    help = (
        "Generate the base conversations (one per fact and side) for the seeded-error experiment and build the replay "
        "transcripts. Use --dry-run first: it writes nothing and makes no API call."
    )

    def add_arguments(self, parser):
        parser.add_argument("--max-usd", default=None, help="Most this call may spend (required unless --dry-run).")
        parser.add_argument("--dry-run", action="store_true", help="Write nothing and make no API call; print the plan and worst-case cost.")
        parser.add_argument("--live", action="store_true", help="Make real API calls for this process (needs --max-usd, a key and a typed 'yes' or --yes).")
        parser.add_argument("--yes", action="store_true", help="With --live: skip the typed confirmation (for scripts).")
        parser.add_argument("--facts", default=None, help="Comma-separated fact ids (default: every ready fact).")
        parser.add_argument("--overwrite", action="store_true", help="Regenerate bases and overwrite existing transcript files.")
        parser.add_argument("--output-dir", default=None, help="Where to write the transcripts (default: generated/transcripts).")

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

    def _remaining_budget(self):
        return Decimal(settings.BUDGET_EVAL_USD_TOTAL) - budget.spend(purposes=budget.EVAL_PURPOSES)

    def _facts(self, raw):
        facts = load_facts()
        if raw is not None:
            wanted = [i.strip() for i in raw.split(",") if i.strip()]
            if not wanted:
                raise CommandError("--facts needs at least one fact id")
            known = {f.id: f for f in facts}
            missing = [i for i in wanted if i not in known]
            if missing:
                raise CommandError(f"unknown fact id(s): {', '.join(missing)}")
            facts = [known[i] for i in dict.fromkeys(wanted)]
        ready = [f for f in facts if generate.usable(f)]
        if not ready:
            raise CommandError("no ready fact to generate for")
        for fact in facts:
            if not generate.usable(fact):
                self.stdout.write(f"Skipping {fact.id}: not ready or no subject.")
        return ready

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        max_usd = self._parse_max_usd(options["max_usd"], required=not dry_run)
        facts = self._facts(options["facts"])
        try:
            items = generate.plan_generation(facts)
        except LLMRefused as exc:  # a model that is not in the price table
            raise CommandError(str(exc)) from exc
        if dry_run:
            return self._dry_run(facts, items, max_usd)
        return self._run(facts, items, max_usd, options)

    def _describe(self, facts, items):
        out = self.stdout.write
        worst = sum((i.worst_case_usd for i in items), Decimal("0"))
        out(f"Facts: {len(facts)}. Calls planned: {len(items)} (one left base and one mirrored right base per fact).")
        out(
            f"Estimated worst-case cost: {usd(worst)} (every call priced as if all input were uncached and the whole reply "
            "allowance were used; real cost is normally well below this)."
        )
        return worst

    def _dry_run(self, facts, items, max_usd):
        out = self.stdout.write
        out("DRY RUN: nothing is written and no API call is made.")
        worst = self._describe(facts, items)
        remaining = self._remaining_budget()
        out(f"Evaluation budget left (BUDGET_EVAL_USD_TOTAL minus replay and judge spend): {usd(remaining)}.")
        if max_usd is not None:
            out(f"This call's limit (--max-usd): {usd(max_usd)}.")
            if worst > max_usd:
                out(f"WARNING: the worst case ({usd(worst)}) is above --max-usd ({usd(max_usd)}); a real call would stop early.")
            if max_usd > remaining:
                out(f"WARNING: --max-usd is above the evaluation budget left ({usd(remaining)}); a real call would be refused.")
        if worst > remaining:
            out(f"WARNING: the worst case is above the evaluation budget left ({usd(remaining)}).")

    def _run(self, facts, items, max_usd, options):
        out = self.stdout.write
        remaining = self._remaining_budget()
        if max_usd > remaining:
            raise CommandError(
                f"--max-usd {usd(max_usd)} is above the evaluation budget left ({usd(remaining)} of "
                f"BUDGET_EVAL_USD_TOTAL {usd(settings.BUDGET_EVAL_USD_TOTAL)}); lower it"
            )
        worst = sum((i.worst_case_usd for i in items), Decimal("0"))
        if options["live"]:
            if not settings.ANTHROPIC_API_KEY:
                raise CommandError("no ANTHROPIC_API_KEY is configured (it belongs in .env); a live run is refused")
            out(
                f"LIVE generation: up to {len(items)} call(s), worst-case cost {usd(worst)}, cap for this call {usd(max_usd)} "
                f"(evaluation budget left {usd(remaining)})."
            )
            if not options["yes"]:
                try:
                    answer = input("Type 'yes' to make real API calls: ")
                except EOFError:
                    answer = ""
                if answer.strip() != "yes":
                    raise CommandError("not confirmed; no call was made")
            switch = override_settings(LLM_ENABLED=True)
        else:
            out("LLM calls are OFF for this call (no --live): nothing will be generated or spent (saved bases are still reused).")
            switch = override_settings(LLM_ENABLED=False)

        self._describe(facts, items)
        overwrite = options["overwrite"]
        with switch:
            report = generate.run_generation(facts, max_usd=max_usd, overwrite=overwrite, on_result=self._progress)
        out(report.summary())
        if report.transcripts:
            to_write, unchanged, conflicts = arms.classify_transcripts(report.transcripts, options["output_dir"])
            try:
                paths = arms.write_transcripts(report.transcripts, options["output_dir"], overwrite=overwrite)
            except FileExistsError as exc:
                raise CommandError(f"{exc}") from exc
            written = len(to_write) + len(conflicts)
            out(f"Wrote {written} transcript(s) to {paths[0].parent}; {len(unchanged)} already up to date (unchanged).")

    def _progress(self, fact, side, result):
        self.stdout.write(f"  {fact.id} {side} base: {usd(result.cost_usd)}")
