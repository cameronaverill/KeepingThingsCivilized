"""7c Revision 3: the home page lists only positions that someone is waiting on (docs/step7c_brief.md, Revision 3).
Everything about starting a discussion lives on the propose page (test_fviews_sides.py); the viewer's own rows are in
test_fviews_mine.py."""
import re
from django.urls import reverse

import fviews_cards as C
import fviews_html as H
import fviews_kit as K


def home(client, q=None):
    return client.get(reverse("forum:home"), {"q": q} if q is not None else {})


def cards(response):
    return C.waiting_cards(H.doc(response))


def flat(response):
    return " ".join(H.unescape(H.doc(response).text()).split())


def viewer():
    return K.client_for(K.make_user())


def user_topic(text, **kw):
    return K.make_topic(text, created_by=K.make_user(), **kw)


def seeded_topic(text, opposing="", **kw):
    return K.make_topic(text, created_by=None, opposing=opposing, **kw)


# --- the page shell -----------------------------------------------------------------------------------------------------

def test_the_home_page_has_the_new_heading_lead_and_how_it_works_link():
    response = home(viewer())
    root = H.doc(response)
    assert [h.text() for h in root.find_all("h1")] == [K.HOME_HEADING]
    lead = next(p for p in root.find_all("p") if "These are positions that someone holds" in p.text())
    assert " ".join(lead.text().split()).startswith(K.HOME_LEAD)
    assert reverse("forum:how_it_works") in H.links(lead)
    assert any(t == K.HOME_HEADING for t in [n.text() for n in root.find_all("title")]) or K.HOME_HEADING in flat(response)


def test_the_home_page_has_the_start_button():
    root = H.doc(home(viewer()))
    start = [a for a in root.find_all("a") if a.text().strip() == K.START_BUTTON]
    assert start and start[0].get("href") == reverse("forum:propose")
    assert "Propose a new one" not in root.text()


def test_the_old_home_wording_is_gone():
    K.wait_on(user_topic("Some waiting claim."))
    text = flat(home(viewer()))
    for phrase in ("Choose a proposition", "Propose a new one", "Search propositions", "Choose the position you hold",
                   "Someone who holds the other position is waiting", "Their opening message"):
        assert phrase not in text


# --- a card per waiting group -----------------------------------------------------------------------------------------

def test_a_topic_nobody_waits_on_is_not_listed_at_all():
    user_topic("Nobody waits on this claim.")
    seeded_topic("Nor on this seeded claim.", "Nor its opposite.")
    response = home(viewer())
    assert cards(response) == []
    assert "Nobody waits on this claim." not in response.content.decode()


def test_the_label_names_the_waiting_person_by_username():
    waiter = K.make_user("waiter_wanda", "waiter_wanda@leakcheck.example")
    K.wait_on(user_topic("Named waiter claim."), "pro", user=waiter)
    response = home(viewer())
    [card] = cards(response)
    assert card.username == "waiter_wanda"
    assert K.waiting_label("waiter_wanda") in flat(response)
    assert "Someone is waiting to discuss:" not in flat(response)


def test_a_pro_waiter_on_a_user_created_topic_shows_the_proposition_and_offers_the_disagree_button():
    topic = user_topic("Cats make better pets than dogs.")
    K.wait_on(topic, "pro")
    [card] = cards(home(viewer()))
    assert card.quote == "Cats make better pets than dogs."
    assert card.side == "con", "the button sends the OPPOSITE of the waiter's side"
    assert card.button_text == "I disagree with this position"
    assert card.topic_id == topic.pk


def test_a_pro_waiter_on_a_seeded_topic_offers_the_opposing_wording_through_the_position_phrase():
    topic = seeded_topic("Cities should cap rents.", "Israel should let rents float.")
    K.wait_on(topic, "pro")
    [card] = cards(home(viewer()))
    assert card.quote == "Cities should cap rents."
    assert (card.side, card.button_text) == ("con", "My position is that Israel should let rents float.")


def test_a_con_waiter_on_a_seeded_topic_shows_the_opposing_wording_and_offers_the_proposition_lower_cased():
    topic = seeded_topic("Cities should cap how much landlords can raise rents each year.", "Cities should not cap rents.")
    K.wait_on(topic, "con")
    [card] = cards(home(viewer()))
    assert card.quote == "Cities should not cap rents."
    assert (card.side, card.button_text) == (
        "pro", "My position is that cities should cap how much landlords can raise rents each year.")


