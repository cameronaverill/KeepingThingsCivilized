"""7c Revision 3: the propose page's seeded-topic section (two position buttons per topic, own-conversation variant, hidden
when empty), starting a discussion from it, the position phrase, and no opening-message preview anywhere."""
import re

import pytest
from django.urls import reverse

import fviews_cards as C
import fviews_html as H
import fviews_kit as K

Choice, choices_in = C.Choice, C.choices_in


def propose(client):
    return client.get(reverse("forum:propose"))


def flat(response):
    return " ".join(H.unescape(H.doc(response).text()).split())


def seeded(text, opposing="", **kw):
    return K.make_topic(text, created_by=None, opposing=opposing, **kw)


def choices(response):
    return choices_in(H.doc(response))


def section(response):
    """The seeded-topic section: the parent of the heading 'Or start from one of these topics'."""
    root = H.doc(response)
    heading = next((h for h in root.walk() if h.tag in ("h2", "h3") and h.text().strip() == K.SEEDED_HEADING), None)
    return heading, (heading.parent if heading is not None else None)


# --- the section ------------------------------------------------------------------------------------------------------

def test_the_section_is_headed_and_lists_seeded_topics_by_id_after_the_form():
    a = seeded("First seeded claim.", "First opposite.")
    b = seeded("Second seeded claim.", "Second opposite.")
    response = propose(K.client_for(K.make_user()))
    heading, box = section(response)
    assert heading is not None and box is not None
    body = response.content.decode()
    assert body.index("My position is that") < body.index(K.SEEDED_HEADING), "the form comes first"
    ids = [int(re.fullmatch(r"/p/(\d+)/enter/", f.get("action")).group(1)) for f in C.enter_forms(box)]
    assert ids == [a.pk, a.pk, b.pk, b.pk]


def test_the_section_is_hidden_when_there_are_no_seeded_topics():
    K.make_topic("Only a user-created claim.", created_by=K.make_user())
    response = propose(K.client_for(K.make_user()))
    assert K.SEEDED_HEADING not in flat(response)
    assert not C.enter_forms(H.doc(response))


def test_user_created_and_hidden_topics_are_not_listed_as_suggestions():
    seeded("Visible seeded claim.", "Visible opposite.")
    K.make_topic("User created claim.", created_by=K.make_user())
    seeded("Hidden seeded claim.", "Hidden opposite.", hidden=True)
    page = propose(K.client_for(K.make_user())).content.decode()
    assert "Visible seeded claim." in page
    assert "User created claim." not in page and "Hidden seeded claim." not in page


def test_each_topic_has_a_neutral_heading_and_two_post_buttons_with_a_hidden_side():
    topic = seeded("Cities should cap how much landlords can raise rents each year.", "Cities should not cap rents.")
    response = propose(K.client_for(K.make_user()))
    heading, box = section(response)
    titles = [n for n in box.walk() if n.tag in ("h3", "h4", "h2") and n.text() == topic.proposition]
    assert titles, "the neutral proposition, capitalised as stored, is the topic's heading"
    got = choices(response)[topic.pk]
    assert set(got) == {"pro", "con"}
    for side, choice in got.items():
        assert choice.form.get("method", "").lower() == "post" and H.hidden_csrf(choice.form)
        assert choice.form.get("action") == reverse("forum:enter", args=[topic.pk])
    assert got["pro"].text == "My position is that cities should cap how much landlords can raise rents each year."
    assert got["con"].text == "My position is that cities should not cap rents."


def test_the_disagree_button_is_used_when_the_topic_has_no_opposing_wording():
    topic = seeded("A seeded claim with one wording.")
    got = choices(propose(K.client_for(K.make_user())))[topic.pk]
    assert got["con"].text == K.DISAGREE_BUTTON == "I disagree with this position"
    assert got["pro"].text == "My position is that a seeded claim with one wording."


@pytest.mark.parametrize("claim", ["Israel should recognise Palestine.", "NATO should expand.", "I would ban cars.", "America should act."])
def test_a_listed_proper_noun_acronym_or_i_keeps_its_capital_after_the_fixed_start(claim):
    topic = seeded(claim, "Something else entirely.")
    assert choices(propose(K.client_for(K.make_user())))[topic.pk]["pro"].text == "My position is that " + claim


def test_button_text_is_escaped():
    seeded("Tom & <b>Jerry</b> rule.", "Nobody <i>else</i> does.")
    page = propose(K.client_for(K.make_user())).content.decode()
    assert "<b>Jerry</b>" not in page and "<i>else</i>" not in page
    assert "&lt;b&gt;Jerry&lt;/b&gt;" in page


