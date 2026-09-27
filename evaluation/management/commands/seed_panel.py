"""`manage.py seed_panel [--name NAME] [--version V] [--rubrics-dir DIR]`: create (or reuse) the two LLM raters and the panel.

    manage.py seed_panel                      # panel "llm-panel": raters llm-sonnet-5 and llm-haiku-4-5

Idempotent. The raters come from `settings.JUDGE_MODELS` (`claude-sonnet-5` becomes `llm-sonnet-5`), kind `llm`, provider
`anthropic`, no temperature (Sonnet 5 accepts none, and none is ever sent). The panel records, per dimension, the rubric
file and its sha256, and the consensus thresholds from the tunables. An existing panel is never edited: when a rubric file
(or a threshold) has changed since the newest panel of that name, a new panel is created with the next version number
("1", "2", ...). With `--version V` that exact panel is used, and it is an error when it exists with different contents.
Makes no API call and never reads a key.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from evaluation.llm_rater import load_rater_prompt
from evaluation.models import Panel, PanelMember, Rater

DEFAULT_PANEL_NAME = "llm-panel"


def rater_name(model):
    """`claude-sonnet-5` -> `llm-sonnet-5`."""
    return "llm-" + (model[len("claude-") :] if model.startswith("claude-") else model)


def _numeric(version):
    return int(version) if version.isdigit() else None


class Command(BaseCommand):
    help = "Create or reuse the two LLM raters (from JUDGE_MODELS) and a versioned panel that records the rubric hashes."

    def create_parser(self, prog_name, subcommand, **kwargs):
        # The brief's `--version V` (a panel version) replaces Django's built-in `--version` (print Django's version).
        return super().create_parser(prog_name, subcommand, conflict_handler="resolve", **kwargs)

    def add_arguments(self, parser):
        parser.add_argument("--name", default=DEFAULT_PANEL_NAME, help=f"Panel name (default: {DEFAULT_PANEL_NAME}).")
        parser.add_argument("--version", default=None, help="Panel version (default: the newest matching one, else the next number).")
        parser.add_argument("--rubrics-dir", default=None, help="Folder with the rubric files (default: RATER_RUBRICS_DIR, else rubrics/).")

    def _raters(self):
        out = self.stdout.write
        raters = []
        for model in settings.JUDGE_MODELS:
            name = rater_name(model)
            rater = Rater.objects.filter(name=name).first()
            if rater is None:
                rater = Rater.objects.create(name=name, kind="llm", provider="anthropic", model=model, temperature=None)
                out(f"Created rater {name} (model {model}).")
            elif rater.kind != "llm" or rater.model != model:
                raise CommandError(f"a rater named {name!r} exists but is not the LLM rater for {model}; not touching it")
            else:
                out(f"Reused rater {name} (model {model}{'' if rater.active else ', INACTIVE'}).")
            raters.append(rater)
        return raters

    def _same(self, panel, dimensions, raters):
        return (
            panel.dimensions == dimensions
            and panel.span_match_min_iou == settings.SPAN_MATCH_MIN_IOU
            and panel.intensity_disagreement_threshold == settings.INTENSITY_DISAGREEMENT_THRESHOLD
            and set(panel.raters.values_list("pk", flat=True)) == {r.pk for r in raters}
        )

    def handle(self, *args, **options):
        out = self.stdout.write
        name = (options["name"] or "").strip()
        if not name:
            raise CommandError("--name needs a name")
        try:
            dimensions = load_rater_prompt(None, rubrics_dir=options["rubrics_dir"]).rubrics
        except (FileNotFoundError, ValueError) as exc:
            raise CommandError(f"cannot load the rubrics: {exc}") from exc
        if not dimensions:
            raise CommandError("no rubric was found")
        with transaction.atomic():
            raters = self._raters()
            version = options["version"]
            if version is not None:
                panel = Panel.objects.filter(name=name, version=version).first()
                if panel is not None and not self._same(panel, dimensions, raters):
                    raise CommandError(
                        f"panel {name} v{version} exists with different rubrics, thresholds or raters, and a panel is never edited; "
                        "omit --version to create the next version"
                    )
            else:
                versions = list(Panel.objects.filter(name=name).order_by("pk"))
                panel = versions[-1] if versions and self._same(versions[-1], dimensions, raters) else None
                numbers = [n for n in (_numeric(p.version) for p in versions) if n is not None]
                version = str(max(numbers, default=0) + 1)
            if panel is None:
                panel = Panel.objects.create(
                    name=name,
                    version=version,
                    dimensions=dimensions,
                    span_match_min_iou=settings.SPAN_MATCH_MIN_IOU,
                    intensity_disagreement_threshold=settings.INTENSITY_DISAGREEMENT_THRESHOLD,
                )
                for rater in raters:
                    PanelMember.objects.create(panel=panel, rater=rater)
                out(f"Created panel {name} v{version}.")
            else:
                out(f"Reused panel {name} v{panel.version}.")
        for dimension, ref in panel.dimensions.items():
            out(f"  {dimension}: rubric {ref['rubric']} sha256 {ref['sha256'][:12]}")
        out(f"  raters: {', '.join(sorted(r.name for r in panel.raters.all()))}")
        out(f"  span_match_min_iou {panel.span_match_min_iou}, intensity_disagreement_threshold {panel.intensity_disagreement_threshold}")