def test_a_con_waiter_on_a_user_created_topic_shows_they_disagree_with_the_proposition():
    topic = user_topic("Cats make better pets than dogs.")
    K.wait_on(topic, "con")
    [card] = cards(home(viewer()))
    assert card.quote == "They disagree with: Cats make better pets than dogs."
    assert (card.side, card.button_text) == ("pro", "My position is that cats make better pets than dogs.")


def test_the_pro_button_keeps_the_capital_of_a_listed_proper_noun_or_acronym():
    K.wait_on(seeded_topic("NATO should expand.", "NATO should not expand."), "con", minutes_ago=5)
    K.wait_on(seeded_topic("Israel should recognise Palestine.", "Israel should not."), "con", minutes_ago=4)
    texts = sorted(c.button_text for c in cards(home(viewer())))
    assert texts == ["My position is that Israel should recognise Palestine.", "My position is that NATO should expand."]


def test_each_card_is_one_post_form_to_the_enter_url_with_csrf_a_hidden_side_and_one_button():
    for i in range(4):
        K.wait_on(user_topic(f"Claim number {'x' * i}."), "pro")
    found = cards(home(viewer()))
    assert len(found) == 4
    for card in found:
        assert card.form.get("method", "").lower() == "post"
        assert card.form.get("action") == reverse("forum:enter", args=[card.topic_id])
        assert H.hidden_csrf(card.form)
        assert len([n for n in card.form.walk() if n.tag == "button"]) == 1
        assert [n.get("value") for n in card.form.walk() if n.tag == "input" and n.get("name") == "side"] == ["con"]
        assert card.button.tag == "button"


def test_the_label_appears_once_per_card_and_no_card_offers_two_buttons():
    for i in range(3):
        K.wait_on(user_topic(f"Another claim {'y' * i}."), "pro")
    response = home(viewer())
    assert len(re.findall(K.WAITING_LABEL_RE, H.unescape(H.doc(response).text()))) == 3
    assert len(C.enter_forms(H.doc(response))) == 3


def test_the_quote_is_escaped_and_never_bold():
    topic = seeded_topic("Tom & <b>Jerry</b> <script>alert(1)</script>", "Nobody & <i>else</i>")
    K.wait_on(topic, "pro")
    K.wait_on(seeded_topic("Second & <u>claim</u>", "Its & <b>other</b>"), "con")
    response = home(viewer())
    page = response.content.decode()
    for raw in ("<b>Jerry</b>", "<script>alert(1)</script>", "<i>else</i>", "<u>claim</u>", "<b>other</b>"):
        assert raw not in page
    assert "Tom &amp; &lt;b&gt;Jerry&lt;/b&gt;" in page
    for card in cards(response):
        assert "<" not in card.quote or "&lt;" not in card.quote  # text only
    quotes = [n for n in H.doc(response).walk() if n.tag == "blockquote"]
    assert quotes and not [n for q in quotes for n in [q] + list(q.ancestors()) if n.tag in ("b", "strong")]


# --- groups, order, exclusions ----------------------------------------------------------------------------------------

def test_waiters_on_the_same_topic_and_side_are_one_card_and_the_two_sides_are_two_cards():
    topic = user_topic("Grouped claim about pets.")
    K.wait_on(topic, "pro", minutes_ago=30)
    K.wait_on(topic, "pro", minutes_ago=20)
    K.wait_on(topic, "pro", minutes_ago=10)
    assert len(cards(home(viewer()))) == 1
    K.raw_waiting(topic, "con", minutes_ago=5)  # the pairing rule never leaves both sides waiting; old rows could
    found = cards(home(viewer()))
    assert sorted((c.side, c.quote) for c in found) == [
        ("con", "Grouped claim about pets."), ("pro", "They disagree with: Grouped claim about pets.")]


def test_groups_are_newest_first_by_the_newest_waiting_conversation_in_each_group():
    t1, t2, t3 = user_topic("Group one claim."), user_topic("Group two claim."), user_topic("Group three claim.")
    K.wait_on(t1, "pro", minutes_ago=50)
    K.wait_on(t2, "pro", minutes_ago=30)
    K.wait_on(t3, "pro", minutes_ago=40)
    K.wait_on(t3, "pro", minutes_ago=5)  # group three's newest conversation is the freshest of all
    order = [c.topic_id for c in cards(home(viewer()))]
    assert order == [t3.pk, t2.pk, t1.pk]