# --- the viewer's own conversation on a seeded topic --------------------------------------------------------------------

@pytest.mark.parametrize("side", ["pro", "con"])
def test_a_waiting_conversation_of_yours_replaces_the_two_buttons_with_one_open_button(side):
    topic = seeded("Own waiting seeded claim.", "Own waiting opposite.")
    other = seeded("A different seeded claim.", "A different opposite.")
    conv, me = K.wait_on(topic, side)
    client = K.client_for(me)
    response = propose(client)
    got = choices(response)
    assert list(got[topic.pk]) == [side] and got[topic.pk][side].text == K.OPEN_OWN_BUTTON == "Open your conversation"
    assert flat(response).count(K.OWN_CONVERSATION) == 1 and K.OWN_CONVERSATION == "You already have a conversation here."
    assert set(got[other.pk]) == {"pro", "con"}
    posted = client.post(got[topic.pk][side].form.get("action"), {"side": side})
    assert posted.status_code == 302 and K.conv_id(posted) == conv.pk


def test_an_active_conversation_gets_the_open_button_for_each_participant():
    duo = K.Duo("Active seeded claim.", opposing="Active seeded opposite.")
    duo.topic.created_by = None
    duo.topic.save()
    for client, side in ((duo.ca, "pro"), (duo.cb, "con")):
        response = propose(client)
        got = choices(response)[duo.topic.pk]
        assert list(got) == [side] and got[side].text == K.OPEN_OWN_BUTTON
        assert K.conv_id(client.post(got[side].form.get("action"), {"side": side})) == duo.conv.pk


def test_an_ended_conversation_no_longer_counts_and_other_peoples_conversations_never_do():
    duo = K.Duo("Ended seeded claim.", opposing="Ended seeded opposite.")
    duo.topic.created_by = None
    duo.topic.save()
    duo.ca.post(duo.end_url)
    response = propose(duo.ca)
    assert set(choices(response)[duo.topic.pk]) == {"pro", "con"} and K.OWN_CONVERSATION not in flat(response)
    stranger = K.client_for(K.make_user())
    other = seeded("Strangers see two buttons.", "Yes.")
    K.wait_on(other, "pro")
    assert set(choices(propose(stranger))[other.pk]) == {"pro", "con"}


# --- starting from the list ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("side", ["pro", "con"])
def test_clicking_a_seeded_button_with_nobody_waiting_creates_a_waiting_conversation_on_that_side(side):
    from forum.models import Conversation

    topic = seeded("Startable seeded claim.", "Startable seeded opposite.")
    user = K.make_user()
    client = K.client_for(user)
    choice = choices(propose(client))[topic.pk][side]
    response = client.post(choice.form.get("action"), {"side": choice.side})
    assert response.status_code == 302
    conv = Conversation.objects.get(pk=K.conv_id(response))
    assert conv.topic == topic and conv.status == "open"
    assert [(p.user_id, p.side) for p in conv.participants.all()] == [(user.pk, side)]
    landing = client.get(response["Location"])
    assert K.WAITING_TITLE in H.unescape(landing.content.decode())


def test_a_seeded_start_then_shows_up_on_the_home_page_of_others_with_the_right_card():
    topic = seeded("Loop seeded claim.", "Loop seeded opposite.")
    starter = K.client_for(K.make_user())
    choice = choices(propose(starter))[topic.pk]["con"]
    starter.post(choice.form.get("action"), {"side": "con"})
    [card] = C.waiting_cards(H.doc(K.client_for(K.make_user()).get(reverse("forum:home"))))
    assert card.quote == "Loop seeded opposite." and card.side == "pro"
    assert card.button_text == "My position is that loop seeded claim."


def test_two_people_clicking_opposite_seeded_buttons_end_up_in_one_active_conversation():
    from forum.models import Conversation

    topic = seeded("Pair seeded claim.", "Pair seeded opposite.")
    a, b = K.client_for(K.make_user()), K.client_for(K.make_user())
    ca = choices(propose(a))[topic.pk]["pro"]
    cb = choices(propose(b))[topic.pk]["con"]
    one = a.post(ca.form.get("action"), {"side": "pro"})
    two = b.post(cb.form.get("action"), {"side": "con"})
    assert K.conv_id(one) == K.conv_id(two)
    assert Conversation.objects.get(pk=K.conv_id(one)).status == "active"


