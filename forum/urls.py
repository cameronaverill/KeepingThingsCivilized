"""Forum URLs (step 7b). Namespace ``forum``; included at the site root by ``config/urls.py``."""
from django.urls import path

from . import views

app_name = "forum"

urlpatterns = [
    path("", views.home, name="home"),
    path("propose/", views.propose, name="propose"),
    path("p/<int:topic_id>/enter/", views.enter, name="enter"),
    path("c/<int:conversation_id>/", views.conversation, name="conversation"),
    path("c/<int:conversation_id>/post/", views.post, name="post"),
    path("c/<int:conversation_id>/end/", views.end, name="end"),
    path("c/<int:conversation_id>/messages/", views.messages, name="messages"),
    path("how-it-works/", views.how_it_works, name="how_it_works"),
]
