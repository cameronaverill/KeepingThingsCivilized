"""The two emails registration sends (step 6a): plain text, through Django 6.1's default mailer (MAILERS["default"]).

Sending never raises: a mail failure is logged (the exception's type only, never the address, the link or the
exception text, which for SMTP errors can contain the address) and reported by the return value, so that a broken
mail server cannot leave a half-registered user behind.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.defaultfilters import pluralize
from django.urls import reverse

from .tokens import make_token

logger = logging.getLogger(__name__)

CONFIRM_SUBJECT = "Confirm your email for the discussion forum"
EXISTS_SUBJECT = "You already have an account on the discussion forum"


def _days_text():
    days = settings.EMAIL_CONFIRM_MAX_AGE_DAYS
    return f"{days} day{pluralize(days)}"


def _send(subject, body, address, kind):
    try:
        message = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [address])
        message.send(using="default")
    except Exception as exc:
        logger.error("could not send the %s email (%s); the account is kept and the user can request a new link", kind, type(exc).__name__)
        return False
    return True


def confirmation_body(request, user):
    link = request.build_absolute_uri(reverse("accounts:confirm", args=[make_token(user)]))
    return (
        f"Hello {user.username},\n\n"
        "To finish creating your account on the discussion forum, open this link to confirm your email address:\n\n"
        f"{link}\n\n"
        f"The link expires in {_days_text()} and works once. If it has expired, you can request a new one at "
        f"{request.build_absolute_uri(reverse('accounts:resend'))}\n\n"
        "If you did not ask for this, you can ignore this email; no account will be activated.\n"
    )


def send_confirmation_email(request, user):
    """Send the confirmation link. Returns True if the mailer accepted the message."""
    return _send(CONFIRM_SUBJECT, confirmation_body(request, user), user.email, "confirmation")


def send_account_exists_email(request, user):
    """Sent when someone registers with an address that already has a confirmed account."""
    login_url = request.build_absolute_uri(reverse("accounts:login"))
    reset_url = request.build_absolute_uri(reverse("accounts:password_reset"))
    body = (
        f"Hello {user.username},\n\n"
        "Someone tried to create a new account on the discussion forum with this email address, but an account "
        "already exists for it, so no new account was made.\n\n"
        f"You can log in here: {login_url}\n"
        f"If you have forgotten your password, you can reset it here: {reset_url}\n\n"
        "If this was not you, you can ignore this email; nothing has changed on your account.\n"
    )
    return _send(EXISTS_SUBJECT, body, user.email, "account-exists")
