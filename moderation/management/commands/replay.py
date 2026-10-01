"""`manage.py replay`: load scripted transcripts as synthetic conversations and replay the moderation pipeline on them.

    manage.py replay --experiment pilot --dry-run                     # no write, no API call: the plan and a worst-case cost
    manage.py replay --experiment pilot --set 'rent_*' --max-usd 0.20  # recorded as skipped_disabled (LLM calls are off)
    manage.py replay --experiment pilot --set 'rent_*' --max-usd 0.20 --live

Without `--live` the command runs with LLM calls switched off for this process only, so every run is recorded
`skipped_disabled` and nothing is spent. `--live` turns LLM calls on for this process only (a process-local
`override_settings(LLM_ENABLED=True)`; config/tunables.py is untouched), and only when `--max-usd` is given and within the
remaining evaluation budget, an API key is configured, the worst-case estimate and the cap have been printed, and the
operator has typed `yes` (or passed `--yes`). Anything else is refused. The command never reads `.env` or prints a key. Spend is counted in the ledger under `purpose="replay"` against
BUDGET_EVAL_USD_TOTAL, and `--max-usd` may not exceed what is left of it. The library is moderation/replay.py.
"""
import fnmatch
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from moderation import budget, replay
from moderation.errors import LLMRefused
from moderation import transcripts as transcript_files


def usd(value):
    return f"${Decimal(value):.4f}"


