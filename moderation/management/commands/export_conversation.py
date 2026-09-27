"""`manage.py export_conversation <id>`: one conversation as JSON on stdout or in a file."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from forum.models import Conversation
from moderation import queries


class Command(BaseCommand):
    help = (
        "Export one conversation (messages, runs, issues, acts, calls and costs) as JSON. Identities (usernames, "
        "emails) are left out unless --include-identities; raw prompts and responses only with --include-raw."
    )

    def add_arguments(self, parser):
        parser.add_argument("conversation_id", type=int)
        parser.add_argument("--include-identities", action="store_true", help="Add username and email per participant.")
        parser.add_argument("--include-raw", action="store_true", help="Add each call's request, raw_response, parsed.")
        parser.add_argument("--output", metavar="PATH", help="Write to this file instead of stdout.")

    def handle(self, *args, **options):
        try:
            text = queries.export_conversation(
                options["conversation_id"],
                include_identities=options["include_identities"],
                include_raw=options["include_raw"],
            )
        except Conversation.DoesNotExist:
            raise CommandError(f"Conversation {options['conversation_id']} does not exist.") from None
        output = options.get("output")
        if output:
            path = Path(output)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            except OSError as error:
                raise CommandError(f"Cannot write {path}: {error.strerror or error}") from None
            return
        self.stdout.write(text, ending="")
