"""7c Revision 3: joining from a waiting card, the race for a card, and the whole round trip from the propose page."""
import re

from django.urls import reverse

import fviews_cards as C
import fviews_html as H
import fviews_kit as K


def home_cards(client):
    return C.waiting_cards(H.doc(client.get(reverse("forum:home"))))


def click(client, card):
    """POST what the card's form would send: its hidden fields (the side) and CSRF is off for these clients."""
    fields = {n.get("name"): n.get("value") for n in card.form.walk() if n.tag == "input" and n.get("name") not in (None, "csrfmiddlewaretoken")}
    return client.post(card.form.get("action"), fields)


def test_joining_from_a_card_lands_in_an_active_conversation_with_the_waiters_messages_visible(field_names):
    author = K.make_user(K.NAME_A, K.EMAIL_A)
    topic = K.make_topic("Cats make better pets than dogs.", created_by=author)
    conv = K.enter(author, topic, "pro")
    part = K.participant_of(conv, author)
    K.seed_message(conv, part, "Waiter-first-words.", minutes_ago=20)
    K.seed_message(conv, part, "Waiter-second-words.", minutes_ago=10)
    joiner = K.make_user(K.NAME_B, K.EMAIL_B)
    jc = K.client_for(joiner)
    [card] = home_cards(jc)
    assert card.side == "con"
    response = click(jc, card)
    assert response.status_code == 302 and K.conv_id(response) == conv.pk
    conv.refresh_from_db()
    assert conv.status == "active" and conv.participants.count() == 2
    assert K.fresh(part).side == "pro" and conv.participants.get(user=joiner).side == "con"
    page = jc.get(response["Location"])
    body = page.content.decode()
    assert body.index("Waiter-first-words.") < body.index("Waiter-second-words.")
    root = H.doc(page)
    for token in ("Waiter-first-words.", "Waiter-second-words."):
        block = K.find_block(root, token, ["You", K.NAME_A])
        assert K.NAME_A in block.text() and "The other participant" not in block.text()
    assert H.form_with_action(root, reverse("forum:post", args=[conv.pk])) is not None, "the joiner can post at once"
    assert K.WAITING_TITLE not in H.unescape(body)
    assert K.leaks(body, joiner, author, other_name_ok=True) == []
    # the card is gone for everyone else, and both people now have an "In discussion" row on Your discussions
    assert home_cards(K.client_for(K.make_user())) == []
    for client in (jc, K.client_for(author)):
        assert K.STATUS_ACTIVE in H.unescape(client.get("/discussions/").content.decode())


def test_the_joiner_takes_the_position_the_button_named_seeded_topic():
    topic = K.make_topic("Cities should cap rents.", created_by=None, opposing="Cities should not cap rents.")
    conv, waiter = K.wait_on(topic, "con")
    jc = K.client_for(K.make_user())
    [card] = home_cards(jc)
    assert (card.quote, card.side, card.button_text) == ("Cities should not cap rents.", "pro", "My position is that cities should cap rents.")
    click(jc, card)
    page = H.unescape(H.doc(jc.get(reverse("forum:conversation", args=[conv.pk]))).text())
    assert "Your position: Cities should cap rents." in page.replace("position:", "position: ").replace("  ", " ")


def test_the_oldest_waiting_conversation_in_the_group_is_the_one_joined():
    topic = K.make_topic("Two wait on this.", created_by=K.make_user())
    older, _ = K.wait_on(topic, "pro", minutes_ago=30)
    newer, _ = K.wait_on(topic, "pro", minutes_ago=10)
    jc = K.client_for(K.make_user())
    [card] = home_cards(jc)
    assert K.conv_id(click(jc, card)) == older.pk
    # the group is still on the home page: the newer waiter remains
    assert [c.topic_id for c in home_cards(K.client_for(K.make_user()))] == [topic.pk]
    assert K.fresh(newer).status == "open"


def test_when_someone_else_takes_the_waiting_person_first_the_joiner_simply_waits_on_the_chosen_side():
    from forum.models import Conversation

    topic = K.make_topic("Contested waiter.", created_by=K.make_user())
    first_conv, waiter = K.wait_on(topic, "pro")
    fast, slow = K.client_for(K.make_user()), K.client_for(K.make_user())
    fast_card, slow_card = home_cards(fast)[0], home_cards(slow)[0]
    assert K.conv_id(click(fast, fast_card)) == first_conv.pk
    late = click(slow, slow_card)
    assert late.status_code == 302, "no error page, no new message"
    conv = Conversation.objects.get(pk=K.conv_id(late))
    assert conv.pk != first_conv.pk and conv.status == "open"
    assert [p.side for p in conv.participants.all()] == ["con"]
    assert K.WAITING_TITLE in H.unescape(slow.get(late["Location"]).content.decode())


def test_a_card_click_for_a_hidden_topic_or_with_a_forged_side_never_breaks_the_page():
    topic = K.make_topic("Soon hidden.", created_by=K.make_user())
    K.wait_on(topic, "pro")
    jc = K.client_for(K.make_user())
    [card] = home_cards(jc)
    topic.hidden = True
    topic.save()
    response = click(jc, card)
    assert response.status_code != 500
    assert not (response.status_code == 302 and re.fullmatch(r"/c/\d+/", response["Location"]))
    forged = K.client_for(K.make_user()).post(card.form.get("action"), {"side": "pro"})
    assert forged.status_code != 500


def test_the_full_round_trip_propose_then_join_from_the_home_page(field_names):
    """A proposes on the propose page; B sees a card with A's position, joins from it; both talk."""
    from forum.models import Conversation

    a, b = K.make_user(K.NAME_A, K.EMAIL_A), K.make_user(K.NAME_B, K.EMAIL_B)
    ac, bc = K.client_for(a), K.client_for(b)
    made = ac.post("/propose/", {field_names["proposition"]: "my position is that dogs are better company than cats"})
    assert made.status_code == 302
    [card] = home_cards(bc)
    assert card.quote == "Dogs are better company than cats"
    assert (card.side, card.button_text) == ("con", "I disagree with this position")
    joined = click(bc, card)
    assert K.conv_id(joined) == K.conv_id(made)
    conv = Conversation.objects.get(pk=K.conv_id(made))
    assert conv.status == "active"
    assert bc.post(reverse("forum:post", args=[conv.pk]), {field_names["message"]: "I disagree, and here is why."}).status_code == 302
    assert "I disagree, and here is why." in ac.get(reverse("forum:conversation", args=[conv.pk])).content.decode()
    assert home_cards(K.client_for(K.make_user())) == []
