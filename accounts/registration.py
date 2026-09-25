"""Registration, email confirmation and resend (step 6a).

Design notes
- No email enumeration: registering with an address that already has an account looks exactly like a fresh
  registration (same redirect, same page). Only the email differs: an existing confirmed account is told, by email,
  that it already exists; a pending one gets a fresh link. The password is hashed in every case so the response time
  does not give the answer away either.
- Resend limit: kept in the cache, keyed by a hash of the normalised address, and applied to EVERY submitted
  address, known or not (a database column could only exist for known users, so the limit would tell known and
  unknown addresses apart). cache.add() is atomic, so two simultaneous requests cannot both pass. Note: the default
  cache is per-process (locmem); a multi-process deployment needs a shared cache backend for the limit to be global.
- The confirmation link is single-use (see tokens.py) and never logs the user in.
"""
import hashlib
import logging
import math
import time

from django import forms
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import password_validators_help_texts, validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import path
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import tokens
from .emails import send_account_exists_email, send_confirmation_email
from .keys import normalize_key
from .models import USERNAME_TAKEN, User
from .validators import validate_username

logger = logging.getLogger(__name__)

EMAIL_MAX_LENGTH = 254
RESEND_ANSWER = "If that address has an unconfirmed account, we sent a new link. It can take a few minutes to arrive."

# --- Resend limit ---------------------------------------------------------------------------------------------------


def _limit_key(email, purpose="confirm"):
    return f"accounts:{purpose}:" + hashlib.sha256(normalize_key(email).encode()).hexdigest()


def _limit_seconds():
    return settings.RESEND_CONFIRMATION_MIN_SECONDS


def claim_send(email, purpose="confirm"):
    """Try to use up this address's turn. Returns 0 if allowed, else the whole seconds still to wait.
    Confirmation links (registering and resending share one turn) and "account already exists" notices have separate
    turns, so a fresh registration does not swallow the notice for a confirmed owner."""
    key = _limit_key(email, purpose)
    for _ in range(3):
        if cache.add(key, time.time(), _limit_seconds()):
            return 0
        stamp = cache.get(key)
        if stamp is not None:
            return max(1, math.ceil(_limit_seconds() - (time.time() - stamp)))
    return 0


def release_send(email, purpose="confirm"):
    """Give the turn back after a failed send, so a mail outage does not also make the user wait."""
    cache.delete(_limit_key(email, purpose))


def mark_sent(email):
    """Start (or restart) the wait for this address; used when registering, which always counts as a send."""
    cache.set(_limit_key(email), time.time(), _limit_seconds())


# --- Forms ---------------------------------------------------------------------------------------------------------


class EmailField(forms.EmailField):
    default_error_messages = {
        "required": "An email address is required. Enter the address where we should send the link.",
        "invalid": "Enter a valid email address, for example name@example.com.",
    }


def _clean_email(value):
    value = (value or "").strip()
    if len(value) > EMAIL_MAX_LENGTH:
        raise ValidationError(f"Email addresses can be at most {EMAIL_MAX_LENGTH} characters long. Use a shorter address.")
    return value


