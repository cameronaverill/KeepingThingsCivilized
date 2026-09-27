"""7b: every URL's method rules, login redirects and the public pages (docs/step7_brief.md, "7b: pages")."""
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import Client
from django.urls import reverse

import fviews_html as H
import fviews_kit as K


def login_path():
    return reverse("accounts:login")


def assert_login_redirect(response, wanted_next):
    assert response.status_code == 302, f"expected a redirect to the login page, got {response.status_code}"
    parsed = urlparse(response["Location"])
    assert parsed.path == login_path()
    assert parse_qs(parsed.query).get("next") == [wanted_next]


# --- the URL table itself ---------------------------------------------------------------------------------------------

def test_url_paths_are_the_contract_paths():
    assert reverse("forum:home") == "/"
    assert reverse("forum:propose") == "/propose/"
    assert reverse("forum:enter", args=[7]) == "/p/7/enter/"
    assert reverse("forum:conversation", args=[7]) == "/c/7/"
    assert reverse("forum:post", args=[7]) == "/c/7/post/"
    assert reverse("forum:end", args=[7]) == "/c/7/end/"
    assert reverse("forum:messages", args=[7]) == "/c/7/messages/"
    assert reverse("forum:how_it_works") == "/how-it-works/"
    assert reverse("forum:mine") == "/discussions/" and reverse("forum:blocked") == "/blocked/"


# --- login redirect ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, args", [
    ("forum:home", []),
    ("forum:propose", []),
    ("forum:conversation", [1]),
    ("forum:messages", [1]),
])
def test_anonymous_get_is_sent_to_login_with_next(name, args):
    url = reverse(name, args=args)
    assert_login_redirect(Client().get(url), url)


def test_next_keeps_the_search_query_on_the_home_page():
    response = Client().get("/?q=cats")
    assert response.status_code == 302
    nxt = parse_qs(urlparse(response["Location"]).query)["next"][0]
    assert nxt == "/?q=cats"


@pytest.mark.parametrize("name", ["forum:propose", "forum:enter", "forum:post", "forum:end"])
def test_anonymous_post_is_sent_to_login_and_changes_nothing(name, field_names):
    from forum.models import Conversation, Message, Topic

    duo = K.Duo()
    topics, convs, msgs = Topic.objects.count(), Conversation.objects.count(), Message.objects.count()
    args = {"forum:propose": [], "forum:enter": [duo.topic.pk], "forum:post": [duo.conv.pk], "forum:end": [duo.conv.pk]}[name]
    url = reverse(name, args=args)
    response = Client().post(url, {field_names["message"]: "hello", field_names["proposition"]: "Anonymous proposition text."})
    assert_login_redirect(response, url)
    assert (Topic.objects.count(), Conversation.objects.count(), Message.objects.count()) == (topics, convs, msgs)
    assert K.fresh(duo.conv).status == "active"


def test_anonymous_never_gets_a_page_from_a_post_only_url_on_get():
    duo = K.Duo()
    for name, args in [("forum:enter", [duo.topic.pk]), ("forum:post", [duo.conv.pk]), ("forum:end", [duo.conv.pk])]:
        response = Client().get(reverse(name, args=args))
        assert response.status_code in (302, 405)
        if response.status_code == 302:
            assert urlparse(response["Location"]).path == login_path()


# --- public pages -----------------------------------------------------------------------------------------------------

def test_how_it_works_is_public():
    response = Client().get(reverse("forum:how_it_works"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")


def test_how_it_works_is_also_served_to_signed_in_users():
    assert K.client_for(K.make_user()).get(reverse("forum:how_it_works")).status_code == 200


@pytest.mark.parametrize("name", ["accounts:login", "accounts:register"])
def test_accounts_pages_stay_public(name):
    assert Client().get(reverse(name)).status_code == 200


# --- method rules (signed in) -----------------------------------------------------------------------------------------

ALL_METHODS = ["get", "post", "put", "patch", "delete"]


def method_table(duo):
    """(url name, args, allowed methods)."""
    return [
        ("forum:home", [], {"get"}),
        ("forum:propose", [], {"get", "post"}),
        ("forum:enter", [duo.topic.pk], {"post"}),
        ("forum:conversation", [duo.conv.pk], {"get"}),
        ("forum:post", [duo.conv.pk], {"post"}),
        ("forum:end", [duo.conv.pk], {"post"}),
        ("forum:messages", [duo.conv.pk], {"get"}),
        ("forum:how_it_works", [], {"get"}),
        ("forum:mine", [], {"get"}),
        ("forum:blocked", [], {"get"}),
        ("forum:block", ["quincy_ray"], {"post"}),
        ("forum:unblock", ["quincy_ray"], {"post"}),
    ]


def test_every_url_refuses_the_methods_it_does_not_take():
    duo = K.Duo()
    for name, args, allowed in method_table(duo):
        url = reverse(name, args=args)
        for method in ALL_METHODS:
            if method in allowed:
                continue
            client = K.client_for(duo.ua)
            response = getattr(client, method)(url)
            assert response.status_code == 405, f"{method.upper()} {url} gave {response.status_code}, wanted 405"


def test_get_never_changes_state_on_the_post_only_urls():
    from forum.models import Conversation, Message, Topic

    duo = K.Duo()
    before = (Topic.objects.count(), Conversation.objects.count(), Message.objects.count())
    for name, args in [("forum:enter", [duo.topic.pk]), ("forum:post", [duo.conv.pk]), ("forum:end", [duo.conv.pk])]:
        duo.ca.get(reverse(name, args=args) + "?text=hello&confirm=1")
    assert (Topic.objects.count(), Conversation.objects.count(), Message.objects.count()) == before
    assert K.fresh(duo.conv).status == "active"


def test_signed_in_gets_ok_pages_from_the_get_urls():
    duo = K.Duo()
    for name, args, allowed in method_table(duo):
        if "get" in allowed:
            response = duo.ca.get(reverse(name, args=args))
            assert response.status_code == 200, name
            assert response["Content-Type"].startswith("text/html") or name == "forum:messages"


# --- nothing that does not exist --------------------------------------------------------------------------------------

def test_entering_a_proposition_that_does_not_exist_is_a_404(field_names):
    response = K.client_for(K.make_user()).post(reverse("forum:enter", args=[987654]))
    assert response.status_code == 404


@pytest.mark.parametrize("name, method", [
    ("forum:conversation", "get"),
    ("forum:messages", "get"),
    ("forum:post", "post"),
    ("forum:end", "post"),
])
def test_a_conversation_that_does_not_exist_is_a_404(name, method):
    client = K.client_for(K.make_user())
    response = getattr(client, method)(reverse(name, args=[987654]))
    assert response.status_code == 404


def test_a_non_numeric_id_is_not_routed():
    client = K.client_for(K.make_user())
    assert client.get("/c/abc/").status_code == 404
    assert client.post("/p/abc/enter/").status_code == 404


def test_the_404_page_of_a_missing_conversation_says_so_in_the_contract_words():
    response = K.client_for(K.make_user()).get(reverse("forum:conversation", args=[987654]))
    assert K.NOT_FOUND_TEXT in H.unescape(response.content.decode())
