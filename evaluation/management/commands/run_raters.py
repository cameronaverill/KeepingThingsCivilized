"""`manage.py run_raters`: have a panel's LLM raters rate the user messages of synthetic conversations.

    manage.py run_raters --panel llm-panel --experiment pilot --max-usd 1 --dry-run     # no write, no API call: the plan and a worst case
    manage.py run_raters --panel llm-panel --experiment pilot --max-usd 1               # LLM calls OFF: nothing is rated or written
    manage.py run_raters --panel llm-panel --conversation 12 --conversation 13 --max-usd 1 --live --match

Targets are the USER messages of the chosen conversations (never moderator messages), and the conversations must be
synthetic unless `--allow-human-source` is given. Without `--live` the command runs with LLM calls switched off for this
process only. `--live` turns them on for this process only (a process-local `override_settings(LLM_ENABLED=True)`, restored
even on error; config/tunables.py is untouched), and only when `--max-usd` is within the remaining evaluation budget
(BUDGET_EVAL_USD_TOTAL minus replay and judge spend), an API key is configured, the worst-case estimate and the cap have
been printed and the operator typed `yes` (or passed `--yes`); this is exactly the guard of `manage.py replay --live`.
Spend is counted in the ledger as `purpose="judge"`. The command never prints a key or a message text.
"""
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from evaluation import llm_rater
from evaluation.models import Panel
from forum.models import Conversation, Message
from moderation import budget


def usd(value):
    return f"${Decimal(value):.4f}"


