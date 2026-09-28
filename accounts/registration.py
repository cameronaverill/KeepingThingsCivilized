"""Registration (step 6, revised by step 6c): a username and a password, nothing else.

Design notes
- No email, no confirmation link, no "check your email" page. A valid form creates an ACTIVE account, logs the person
  in at once and sends them to the forum home page.
- A taken username is said plainly ("That username is taken."); there is nothing to protect by hiding it. The check
  compares the derived username key (case and Unicode form folded), the same rule the model enforces.
- Nothing is created unless every check passes. If another registration takes the same name a moment later, the model's
  save() raises a ValidationError and the form shows the same "taken" error (the unique constraint is the backstop).
- Logging in uses the same backend the login view ends up with (ModelBackend, behind django-axes' backend), so the
  session looks exactly like one from the login page. Registering is not a failed login and is not throttled here.
"""
import logging

from django import forms
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.password_validation import password_validators_help_texts, validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import path
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from .keys import normalize_key
from .models import User
from .validators import validate_username

logger = logging.getLogger(__name__)

USERNAME_TAKEN_MESSAGE = "That username is taken."
LOGIN_BACKEND = "django.contrib.auth.backends.ModelBackend"


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
            raise ValidationError(USERNAME_TAKEN_MESSAGE, code="username_taken")
        return username

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get("password1"), cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two passwords do not match. Type the same password in both boxes.")
        elif password1:
            candidate = User(username=cleaned.get("username", ""))
            try:
                validate_password(password1, candidate)
            except ValidationError as error:
                self.add_error("password1", error)
        return cleaned


def _create_user(form):
    """Create the active account. Returns the user, or None (with an error on the form) if the name was just taken."""
    data = form.cleaned_data
    user = User(username=data["username"], is_active=True)
    user.set_password(data["password1"])
    try:
        with transaction.atomic():
            user.save()
    except ValidationError as error:
        errors = error.message_dict if hasattr(error, "error_dict") else {}
        if "username" in errors:
            form.add_error("username", USERNAME_TAKEN_MESSAGE)
            logger.info("register: rejected, username taken")
        else:
            form.add_error(None, error)
        return None
    return user


def _render_form(request, form):
    return render(request, "accounts/register.html", {"form": form, "password_help": password_validators_help_texts()})


@sensitive_post_parameters("password1", "password2")
@require_http_methods(["GET", "HEAD", "POST"])
def register(request):
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = _create_user(form)
        if user is not None:
            logger.info("register: user %s registered", user.pk)
            login(request, user, backend=LOGIN_BACKEND)
            return redirect("forum:home")
    return _render_form(request, form)


urlpatterns = [
    path("register/", register, name="register"),
]