def test_the_seeded_section_shows_no_preview_or_message_text():
    topic = seeded("Waiting seeded claim.", "Waiting seeded opposite.")
    conv, author = K.wait_on(topic, "pro")
    K.seed_message(conv, K.participant_of(conv, author), "Opener-secret-words.", minutes_ago=10)
    response = propose(K.client_for(K.make_user()))
    text = flat(response)
    assert "Opener-secret-words." not in response.content.decode()
    for phrase in ("Their opening message", "Shortened here", "Someone who holds the other position"):
        assert phrase not in text


def test_the_old_preview_setting_and_rules_are_gone(settings):
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / "forum" / "static" / "forum" / "site.css").read_text()
    assert "preview" not in css
    assert not hasattr(settings, "WAITING_PREVIEW_CHARS")
    import forum.services as services

    assert not hasattr(services, "waiting_previews")


# --- the position_phrase function and filter ---------------------------------------------------------------------------

def position_phrase(text):
    from forum.templatetags.forum_text import position_phrase as function

    return function(text)


EXCEPTIONS = """America American Americans Canada Canadian China Chinese Europe European Germany Israel Israeli Mexico Mexican
Russia Russian Ukraine Ukrainian Palestinian Palestine Iran India Britain British France French Congress Democrats Republicans
Trump Biden Muslims Christians Jews Black White Asian Hispanic Latino""".split()


def test_the_function_lower_cases_only_the_first_letter():
    assert position_phrase("Cities Should Cap Rents") == "cities Should Cap Rents"
    assert position_phrase("Cities should cap Rent.") == "cities should cap Rent."
    assert position_phrase("cities should") == "cities should"
    assert position_phrase("") == ""
    assert position_phrase("A") == "a"
    assert position_phrase("The 2020 election was fair.") == "the 2020 election was fair."


@pytest.mark.parametrize("name", EXCEPTIONS)
def test_every_listed_proper_noun_keeps_its_capital(name):
    sentence = f"{name} should change its rules."
    assert position_phrase(sentence) == sentence


@pytest.mark.parametrize("sentence", [
    "NATO should expand.", "UN funding should rise.", "AI should be regulated.", "GDP growth matters.", "USA should lead.",
    "COVID-19 rules should end.", "5G should be public.", "H1B visas should be capped.", "3D printing should be taught.", "100 cities should try it.",
    "Covid-19 rules should end.",
])
def test_acronyms_and_number_like_first_words_are_left_alone(sentence):
    assert position_phrase(sentence) == sentence


@pytest.mark.parametrize("sentence", ["I think so.", "I would ban them.", "I'm certain.", "I'll pay.", "I've seen it.", "I'd agree."])
def test_the_word_i_and_i_apostrophe_words_are_left_alone(sentence):
    assert position_phrase(sentence) == sentence


@pytest.mark.parametrize("sentence", ["It is fine.", "Ideas matter.", "Islands are lovely.", "In cities we win."])
def test_words_that_merely_start_with_i_are_lower_cased(sentence):
    assert position_phrase(sentence) == sentence[0].lower() + sentence[1:]


@pytest.mark.parametrize("sentence", ["Blackberries are fine.", "Whitehouse rules apply.", "Americana is fine.", "Chinatown is fine.",
                                      "Frenchfries are fine.", "Indiana is fine.", "Congressional rules apply.", "Trumpet lessons are fine."])
def test_the_exception_list_matches_whole_words_only(sentence):
    assert position_phrase(sentence) == sentence[0].lower() + sentence[1:]


def test_the_function_is_stable_when_applied_twice():
    for sentence in ("Cities should cap rents.", "Israel should act.", "NATO should act.", "I agree."):
        assert position_phrase(position_phrase(sentence)) == position_phrase(sentence)


def test_non_ascii_first_letters_are_lower_cased():
    assert position_phrase("\u00c9coles should be free.") == "\u00e9coles should be free."


def test_the_template_filter_is_registered_and_escapes():
    from django.template import Context, Template

    template = Template("{% load forum_text %}{{ text|position_phrase }}")
    assert template.render(Context({"text": "Cities should cap."})) == "cities should cap."
    assert template.render(Context({"text": "NATO & <b>bold</b>"})) == "NATO &amp; &lt;b&gt;bold&lt;/b&gt;"
    assert template.render(Context({"text": "Tom & <b>rule</b>"})) == "tom &amp; &lt;b&gt;rule&lt;/b&gt;"
