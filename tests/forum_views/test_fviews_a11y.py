"""7b: accessible names, real buttons, page shell, the How this works page, numbers taken from settings."""
import re

import pytest
from django.test import Client
from django.urls import reverse

import fviews_html as H
import fviews_kit as K


def all_pages():
    """(label, client, url) for every kind of page: forum pages in each state and the restyled accounts pages."""
    duo = K.Duo("Trains are better than planes.")
    duo.seed(duo.pa, "Token-one", 40)
    m2 = duo.seed(duo.pb, "Token-two", 39)
    K.add_moderator_post(duo.conv, m2, "Mod-token", [(duo.pb.label, duo.pb.label, [m2])])
    waiting_user = K.make_user()
    waiting = K.enter(waiting_user, K.make_topic("A proposition that is waiting.", created_by=waiting_user))
    closed = K.Duo("A proposition that is closed.", names=("closed_carl", "closed_cora"))
    closed.ca.post(closed.end_url)
    anon = Client()
    blocker = K.client_for(K.make_user("a11y_blocker"))
    blocker.post("/users/" + K.NAME_A + "/block/")
    return [
        ("home", duo.ca, "/"),
        ("home search", duo.ca, "/?q=trains"),
        ("propose", duo.ca, "/propose/"),
        ("your discussions", duo.ca, "/discussions/"),
        ("your discussions search", duo.ca, "/discussions/?q=trains"),
        ("blocked people", blocker, "/blocked/"),
        ("how it works", duo.ca, "/how-it-works/"),
        ("how it works (anonymous)", anon, "/how-it-works/"),
        ("conversation active", duo.ca, duo.url),
        ("conversation waiting", K.client_for(waiting_user), reverse("forum:conversation", args=[waiting.pk])),
        ("conversation closed", closed.cb, closed.url),
        ("login", anon, reverse("accounts:login")),
        ("register", anon, reverse("accounts:register")),
        ("password change", duo.ca, reverse("accounts:password_change")),
    ]


@pytest.fixture
def pages():
    return [(label, client.get(url)) for label, client, url in all_pages()]


def test_every_page_renders(pages):
    for label, response in pages:
        assert response.status_code == 200, label


def test_every_input_has_an_accessible_name(pages):
    for label, response in pages:
        bad = H.unlabelled_controls(H.doc(response))
        assert not bad, f"{label}: controls with no <label>/aria-label: {bad}"


def test_buttons_are_real_buttons(pages):
    for label, response in pages:
        assert not H.fake_buttons(H.doc(response)), f"{label}: {H.fake_buttons(H.doc(response))}"


def test_links_are_real_links_and_no_clickable_divs(pages):
    for label, response in pages:
        root = H.doc(response)
        assert not [a for a in root.find_all("a") if a.get("href") is None], f"{label}: <a> without href"
        assert not [n for n in root.walk() if n.tag in ("div", "span", "li") and "onclick" in n.attrs], label


def test_every_page_has_the_shell_lang_title_viewport_and_main(pages):
    for label, response in pages:
        root = H.doc(response)
        html = root.find("html")
        assert html is not None and html.get("lang"), f"{label}: <html lang>"
        title = root.find("title")
        assert title is not None and title.text().strip(), f"{label}: <title>"
        viewport = [m for m in root.find_all("meta") if m.get("name") == "viewport"]
        assert viewport and "width=device-width" in viewport[0].get("content", ""), f"{label}: viewport meta"
        assert root.find("main") is not None, f"{label}: <main> landmark"
        assert root.find("h1") is not None, f"{label}: a page heading"


def test_page_titles_are_not_all_the_same(pages):
    titles = {H.doc(r).find("title").text() for label, r in pages if label in ("home", "propose", "how it works", "login")}
    assert len(titles) >= 3


def test_every_page_uses_the_shared_stylesheet_and_only_local_assets(pages):
    for label, response in pages:
        root = H.doc(response)
        sheets = H.stylesheets(root)
        assert any(s.endswith("forum/site.css") for s in sheets), f"{label}: {sheets}"
        for url in sheets + H.scripts_src(root):
            assert url.startswith("/") and not url.startswith("//"), f"{label}: external asset {url}"


