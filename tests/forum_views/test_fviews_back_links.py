"""7c Revision 3 follow-up: the link and button wording that says "home" instead of "propositions" (docs/step7c_brief.md
plus the architect's rulings)."""
import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

import fviews_html as H
import fviews_kit as K


def link_texts(root):
    return {a.text().strip(): a.get("href") for a in root.find_all("a")}


def test_the_conversation_page_link_reads_home():
    duo = K.Duo()
    links = link_texts(H.doc(duo.ca.get(duo.url)))
    assert links.get("← Home") == reverse("forum:home")
    assert "← All propositions" not in links


def test_the_propose_page_back_link_reads_back_to_home():
    links = link_texts(H.doc(K.client_for(K.make_user()).get("/propose/")))
    assert links.get("← Back to home") == reverse("forum:home")
    assert not [t for t in links if "all propositions" in t.lower()]


def test_how_it_works_ends_with_a_back_to_home_link_for_everyone():
    for client in (Client(), K.client_for(K.make_user())):
        links = link_texts(H.doc(client.get("/how-it-works/")))
        assert links.get("Back to home") == reverse("forum:home") or links.get("← Back to home") == reverse("forum:home") or \
            any(t.endswith("Back to home") and h == reverse("forum:home") for t, h in links.items())
        assert not [t for t in links if "Back to propositions" in t]


@pytest.mark.parametrize("template", ["400.html", "403.html", "404.html", "500.html", "403_csrf.html"])
def test_every_error_template_says_back_to_home_and_links_home(template):
    html = render_to_string(template, {})
    root = H.parse(html)
    links = link_texts(root)
    assert any(t.endswith("Back to home") and h == reverse("forum:home") for t, h in links.items()), links
    assert "Back to propositions" not in html


def test_the_403_lead_says_go_back_to_the_home_page_and_try_again():
    text = " ".join(H.unescape(H.parse(render_to_string("403.html", {})).text()).split())
    assert "go back to the home page and try again." in text


def test_the_real_error_pages_carry_the_link_too():
    client = K.client_for(K.make_user())
    for response in (client.get("/no/such/page/"), client.get(reverse("forum:conversation", args=[987654]))):
        assert response.status_code == 404
        links = link_texts(H.doc(response))
        assert any(t.endswith("Back to home") and h == reverse("forum:home") for t, h in links.items()), links
    bad_host = Client().get("/how-it-works/", HTTP_HOST="evil.example.com")
    assert bad_host.status_code == 400
    assert any(t.endswith("Back to home") for t in link_texts(H.doc(bad_host)))
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(K.make_user(), backend=K.MODEL_BACKEND)
    csrf = strict.post("/propose/", {"text": "x"})
    assert csrf.status_code == 403
    assert any(t.endswith("Back to home") for t in link_texts(H.doc(csrf)))
