"""A throwaway URL table for the LOGIN_URL test: the real routes plus one page that needs a login."""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.urls import path

from config.urls import urlpatterns as real_urlpatterns


@login_required
def secret(request):
    return HttpResponse("the secret page")


urlpatterns = [path("secret/", secret, name="auth_test_secret"), *real_urlpatterns]
