"""URL table for the accounts app. Two independent modules each provide their own `urlpatterns`:
registration.py (register, confirm, resend; step 6a) and authviews.py (login, logout, password reset and change; step 6b).
Reverse names use the `accounts:` namespace, for example reverse("accounts:login")."""
from django.urls import path

from . import authviews, registration

app_name = "accounts"

urlpatterns = [*registration.urlpatterns, *authviews.urlpatterns]