def test_a_card_shows_only_the_waiters_username_and_no_times_counts_emails_or_ids():
    author = K.make_user("waiter_wanda", "waiter_wanda@leakcheck.example")
    topic = user_topic("A claim with no digits in it.")
    K.wait_on(topic, "pro", user=author)
    K.wait_on(topic, "pro")
    seen = K.make_user("viewer_vic", "viewer_vic@leakcheck.example")
    response = home(K.client_for(seen))
    page, text = response.content.decode(), H.doc(response).text()
    assert K.leaks(page, seen, author, own_name_in_header_ok=True, other_name_ok=True) == []
    assert "waiter_wanda" in page and "waiter_wanda@" not in page
    assert not re.search(r"<time\b|datetime=|\bdata-(seq|created|conversation|participant|since)", page)
    assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}\b", text)
    assert not re.search(r'href="/c/\d+', page), "no link into anyone's conversation from the waiting list"
    assert not re.search(r"\b\d+\s+(?:conversations?|threads?|messages?|open|active|waiting|participants?|people|users?|discussions?|others?)\b", text, re.I)
    assert not re.search(r"(?i)\b(?:since|ago|minutes?|hours?)\b", text)


def test_the_home_page_of_a_person_with_conversations_shows_no_conversation_links_and_no_partner_data():
    duo = K.Duo("Own pair claim.", names=("thread_one", "thread_two"))
    duo.seed(duo.pa, "Own-message-no-digits.", 30)
    other = K.make_user("waiter_zed", "waiter_zed@leakcheck.example")
    K.wait_on(user_topic("Someone else's waiting claim."), "pro", user=other)
    for client, viewer_user, person in ((duo.ca, duo.ua, duo.ub), (duo.cb, duo.ub, duo.ua)):
        response = home(client)
        page, text = response.content.decode(), H.doc(response).text()
        assert person.username not in page and person.email not in page, "a partner's name is not on the home page"
        assert K.leaks(page, viewer_user, other, own_name_in_header_ok=True, other_name_ok=True) == []
        assert not re.search(r'href="/c/\d+', page)
        assert "Own-message-no-digits." not in page


def test_the_viewers_own_waiting_conversation_and_topic_are_not_in_the_waiting_list():
    topic = user_topic("Mine to wait on.")
    conv, me = K.wait_on(topic, "pro")
    other_topic = user_topic("Someone else's claim.")
    K.wait_on(other_topic, "pro")
    cs = cards(home(K.client_for(me)))
    assert [c.topic_id for c in cs] == [other_topic.pk]


def test_a_topic_where_the_viewer_already_has_a_conversation_is_never_listed():
    topic = user_topic("Both wait on this one.")
    conv, me = K.wait_on(topic, "pro", minutes_ago=20)
    K.wait_on(topic, "pro", minutes_ago=10)  # someone else waits on the same side: a separate conversation
    response = home(K.client_for(me))
    assert cards(response) == [] and "is waiting to discuss:" not in H.doc(response).text()
    duo = K.Duo("Active and someone waits.", names=("act_one", "act_two"))
    K.wait_on(duo.topic, "pro")
    for client in (duo.ca, duo.cb):
        assert duo.topic.pk not in [c.topic_id for c in cards(home(client))]


def test_active_ended_and_hidden_conversations_are_not_listed():
    duo = K.Duo("Active pair claim.", names=("card_one", "card_two"))
    ended = K.Duo("Ended pair claim.", names=("card_three", "card_four"))
    ended.ca.post(ended.end_url)
    wconv, wuser = K.wait_on(user_topic("Ended waiting claim."), "pro")
    K.client_for(wuser).post(reverse("forum:end", args=[wconv.pk]))
    hidden = user_topic("Hidden waiting claim.")
    K.wait_on(hidden, "pro")
    hidden.hidden = True
    hidden.save()
    K.wait_on(user_topic("The only visible one."), "pro")
    response = home(viewer())
    assert [c.quote for c in cards(response)] == ["The only visible one."]
    page = response.content.decode()
    for text in ("Active pair claim.", "Ended pair claim.", "Ended waiting claim.", "Hidden waiting claim."):
        assert text not in page


def test_a_waiting_persons_message_text_is_never_on_the_home_page():
    topic = user_topic("Claim whose waiter has posted.")
    conv, author = K.wait_on(topic, "pro")
    K.seed_message(conv, K.participant_of(conv, author), "Secret-opener-words.", minutes_ago=10)
    K.seed_message(conv, K.participant_of(conv, author), "Second-secret-words.", minutes_ago=9)
    page = home(viewer()).content.decode()
    assert "Secret-opener-words." not in page and "Second-secret-words." not in page


