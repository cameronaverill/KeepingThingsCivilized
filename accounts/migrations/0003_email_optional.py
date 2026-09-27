"""Email becomes optional (step 6c: accounts have a username and a password only).

`email` gets blank=True and default="". `email_key` stops being unique outright and is instead unique only when it is
not empty, so any number of accounts can have no email.

Going backwards puts back the old rules (an email is required and unique), so accounts without one are first given a
placeholder address `<username>@no-email.invalid` (the .invalid domain can never receive mail). That step runs before
the unique constraint on `email_key` is restored, so a backward migration works on a database that has such accounts.
"""
import unicodedata

from django.db import migrations, models


def normalize_key(value):
    """A frozen copy of accounts.keys.normalize_key, so this migration never changes when the app code does."""
    stripped = unicodedata.normalize("NFKC", value.strip())
    return unicodedata.normalize("NFKC", stripped.casefold())


def give_blank_emails_placeholders(apps, schema_editor):
    """Reverse step: every account needs a distinct email again, so fill in the empty ones."""
    User = apps.get_model("accounts", "User")
    db = schema_editor.connection.alias
    taken = set(User.objects.using(db).exclude(email_key="").values_list("email_key", flat=True))
    for user in User.objects.using(db).filter(email_key="").order_by("pk"):
        address = f"{user.username}@no-email.invalid"
        counter = 1
        while normalize_key(address) in taken:  # only if someone really used such an address
            counter += 1
            address = f"{user.username}-{counter}@no-email.invalid"
        taken.add(normalize_key(address))
        user.email = address
        user.email_key = normalize_key(address)
        user.save(update_fields=["email", "email_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_user_keys"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(blank=True, default="", max_length=254, verbose_name="email address"),
        ),
        migrations.AlterField(
            model_name="user",
            name="email_key",
            field=models.CharField(editable=False, max_length=4580),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                condition=models.Q(("email_key", ""), _negated=True),
                fields=("email_key",),
                name="accounts_user_email_key_unique_when_set",
            ),
        ),
        # Last in the list, so it runs FIRST when reversing (before the unique constraint on email_key comes back).
        migrations.RunPython(migrations.RunPython.noop, give_blank_emails_placeholders),
    ]
