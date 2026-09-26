"""7b: the home page, the proposition list (docs/step7_brief.md owner decisions and "7b: pages")."""
import re

from django.urls import reverse

import fviews_html as H
import fviews_kit as K


def home(client, q=None):
    return client.get(reverse("forum:home"), {"q": q} if q is not None else {})


def order_of(response, tokens):
    page = response.content.decode()
    positions = [page.find(t) for t in tokens]
    assert all(p >= 0 for p in positions), f"missing from the page: {[t for t, p in zip(tokens, positions) if p < 0]}"
    return positions


def cards(response):
    """The forms that enter a proposition: {topic id: form node}."""
    root = H.doc(response)
    found = {}
    for form in H.forms(root):
        match = re.fullmatch(r"/p/(\d+)/enter/", form.get("action", ""))
        if match:
            found[int(match.group(1))] = form
    return found


# --- newest first, proposition text only ------------------------------------------------------------------------------

def test_propositions_are_listed_newest_first():
    user = K.make_user()
    K.make_topic("Oldest-token-alpha is best.", created_by=user, minutes_ago=30)
    K.make_topic("Middle-token-bravo is best.", created_by=user, minutes_ago=20)
    K.make_topic("Newest-token-charlie is best.", created_by=user, minutes_ago=10)
    response = home(K.client_for(user))
    first, second, third = order_of(response, ["Newest-token-charlie", "Middle-token-bravo", "Oldest-token-alpha"])
    assert first < second < third


def test_a_newly_created_proposition_goes_to_the_top_immediately():
    user = K.make_user()
    K.make_topic("Older-token-delta.", created_by=user, minutes_ago=5)
    K.make_topic("Brand-new-token-echo.", created_by=user)
    first, second = order_of(home(K.client_for(user)), ["Brand-new-token-echo", "Older-token-delta"])
    assert first < second


def test_every_logged_in_user_sees_propositions_made_by_others():
    author, reader = K.make_user(), K.make_user()
    K.make_topic("Written-by-someone-else-token.", created_by=author)
    assert "Written-by-someone-else-token." in home(K.client_for(reader)).content.decode()


def test_seeded_propositions_show_the_proposition_only_never_the_title_or_description():
    K.make_topic("Seeded proposition text zeta.", title="SEEDED-TITLE-QZ", created_by=None)
    from forum.models import Topic

    Topic.objects.filter(title="SEEDED-TITLE-QZ").update(description="DESCRIPTION-QZ-should-not-show")
    response = home(K.client_for(K.make_user()))
    page = response.content.decode()
    assert "Seeded proposition text zeta." in page
    assert "SEEDED-TITLE-QZ" not in page
    assert "DESCRIPTION-QZ-should-not-show" not in page


def test_hidden_propositions_are_not_listed_or_searchable():
    user = K.make_user()
    K.make_topic("Visible-token-foxtrot.", created_by=user)
    K.make_topic("Hidden-token-golf.", created_by=user, hidden=True)
    client = K.client_for(user)
    assert "Hidden-token-golf" not in home(client).content.decode()
    assert "Hidden-token-golf" not in home(client, "golf").content.decode()
    assert "Visible-token-foxtrot" in home(client).content.decode()


def test_proposition_text_is_escaped():
    user = K.make_user()
    K.make_topic("Tom & Jerry <b>rule</b> <script>alert(1)</script>", created_by=user)
    page = home(K.client_for(user)).content.decode()
    assert "<b>rule</b>" not in page and "<script>alert(1)</script>" not in page
    assert "Tom &amp; Jerry &lt;b&gt;rule&lt;/b&gt;" in page


def test_no_thread_lists_counts_or_conversation_links_on_the_home_page():
    duo = K.Duo("Thread-free proposition text.")
    K.enter(K.make_user(), K.make_topic("Another proposition with a waiting thread.", created_by=duo.ua))
    duo.seed(duo.pa, "A message that must not leak onto the list.")
    page = H.unescape(home(duo.ca).content.decode())
    assert "A message that must not leak" not in page
    assert not re.search(r'href="/c/\d+', page), "the list must not link to conversations"
    text = H.parse(page).text()
    assert not re.search(r"\b\d+\s+(?:conversations?|threads?|messages?|open|active|waiting|participants?|people|users?|debates?)\b", text, re.I)
    assert not re.search(r"(?i)\b(?:conversations?|threads?|messages?|replies)\s*[:(]\s*\d", text)


# --- each card is a POST form-button ----------------------------------------------------------------------------------

def test_each_visible_proposition_is_a_post_form_to_the_enter_url_with_csrf_and_a_button():
    user = K.make_user()
    topics = [K.make_topic(f"Card-token-{i} proposition.", created_by=user, minutes_ago=i) for i in range(1, 6)]
    response = home(K.client_for(user))
    found = cards(response)
    assert set(found) == {t.pk for t in topics}
    for topic in topics:
        form = found[topic.pk]
        assert form.get("method", "").lower() == "post"
        assert H.hidden_csrf(form)
        assert H.has_button(form), "the card is a <button> inside the form"
        assert f"Card-token-{topics.index(topic) + 1} proposition." in form.text()


def test_the_enter_action_is_never_a_plain_link():
    user = K.make_user()
    topic = K.make_topic("Never-a-link proposition.", created_by=user)
    hrefs = H.links(H.doc(home(K.client_for(user))))
    assert reverse("forum:enter", args=[topic.pk]) not in hrefs


def test_the_home_page_carries_the_one_sentence_pairing_rule_somewhere_visible():
    """The exact sentence is the builder's; the page must at least explain pairing in words (someone else / other person)."""
    text = H.parse(home(K.client_for(K.make_user())).content.decode()).text().lower()
    assert re.search(r"someone else|another person|other person|the next person|second person|another participant", text)


