"""7c two-position model: the conversation header lines, the propose page, the How this works paragraph, wording that must
be gone, and the words the site never uses. Every exact string comes from docs/step7c_brief.md."""
import re

import pytest
from django.test import Client
from django.urls import reverse

import fviews_html as H
import fviews_kit as K

PRO_TEXT = "Cats make better pets than dogs"
CON_TEXT = "Cats make worse pets than dogs"


def flat(response):
    """Visible text with a space forced after every colon, so 'Your position:' + 'text' compares the same however it is marked up."""
    return re.sub(r":(?=\S)", ": ", H.unescape(H.doc(response).text()))


def has_line(text, label, value):
    return re.search(re.escape(label) + r"\s*" + re.escape(value), text) is not None


def waiting_user_topic(proposition, side, opposing="", name=K.NAME_A):
    user = K.make_user(name, f"{name}@leakcheck.example")
    topic = K.make_topic(proposition, created_by=user, opposing=opposing)
    conv = K.enter(user, topic, side)
    return user, topic, conv, K.client_for(user), reverse("forum:conversation", args=[conv.pk])


# --- conversation header (Revision 2): only the viewer's own position --------------------------------------------------------

OTHER_LINES = ("The other participant's position", "The other participant disagrees with it", "disagrees with it",
               "You disagree with it")


def assert_no_other_line(text):
    for phrase in OTHER_LINES:
        assert phrase not in text, f"a line about the other participant's position: {phrase!r}"


def seeded_duo():
    return K.Duo(PRO_TEXT + ".", opposing=CON_TEXT + ".")


def test_seeded_pro_holder_sees_only_their_own_position_when_active():
    duo = seeded_duo()
    text = flat(duo.ca.get(duo.url))
    assert has_line(text, "Your position:", PRO_TEXT + ".")
    assert CON_TEXT not in text
    assert_no_other_line(text)


def test_seeded_con_holder_sees_the_opposing_wording_as_their_own_position():
    duo = seeded_duo()
    text = flat(duo.cb.get(duo.url))
    assert has_line(text, "Your position:", CON_TEXT + ".")
    root = H.doc(duo.cb.get(duo.url))
    assert not [n for n in root.walk() if n.get("class", "").find("position") >= 0 and PRO_TEXT in n.text()], \
        "the position area shows only the viewer's own wording (the page title is the neutral claim)"
    assert_no_other_line(text)


def test_the_position_line_sits_under_the_title_and_before_the_messages():
    duo = seeded_duo()
    duo.seed(duo.pa, "First-message-token", 30)
    body = duo.ca.get(duo.url).content.decode()
    assert body.index("<h1") < body.index("Your position:") < body.index("First-message-token")
    other = K.Duo("Cities should cap rents.", names=("line_one", "line_two"))
    other.seed(other.pa, "Second-first-message", 30)
    body = other.cb.get(other.url).content.decode()
    assert body.index("<h1") < body.index("You disagree with this position:") < body.index("Second-first-message")


@pytest.mark.parametrize("side, mine", [("pro", PRO_TEXT + "."), ("con", CON_TEXT + ".")])
def test_a_waiting_seeded_conversation_shows_your_own_position_and_nothing_else(side, mine):
    user, topic, conv, client, url = waiting_user_topic(PRO_TEXT + ".", side, CON_TEXT + ".")
    text = flat(client.get(url))
    assert has_line(text, "Your position:", mine)
    assert_no_other_line(text)


def test_closed_seeded_conversations_keep_only_the_viewers_own_line():
    duo = seeded_duo()
    duo.ca.post(duo.end_url)
    for client, mine in ((duo.ca, PRO_TEXT), (duo.cb, CON_TEXT)):
        text = flat(client.get(duo.url))
        assert has_line(text, "Your position:", mine + ".")
        assert_no_other_line(text)


def test_a_closed_waiting_conversation_keeps_your_position_line():
    user, topic, conv, client, url = waiting_user_topic(PRO_TEXT + ".", "pro", CON_TEXT + ".")
    client.post(reverse("forum:end", args=[conv.pk]))
    text = flat(client.get(url))
    assert has_line(text, "Your position:", PRO_TEXT + ".")
    assert_no_other_line(text)


