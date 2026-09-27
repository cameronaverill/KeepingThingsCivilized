"""Real foreign keys instead of plain ids (foreign-key cleanup, evaluation half).

* `Rating.llm_call_id` (a plain integer) becomes `Rating.llm_call`, a nullable PROTECT foreign key to
  `moderation.LLMCall`; the database column keeps its name, `llm_call_id`, and existing values stay.
* `Annotation.source` ("self" or "rater:<name>") is replaced by `Annotation.rater`, a nullable PROTECT foreign key to
  `Rater`: "self" becomes null, "rater:<name>" becomes the rater with that name. If a name has no rater, or a rating
  cites a call that does not exist, the migration stops with a clear error instead of losing data. It reverses to the
  old text form.

SQLite rebuilds a table when a column changes. That drops the triggers on the table, and a trigger on another table
whose body names the table being rebuilt (the finding, rater and consensus triggers name `evaluation_rating`) makes the
rebuild fail. So every evaluation trigger is dropped first and created again at the end, with the same SQL as
migration 0001, in both directions.
"""

from importlib import import_module

import django.db.models.deletion
from django.db import migrations, models

_initial = import_module("evaluation.migrations.0001_initial")
_TRIGGERS = list(_initial._TRIGGERS)  # (name, table, event, body): all of them, see the docstring


def _drop_sql():
    return [f"DROP TRIGGER IF EXISTS {name};" for name, *_ in _TRIGGERS]


def _create_sql():
    return [_initial._create_sql(*trigger) for trigger in _TRIGGERS]


def check_llm_calls_exist(apps, schema_editor):
    Rating = apps.get_model("evaluation", "Rating")
    LLMCall = apps.get_model("moderation", "LLMCall")
    cited = set(Rating.objects.exclude(llm_call_id=None).values_list("llm_call_id", flat=True))
    missing = sorted(cited - set(LLMCall.objects.filter(pk__in=cited).values_list("pk", flat=True)))
    if missing:
        raise RuntimeError(
            f"Cannot make Rating.llm_call a foreign key: ratings cite LLM call ids that do not exist: {missing}. "
            "Fix or clear those ratings first."
        )


def source_to_rater(apps, schema_editor):
    Annotation = apps.get_model("evaluation", "Annotation")
    Rater = apps.get_model("evaluation", "Rater")
    raters = {name: pk for pk, name in Rater.objects.values_list("pk", "name")}
    plan = []
    problems = []
    for pk, source in Annotation.objects.values_list("pk", "source"):
        if source == "self":
            plan.append((pk, None))
        elif source.startswith("rater:") and len(source) > len("rater:") and source[len("rater:"):] in raters:
            plan.append((pk, raters[source[len("rater:"):]]))
        else:
            problems.append(f"annotation {pk}: source {source!r}")
    if problems:
        raise RuntimeError(
            "Cannot convert Annotation.source to Annotation.rater: no rater has the name given (or the source is "
            "not 'self' or 'rater:<name>'): " + "; ".join(problems) + ". Create the raters or fix the rows first."
        )
    for pk, rater_id in plan:
        Annotation.objects.filter(pk=pk).update(rater_id=rater_id)


def rater_to_source(apps, schema_editor):
    Annotation = apps.get_model("evaluation", "Annotation")
    Rater = apps.get_model("evaluation", "Rater")
    names = dict(Rater.objects.values_list("pk", "name"))
    for pk, rater_id in Annotation.objects.values_list("pk", "rater_id"):
        source = "self" if rater_id is None else f"rater:{names[rater_id]}"
        Annotation.objects.filter(pk=pk).update(source=source)


class Migration(migrations.Migration):
    dependencies = [
        ("evaluation", "0001_initial"),
        ("moderation", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=_drop_sql(), reverse_sql=_create_sql()),
        # Rating.llm_call_id (plain integer) -> Rating.llm_call (foreign key, same column name).
        migrations.RunPython(check_llm_calls_exist, migrations.RunPython.noop),
        migrations.RenameField(model_name="rating", old_name="llm_call_id", new_name="llm_call"),
        migrations.AlterField(
            model_name="rating",
            name="llm_call",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="evaluation_ratings",
                to="moderation.llmcall",
            ),
        ),
        # Annotation.source -> Annotation.rater. The constraint goes first so that the reverse can add it back after
        # the text has been restored.
        migrations.AddField(
            model_name="annotation",
            name="rater",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="annotations",
                to="evaluation.rater",
            ),
        ),
        migrations.RemoveConstraint(model_name="annotation", name="evaluation_annotation_source_valid"),
        migrations.RunPython(source_to_rater, rater_to_source),
        # A blank default, so that the reverse can add the column back to a table that has rows.
        migrations.AlterField(
            model_name="annotation", name="source", field=models.CharField(default="", max_length=110)
        ),
        migrations.RemoveField(model_name="annotation", name="source"),
        migrations.RunSQL(sql=_create_sql(), reverse_sql=_drop_sql()),
    ]
