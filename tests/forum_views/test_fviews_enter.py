"""7b: entering a proposition follows the pairing rule; the waiting page (docs/step7_brief.md)."""
import re

from django.urls import reverse

import fviews_html as H
import fviews_kit as K


def enter_url(topic):
    return reverse("forum:enter", args=[topic.pk])


def enter_via_view(client, topic):
    return client.post(enter_url(topic))


def conv_id_from(response):
    match = re.fullmatch(r"/c/(\d+)/", response["Location"])
    assert match, f"expected a redirect into a conversation, got {response.status_code} {response.get('Location')}"
    return int(match.group(1))


def new_topic(text="Coffee is better than tea."):
    return K.make_topic(text, created_by=K.make_user())


# --- pairing ----------------------------------------------------------------------------------------------------------

def test_entering_starts_a_waiting_conversation_and_redirects_into_it():
    from forum.models import Conversation

    topic, user = new_topic(), K.make_user()
    response = enter_via_view(K.client_for(user), topic)
    assert response.status_code == 302
    conv = Conversation.objects.get(pk=conv_id_from(response))
    assert conv.topic == topic and conv.status == "open"
    assert [p.user_id for p in conv.participants.all()] == [user.pk]


def test_a_second_person_joins_the_waiting_conversation_and_it_becomes_active():
    from forum.models import Conversation

    topic = new_topic()
    first = enter_via_view(K.client_for(K.make_user()), topic)
    second = enter_via_view(K.client_for(K.make_user()), topic)
    assert conv_id_from(first) == conv_id_from(second)
    conv = Conversation.objects.get(pk=conv_id_from(first))
    assert conv.status == "active" and conv.participants.count() == 2


def test_a_third_person_gets_a_new_waiting_conversation_not_a_third_seat():
    from forum.models import Conversation

    topic = new_topic()
    ids = [conv_id_from(enter_via_view(K.client_for(K.make_user()), topic)) for _ in range(3)]
    assert ids[0] == ids[1] != ids[2]
    assert Conversation.objects.get(pk=ids[0]).participants.count() == 2
    third = Conversation.objects.get(pk=ids[2])
    assert third.status == "open" and third.participants.count() == 1


def test_entering_twice_returns_to_the_same_waiting_conversation():
    from forum.models import Conversation

    topic, client = new_topic(), K.client_for(K.make_user())
    a = conv_id_from(enter_via_view(client, topic))
    b = conv_id_from(enter_via_view(client, topic))
    assert a == b
    assert Conversation.objects.count() == 1


def test_a_user_is_never_paired_with_themself_even_from_two_browsers():
    from forum.models import Conversation

    topic, user = new_topic(), K.make_user()
    a = conv_id_from(enter_via_view(K.client_for(user), topic))
    b = conv_id_from(enter_via_view(K.client_for(user), topic))
    assert a == b
    conv = Conversation.objects.get(pk=a)
    assert conv.status == "open" and conv.participants.count() == 1


def test_entering_again_while_the_conversation_is_active_returns_to_it():
    duo = K.Duo()
    response = enter_via_view(duo.ca, duo.topic)
    assert conv_id_from(response) == duo.conv.pk


def test_after_a_conversation_is_ended_entering_starts_a_new_one():
    duo = K.Duo()
    duo.ca.post(duo.end_url)
    response = enter_via_view(duo.ca, duo.topic)
    assert conv_id_from(response) != duo.conv.pk


def test_different_propositions_have_separate_conversations():
    a, b = new_topic("First proposition."), new_topic("Second proposition.")
    client = K.client_for(K.make_user())
    assert conv_id_from(enter_via_view(client, a)) != conv_id_from(enter_via_view(client, b))


def test_a_hidden_proposition_cannot_be_entered():
    from forum.models import Conversation

    hidden = K.make_topic("Hidden proposition text.", created_by=K.make_user(), hidden=True)
    before = Conversation.objects.count()
    response = enter_via_view(K.client_for(K.make_user()), hidden)
    assert response.status_code != 500
    assert Conversation.objects.count() == before
    if response.status_code == 302:
        assert not re.fullmatch(r"/c/\d+/", response["Location"])


