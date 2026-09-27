"""Wave 16 items 1 & 2 (docs/wave16_brief.md): the site is renamed "Think Together" everywhere a user can see it, and
the header nav gets a second, equally-styled link ("Waiting to discuss") before "Your discussions"."""
from pathlib import Path

from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

import fviews_html as H
import fviews_kit as K

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OLD_NAME = "AI-Moderated Discussion Forum"
NEW_NAME = "Think Together"

ERROR_TEMPLATES = ["400.html", "403.html", "403_csrf.html", "404.html", "500.html"]


def _all_template_files():
    files = []
    for app_dir in ("forum", "accounts"):
        templates = REPO_ROOT / app_dir / "templates"
        if templates.is_dir():
            files.extend(sorted(templates.rglob("*.html")))
    return files


# --- item 1: the old brand string is gone everywhere a user can see it -------------------------------------------------

def test_no_template_visible_to_a_user_contains_the_old_site_name():
    for path in _all_template_files():
        text = path.read_text(encoding="utf-8")
        assert OLD_NAME not in text, f"{path.relative_to(REPO_ROOT)} still says {OLD_NAME!r}"


def test_the_readme_does_not_contain_the_old_site_name():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert OLD_NAME not in readme


def test_every_standalone_error_template_says_think_together_not_the_old_name():
    for template in ERROR_TEMPLATES:
        html = render_to_string(template, {})
        assert NEW_NAME in html
        assert OLD_NAME not in html


def test_the_home_page_shows_think_together_as_title_or_brand_link():
    response = K.client_for(K.make_user()).get(reverse("forum:home"))
    root = H.doc(response)
    brand_texts = [a.text().strip() for a in root.find_all("a") if "brand" in a.get("class", "")]
    titles = [t.text().strip() for t in root.find_all("title")]
    assert NEW_NAME in brand_texts or NEW_NAME in titles
    assert OLD_NAME not in H.raw(response)


def test_the_register_page_shows_think_together_as_title_or_brand_link():
    response = Client().get(reverse("accounts:register"))
    root = H.doc(response)
    brand_texts = [a.text().strip() for a in root.find_all("a") if "brand" in a.get("class", "")]
    titles = [t.text().strip() for t in root.find_all("title")]
    assert NEW_NAME in brand_texts or NEW_NAME in titles
    assert OLD_NAME not in H.raw(response)


def test_a_404_page_shows_think_together_as_title_or_brand_link():
    response = K.client_for(K.make_user()).get("/no/such/page/")
    assert response.status_code == 404
    root = H.doc(response)
    brand_texts = [a.text().strip() for a in root.find_all("a") if "brand" in a.get("class", "")]
    titles = [t.text().strip() for t in root.find_all("title")]
    assert NEW_NAME in brand_texts or NEW_NAME in titles
    assert OLD_NAME not in H.raw(response)


# --- item 2: "Waiting to discuss" joins "Your discussions" with equal visual weight ------------------------------------

def nav_links(response):
    root = H.doc(response)
    header = root.find("header")
    nav = header.find("nav") if header is not None else None
    holder = nav if nav is not None else header
    return [(a.text().strip(), a.get("href"), a.get("class", "")) for a in (holder.find_all("a") if holder is not None else [])]


def test_logged_in_nav_has_both_links_in_order_same_class_right_urls():
    response = K.client_for(K.make_user()).get(reverse("forum:home"))
    links = nav_links(response)
    texts = [t for t, _, _ in links]
    assert "How this works" in texts
    i_wait = texts.index("Waiting to discuss")
    i_yours = texts.index("Your discussions")
    assert i_wait < i_yours, "Waiting to discuss comes before Your discussions"
    by_text = {t: (href, cls) for t, href, cls in links}
    wait_href, wait_cls = by_text["Waiting to discuss"]
    yours_href, yours_cls = by_text["Your discussions"]
    assert wait_href == reverse("forum:home")
    assert yours_href == reverse("forum:mine")
    assert wait_cls.split() and set(wait_cls.split()) & set(yours_cls.split()), \
        f"the two links must share a CSS class: {wait_cls!r} vs {yours_cls!r}"
    assert "nav-link" in wait_cls and "nav-link" in yours_cls


def test_logged_out_nav_has_neither_waiting_to_discuss_nor_your_discussions():
    for url in (reverse("forum:how_it_works"), reverse("accounts:login"), reverse("accounts:register")):
        response = Client().get(url)
        text = H.doc(response).text()
        assert "Waiting to discuss" not in text
        assert "Your discussions" not in text
