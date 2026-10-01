"""`manage.py summarize_research --experiment NAME [--output PATH]`: summarise `generated/judgments/research_<NAME>.jsonl`
(no API call, no database)."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from seeding import research_analyze, research_eval


class Command(BaseCommand):
    help = "Print (and write) the preliminary summary of the judged research notes."

    def add_arguments(self, parser):
        parser.add_argument("--experiment", required=True)
        parser.add_argument("--output", default=None, help="Where to write the markdown (default: generated/research_<experiment>.md).")

    def handle(self, *args, **options):
        name = options["experiment"]
        source = research_eval.judgments_path(name)
        if not source.exists():
            raise CommandError(f"no judgments at {source}; run judge_research first")
        rows = research_analyze.load_judgments(source)
        if not rows:
            raise CommandError(f"{source} has no judgments")
        text = research_analyze.render_markdown(research_analyze.summarize(rows))
        self.stdout.write(text)
        target = Path(options["output"]) if options["output"] else Path("generated") / f"research_{name}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.stdout.write(f"Wrote {target}.")
