"""The one place step 2 code asks for the time, so tests can patch `moderation.clock.now`."""
from django.utils import timezone


def now():
    return timezone.now()
