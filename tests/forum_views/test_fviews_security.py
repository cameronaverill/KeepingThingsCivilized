"""7b: CSRF on every POST, no-store caching, and error pages that show no internals (also under DEBUG=False)."""
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse

import fviews_html as H
import fviews_kit as K

INTERNAL_MARKERS = ["Traceback", "RuntimeError", "secret-internal-xyz", "/workspace", "site-packages", "forum/services",
                    "forum/views", ".py\", line", "Exception Value", "DoesNotExist", "OperationalError", "IntegrityError"]


def no_internals(response):
    body = response.content.decode()
    found = [m for m in INTERNAL_MARKERS if m in body]
    assert not found, f"internal text on an error page: {found}"


# --- CSRF -------------------------------------------------------------------------------------------------------------

def post_targets(duo, field_names):
    return [
        ("propose", reverse("forum:propose"), {field_names["proposition"]: "A brand new csrf proposition."}),
        ("enter", reverse("forum:enter", args=[duo.topic.pk]), {"side": "pro"}),
        ("post", duo.post_url, {field_names["message"]: "csrf message"}),
        ("end", duo.end_url, {}),
    ]


def snapshot(duo):
    from forum.models import Conversation, Message, Topic

    return (Topic.objects.count(), Conversation.objects.count(), Message.objects.count(), K.fresh(duo.conv).status,
            K.runs().count())


@pytest.mark.parametrize("which", ["propose", "enter", "post", "end"])
def test_a_post_without_a_csrf_token_is_refused_and_changes_nothing(which, field_names):
    duo = K.Duo(csrf=True)
    url, data = next((u, d) for n, u, d in post_targets(duo, field_names) if n == which)
    before = snapshot(duo)
    response = duo.ca.post(url, data)
    assert response.status_code == 403
    assert snapshot(duo) == before
    no_internals(response)


@pytest.mark.parametrize("which", ["propose", "enter", "post", "end"])
def test_a_post_with_a_wrong_csrf_token_is_refused(which, field_names):
    duo = K.Duo(csrf=True)
    K.csrf_token(duo.ca)
    url, data = next((u, d) for n, u, d in post_targets(duo, field_names) if n == which)
    before = snapshot(duo)
    response = duo.ca.post(url, {**data, "csrfmiddlewaretoken": "x" * 64})
    assert response.status_code == 403
    assert snapshot(duo) == before


@pytest.mark.parametrize("which", ["propose", "enter", "post", "end"])
def test_a_post_with_the_token_works_for_every_state_changing_url(which, field_names):
    duo = K.Duo(csrf=True)
    url, data = next((u, d) for n, u, d in post_targets(duo, field_names) if n == which)
    response = K.csrf_post(duo.ca, url, data)
    assert response.status_code == 302, f"{which} with a valid token gave {response.status_code}"