# user-created topics (no opposing wording)

def user_created_duo(proposition=PRO_TEXT + "."):
    return K.Duo(proposition)


def test_user_created_pro_holder_sees_your_position_with_the_proposition_as_stored():
    duo = user_created_duo()
    text = flat(duo.ca.get(duo.url))
    assert has_line(text, "Your position:", PRO_TEXT + ".")
    assert "You disagree with this position" not in text
    assert_no_other_line(text)


def test_user_created_con_holder_sees_you_disagree_with_this_position_in_every_state():
    duo = user_created_duo()
    for label, page in (("active", duo.cb.get(duo.url)),):
        text = flat(page)
        assert "You disagree with this position: " + PRO_TEXT + "." in text, label
        assert "Your position:" not in text, label
        assert_no_other_line(text)
    duo.cb.post(duo.end_url)
    closed = flat(duo.cb.get(duo.url))
    assert "You disagree with this position: " + PRO_TEXT + "." in closed
    assert_no_other_line(closed)


def test_user_created_waiting_holders_see_only_their_own_line():
    user, topic, conv, client, url = waiting_user_topic(PRO_TEXT + ".", "pro")
    text = flat(client.get(url))
    assert has_line(text, "Your position:", PRO_TEXT + ".")
    assert_no_other_line(text)
    user2, topic2, conv2, client2, url2 = waiting_user_topic("Another claim about pets.", "con", name="waiting_con_user")
    text2 = flat(client2.get(url2))
    assert "You disagree with this position: Another claim about pets." in text2
    assert "Your position:" not in text2
    assert_no_other_line(text2)


def test_the_proposition_is_shown_as_stored_not_with_a_lowered_first_letter():
    duo = user_created_duo("Israel should keep talking.")
    assert "Your position: Israel should keep talking." in flat(duo.ca.get(duo.url))
    duo2 = K.Duo("Cities should cap rents.", names=("cap_one", "cap_two"))
    assert "Your position: Cities should cap rents." in flat(duo2.ca.get(duo2.url))
    assert "You disagree with this position: Cities should cap rents." in flat(duo2.cb.get(duo2.url))


def test_position_text_is_escaped():
    duo = K.Duo("<i>Italic</i> claim & more", opposing="<b>Bold</b> counter")
    for client in (duo.ca, duo.cb):
        body = client.get(duo.url).content.decode()
        assert "<i>Italic</i>" not in body and "<b>Bold</b>" not in body
    assert "&lt;i&gt;Italic&lt;/i&gt;" in duo.ca.get(duo.url).content.decode()
    assert "&lt;b&gt;Bold&lt;/b&gt;" in duo.cb.get(duo.url).content.decode()


def test_no_state_ever_says_anything_about_the_other_participants_position():
    for label, response in every_state().items():
        if label.startswith(("home", "propose", "how")):
            continue
        assert_no_other_line(flat(response))


def test_position_lines_never_show_labels_names_or_the_words_pro_and_con():
    duo = seeded_duo()
    for client, viewer, other in ((duo.ca, duo.ua, duo.ub), (duo.cb, duo.ub, duo.ua)):
        response = client.get(duo.url)
        assert K.leaks(response.content.decode(), viewer, other, other_name_ok=True) == []
        assert not re.search(r"\b(?:pro|con)\b", H.doc(response).text(), re.I), "internal side names are not shown to people"


# --- propose page ------------------------------------------------------------------------------------------------------

def propose_page(client=None):
    return (client or K.client_for(K.make_user())).get("/propose/")


def test_propose_heading_and_lead_are_the_final_wording():
    response = propose_page()
    root = H.doc(response)
    assert any(h.text() == "Start a new discussion" for h in root.find_all("h1"))
    text = " ".join(flat(response).split())
    assert " ".join(K.PROPOSE_LEAD.split()) in text
    lead = next(p for p in root.find_all("p") if "State a position you hold" in p.text())
    assert " ".join(lead.text().split()) == " ".join(K.PROPOSE_LEAD.split()), "the lead ends after 'both sides.'"
    assert K.PROPOSE_LEAD_OLD_TAIL not in text and "shown, shortened" not in text


