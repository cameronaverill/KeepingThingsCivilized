"""`manage.py export_all --output-dir DIR`: every (filtered) conversation as its own JSON file, plus an index."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from moderation import queries


class Command(BaseCommand):
    help = (
        "Write conversation_<id>.json for each conversation and index.json (the listing) into --output-dir. "
        "Refuses to overwrite existing files unless --force."
    )

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", required=True, metavar="DIR")
        parser.add_argument("--include-identities", action="store_true", help="Add username and email per participant.")
        parser.add_argument("--include-raw", action="store_true", help="Add each call's request, raw_response, parsed.")
        parser.add_argument("--source")
        parser.add_argument("--status")
        parser.add_argument("--experiment", help="Experiment name.")
        parser.add_argument("--force", action="store_true", help="Overwrite existing files.")

    def handle(self, *args, **options):
        try:
            index = queries.list_conversations(
                source=options["source"], status=options["status"], experiment=options["experiment"]
            )
        except ValueError as error:
            raise CommandError(str(error)) from None
        out_dir = Path(options["output_dir"])
        if out_dir.exists() and not out_dir.is_dir():
            raise CommandError(f"{out_dir} exists and is not a directory.")
        targets = [out_dir / f"conversation_{row['id']}.json" for row in index] + [out_dir / "index.json"]
        existing = [t for t in targets if t.exists()]
        if existing and not options["force"]:
            names = ", ".join(t.name for t in existing[:5]) + (", ..." if len(existing) > 5 else "")
            raise CommandError(f"{len(existing)} file(s) already exist in {out_dir} ({names}); use --force to overwrite.")
        # Build everything first so a failure writes nothing.
        payloads = [
            queries.export_conversation(
                row["id"], include_identities=options["include_identities"], include_raw=options["include_raw"]
            )
            for row in index
        ]
        payloads.append(queries.dumps(index))
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            for target, text in zip(targets, payloads):
                target.write_text(text, encoding="utf-8")
        except OSError as error:
            raise CommandError(f"Cannot write to {out_dir}: {error.strerror or error}") from None
        self.stdout.write(f"Wrote {len(index)} conversation file(s) and index.json to {out_dir}.")