def test_signed_in_pages_have_the_header_with_a_post_logout_form(pages):
    for label, response in pages:
        if label in ("login", "register", "how it works (anonymous)"):
            continue
        root = H.doc(response)
        header = root.find("header")
        assert header is not None, label
        assert [f for f in H.forms(header) if f.get("action") == reverse("accounts:logout") and f.get("method", "").lower() == "post"], label


def test_the_active_conversation_loads_compose_js_and_poll_js_from_static_files():
    duo = K.Duo()
    sources = H.scripts_src(H.doc(duo.ca.get(duo.url)))
    assert any(s.endswith("forum/compose.js") for s in sources), sources
    assert any(s.endswith("forum/poll.js") for s in sources), sources


def test_the_propose_page_loads_the_counter_script():
    sources = H.scripts_src(H.doc(K.client_for(K.make_user()).get("/propose/")))
    assert any(s.endswith("forum/compose.js") for s in sources), sources


def test_poll_seconds_are_rendered_from_settings(settings):
    settings.POLL_SECONDS = 7
    duo = K.Duo()
    root = H.doc(duo.ca.get(duo.url))
    values = [(k, v) for n in root.walk() for k, v in n.attrs.items() if k.startswith("data-") and "poll" in k.lower()]
    assert values, "a data- attribute with 'poll' in its name carries the interval"
    assert any(v in ("7", "7000") for _, v in values), values


def test_poll_seconds_default_is_three():
    duo = K.Duo()
    root = H.doc(duo.ca.get(duo.url))
    values = [v for n in root.walk() for k, v in n.attrs.items() if k.startswith("data-") and "poll" in k.lower()]
    assert any(v in ("3", "3000") for v in values), values


# --- the How this works page -----------------------------------------------------------------------------------------

def how_text():
    return H.doc(Client().get(reverse("forum:how_it_works"))).text()


def test_how_it_works_explains_the_limits_and_the_moderator_in_words():
    text = how_text()
    for needle in ("3,000", "30 seconds", "200"):
        assert needle in text, f"the page should explain: {needle}"
    assert re.search(r"\b20\b[^.]{0,40}(?:a day|per day|daily|propositions)", text), "the daily proposition limit is explained"
    assert re.search(r"\b30\b\D{0,15}messages", text), "the per-conversation message cap is explained"
    assert "AI" in text and "moderator" in text.lower()


def test_how_it_works_numbers_come_from_settings(settings):
    settings.MAX_MESSAGE_CHARS = 1234
    settings.MIN_SECONDS_BETWEEN_MESSAGES = 45
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 12
    settings.MAX_PROPOSITION_CHARS = 77
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 9
    text = how_text()
    for needle in ("1,234", "45 seconds", "77"):
        assert needle in text, needle
    assert re.search(r"\b9\b[^.]{0,40}(?:a day|per day|daily|propositions)", text)
    assert re.search(r"\b12\b\D{0,15}messages", text)
    for stale in ("3,000", "30 seconds"):
        assert stale not in text, stale
    assert not re.search(r"\b20\b[^.]{0,40}(?:a day|per day|daily|propositions)", text)
    assert not re.search(r"\b30\b\D{0,15}messages", text)


def test_how_it_works_does_not_promise_labels_or_names():
    text = how_text()
    assert "Participant A" not in text and "Participant B" not in text
    assert "Each of you is shown only as" not in text


# --- accounts restyle keeps the words the step 6 tests pin --------------------------------------------------------------

def test_the_login_page_still_says_what_step_6_pinned():
    text = H.doc(Client().get(reverse("accounts:login"))).text()
    assert "Log in" in text or "Login" in text
    root = H.doc(Client().get(reverse("accounts:login")))
    names = {n.get("name") for n in H.controls(root)}
    assert {"username", "password"} <= names
