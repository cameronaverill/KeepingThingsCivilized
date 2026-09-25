"""Real case-insensitive uniqueness: adds the derived username_key and email_key columns, backfills them for existing
rows, drops the old LOWER() constraints (SQLite's LOWER() only folds ASCII) and puts unique constraints on the keys.
"""
import unicodedata

import accounts.validators
from django.db import migrations, models


def normalize_key(value):
    """A frozen copy of accounts.keys.normalize_key, so this migration never changes when the app code does."""
    stripped = unicodedata.normalize("NFKC", value.strip())
    return unicodedata.normalize("NFKC", stripped.casefold())


def backfill_keys(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    db = schema_editor.connection.alias
    users = list(User.objects.using(db).order_by("pk"))
    collisions = []
    for field in ("username", "email"):
        groups = {}
        for user in users:
            groups.setdefault(normalize_key(getattr(user, field)), []).append(getattr(user, field))
        collisions += [
            f"{field}: {', '.join(repr(value) for value in values)}" for values in groups.values() if len(values) > 1
        ]
    if collisions:
        raise RuntimeError(
            "Cannot make usernames and emails unique ignoring case and Unicode form, because these existing accounts "
            "collide: " + "; ".join(collisions) + ". Resolve them by hand (rename or delete the duplicates), then run "
            "migrate again. No data has been changed."
        )
    for user in users:
        user.username_key = normalize_key(user.username)
        user.email_key = normalize_key(user.email)
        user.save(update_fields=["username_key", "email_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        # The old Meta (which only held the constraints) is gone, so the model inherits AbstractUser's Meta again.
        migrations.AlterModelOptions(
            name="user",
            options={"verbose_name": "user", "verbose_name_plural": "users"},
        ),
        migrations.AddField(
            model_name="user",
            name="username_key",
            field=models.CharField(default="", editable=False, max_length=30),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="user",
            name="email_key",
            field=models.CharField(default="", editable=False, max_length=4580),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="user",
            name="username",
            field=models.CharField(
                error_messages={"unique": "A user with that username already exists."},
                help_text="Required. 3 to 30 characters: letters A-Z, digits, hyphens and underscores.",
                max_length=30,
                unique=True,
                validators=[accounts.validators.validate_username],
                verbose_name="username",
            ),
        ),
        migrations.RunPython(backfill_keys, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="user", name="accounts_user_username_ci_unique"),
        migrations.RemoveConstraint(model_name="user", name="accounts_user_email_ci_unique"),
        migrations.AlterField(
            model_name="user",
            name="username_key",
            field=models.CharField(editable=False, max_length=30, unique=True),
        ),
        migrations.AlterField(
            model_name="user",
            name="email_key",
            field=models.CharField(editable=False, max_length=4580, unique=True),
        ),
    ]