def test_propose_form_starts_with_the_fixed_label_my_position_is_that():
    root = H.doc(propose_page())
    form = next(f for f in H.forms(root) if H.text_control(f) is not None)
    area = H.text_control(form)
    labels = [l for l in form.find_all("label") if l.get("for") == area.get("id")]
    assert labels and labels[0].text().strip() == K.POSITION_PREFIX == "My position is that"
    order = [n for n in form.walk() if n is area or n is labels[0]]
    assert order[0] is labels[0], "the fixed start is above the box"
    assert not H.unlabelled_controls(root)


def test_propose_button_and_hint_are_the_final_wording(settings):
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 7
    response = propose_page()
    root = H.doc(response)
    form = next(f for f in H.forms(root) if H.text_control(f) is not None)
    assert "Publish and start discussing" in [b.text().strip() for b in form.find_all("button")]
    assert re.search(r"It appears on the home page immediately\. You can create up to 7 (?:propositions )?per day\.", flat(response))


def test_propose_old_wording_is_gone():
    text = flat(propose_page())
    for phrase in ("Two people will discuss it", "you will not be told which side", "Publish proposition", "Propose a new debate"):
        assert phrase not in text
    assert not re.search(r"(?i)told which side", text)


@pytest.mark.parametrize("typed, stored", [
    ("dogs are loud", "Dogs are loud"),
    ("Dogs are loud", "Dogs are loud"),
    ("My position is that cats rule", "Cats rule"),
    ("my position is that cats rule", "Cats rule"),
    ("MY POSITION IS THAT  cats rule", "Cats rule"),
])
def test_the_typed_completion_becomes_the_claim_and_the_proposer_holds_pro(field_names, typed, stored):
    from forum.models import Topic

    user = K.make_user()
    response = K.client_for(user).post("/propose/", {field_names["proposition"]: typed})
    assert response.status_code == 302
    topic = Topic.objects.get()
    assert topic.proposition == stored and topic.opposing_position == ""
    from forum.models import Conversation

    assert K.participant_of(Conversation.objects.get(pk=K.conv_id(response)), user).side == "pro"


def test_the_limit_applies_to_the_stored_claim_not_the_fixed_start(field_names):
    client = K.client_for(K.make_user())
    ok = client.post("/propose/", {field_names["proposition"]: "my position is that " + "x" * 200})
    assert ok.status_code == 302
    bad = client.post("/propose/", {field_names["proposition"]: "My position is that " + "y" * 201})
    assert bad.status_code == 200
    alert = H.alert_text(bad)
    assert "201" in alert and "200" in alert


def test_a_duplicate_is_found_even_with_the_fixed_start_typed(field_names):
    K.make_topic("Dogs are great pets.", created_by=K.make_user())
    response = K.client_for(K.make_user()).post("/propose/", {field_names["proposition"]: "My position is that dogs are great pets."})
    assert response.status_code == 200 and "already exists" in H.alert_text(response)


def test_the_duplicate_refusal_offers_to_join_the_existing_one_as_pro(field_names):
    from forum.models import Conversation

    existing = K.make_topic("Dogs are great pets.", created_by=K.make_user())
    client = K.client_for(K.make_user())
    response = client.post("/propose/", {field_names["proposition"]: "dogs are great pets."})
    root = H.doc(response)
    target = reverse("forum:enter", args=[existing.pk])
    button = next((n for n in root.walk() if n.tag == "button" and n.get("formaction") == target), None)
    form = next((f for f in H.forms(root) if f.get("action") == target), None)
    assert button is not None or form is not None, "a control that enters the existing proposition"
    holder = button.parent if button is not None else form
    while holder is not None and holder.tag != "form":
        holder = holder.parent
    sides = [n.get("value") for n in (holder.walk() if holder is not None else [])
             if n.tag == "input" and n.get("name") == "side"]
    if button is not None and button.get("name") == "side":
        sides.append(button.get("value"))
    assert "pro" in sides, "the button that joins the duplicate sends side=pro"
    posted = client.post(target, {"side": "pro"})
    assert posted.status_code == 302
    assert Conversation.objects.get(pk=K.conv_id(posted)).participants.get().side == "pro"