class RegisterForm(forms.Form):
    username = forms.CharField(
        label="Username",
        strip=True,
        error_messages={"required": "A username is required. Choose one."},
        widget=forms.TextInput(attrs={"autocomplete": "username", "autocapitalize": "none", "spellcheck": "false"}),
        help_text=(
            f"{settings.USERNAME_MIN_LENGTH} to {settings.USERNAME_MAX_LENGTH} characters: "
            "letters A-Z, digits, hyphens and underscores."
        ),
    )
    email = EmailField(
        label="Email address",
        max_length=None,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
        help_text="We send a confirmation link here.",
    )
    password1 = forms.CharField(
        label="Password",
        strip=False,
        error_messages={"required": "A password is required. Choose one."},
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Password again",
        strip=False,
        error_messages={"required": "Confirming the password is required. Type it again to make sure it is what you meant."},
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_username(self):
        username = self.cleaned_data["username"]
        validate_username(username)
        if User.objects.filter(username_key=normalize_key(username)).exists():
            raise ValidationError(f"{USERNAME_TAKEN} Please choose a different one.")
        return username

    def clean_email(self):
        return _clean_email(self.cleaned_data["email"])

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get("password1"), cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two passwords do not match. Type the same password in both boxes.")
        elif password1:
            candidate = User(username=cleaned.get("username", ""), email=cleaned.get("email", ""))
            try:
                validate_password(password1, candidate)
            except ValidationError as error:
                self.add_error("password1", error)
        return cleaned


class ResendForm(forms.Form):
    email = EmailField(
        label="Email address",
        max_length=None,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )

    def clean_email(self):
        return _clean_email(self.cleaned_data["email"])


# --- Views ---------------------------------------------------------------------------------------------------------


def _existing_user(email):
    return User.objects.filter(email_key=normalize_key(email)).first()


def _create_pending_user(form):
    """Create the inactive account. Returns the user, or None if the address turned out to belong to someone
    (a race with another registration), or the username was taken a moment ago (an error is added to the form)."""
    data = form.cleaned_data
    user = User(username=data["username"], email=data["email"], is_active=False)
    user.set_password(data["password1"])
    try:
        user.save()
    except ValidationError as error:
        errors = error.message_dict if hasattr(error, "error_dict") else {}
        if "username" in errors:
            for message in errors["username"]:
                form.add_error("username", message)
        return None
    return user


@require_http_methods(["GET", "HEAD", "POST"])
def register(request):
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        existing = _existing_user(data["email"])
        if existing is None:
            user = _create_pending_user(form)
            if user is None:
                if form.errors:
                    context = {"form": form, "password_help": password_validators_help_texts()}
                    return render(request, "accounts/register.html", context)
                existing = _existing_user(data["email"])  # lost a race for this address: treat as existing
            else:
                mark_sent(user.email)
                if not send_confirmation_email(request, user):
                    release_send(user.email)
        if existing is not None:
            make_password(data["password1"])  # same work as a real registration, so timing does not differ
            if tokens.is_pending(existing):
                if claim_send(existing.email) == 0 and not send_confirmation_email(request, existing):
                    release_send(existing.email)
            elif claim_send(existing.email, "exists") == 0 and not send_account_exists_email(request, existing):
                release_send(existing.email, "exists")
        return redirect("accounts:check_email")
    return render(request, "accounts/register.html", {"form": form, "password_help": password_validators_help_texts()})


@require_http_methods(["GET", "HEAD"])
def check_email(request):
    return render(request, "accounts/check_email.html", {"expiry_days": settings.EMAIL_CONFIRM_MAX_AGE_DAYS})


@require_http_methods(["GET", "HEAD"])
def confirm(request, token):
    result = tokens.check_token(token)
    if result.status == tokens.OK:
        # One conditional UPDATE, so two simultaneous clicks cannot both succeed.
        changed = User.objects.filter(pk=result.user.pk, is_active=False, email_verified_at__isnull=True).update(
            is_active=True, email_verified_at=timezone.now()
        )
        if changed:
            return render(request, "accounts/confirm_done.html")
        return render(request, "accounts/confirm_used.html")
    template = {
        tokens.EXPIRED: "accounts/confirm_expired.html",
        tokens.USED: "accounts/confirm_used.html",
    }.get(result.status, "accounts/confirm_invalid.html")
    context = {"form": ResendForm(), "expiry_days": settings.EMAIL_CONFIRM_MAX_AGE_DAYS}
    return render(request, template, context)


@require_http_methods(["GET", "HEAD", "POST"])
def resend(request):
    form = ResendForm(request.POST or None)
    context = {"form": form, "expiry_days": settings.EMAIL_CONFIRM_MAX_AGE_DAYS}
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        wait = claim_send(email)  # applies to every address, existing or not, so the answer never differs
        if wait:
            context["wait_seconds"] = wait
        else:
            user = _existing_user(email)
            if user is not None and tokens.is_pending(user):
                if not send_confirmation_email(request, user):
                    release_send(email)
            context["answer"] = RESEND_ANSWER
            context["form"] = ResendForm()
    return render(request, "accounts/resend.html", context)


urlpatterns = [
    path("register/", register, name="register"),
    path("register/check-email/", check_email, name="check_email"),
    path("confirm/<path:token>/", confirm, name="confirm"),
    path("resend-confirmation/", resend, name="resend"),
]
