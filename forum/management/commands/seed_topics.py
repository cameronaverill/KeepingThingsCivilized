"""``manage.py seed_topics [--dry-run] [--file PATH]``: create or update the seeded topics (plan sections 2, 8, 9).

Seeded topics come from ``forum/seed_topics.json`` (or ``--file``), have ``created_by = None`` and start visible. The
command is idempotent and keyed by ``title``: a second run creates nothing and only updates ``description``,
``proposition``, ``opposing_position`` and ``leans`` when the file changed. Every entry states both positions
(``proposition`` is the "pro" wording, ``opposing_position`` the "con" wording; step 7c). It never touches
user-created propositions and never deletes
anything. The whole file is validated before the first write, so an invalid file changes nothing.

The ``leans`` in the packaged file are a DRAFT for the owner's review (plan step 17); the output says so on every run.
"""

import json
import math
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from forum.limits import count_message_chars
from forum.models import Topic
from forum.services import proposition_key

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "seed_topics.json"

ENTRY_KEYS = ("title", "description", "proposition", "opposing_position", "leans")
SIDES = ("pro", "con")
# Scheme -> axes (plan section 2). Values run from -1 (left-coded) to +1 (right-coded).
SCHEMES = {"compass": ("economic", "social"), "us_partisan": ("party",)}
TITLE_MAX = Topic._meta.get_field("title").max_length

DRAFT_NOTICE = (
    "NOTE: the 'leans' values in the seed file are a DRAFT for the owner's review (plan step 17), "
    "not an authoritative coding of any position."
)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _label(index, entry):
    title = entry.get("title") if isinstance(entry, dict) else None
    if isinstance(title, str) and title:
        return f'entry {index} ("{title}")'
    return f"entry {index}"


def _normalise_leans(leans):
    """Return ``leans`` with every value as a float. Raises ValueError with a plain message on any deviation."""
    if not isinstance(leans, dict):
        raise ValueError("'leans' must be an object with the sides 'pro' and 'con'")
    if set(leans) != set(SIDES):
        raise ValueError(f"'leans' must have exactly the sides {list(SIDES)}, found {sorted(map(str, leans))}")
    out = {}
    for side in SIDES:
        schemes = leans[side]
        if not isinstance(schemes, dict):
            raise ValueError(f"leans.{side} must be an object keyed by scheme")
        if set(schemes) != set(SCHEMES):
            raise ValueError(
                f"leans.{side} must have exactly the schemes {list(SCHEMES)}, found {sorted(map(str, schemes))}"
            )
        out[side] = {}
        for scheme, axes in SCHEMES.items():
            axis_map = schemes[scheme]
            where = f"leans.{side}.{scheme}"
            if not isinstance(axis_map, dict):
                raise ValueError(f"{where} must be an object keyed by axis")
            if set(axis_map) != set(axes):
                raise ValueError(f"{where} must have exactly the axes {list(axes)}, found {sorted(map(str, axis_map))}")
            out[side][scheme] = {}
            for axis in axes:
                cell = axis_map[axis]
                cell_where = f"{where}.{axis}"
                if not isinstance(cell, dict) or set(cell) != {"value", "rationale"}:
                    raise ValueError(f"{cell_where} must be an object with exactly 'value' and 'rationale'")
                value = cell["value"]
                if not _is_number(value) or not math.isfinite(value) or not -1 <= value <= 1:
                    raise ValueError(f"{cell_where}.value must be a number between -1 and 1, found {value!r}")
                rationale = cell["rationale"]
                if not isinstance(rationale, str) or not rationale.strip():
                    raise ValueError(f"{cell_where}.rationale must be a non-empty string")
                out[side][scheme][axis] = {"value": float(value), "rationale": rationale}
    return out


