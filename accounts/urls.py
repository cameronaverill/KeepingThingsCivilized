"""URL table for the accounts app. Two independent modules each provide their own `urlpatterns`:
registration.py (register: a unique username and a password, no email) and authviews.py (login, logout and password
change). Reverse names use the `accounts:` namespace, for example reverse("accounts:login")."""
from django.urls import path

from . import authviews, registration

app_name = "accounts"

urlpatterns = [*registration.urlpatterns, *authviews.urlpatterns]
