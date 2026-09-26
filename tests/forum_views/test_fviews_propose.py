"""7b: the propose page (docs/step7_brief.md: create_proposition through a form, refusals shown with the text kept)."""
import re

from django.urls import reverse

import fviews_html as H
import fviews_kit as K

URL = "/propose/"


def propose(client, field_names, text):
    return client.post(URL, {field_names["proposition"]: text})


def form_of(response):
    root = H.doc(response)
    return next(f for f in H.forms(root) if H.text_control(f) is not None)


def shown_text(response):
    """What the browser would show in the proposition box after this response."""
    return H.control_value(H.text_control(form_of(response)))


def topic_count():
    from forum.models import Topic

    return Topic.objects.count()


# --- the page ---------------------------------------------------------------------------------------------------------

def test_get_shows_a_labelled_post_form_with_csrf_and_a_button():
    response = K.client_for(K.make_user()).get(URL)
    assert response.status_code == 200
    form = form_of(response)
    assert form.get("method", "").lower() == "post"
    assert form.get("action", "") in ("", URL)
    assert H.hidden_csrf(form)
    assert H.has_button(form)
    assert not H.unlabelled_controls(H.doc(response))


def test_the_box_states_the_limit_has_a_live_counter_and_no_maxlength():
    response = K.client_for(K.make_user()).get(URL)
    control = H.text_control(form_of(response))
    assert "maxlength" not in control.attrs, "nothing may be silently truncated"
    root = H.doc(response)
    counters = H.counter_nodes(root)
    assert counters, "counter markup (an element whose id, class or data- attribute says counter) is required"
    assert "200" in root.text()
    assert any("200" in n.text() or any("200" in str(v) for v in n.attrs.values()) for n in counters + [form_of(response)])


def test_the_limit_comes_from_settings(settings):
    settings.MAX_PROPOSITION_CHARS = 77
    response = K.client_for(K.make_user()).get(URL)
    text = H.doc(response).text()
    assert "77" in text and "200" not in text


# --- success ----------------------------------------------------------------------------------------------------------

def test_a_valid_proposition_creates_a_topic_and_redirects_into_a_waiting_conversation(field_names):
    from forum.models import Conversation, Topic

    user = K.make_user()
    client = K.client_for(user)
    response = propose(client, field_names, "  Dogs   are\n\ngreat  pets.  ")
    assert response.status_code == 302
    match = re.fullmatch(r"/c/(\d+)/", response["Location"])
    assert match, response["Location"]
    topic = Topic.objects.get()
    assert topic.proposition == "Dogs are great pets."
    assert topic.created_by == user and topic.hidden is False and topic.title == ""
    conv = Conversation.objects.get(pk=int(match.group(1)))
    assert conv.topic == topic and conv.status == "open"
    assert list(conv.participants.values_list("user", flat=True)) == [user.pk]
    landing = client.get(response["Location"])
    assert landing.status_code == 200
    assert K.WAITING_TEXT in H.unescape(landing.content.decode())


def test_the_new_proposition_is_on_the_home_page_at_once(field_names):
    user = K.make_user()
    client = K.client_for(user)
    propose(client, field_names, "Appears-immediately-token.")
    assert "Appears-immediately-token." in client.get("/").content.decode()
    other = K.client_for(K.make_user())
    assert "Appears-immediately-token." in other.get("/").content.decode()


def test_exactly_200_characters_is_accepted(field_names):
    text = "x" * 200
    response = propose(K.client_for(K.make_user()), field_names, text)
    assert response.status_code == 302
    from forum.models import Topic

    assert Topic.objects.get().proposition == text


def test_length_counts_characters_the_same_way_as_messages(field_names):
    """200 emoji, or 200 letters written as base letter + combining mark (200 after NFC), are 200 characters."""
    client = K.client_for(K.make_user())
    assert propose(client, field_names, "\U0001f600" * 200).status_code == 302
    assert propose(client, field_names, "é" * 200).status_code == 302
    assert propose(client, field_names, "\U0001f600" * 201).status_code == 200


# --- refusals: reason, next step, text kept, nothing saved ------------------------------------------------------------

