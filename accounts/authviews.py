"""Login, logout, login throttling, password reset and change (step 6b).

Throttling is django-axes (settings in config/settings.py, driven by the LOGIN_* tunables). Two hooks in this module are
named in those settings: axes_username() and lockout_response().

Decisions worth knowing:
- Unconfirmed (inactive) accounts do NOT receive password reset emails. An address nobody has confirmed should not be
  mailed on request (that would let anyone use the form to send mail to a stranger), and the reset page must not reveal
  whether an account exists. The "sent" page tells people who never confirmed to use the resend link instead.
- A wrong username and a wrong password give the same message. The special "please confirm your email" message is only
  shown when the password is also correct, so it reveals nothing to someone who does not know the password.
"""
import logging
import math

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.template import loader
from django.db.models import Max, Sum
from django.shortcuts import render
from django.urls import NoReverseMatch, path, reverse, reverse_lazy
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.views.decorators.debug import sensitive_variables

from .keys import normalize_key

INVALID_LOGIN = (
    "Incorrect username or password. Check both and try again, or reset your password if you have forgotten it."
)
UNCONFIRMED = (
    "Please confirm your email first; we sent you a link when you registered. "
    "The link may be in your spam folder; if it has expired or is lost, you can ask for a new one."
)


def _minutes(count):
    return f"{count} minute" if count == 1 else f"{count} minutes"


def _resend_url():
    """URL of the 6a resend page, or None if that page does not exist (yet)."""
    try:
        return reverse("accounts:resend")
    except NoReverseMatch:
        return None


# --- django-axes hooks ------------------------------------------------------------------


def axes_username(request, credentials):
    """The username axes counts failures against: the account's comparison key, so 'Bob' and 'bob' share a counter."""
    username = (credentials or {}).get("username")
    if username is None and request is not None:
        username = request.POST.get("username")
    return normalize_key(username) if isinstance(username, str) else username


def _lockout_minutes(request, credentials):
    """Whole minutes (rounded up) until the lock that applies to this request ends; the full cool-off if unknown."""
    from axes.handlers.proxy import AxesProxyHandler

    cooloff = settings.AXES_COOLOFF_TIME
    limit = settings.AXES_FAILURE_LIMIT
    latest = None
    try:
        for attempts in AxesProxyHandler.get_implementation().get_user_attempts(request, credentials):
            totals = attempts.aggregate(failures=Sum("failures_since_start"), last=Max("attempt_time"))
            if (totals["failures"] or 0) >= limit and totals["last"] is not None:
                latest = totals["last"] if latest is None else max(latest, totals["last"])
    except Exception:  # never let the wait calculation break the lockout page
        latest = None
    if latest is None:
        return max(1, math.ceil(cooloff.total_seconds() / 60))
    now = getattr(request, "axes_attempt_time", None) or timezone.now()  # axes' own clock for this request
    remaining = (latest + cooloff - now).total_seconds()
    return max(1, min(math.ceil(remaining / 60), math.ceil(cooloff.total_seconds() / 60)))


def lockout_message(minutes):
    return (
        f"Too many failed attempts. Try again in {_minutes(minutes)}. "
        "This pause protects accounts from password guessing. If you have forgotten your password, "
        "you can reset it by email instead."
    )


def lockout_response(request, response=None, credentials=None):
    """The response axes gives a locked-out client: the login page with the wait stated (HTTP 429)."""
    minutes = _lockout_minutes(request, credentials)
    posted = request.POST.get("username", "") if request.method == "POST" else ""
    form = LoginForm(request, initial={"username": posted})
    context = {
        "form": form,
        "locked_out": True,
        "lockout_message": lockout_message(minutes),
        "lockout_minutes": minutes,
        "next": request.POST.get("next", request.GET.get("next", "")),
        "resend_url": None,
    }
    lockout = render(request, "accounts/login.html", context, status=429)
    add_never_cache_headers(lockout)
    return lockout


# --- login and logout -------------------------------------------------------------------


class LoginForm(AuthenticationForm):
    error_messages = {
        "invalid_login": INVALID_LOGIN,
        "inactive": UNCONFIRMED,
    }

    unconfirmed = False

    @sensitive_variables()
    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username is None or not password:
            return self.cleaned_data
        User = get_user_model()
        # Accept any spelling of the username that the site treats as the same account.
        account = User._default_manager.filter(username_key=normalize_key(username)).first()
        real_username = account.get_username() if account else username
        self.user_cache = authenticate(self.request, username=real_username, password=password)
        if self.user_cache is None:
            locked = getattr(self.request, "axes_locked_out", False)
            if account is not None and not account.is_active and not locked and account.check_password(password):
                self.unconfirmed = True
                raise ValidationError(self.error_messages["inactive"], code="inactive")
            raise self.get_invalid_login_error()
        self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


class LoginView(auth_views.LoginView):
    form_class = LoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get("form")
        context["resend_url"] = _resend_url() if getattr(form, "unconfirmed", False) else None
        return context


class LogoutView(auth_views.LogoutView):
    """POST only. A GET gets a page that says why and offers the button, instead of an empty 405."""

    def http_method_not_allowed(self, request, *args, **kwargs):
        if request.method == "GET":
            response = render(request, "accounts/logout_confirm.html", status=405)
            response["Allow"] = "POST, OPTIONS"
            return response
        return super().http_method_not_allowed(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.success(request, "You have been logged out.")
        return response


# --- password reset ---------------------------------------------------------------------


class ResetForm(PasswordResetForm):
    def get_users(self, email):
        """Active accounts with a usable password whose address is the same after normalisation (case, Unicode form)."""
        key = normalize_key(email)
        candidates = get_user_model()._default_manager.filter(email_key=key, is_active=True)
        return (user for user in candidates if user.has_usable_password())

    def send_mail(
        self, subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name=None
    ):
        """Plain text, through the default MAILERS mailer. Failures are logged without the address or link."""
        subject = "".join(self._render(subject_template_name, context).splitlines())
        body = self._render(email_template_name, context)
        message = EmailMultiAlternatives(subject, body, from_email or settings.DEFAULT_FROM_EMAIL, [to_email])
        try:
            mail.mailers.default.send_messages([message])
        except Exception:
            logging.getLogger(__name__).exception("Could not send a password reset email (user id %s)", context["user"].pk)

    @staticmethod
    def _render(template_name, context):
        return loader.render_to_string(template_name, context)


class PasswordResetView(auth_views.PasswordResetView):
    form_class = ResetForm
    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")
    from_email = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["resend_url"] = _resend_url()
        return context

    def form_valid(self, form):
        self.extra_email_context = {
            "expiry_days": max(1, round(settings.PASSWORD_RESET_TIMEOUT / 86400)),
            "login_path": reverse("accounts:login"),
        }
        return super().form_valid(form)


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["resend_url"] = _resend_url()
        return context


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


# --- password change --------------------------------------------------------------------


class PasswordChangeView(auth_views.PasswordChangeView):
    """Login required; the session stays logged in (Django calls update_session_auth_hash)."""

    template_name = "accounts/password_change_form.html"
    success_url = reverse_lazy("accounts:password_change_done")


class PasswordChangeDoneView(auth_views.PasswordChangeDoneView):
    template_name = "accounts/password_change_done.html"


urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("password-reset/", PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("reset/complete/", PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("reset/<uidb64>/<token>/", PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password-change/", PasswordChangeView.as_view(), name="password_change"),
    path("password-change/done/", PasswordChangeDoneView.as_view(), name="password_change_done"),
]
