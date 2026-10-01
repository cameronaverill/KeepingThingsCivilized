"""`manage.py summarize_pilot --experiment NAME [--output PATH]`: summarise `generated/judgments/<NAME>.jsonl` (no API call, no database)."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from seeding import analyze, judge


class Command(BaseCommand):
    help = "Print (and write) the preliminary summary of the judged seeded-error pilot."

    def add_arguments(self, parser):
        parser.add_argument("--experiment", required=True)
        parser.add_argument("--output", default=None, help="Where to write the markdown (default: generated/pilot_<experiment>.md).")

    def handle(self, *args, **options):
        name = options["experiment"]
        source = judge.judgments_path(name)
        if not source.exists():
            raise CommandError(f"no judgments at {source}; run judge_responses first")
        rows = analyze.load_judgments(source)
        if not rows:
            raise CommandError(f"{source} has no judgments")
        text = analyze.render_markdown(analyze.summarize(rows))
        self.stdout.write(text)
        target = Path(options["output"]) if options["output"] else Path("generated") / f"pilot_{name}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.stdout.write(f"Wrote {target}.")
