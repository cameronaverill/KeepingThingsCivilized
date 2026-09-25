from django.contrib import admin
from django.http import HttpResponse
from django.urls import path


def home(request):
    # Placeholder until the forum's real home page arrives in step 7.
    return HttpResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>AI-Moderated Discussion Forum</title></head>"
        "<body><h1>AI-Moderated Discussion Forum</h1>"
        "<p>The site is under construction.</p></body></html>"
    )


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", home, name="home"),
]
