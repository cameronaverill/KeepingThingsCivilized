from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models, router

from config import tunables

from .keys import normalize_key
from .validators import validate_username

USERNAME_TAKEN = "A user with that username already exists."
EMAIL_TAKEN = "A user with that email address already exists."

# Casefolding and NFKC can expand a character (up to 18 characters for a few symbols), so the email key needs more
# room than the 254 characters an email may have.
EMAIL_KEY_MAX_LENGTH = 254 * 18 + 8


class User(AbstractUser):
    """A forum account. Usernames and emails are unique regardless of case and Unicode form.

    Two derived columns, username_key and email_key, hold normalize_key() of each value. They are filled in by save()
    and carry the real unique constraints. save() also checks them first, so every code path (forms, create_user,
    createsuperuser, admin, plain save()) gets a friendly ValidationError instead of an IntegrityError. The unique
    constraints stay as the database backstop.

    Registration (step 6) creates accounts with is_active=False until the emailed link is followed.
    """

    username = models.CharField(
        "username",
        max_length=tunables.USERNAME_MAX_LENGTH,
        unique=True,
        help_text=(
            f"Required. {tunables.USERNAME_MIN_LENGTH} to {tunables.USERNAME_MAX_LENGTH} characters: "
            "letters A-Z, digits, hyphens and underscores."
        ),
        validators=[validate_username],
        error_messages={"unique": USERNAME_TAKEN},
    )
    # Required, unlike Django's default.
    email = models.EmailField("email address", max_length=254)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    username_key = models.CharField(max_length=tunables.USERNAME_MAX_LENGTH, unique=True, editable=False)
    email_key = models.CharField(max_length=EMAIL_KEY_MAX_LENGTH, unique=True, editable=False)

    # --- keys -------------------------------------------------------------------------------------------------

    def _strip_and_set_keys(self):
        self.username = (self.username or "").strip()
        self.email = (self.email or "").strip()
        self.username_key = normalize_key(self.username)
        self.email_key = normalize_key(self.email)

    def _key_collisions(self, using=None, check_username=True, check_email=True):
        """{field: [message]} for a username or email whose key already belongs to a different user."""
        others = type(self)._base_manager.using(using or router.db_for_write(type(self), instance=self))
        if self.pk is not None:
            others = others.exclude(pk=self.pk)
        errors = {}
        if check_username and self.username_key and others.filter(username_key=self.username_key).exists():
            errors["username"] = [USERNAME_TAKEN]
        if check_email and self.email_key and others.filter(email_key=self.email_key).exists():
            errors["email"] = [EMAIL_TAKEN]
        return errors

    # --- validation ---------------------------------------------------------------------------------------------

    def clean_fields(self, exclude=None):
        # The keys are derived, so they are filled in here rather than asked of a form; only the real fields are checked.
        self._strip_and_set_keys()
        exclude = set(exclude or ()) | {"username_key", "email_key"}
        super().clean_fields(exclude=exclude)

    def validate_unique(self, exclude=None):
        exclude = set(exclude or ())
        errors = {}
        try:
            super().validate_unique(exclude=exclude | {"username_key", "email_key"})
        except ValidationError as error:
            errors = {field: list(messages) for field, messages in error.message_dict.items()}
        collisions = self._key_collisions(
            using=self._state.db, check_username="username" not in exclude, check_email="email" not in exclude
        )
        for field, messages in collisions.items():
            for message in messages:
                if message not in errors.setdefault(field, []):
                    errors[field].append(message)
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self._strip_and_set_keys()
        errors = {}
        try:
            validate_username(self.username)
        except ValidationError as error:
            errors["username"] = error.messages
        if not self.email_key:
            errors["email"] = ["Enter an email address."]
        if not errors:
            errors = self._key_collisions(using=kwargs.get("using"))
        if errors:
            raise ValidationError(errors)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            fields = set(update_fields)
            if "username" in fields:
                fields.add("username_key")
            if "email" in fields:
                fields.add("email_key")
            kwargs["update_fields"] = fields
        super().save(*args, **kwargs)
