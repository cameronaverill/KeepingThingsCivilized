from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


class User(AbstractUser):
    """A forum account. Usernames and emails are unique regardless of upper/lower case.

    Registration (step 6) creates accounts with is_active=False until the emailed link is followed.
    """

    # Required, unlike Django's default.
    email = models.EmailField("email address", max_length=254)
    email_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("username"), name="accounts_user_username_ci_unique"),
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
        ]
