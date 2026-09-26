# Step 7a: user-created propositions (created_by, hidden, optional title) and who ended a conversation.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def fill_empty_propositions(apps, schema_editor):
    """The new check constraint forbids an empty proposition, so give any old row without one its title (or a placeholder)."""
    Topic = apps.get_model("forum", "Topic")
    for topic in Topic.objects.filter(proposition=""):
        topic.proposition = topic.title or f"Proposition {topic.pk}"
        topic.save(update_fields=["proposition"])


class Migration(migrations.Migration):

    dependencies = [
        ('forum', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='conversation',
            name='ended_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='conversation',
            name='ended_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='ended_conversations', to='forum.participant'),
        ),
        migrations.AddField(
            model_name='topic',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='created_topics', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='topic',
            name='hidden',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='topic',
            name='title',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddConstraint(
            model_name='topic',
            constraint=models.UniqueConstraint(condition=models.Q(('title', ''), _negated=True), fields=('title',), name='forum_topic_title_unique_if_set'),
        ),
        migrations.RunPython(fill_empty_propositions, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='topic',
            constraint=models.CheckConstraint(condition=models.Q(('proposition', ''), _negated=True), name='forum_topic_proposition_not_empty'),
        ),
    ]