# --- search -----------------------------------------------------------------------------------------------------------

def test_the_search_box_is_a_labelled_get_form_named_q():
    root = H.doc(home(K.client_for(K.make_user())))
    search = [f for f in H.forms(root) if f.get("method", "get").lower() == "get" and any(n.get("name") == "q" for n in f.walk())]
    assert len(search) == 1, "one GET form with a control named q"
    assert not H.unlabelled_controls(search[0])
    action = search[0].get("action", "")
    assert action in ("", "/", reverse("forum:home"))


def test_search_finds_matching_propositions_and_only_those():
    user = K.make_user()
    K.make_topic("Vegetables are tastier than sweets.", created_by=user, minutes_ago=3)
    K.make_topic("Cities should ban cars.", created_by=user, minutes_ago=2)
    K.make_topic("Remote work beats office work.", created_by=user, minutes_ago=1)
    client = K.client_for(user)
    page = home(client, "cars").content.decode()
    assert "Cities should ban cars." in page
    assert "Vegetables are tastier" not in page and "Remote work beats" not in page


def test_search_ignores_case():
    user = K.make_user()
    K.make_topic("Vegetables are tastier than sweets.", created_by=user)
    client = K.client_for(user)
    for q in ("VEGETABLES", "vegetables", "VeGeTaBlEs", "TASTIER THAN"):
        assert "Vegetables are tastier than sweets." in home(client, q).content.decode(), q


def test_search_matches_inside_the_text_not_only_at_the_start():
    user = K.make_user()
    K.make_topic("Vegetables are tastier than sweets.", created_by=user)
    assert "tastier than sweets" in home(K.client_for(user), "tier than sw").content.decode()


def test_search_with_no_match_lists_nothing_and_keeps_the_search_text():
    user = K.make_user()
    K.make_topic("Vegetables are tastier than sweets.", created_by=user)
    response = home(K.client_for(user), "zzzz-no-such-thing")
    assert response.status_code == 200
    assert not cards(response)
    root = H.doc(response)
    box = next(n for n in root.walk() if n.tag == "input" and n.get("name") == "q")
    assert box.get("value") == "zzzz-no-such-thing"


def test_an_empty_or_blank_search_lists_everything():
    user = K.make_user()
    a = K.make_topic("First-listed-token.", created_by=user, minutes_ago=2)
    b = K.make_topic("Second-listed-token.", created_by=user, minutes_ago=1)
    for q in ("", "   "):
        assert set(cards(home(K.client_for(user), q))) == {a.pk, b.pk}


def test_search_trims_surrounding_spaces():
    user = K.make_user()
    K.make_topic("Vegetables are tastier than sweets.", created_by=user)
    assert "Vegetables are tastier" in home(K.client_for(user), "  vegetables  ").content.decode()


def test_search_treats_percent_and_underscore_literally():
    user = K.make_user()
    K.make_topic("Taxes above 100% are unfair.", created_by=user, minutes_ago=2)
    K.make_topic("Snake_case is better than camelCase.", created_by=user, minutes_ago=1)
    K.make_topic("Plain proposition without symbols.", created_by=user, minutes_ago=3)
    client = K.client_for(user)
    assert len(cards(home(client, "%"))) == 1
    assert len(cards(home(client, "100%"))) == 1
    assert len(cards(home(client, "_"))) == 1
    assert len(cards(home(client, "e_c"))) == 1
    assert len(cards(home(client, "\\"))) == 0


def test_search_is_not_injectable_and_never_errors():
    user = K.make_user()
    K.make_topic("Ordinary proposition text.", created_by=user)
    client = K.client_for(user)
    assert len(cards(home(client, "ordinary"))) == 1, "control: a plain search does find the proposition"
    for q in ("' OR 1=1 --", "\" ; DROP TABLE forum_topic; --", "<script>alert(1)</script>", "x" * 20000, "\u0000"):
        response = home(client, q)
        assert response.status_code == 200, q[:30]
        assert not cards(response), q[:30]
    assert len(cards(home(client, "ordinary"))) == 1, "and the table is still there"
    assert "<script>alert(1)</script>" not in home(client, "<script>alert(1)</script>").content.decode()


def test_search_within_results_keeps_newest_first():
    user = K.make_user()
    K.make_topic("Match-token older.", created_by=user, minutes_ago=20)
    K.make_topic("Match-token newer.", created_by=user, minutes_ago=5)
    K.make_topic("Other unrelated.", created_by=user, minutes_ago=1)
    first, second = order_of(home(K.client_for(user), "match-token"), ["Match-token newer.", "Match-token older."])
    assert first < second


# --- the site header --------------------------------------------------------------------------------------------------

def test_header_shows_the_signed_in_username_how_it_works_and_a_post_logout_form():
    user = K.make_user("headeruser")
    root = H.doc(home(K.client_for(user)))
    header = root.find("header")
    assert header is not None, "the shared base template has a <header>"
    assert "headeruser" in header.text()
    assert reverse("forum:how_it_works") in H.links(header)
    logout = [f for f in H.forms(header) if f.get("action") == reverse("accounts:logout")]
    assert len(logout) == 1
    assert logout[0].get("method", "").lower() == "post"
    assert H.hidden_csrf(logout[0]) and H.has_button(logout[0])


def test_home_offers_a_way_to_propose():
    root = H.doc(home(K.client_for(K.make_user())))
    assert reverse("forum:propose") in H.links(root)


def test_base_template_shows_django_messages():
    from django.contrib.messages.storage.base import Message
    from django.template.loader import render_to_string
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.user = K.make_user()
    rendered = render_to_string("forum/base.html", {"messages": [Message(25, "Message-marker-for-base-template")]}, request)
    assert "Message-marker-for-base-template" in rendered