class Command(BaseCommand):
    help = (
        "Load golden transcripts as synthetic conversations and replay the moderation pipeline on them (kind=replay, "
        "never posts). Use --dry-run first: it writes nothing and makes no API call."
    )

    def add_arguments(self, parser):
        parser.add_argument("--experiment", required=True, help="Experiment name (created or reused; loading is idempotent).")
        parser.add_argument("--directory", default=None, help="Folder of transcript files (default: golden/transcripts).")
        parser.add_argument(
            "--set", dest="sets", action="append", metavar="PATTERN",
            help="Only transcript ids matching this glob (repeatable). Default: every transcript.",
        )
        parser.add_argument("--assignments", choices=replay.ASSIGNMENT_CHOICES, default="both", help="Label assignments (default: both).")
        parser.add_argument("--replicates", type=int, default=1, help="Runs per conversation (default: 1).")
        parser.add_argument("--max-usd", default=None, help="Most this call may spend (required unless --dry-run).")
        parser.add_argument("--dry-run", action="store_true", help="Write nothing and make no API call; print the plan and worst-case cost.")
        parser.add_argument(
            "--live", action="store_true",
            help="Make real API calls for this process (needs --max-usd, a key and a typed 'yes' or --yes).",
        )
        parser.add_argument("--yes", action="store_true", help="With --live: skip the typed confirmation (for scripts).")

    # --- Argument handling ---------------------------------------------------------------------------------------------

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

    def _select(self, files, patterns):
        if not patterns:
            return files
        selected = [(path, data) for path, data in files if any(fnmatch.fnmatchcase(data["id"], p) for p in patterns)]
        unmatched = [p for p in patterns if not any(fnmatch.fnmatchcase(data["id"], p) for _path, data in files)]
        if unmatched:
            raise CommandError(f"--set pattern(s) matching no transcript: {', '.join(unmatched)}")
        return selected

    def handle(self, *args, **options):
        out = self.stdout.write
        name = (options["experiment"] or "").strip()
        if not name:
            raise CommandError("--experiment needs a name")
        replicates = options["replicates"]
        if replicates < 1:
            raise CommandError("--replicates must be at least 1")
        dry_run = options["dry_run"]
        max_usd = self._parse_max_usd(options["max_usd"], required=not dry_run)

        files = transcript_files.load_transcript_files(options["directory"])  # a malformed file or duplicate id is an error
        if not files:
            raise CommandError(f"no transcripts found in {options['directory'] or transcript_files.TRANSCRIPTS_DIR}")
        known = {data["id"]: data for _path, data in files}
        selected = self._select(files, options["sets"])
        if not selected:
            raise CommandError("no transcripts selected")

        try:
            if dry_run:
                return self._dry_run(name, selected, known, options["assignments"], replicates, max_usd)
            return self._run(name, selected, known, options, replicates, max_usd)
        except LLMRefused as exc:  # an unknown model in the tunables (ModelNotAllowed)
            raise CommandError(str(exc)) from exc

    # --- Dry run -------------------------------------------------------------------------------------------------------

    def _dry_run(self, name, selected, known, assignments, replicates, max_usd):
        out = self.stdout.write
        plan = replay.dry_run_plan(name, selected, assignments=assignments, replicates=replicates, known=known)
        out(f"DRY RUN: nothing is written and no API call is made. Experiment: {plan.experiment_name}.")
        out(f"Transcripts: {len(plan.transcript_ids)} ({', '.join(plan.transcript_ids)})")
        out(f"Label assignments: {', '.join(plan.assignments)}. Replicates: {plan.replicates}.")
        out(f"Models: Master {settings.MASTER_MODEL}, Intervenor {settings.INTERVENOR_MODEL}.")
        for entry in plan.conversations:
            state = "exists" if entry["exists"] else "would be created"
            out(
                f"  {entry['key']}: conversation {state}, {entry['runs'] - entry['runs_done']} of {entry['runs']} run(s) to do, "
                f"worst case {usd(entry['worst_case_usd'])}"
            )
        out(f"Conversations that would be created: {plan.conversations_to_create} of {len(plan.conversations)}.")
        out(f"Runs that a real call would make: {plan.runs_to_do} (each is a Master call and, if it finds a valid issue, an Intervenor call).")
        out(
            f"Estimated worst-case cost: {usd(plan.worst_case_usd)} (every call priced as if all input were uncached and the "
            "whole reply allowance were used; real cost is normally well below this)."
        )
        remaining = self._remaining_budget()
        out(f"Evaluation budget left (BUDGET_EVAL_USD_TOTAL minus replay and judge spend): {usd(remaining)}.")
        if max_usd is not None:
            out(f"This call's limit (--max-usd): {usd(max_usd)}.")
            if plan.worst_case_usd > max_usd:
                out(f"WARNING: the worst case ({usd(plan.worst_case_usd)}) is above --max-usd ({usd(max_usd)}); a real call would stop early.")
            if max_usd > remaining:
                out(f"WARNING: --max-usd is above the evaluation budget left ({usd(remaining)}); a real call would be refused.")
        if plan.worst_case_usd > remaining:
            out(f"WARNING: the worst case is above the evaluation budget left ({usd(remaining)}).")

    def _remaining_budget(self):
        return Decimal(settings.BUDGET_EVAL_USD_TOTAL) - budget.spend(purposes=budget.EVAL_PURPOSES)

    # --- A real (or switched-off) run ------------------------------------------------------------------------------------

    def _run(self, name, selected, known, options, replicates, max_usd):
        out = self.stdout.write
        replay.validate_transcripts(selected, known=known)  # before any write or guard, like every replay
        remaining = self._remaining_budget()
        if max_usd > remaining:
            raise CommandError(
                f"--max-usd {usd(max_usd)} is above the evaluation budget left ({usd(remaining)} of "
                f"BUDGET_EVAL_USD_TOTAL {usd(settings.BUDGET_EVAL_USD_TOTAL)}); lower it"
            )
        live = options["live"]
        if live:
            if not settings.ANTHROPIC_API_KEY:
                raise CommandError("no ANTHROPIC_API_KEY is configured (it belongs in .env); a live replay is refused")
            estimate = replay.dry_run_plan(
                name, selected, assignments=options["assignments"], replicates=replicates, known=known
            )
            out(
                f"LIVE replay of experiment {name!r}: {estimate.runs_to_do} run(s), worst-case cost "
                f"{usd(estimate.worst_case_usd)}, cap for this call {usd(max_usd)} "
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
            out("LLM calls are OFF for this call (no --live): every run will be recorded skipped_disabled and nothing is spent.")
            switch = replay.llm_switched_off()

        with switch:
            plan = replay.load_experiment(
                name, selected, assignments=options["assignments"], replicates=replicates, known=known
            )
            specs = replay.plan_runs(plan)
            out(
                f"Experiment {name!r}: {len(plan.conversations)} conversation(s) "
                f"({len(plan.created_conversations)} newly created), {len(specs)} run(s) planned, "
                f"limit for this call {usd(max_usd)}."
            )
            report = replay.execute_runs(specs, max_usd=max_usd, on_result=self._progress)
        out(report.summary())

    def _progress(self, result):
        self.stdout.write(
            f"  {result.spec.transcript_id} replicate {result.spec.replicate}: {result.status}"
            + (f" ({result.failure_reason})" if result.failure_reason else "")
            + f", {usd(result.cost)}"
        )