class Command(BaseCommand):
    help = (
        "Have the LLM raters of a panel rate the user messages of synthetic conversations. Use --dry-run first: it writes "
        "nothing and makes no API call."
    )

    def create_parser(self, prog_name, subcommand, **kwargs):
        # The brief's `--version V` (a panel version) replaces Django's built-in `--version` (print Django's version).
        return super().create_parser(prog_name, subcommand, conflict_handler="resolve", **kwargs)

    def add_arguments(self, parser):
        parser.add_argument("--panel", required=True, help="Panel name (see seed_panel).")
        parser.add_argument("--version", default=None, help="Panel version (default: the newest of that name).")
        parser.add_argument("--experiment", default=None, help="Rate the conversations of this experiment.")
        parser.add_argument("--conversation", dest="conversations", action="append", type=int, metavar="ID", help="Rate this conversation (repeatable).")
        parser.add_argument("--dimension", dest="dimensions", action="append", metavar="D", help="Only this dimension (repeatable). Default: the panel's.")
        parser.add_argument("--replicates", type=int, default=1, help="Ratings per rater and message (default: 1).")
        parser.add_argument("--max-usd", default=None, help="Most this call may spend (required unless --dry-run).")
        parser.add_argument("--dry-run", action="store_true", help="Write nothing and make no API call; print the plan and worst-case cost.")
        parser.add_argument("--live", action="store_true", help="Make real API calls for this process (needs --max-usd, a key and a typed 'yes' or --yes).")
        parser.add_argument("--yes", action="store_true", help="With --live: skip the typed confirmation (for scripts).")
        parser.add_argument("--match", action="store_true", help="Afterwards match the Master's issues to the consensus findings.")
        parser.add_argument("--allow-human-source", action="store_true", help="Also accept conversations of real users.")

    # --- Arguments -----------------------------------------------------------------------------------------------------

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

    def _panel(self, name, version):
        panels = Panel.objects.filter(name=name)
        if version is not None:
            panels = panels.filter(version=version)
        panel = panels.order_by("-pk").first()
        if panel is None:
            raise CommandError(f"no panel named {name!r}" + (f" version {version!r}" if version is not None else "") + "; run seed_panel")
        return panel

    def _conversations(self, options):
        experiment, ids = options["experiment"], options["conversations"]
        if bool(experiment) == bool(ids):
            raise CommandError("give exactly one of --experiment NAME or --conversation ID (repeatable)")
        if experiment:
            conversations = list(Conversation.objects.filter(experiment__name=experiment).order_by("pk"))
            if not conversations:
                raise CommandError(f"no conversation belongs to an experiment named {experiment!r}")
        else:
            by_id = {c.pk: c for c in Conversation.objects.filter(pk__in=ids)}
            missing = [i for i in ids if i not in by_id]
            if missing:
                raise CommandError(f"no conversation with id {', '.join(map(str, missing))}")
            conversations = [by_id[i] for i in dict.fromkeys(ids)]
        if not options["allow_human_source"]:
            human = [c.pk for c in conversations if c.source != "synthetic"]
            if human:
                raise CommandError(
                    f"conversation(s) {', '.join(map(str, human))} are not synthetic; rating real users' messages needs --allow-human-source"
                )
        return conversations

    def _remaining_budget(self):
        return Decimal(settings.BUDGET_EVAL_USD_TOTAL) - budget.spend(purposes=budget.EVAL_PURPOSES)

    def handle(self, *args, **options):
        replicates = options["replicates"]
        if replicates < 1:
            raise CommandError("--replicates must be at least 1")
        dry_run = options["dry_run"]
        max_usd = self._parse_max_usd(options["max_usd"], required=not dry_run)
        panel = self._panel(options["panel"], options["version"])
        conversations = self._conversations(options)
        targets = list(
            Message.objects.filter(conversation__in=conversations, author_type="user").order_by("conversation_id", "seq_no")
        )
        if not targets:
            raise CommandError("the chosen conversations have no user messages")
        try:
            plan = llm_rater.plan_panel(panel, targets, replicates=replicates, dimensions=options["dimensions"])
        except (ValueError, FileNotFoundError) as exc:
            raise CommandError(str(exc)) from exc
        except llm_rater.LLMRefused as exc:  # a model that is not in the price table
            raise CommandError(str(exc)) from exc
        if not plan.items:
            raise CommandError(f"panel {panel} has no active LLM rater")
        if dry_run:
            return self._dry_run(panel, conversations, targets, plan, replicates, max_usd, options)
        return self._run(panel, conversations, targets, plan, replicates, max_usd, options)

    # --- Dry run -------------------------------------------------------------------------------------------------------

    def _describe(self, panel, conversations, targets, plan, replicates):
        out = self.stdout.write
        raters = sorted({item.rater.name for item in plan.items})
        out(f"Panel {panel} (raters: {', '.join(raters)}). Dimensions: {', '.join(plan.dimensions)}. Prompt {plan.prompt.name}.")
        out(f"Conversations: {len(conversations)}. User messages to rate: {len(targets)}. Replicates: {replicates}.")
        todo = plan.to_do
        out(f"Ratings planned: {len(plan.items)}, already done (skipped): {len(plan.items) - len(todo)}, to do: {len(todo)}.")
        out(
            f"Estimated worst-case cost: {usd(plan.worst_case_usd)} (every call priced as if all input were uncached and the "
            "whole reply allowance were used; real cost is normally well below this)."
        )

    def _dry_run(self, panel, conversations, targets, plan, replicates, max_usd, options):
        out = self.stdout.write
        out("DRY RUN: nothing is written and no API call is made.")
        self._describe(panel, conversations, targets, plan, replicates)
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
        if options["match"]:
            out("--match: after a real run the Master's issues would be matched to the consensus findings (not done in a dry run).")

    # --- A real (or switched-off) run ---------------------------------------------------------------------------------------

    def _run(self, panel, conversations, targets, plan, replicates, max_usd, options):
        out = self.stdout.write
        remaining = self._remaining_budget()
        if max_usd > remaining:
            raise CommandError(
                f"--max-usd {usd(max_usd)} is above the evaluation budget left ({usd(remaining)} of "
                f"BUDGET_EVAL_USD_TOTAL {usd(settings.BUDGET_EVAL_USD_TOTAL)}); lower it"
            )
        if options["live"]:
            if not settings.ANTHROPIC_API_KEY:
                raise CommandError("no ANTHROPIC_API_KEY is configured (it belongs in .env); a live run is refused")
            out(
                f"LIVE rating with panel {panel}: {len(plan.to_do)} call(s), worst-case cost {usd(plan.worst_case_usd)}, "
                f"cap for this call {usd(max_usd)} (evaluation budget left {usd(remaining)})."
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
            out("LLM calls are OFF for this call (no --live): nothing will be rated, written or spent (the ratings are reported as not run).")
            switch = override_settings(LLM_ENABLED=False)

        self._describe(panel, conversations, targets, plan, replicates)
        with switch:
            report = llm_rater.run_panel(
                panel, targets, replicates=replicates, max_usd=max_usd, dimensions=plan.dimensions, on_result=self._progress
            )
        out(report.summary())
        if options["match"]:
            self._match(panel, conversations)

    def _progress(self, result):
        rating = result.rating
        self.stdout.write(
            f"  {rating.rater.name} message {rating.target_id} replicate {rating.replicate}: {rating.status}"
            + (f" ({result.failure_reason})" if result.failure_reason else "")
            + f", {len(result.findings)} finding(s), {len(result.rejected)} dropped, {usd(result.cost_usd)}"
        )

    def _match(self, panel, conversations):
        out = self.stdout.write
        try:
            from evaluation import matching
        except ImportError as exc:
            raise CommandError(f"--match needs evaluation/matching.py, which is not available: {exc}") from exc
        report = matching.match_issues(panel, [c.pk for c in conversations])
        out(
            f"Matching: {report.linked} link(s) made; {len(report.issues_unmatched)} valid issue(s) without a finding, "
            f"{len(report.findings_unmatched)} finding(s) without an issue, {len(report.unrated_messages)} unrated message(s)."
        )