def test_a_hidden_proposition_gives_the_user_a_visible_reason():
    hidden = K.make_topic("Hidden proposition text.", created_by=K.make_user(), hidden=True)
    client = K.client_for(K.make_user())
    response = client.post(enter_url(hidden), follow=True)
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert H.alert_text(response).strip(), "a refusal must be shown to the user, not swallowed"


def test_too_many_open_conversations_is_explained_on_the_page():
    from forum.models import Conversation

    user = K.make_user()
    for i in range(5):
        K.enter(user, new_topic(f"Open one {i}."))
    before = Conversation.objects.count()
    client = K.client_for(user)
    response = client.post(enter_url(new_topic("The sixth.")), follow=True)
    assert response.status_code == 200
    alert = H.alert_text(response)
    assert "5 open conversations" in alert
    assert "End one before starting another." in alert
    assert Conversation.objects.count() == before


def test_the_open_conversation_limit_in_the_message_comes_from_settings(settings):
    settings.MAX_OPEN_CONVERSATIONS = 2
    user = K.make_user()
    for i in range(2):
        K.enter(user, new_topic(f"Open one {i}."))
    response = K.client_for(user).post(enter_url(new_topic("The third.")), follow=True)
    assert "2 open conversations" in H.alert_text(response)


# --- the waiting page -------------------------------------------------------------------------------------------------

def waiting_page(proposition="Cats make better pets than dogs."):
    user = K.make_user(K.NAME_A, K.EMAIL_A)
    topic = K.make_topic(proposition, created_by=user)
    conv = K.enter(user, topic)
    client = K.client_for(user)
    return client, conv, client.get(reverse("forum:conversation", args=[conv.pk]))


def test_the_waiting_page_says_when_you_can_post_and_shows_the_proposition():
    _, _, response = waiting_page("Cats make better pets than dogs.")
    assert response.status_code == 200
    text = H.unescape(response.content.decode())
    assert K.WAITING_TEXT in text
    assert "Cats make better pets than dogs." in text


def test_the_waiting_page_has_no_composer():
    _, conv, response = waiting_page()
    root = H.doc(response)
    assert not root.find_all("textarea")
    assert H.form_with_action(root, reverse("forum:post", args=[conv.pk])) is None
    assert not [n for n in H.controls(root) if n.get("name") == "text" or n.tag == "textarea"]


def test_the_waiting_page_lets_the_creator_end_it_with_a_post_button():
    _, conv, response = waiting_page()
    root = H.doc(response)
    form = H.form_with_action(root, reverse("forum:end", args=[conv.pk]))
    assert form is not None and form.get("method", "").lower() == "post"
    assert H.hidden_csrf(form) and H.has_button(form)


def test_the_waiting_page_never_expires_a_conversation_by_age(clock):
    client, conv, _ = waiting_page()
    clock.advance(60 * 60 * 24 * 40)  # forty days
    client = K.client_for(conv.participants.get().user)  # a fresh sign-in: the old session is older than 30 days
    response = client.get(reverse("forum:conversation", args=[conv.pk]))
    assert response.status_code == 200
    assert K.WAITING_TEXT in H.unescape(response.content.decode())
    assert K.fresh(conv).status == "open"


def test_when_someone_joins_the_first_persons_page_now_offers_the_composer(field_names):
    client, conv, _ = waiting_page()
    K.enter(K.make_user(K.NAME_B, K.EMAIL_B), conv.topic)
    response = client.get(reverse("forum:conversation", args=[conv.pk]))
    root = H.doc(response)
    assert H.form_with_action(root, reverse("forum:post", args=[conv.pk])) is not None
    assert K.WAITING_TEXT not in H.unescape(response.content.decode())


def test_posting_to_a_waiting_conversation_is_explained_and_saves_nothing(field_names):
    client, conv, _ = waiting_page()
    response = client.post(reverse("forum:post", args=[conv.pk]), {field_names["message"]: "Anyone there?"})
    assert response.status_code == 200
    assert K.WAITING_TEXT in H.unescape(H.alert_text(response))
    assert conv.messages.count() == 0
    assert K.runs(conv).count() == 0
