import re

from django.core.exceptions import ValidationError

from config import tunables

_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def validate_username(value):
    """ASCII letters, digits, hyphen and underscore only, within the tunable length limits.

    ASCII only so that no two usernames can look alike on screen (Cyrillic "a" and Latin "a", and so on).
    """
    low, high = tunables.USERNAME_MIN_LENGTH, tunables.USERNAME_MAX_LENGTH
    if not (value.isascii() and _USERNAME_PATTERN.fullmatch(value)):
        raise ValidationError(
            "Usernames may contain only the letters A-Z, digits, hyphens and underscores.",
            code="invalid_username",
        )
    if not low <= len(value) <= high:
        raise ValidationError(
            f"Usernames must be between {low} and {high} characters long.",
            code="invalid_username_length",
        )