def test_201_characters_is_refused_with_the_count_and_the_limit_and_the_text_kept(field_names):
    text = "y" * 201
    before = topic_count()
    response = propose(K.client_for(K.make_user()), field_names, text)
    assert response.status_code == 200
    alert = H.alert_text(response)
    assert "201" in alert and "200" in alert
    assert re.search(r"(?i)shorten|shorter|too long|limit", alert)
    assert shown_text(response) == text
    assert topic_count() == before


def test_a_refused_text_is_shown_back_escaped_and_intact(field_names):
    text = "<script>alert('x')</script> & \"quotes\" " + "z" * 200
    response = propose(K.client_for(K.make_user()), field_names, text)
    assert response.status_code == 200
    assert "<script>alert('x')</script>" not in response.content.decode()
    assert shown_text(response) == text


def test_an_empty_proposition_is_refused_with_a_reason(field_names):
    for text in ("", "   ", "\n\t \r\n"):
        before = topic_count()
        response = propose(K.client_for(K.make_user()), field_names, text)
        assert response.status_code == 200
        assert "empty" in H.alert_text(response).lower()
        assert topic_count() == before


def test_a_missing_field_is_answered_like_an_empty_one_not_with_an_error(field_names):
    response = K.client_for(K.make_user()).post(URL, {})
    assert response.status_code == 200
    assert H.alert_text(response)


def test_a_duplicate_is_refused_and_points_to_the_list(field_names):
    K.make_topic("Dogs are great pets.", created_by=K.make_user())
    before = topic_count()
    for text in ("Dogs are great pets.", "  DOGS   ARE great\tpets. "):
        response = propose(K.client_for(K.make_user()), field_names, text)
        assert response.status_code == 200
        alert = H.alert_text(response)
        assert "already exists" in alert
        assert "Choose it from the list" in alert
        assert topic_count() == before


def test_the_daily_limit_is_explained_and_nothing_is_created(field_names):
    user = K.make_user()
    for i in range(20):
        K.make_topic(f"Daily-limit filler proposition {i}.", created_by=user)
    before = topic_count()
    response = propose(K.client_for(user), field_names, "The twenty-first one.")
    assert response.status_code == 200
    alert = H.alert_text(response)
    assert "20 propositions today" in alert and "daily limit" in alert
    assert re.search(r"(?i)tomorrow", alert)
    assert shown_text(response) == "The twenty-first one."
    assert topic_count() == before


def test_the_daily_limit_is_per_user(field_names):
    user = K.make_user()
    for i in range(20):
        K.make_topic(f"Filler proposition {i}.", created_by=user)
    other = K.client_for(K.make_user())
    assert propose(other, field_names, "Someone else may still propose.").status_code == 302


def test_the_twentieth_proposition_is_accepted(field_names):
    user = K.make_user()
    for i in range(19):
        K.make_topic(f"Filler proposition {i}.", created_by=user)
    assert propose(K.client_for(user), field_names, "The twentieth one.").status_code == 302


def test_the_limits_in_the_messages_come_from_settings(settings, field_names):
    settings.MAX_PROPOSITION_CHARS = 40
    response = propose(K.client_for(K.make_user()), field_names, "w" * 41)
    alert = H.alert_text(response)
    assert "41" in alert and "40" in alert and "200" not in alert
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 2
    user = K.make_user()
    for i in range(2):
        K.make_topic(f"Filler {i}.", created_by=user)
    alert = H.alert_text(propose(K.client_for(user), field_names, "Third."))
    assert "2 propositions today" in alert


def test_too_many_open_conversations_is_explained_and_no_proposition_is_left_behind(field_names):
    """Contract ambiguity settled by the tester: the proposition and its first conversation are one action, so a refusal
    to enter saves no proposition (otherwise the user's retry would hit 'already exists')."""
    user = K.make_user()
    for i in range(5):
        K.enter(user, K.make_topic(f"Already-open proposition {i}.", created_by=user))
    before = topic_count()
    response = propose(K.client_for(user), field_names, "One too many conversations.")
    assert response.status_code == 200
    alert = H.alert_text(response)
    assert "5 open conversations" in alert and "End one before starting another." in alert
    assert shown_text(response) == "One too many conversations."
    assert topic_count() == before


def test_a_refusal_changes_no_conversation_and_creates_no_run(field_names):
    from forum.models import Conversation

    before = Conversation.objects.count()
    propose(K.client_for(K.make_user()), field_names, "q" * 201)
    assert Conversation.objects.count() == before
    assert K.runs().count() == 0