def test_the_propose_error_banner_still_keeps_the_text_and_sits_with_the_form(field_names):
    response = K.client_for(K.make_user()).post("/propose/", {field_names["proposition"]: "z" * 201})
    assert response.status_code == 200
    form = next(f for f in H.forms(H.doc(response)) if H.text_control(f) is not None)
    assert H.control_value(H.text_control(form)) == "z" * 201


# --- How this works ----------------------------------------------------------------------------------------------------

def how_text():
    return " ".join(flat(Client().get(reverse("forum:how_it_works"))).split())


def test_how_it_works_has_the_two_position_paragraph_verbatim_and_ending_there():
    """Wave 16 item 7 shortened this paragraph: it now ends at "...private to their two participants." with no
    trailing username/blocking sentences."""
    text = how_text()
    assert " ".join(K.HOW_PARAGRAPH.split()) in text
    root = H.doc(Client().get(reverse("forum:how_it_works")))
    para = next(p for p in root.find_all("p") if "Every conversation is between two opposing positions" in p.text())
    normalised = H.norm(para.text())
    assert H.norm(K.HOW_PARAGRAPH) in normalised
    assert normalised.endswith("Conversations are private to their two participants.")


def test_how_it_works_no_longer_says_people_are_not_told_which_side():
    text = how_text()
    assert not re.search(r"(?i)told which side|which side (the other|you take)|nobody is told", text)
    assert "exception" not in text.lower()
    assert not re.search(r"(?i)still waiting for|shown on the home page|opening message|what they would be joining", text)


def test_how_it_works_explains_the_limits():
    """Wave 16 item 7 removed the username/blocking sentences from this page entirely (that explanation now lives
    only in the block control itself); the numeric limits remain."""
    text = how_text()
    for needle in ("3,000", "30 seconds", "200"):
        assert needle in text


# --- wording that must not be on any page -------------------------------------------------------------------------------

FORBIDDEN = re.compile(r"(?i)\bdebat\w*|\bcosts?\b|\bbudgets?\b|\bspending\b|\btokens?\b|\bAPI\b")


def every_state():
    duo = K.Duo("Every-state proposition text.", opposing="Every-state counter text.", names=("state_one", "state_two"))
    duo.seed(duo.pa, "Some opening words.", 30)
    w_user, w_topic, w_conv, w_client, w_url = waiting_user_topic("Waiting-state proposition text.", "pro", name="state_three")
    K.seed_message(w_conv, K.participant_of(w_conv, w_user), "Waiting opener words.", minutes_ago=20)
    viewer = K.client_for(K.make_user("state_four"))
    closed = K.Duo("Closed-state proposition text.", names=("state_five", "state_six"))
    closed.ca.post(closed.end_url)
    pages = {
        "home": viewer.get("/"), "home (own cards)": w_client.get("/"), "propose": viewer.get("/propose/"),
        "how": viewer.get("/how-it-works/"), "active a": duo.ca.get(duo.url), "active b": duo.cb.get(duo.url),
        "waiting": w_client.get(w_url), "closed": closed.cb.get(closed.url),
    }
    return pages


def test_no_page_state_mentions_costs_tokens_the_api_or_debate():
    for label, response in every_state().items():
        found = FORBIDDEN.findall(H.doc(response).text())
        assert not found, f"{label}: {found}"


def test_no_page_state_carries_the_old_waiting_or_side_wording():
    for label, response in every_state().items():
        text = H.doc(response).text()
        for phrase in K.OLD_PHRASES:
            assert phrase not in text, f"{label}: {phrase!r}"