def _validate_entry(index, entry):
    """Return the cleaned entry (``leans`` normalised). Raises CommandError naming the entry."""
    label = _label(index, entry)
    if not isinstance(entry, dict):
        raise CommandError(f"{label}: must be an object with the keys {list(ENTRY_KEYS)}")
    missing = [key for key in ENTRY_KEYS if key not in entry]
    if missing:
        raise CommandError(f"{label}: missing key(s) {missing}")
    unknown = sorted(str(key) for key in entry if key not in ENTRY_KEYS)
    if unknown:
        raise CommandError(f"{label}: unknown key(s) {unknown}")
    for key in ("title", "description", "proposition", "opposing_position"):
        if not isinstance(entry[key], str):
            raise CommandError(f"{label}: '{key}' must be a string")
    if not entry["title"].strip():
        raise CommandError(f"{label}: 'title' must not be empty (it is the key that makes the command idempotent)")
    if len(entry["title"]) > TITLE_MAX:
        raise CommandError(f"{label}: 'title' is {len(entry['title'])} characters; the limit is {TITLE_MAX}")
    if not entry["proposition"].strip():
        raise CommandError(f"{label}: 'proposition' must not be empty")
    limit = settings.MAX_PROPOSITION_CHARS
    count = count_message_chars(entry["proposition"])
    if count > limit:
        raise CommandError(f"{label}: 'proposition' is {count} characters; the limit (MAX_PROPOSITION_CHARS) is {limit}")
    if not entry["opposing_position"].strip():
        raise CommandError(f"{label}: 'opposing_position' must not be empty")
    opposing_count = count_message_chars(entry["opposing_position"])
    if opposing_count > limit:
        raise CommandError(
            f"{label}: 'opposing_position' is {opposing_count} characters; the limit (MAX_PROPOSITION_CHARS) is {limit}"
        )
    if proposition_key(entry["opposing_position"]) == proposition_key(entry["proposition"]):
        raise CommandError(f"{label}: 'opposing_position' must differ from 'proposition'")
    try:
        leans = _normalise_leans(entry["leans"])
    except ValueError as error:
        raise CommandError(f"{label}: {error}") from None
    return {
        "title": entry["title"],
        "description": entry["description"],
        "proposition": entry["proposition"],
        "opposing_position": entry["opposing_position"],
        "leans": leans,
    }


def _load(path):
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise CommandError(f"Cannot read seed file {path}: {error.strerror or error}") from None
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise CommandError(f"Seed file {path} is not valid JSON: {error}") from None
    if not isinstance(data, list):
        raise CommandError(f"Seed file {path} must contain a list of entries")
    entries = [_validate_entry(i, entry) for i, entry in enumerate(data, start=1)]
    seen = {}
    for i, entry in enumerate(entries, start=1):
        if entry["title"] in seen:
            raise CommandError(
                f'entry {i} ("{entry["title"]}"): duplicate title (also entry {seen[entry["title"]]})'
            )
        seen[entry["title"]] = i
    return entries


def _same_leans(stored, new):
    """Compare stored JSON with the normalised leans, ignoring int/float differences after a JSON round trip."""
    try:
        return _normalise_leans(stored) == new
    except ValueError:
        return False


class Command(BaseCommand):
    help = (
        "Create or update the seeded topics from forum/seed_topics.json (idempotent, keyed by title). "
        "The 'leans' in the packaged file are a draft for the owner's review."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print what would change; write nothing.")
        parser.add_argument("--file", default=str(DEFAULT_FILE), help="Seed file (default: the packaged one).")

    def handle(self, *args, dry_run=False, file=None, **options):
        entries = _load(file or DEFAULT_FILE)

        # Plan every change before writing anything, so a conflict aborts with nothing written.
        plan = []
        for entry in entries:
            existing = Topic.objects.filter(title=entry["title"]).first()
            if existing is None:
                plan.append(("create", entry, None))
            elif existing.created_by_id is not None:
                raise CommandError(
                    f'"{entry["title"]}": a user-created topic already uses this title; seed_topics never touches '
                    "user-created propositions, so nothing was written"
                )
            else:
                unchanged = (
                    existing.description == entry["description"]
                    and existing.proposition == entry["proposition"]
                    and existing.opposing_position == entry["opposing_position"]
                    and _same_leans(existing.leans, entry["leans"])
                )
                plan.append(("unchanged" if unchanged else "update", entry, existing))

        self.stdout.write(DRAFT_NOTICE)
        prefix = "Would " if dry_run else ""
        counts = {"create": 0, "update": 0, "unchanged": 0}
        with transaction.atomic():
            for action, entry, existing in plan:
                counts[action] += 1
                if action == "unchanged":
                    self.stdout.write(f'unchanged: "{entry["title"]}"')
                    continue
                self.stdout.write(f'{prefix}{"create" if action == "create" else "update"}: "{entry["title"]}"')
                if dry_run:
                    continue
                if action == "create":
                    Topic.objects.create(created_by=None, hidden=False, **entry)
                else:
                    # `hidden` is deliberately left alone: an admin may have hidden a seeded topic.
                    existing.description = entry["description"]
                    existing.proposition = entry["proposition"]
                    existing.opposing_position = entry["opposing_position"]
                    existing.leans = entry["leans"]
                    existing.save(update_fields=["description", "proposition", "opposing_position", "leans"])

        summary = f"created {counts['create']}, updated {counts['update']}, unchanged {counts['unchanged']}"
        if dry_run:
            summary += " (dry run: nothing was written)"
        self.stdout.write(summary)