def test_the_token_in_the_header_also_counts_like_a_browser_script_would_send(field_names):
    duo = K.Duo(csrf=True)
    token = K.csrf_token(duo.ca)
    response = duo.ca.post(duo.post_url, {field_names["message"]: "header token"}, HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 302


def test_every_form_on_every_page_that_posts_carries_a_token(field_names):
    duo = K.Duo()
    duo.seed(duo.pa, "hello", 30)
    pages = [reverse("forum:home"), reverse("forum:propose"), duo.url, reverse("forum:how_it_works")]
    for url in pages:
        for form in H.forms(H.doc(duo.ca.get(url))):
            if form.get("method", "get").lower() == "post":
                assert H.hidden_csrf(form), f"{url}: a POST form without a CSRF token: {form.attrs}"
            else:
                assert not H.hidden_csrf(form), f"{url}: a GET form must not carry the token in the URL"


# --- caching ----------------------------------------------------------------------------------------------------------

def test_conversation_pages_and_polling_are_never_cached():
    duo = K.Duo()
    waiting_user = K.make_user()
    waiting = K.enter(waiting_user, K.make_topic("Waiting one.", created_by=waiting_user))
    duo.seed(duo.pa, "x", 30)
    urls = [(duo.ca, duo.url), (duo.ca, duo.poll_url),
            (K.client_for(waiting_user), reverse("forum:conversation", args=[waiting.pk])),
            (K.client_for(waiting_user), reverse("forum:messages", args=[waiting.pk]))]
    for client, url in urls:
        assert "no-store" in client.get(url)["Cache-Control"], url
    duo.ca.post(duo.end_url)
    assert "no-store" in duo.cb.get(duo.url)["Cache-Control"], "the closed page too"
    assert "no-store" in duo.cb.get(duo.poll_url)["Cache-Control"]


def test_a_refused_post_page_is_not_cached_either(field_names):
    duo = K.Duo()
    response = duo.ca.post(duo.post_url, {field_names["message"]: ""})
    assert response.status_code == 200 and "no-store" in response["Cache-Control"]


# --- DEBUG=False and error pages --------------------------------------------------------------------------------------

@pytest.fixture
def production_like(settings):
    settings.DEBUG = False
    return settings


def test_every_page_renders_with_debug_off(production_like, field_names):
    duo = K.Duo()
    duo.seed(duo.pa, "hello", 30)
    waiting_user = K.make_user()
    waiting = K.enter(waiting_user, K.make_topic("Waiting one.", created_by=waiting_user))
    urls = ["/", "/propose/", "/how-it-works/", duo.url, duo.poll_url, "/?q=zzz"]
    for url in urls:
        assert duo.ca.get(url).status_code == 200, url
    assert K.client_for(waiting_user).get(reverse("forum:conversation", args=[waiting.pk])).status_code == 200
    duo.ca.post(duo.end_url)
    assert duo.cb.get(duo.url).status_code == 200
    assert Client().get("/how-it-works/").status_code == 200


def test_error_templates_exist_in_the_forum_app():
    from django.template.loader import get_template

    for name in ("400.html", "403.html", "404.html", "500.html"):
        template = get_template(name)
        assert "forum/templates" in template.origin.name.replace("\\", "/"), f"{name} comes from {template.origin.name}"


def test_a_plain_404_page_is_friendly_and_shows_no_internals(production_like):
    response = K.client_for(K.make_user()).get("/no/such/page/")
    assert response.status_code == 404
    no_internals(response)
    assert "<html" in response.content.decode().lower()
    assert "Not Found" not in response.content.decode() or "<title>" in response.content.decode()


def test_a_bad_host_header_gets_the_400_page_without_internals(production_like):
    response = Client().get("/how-it-works/", HTTP_HOST="evil.example.com")
    assert response.status_code == 400
    body = response.content.decode()
    assert "ALLOWED_HOSTS" not in body and "evil.example.com" not in body and "Invalid HTTP_HOST" not in body
    no_internals(response)


def test_a_csrf_failure_page_shows_no_internals(production_like, field_names):
    duo = K.Duo(csrf=True)
    response = duo.ca.post(duo.post_url, {field_names["message"]: "no token"})
    assert response.status_code == 403
    body = response.content.decode()
    assert "CSRF token missing" not in body and "Reason given" not in body
    no_internals(response)


SECRET = "secret-internal-xyz in /workspace/forum/services.py line 1 RuntimeError Traceback"


def test_an_unexpected_failure_while_posting_shows_a_friendly_page_and_keeps_the_text(production_like, field_names):
    duo = K.Duo()
    client = K.client_for(duo.ua)
    client.raise_request_exception = False
    with mock.patch("forum.models.Message.save", side_effect=RuntimeError(SECRET)):
        response = client.post(duo.post_url, {field_names["message"]: "My careful text."})
    assert response.status_code in (200, 500)
    no_internals(response)
    assert response.content.strip(), "an error page has words on it"
    assert K.user_messages(duo.conv).count() == 0
    assert K.runs(duo.conv).count() == 0
    if response.status_code == 200:
        assert "My careful text." in H.control_value(H.text_control(H.form_with_action(H.doc(response), duo.post_url)))
    else:
        assert "wrong" in response.content.decode().lower() or "error" in response.content.decode().lower()


def test_an_unexpected_failure_while_entering_or_proposing_shows_no_internals(production_like, field_names):
    from forum.models import Participant, Topic

    client = K.client_for(K.make_user())
    client.raise_request_exception = False
    topic = K.make_topic("Failing proposition.", created_by=K.make_user())
    with mock.patch.object(Participant, "save", side_effect=RuntimeError(SECRET)):
        response = client.post(reverse("forum:enter", args=[topic.pk]), {"side": "pro"})
    assert response.status_code in (200, 500)
    no_internals(response)
    with mock.patch.object(Topic, "save", side_effect=RuntimeError(SECRET)):
        response = client.post(reverse("forum:propose"), {field_names["proposition"]: "Also failing."})
    assert response.status_code in (200, 500)
    no_internals(response)


def test_an_unexpected_failure_on_a_get_page_shows_no_internals(production_like):
    """The first template render of the request blows up (as a bug in a page would); the 500 page must still be clean."""
    from django.template.backends.django import Template as BackendTemplate

    real = BackendTemplate.render
    state = {"calls": 0}

    def first_render_fails(self, context=None, request=None):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError(SECRET)
        return real(self, context, request)

    duo = K.Duo()
    client = K.client_for(duo.ua)
    client.raise_request_exception = False
    with mock.patch.object(BackendTemplate, "render", first_render_fails):
        response = client.get(duo.url)
    assert state["calls"] >= 1, "the conversation page must be rendered through a template"
    assert response.status_code == 500
    no_internals(response)
    assert response.content.strip()