def test_many_waiting_groups_are_all_on_the_page():
    topics = [user_topic(f"Bulk waiting claim number {i}.") for i in range(1, 41)]
    for i, topic in enumerate(topics):
        K.wait_on(topic, "pro", minutes_ago=100 - i)
    found = {c.topic_id for c in cards(home(viewer()))}
    assert found == {t.pk for t in topics}


# --- empty states ------------------------------------------------------------------------------------------------------

def test_the_empty_state_and_its_start_link():
    response = home(viewer())
    root = H.doc(response)
    assert K.EMPTY_HOME in flat(response)
    para = next(p for p in root.find_all("p") if "Nobody is waiting right now" in p.text())
    assert reverse("forum:propose") in H.links(para), "the sentence carries the link to start a discussion"
    assert not C.enter_forms(root)


def test_there_is_no_no_match_text_any_more_and_no_search_form():
    K.wait_on(user_topic("Some claim."), "pro")
    for q in (None, "nomatchatall"):
        response = home(viewer(), q)
        text = flat(response)
        assert "No waiting position matches" not in text and "No proposition matches" not in text
        assert K.NO_MATCH not in text


# --- no search on the home page (Revision 4) ---------------------------------------------------------------------------

def test_the_home_page_has_no_search_box_at_all():
    K.wait_on(user_topic("Some claim to look for."), "pro")
    root = H.doc(home(viewer()))
    assert not [n for n in root.walk() if n.tag == "input" and (n.get("name") == "q" or n.get("type") == "search")]
    assert not [f for f in H.forms(root) if f.get("role") == "search"]
    assert "Search your discussions" not in root.text() and "Search waiting positions" not in root.text()


def test_a_q_parameter_on_the_home_url_is_ignored():
    topics = [user_topic("Alpha waiting claim."), user_topic("Beta waiting claim.")]
    for i, t in enumerate(topics):
        K.wait_on(t, "pro", minutes_ago=10 - i)
    client = viewer()
    everything = [c.topic_id for c in cards(home(client))]
    assert len(everything) == 2
    for q in ("alpha", "no-such-thing", "", "x" * 5000, "\u0000", "' OR 1=1 --"):
        response = home(client, q)
        assert response.status_code == 200, q[:10]
        assert [c.topic_id for c in cards(response)] == everything, q[:10]
    assert "Alpha waiting claim." in home(client, "beta").content.decode()


def test_no_your_conversations_or_your_discussions_section_on_the_home_page():
    conv, me = K.wait_on(user_topic("Mine, waiting."), "pro")
    K.wait_on(user_topic("Someone else's."), "pro")
    response = home(K.client_for(me))
    root = H.doc(response)
    assert K.OLD_HOME_SECTION not in root.text()
    assert not [h for h in root.find_all("h2") if h.text().strip() in (K.YOUR_DISCUSSIONS, K.OLD_HOME_SECTION)]
    assert not [a for a in root.find_all("a") if a.text().strip() == "Open"], "no row of own conversations on the home page"
    assert not re.search(r'href="/c/\d+', response.content.decode())


def test_the_waiting_list_comes_first_after_the_lead_and_the_start_button():
    K.wait_on(user_topic("First and only waiting card."), "pro")
    body = home(viewer()).content.decode()
    assert body.index("<h1") < body.index(K.START_BUTTON) < body.index("is waiting to discuss:")
    assert body.index("These are positions that someone holds") < body.index("is waiting to discuss:")


# --- the site header and shell -----------------------------------------------------------------------------------------

def test_header_shows_the_signed_in_username_how_it_works_and_a_post_logout_form():
    user = K.make_user("headeruser")
    root = H.doc(home(K.client_for(user)))
    header = root.find("header")
    assert header is not None and "headeruser" in header.text()
    assert reverse("forum:how_it_works") in H.links(header)
    logout = [f for f in H.forms(header) if f.get("action") == reverse("accounts:logout")]
    assert len(logout) == 1 and logout[0].get("method", "").lower() == "post"
    assert H.hidden_csrf(logout[0]) and H.has_button(logout[0])


def test_base_template_shows_django_messages():
    from django.contrib.messages.storage.base import Message
    from django.template.loader import render_to_string
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.user = K.make_user()
    rendered = render_to_string("forum/base.html", {"messages": [Message(25, "Message-marker-for-base-template")]}, request)
    assert "Message-marker-for-base-template" in rendered
