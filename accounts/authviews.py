"""Login, logout, login throttling and password change (steps 6b and 6c).

Throttling is django-axes (settings in config/settings.py, driven by the LOGIN_* tunables). Two hooks in this module are
named in those settings: axes_username() and lockout_response().

Decisions worth knowing:
- Accounts are username + password only (step 6c): no email, no confirmation, no reset by email. A forgotten password is
  reset by the site owner (`manage.py changepassword <username>`).
- A wrong username and a wrong password give the same message.
"""
import math

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm
from django.db.models import Max, Sum
from django.shortcuts import render
from django.urls import path, reverse_lazy
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.views.decorators.debug import sensitive_variables

from .keys import normalize_key

INVALID_LOGIN = "Incorrect username or password. Check both and try again."


def _minutes(count):
    return f"{count} minute" if count == 1 else f"{count} minutes"


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
        "This pause protects accounts from password guessing."
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
    }
    lockout = render(request, "accounts/login.html", context, status=429)
    add_never_cache_headers(lockout)
    return lockout


# --- login and logout -------------------------------------------------------------------


class LoginForm(AuthenticationForm):
    error_messages = {
        "invalid_login": INVALID_LOGIN,
        "inactive": INVALID_LOGIN,  # an inactive account cannot authenticate; the same generic message if it ever gets here
    }

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
            raise self.get_invalid_login_error()
        self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


class LoginView(auth_views.LoginView):
    form_class = LoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


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
    path("password-change/", PasswordChangeView.as_view(), name="password_change"),
    path("password-change/done/", PasswordChangeDoneView.as_view(), name="password_change_done"),
]
