"""7b/7c: entering a proposition with a chosen side follows the pairing rule (docs/step7_brief.md, docs/step7c_brief.md
"Two-position model"). The waiting page itself is tested in test_fviews_waiting.py."""
import re

import pytest
from django.urls import reverse

import fviews_html as H
import fviews_kit as K


def enter_url(topic):
    return reverse("forum:enter", args=[topic.pk])


def new_topic(text="Coffee is better than tea.", opposing=""):
    return K.make_topic(text, created_by=K.make_user(), opposing=opposing)


def conv_of(response):
    from forum.models import Conversation

    return Conversation.objects.get(pk=K.conv_id(response))


def side_of(conv, user):
    return conv.participants.get(user=user).side


# --- pairing by opposite side -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("side", ["pro", "con"])
def test_entering_with_a_side_starts_a_waiting_conversation_holding_that_side(side):
    topic, user = new_topic(), K.make_user()
    response = K.enter_view(K.client_for(user), topic, side)
    assert response.status_code == 302
    conv = conv_of(response)
    assert conv.topic == topic and conv.status == "open"
    assert [(p.user_id, p.side) for p in conv.participants.all()] == [(user.pk, side)]
    assert conv.label_seed is not None, "the seed is drawn when the waiting conversation is created"


@pytest.mark.parametrize("first, second", [("pro", "con"), ("con", "pro")])
def test_the_opposite_side_joins_the_waiting_conversation_and_it_becomes_active(first, second):
    topic = new_topic()
    a, b = K.make_user(), K.make_user()
    one = K.enter_view(K.client_for(a), topic, first)
    two = K.enter_view(K.client_for(b), topic, second)
    assert K.conv_id(one) == K.conv_id(two)
    conv = conv_of(one)
    assert conv.status == "active" and conv.participants.count() == 2
    assert side_of(conv, a) == first and side_of(conv, b) == second
    assert len({p.label for p in conv.participants.all()}) == 2


@pytest.mark.parametrize("side", ["pro", "con"])
def test_two_people_waiting_on_the_same_side_do_not_pair(side):
    from forum.models import Conversation

    topic = new_topic()
    one = K.enter_view(K.client_for(K.make_user()), topic, side)
    two = K.enter_view(K.client_for(K.make_user()), topic, side)
    assert K.conv_id(one) != K.conv_id(two)
    assert Conversation.objects.filter(status="open").count() == 2
    assert all(c.participants.count() == 1 for c in Conversation.objects.all())


def test_the_oldest_waiting_conversation_on_the_opposite_side_is_joined_first():
    topic = new_topic()
    first = K.conv_id(K.enter_view(K.client_for(K.make_user()), topic, "pro"))
    second = K.conv_id(K.enter_view(K.client_for(K.make_user()), topic, "pro"))
    joiner = K.conv_id(K.enter_view(K.client_for(K.make_user()), topic, "con"))
    assert joiner == first != second


def test_a_third_person_gets_a_new_waiting_conversation_not_a_third_seat():
    topic = new_topic()
    ids = [K.conv_id(K.enter_view(K.client_for(K.make_user()), topic, side)) for side in ("pro", "con", "con")]
    assert ids[0] == ids[1] != ids[2]
    from forum.models import Conversation

    assert Conversation.objects.get(pk=ids[0]).participants.count() == 2
    third = Conversation.objects.get(pk=ids[2])
    assert third.status == "open" and third.participants.count() == 1


@pytest.mark.parametrize("again", ["pro", "con"])
def test_entering_again_returns_to_your_own_waiting_conversation_whatever_side_you_pick(again):
    from forum.models import Conversation

    topic, client = new_topic(), K.client_for(K.make_user())
    a = K.conv_id(K.enter_view(client, topic, "pro"))
    b = K.conv_id(K.enter_view(client, topic, again))
    assert a == b and Conversation.objects.count() == 1
    assert Conversation.objects.get().participants.count() == 1


def test_a_user_is_never_paired_with_themself_even_from_two_browsers():
    topic, user = new_topic(), K.make_user()
    a = K.conv_id(K.enter_view(K.client_for(user), topic, "pro"))
    b = K.conv_id(K.enter_view(K.client_for(user), topic, "con"))
    assert a == b
    conv = conv_of(K.enter_view(K.client_for(user), topic, "con"))
    assert conv.status == "open" and conv.participants.count() == 1


@pytest.mark.parametrize("again", ["pro", "con"])
def test_entering_again_while_active_returns_to_it_whatever_side_is_sent(again):
    duo = K.Duo()
    response = K.enter_view(duo.ca, duo.topic, again)
    assert K.conv_id(response) == duo.conv.pk
    assert K.fresh(duo.pa).side == "pro" and K.fresh(duo.pb).side == "con", "nobody's side is rewritten"


def test_after_a_conversation_is_ended_entering_starts_a_new_one():
    duo = K.Duo()
    duo.ca.post(duo.end_url)
    assert K.conv_id(K.enter_view(duo.ca, duo.topic, "pro")) != duo.conv.pk


def test_different_propositions_have_separate_conversations():
    a, b = new_topic("First proposition."), new_topic("Second proposition.")
    client = K.client_for(K.make_user())
    assert K.conv_id(K.enter_view(client, a, "pro")) != K.conv_id(K.enter_view(client, b, "pro"))


def test_a_legacy_waiting_conversation_with_a_blank_side_counts_as_pro():
    """Old rows: one participant, side blank, no seed. A 'con' joins it; a 'pro' does not."""
    from forum.models import Conversation, Participant

    topic = new_topic()
    old_user = K.make_user()
    old = Conversation.objects.create(topic=topic, status="open")
    Participant.objects.create(conversation=old, user=old_user, label="A", join_order=1)
    same = K.enter_view(K.client_for(K.make_user()), topic, "pro")
    assert K.conv_id(same) != old.pk
    joined = K.enter_view(K.client_for(K.make_user()), topic, "con")
    assert K.conv_id(joined) == old.pk and K.fresh(old).status == "active"


# --- a missing or bad side ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [None, "", "left", "pro,con", "1", "<script>", "a" * 500, "none"])
def test_a_missing_or_bad_side_is_explained_and_nothing_is_created(bad):
    from forum.models import Conversation, Participant

    topic = new_topic()
    client = K.client_for(K.make_user())
    before = (Conversation.objects.count(), Participant.objects.count())
    response = K.enter_view(client, topic, bad)
    assert response.status_code in (200, 400), response.status_code
    assert K.CHOOSE_POSITION in H.unescape(H.alert_text(response)), "a role=alert message says what to do"
    assert (Conversation.objects.count(), Participant.objects.count()) == before
    assert K.runs().count() == 0
    body = response.content.decode()
    assert "Traceback" not in body and "invalid_side" not in body


@pytest.mark.parametrize("odd", ["PRO", "Pro", " pro", "pro "])
def test_an_oddly_written_side_is_either_refused_or_stored_as_pro_never_stored_as_written(odd):
    """The brief does not say whether the view tidies the value; either answer is fine, garbage in the database is not."""
    from forum.models import Participant

    response = K.enter_view(K.client_for(K.make_user()), new_topic(), odd)
    assert response.status_code in (200, 302, 400)
    assert set(Participant.objects.values_list("side", flat=True)) <= {"pro"}
    if response.status_code != 302:
        assert K.CHOOSE_POSITION in H.unescape(H.alert_text(response))


def test_two_sides_in_one_request_are_not_a_way_to_pick_both():
    from forum.models import Conversation

    topic = new_topic()
    response = K.client_for(K.make_user()).post(enter_url(topic), {"side": ["pro", "con"]})
    assert response.status_code in (200, 302, 400)
    for conv in Conversation.objects.all():
        assert conv.participants.count() == 1 and conv.participants.get().side in ("pro", "con")


# --- refusals ----------------------------------------------------------------------------------------------------------

def test_a_hidden_proposition_cannot_be_entered_on_either_side():
    from forum.models import Conversation

    hidden = K.make_topic("Hidden proposition text.", created_by=K.make_user(), hidden=True)
    before = Conversation.objects.count()
    for side in ("pro", "con"):
        response = K.enter_view(K.client_for(K.make_user()), hidden, side)
        assert response.status_code != 500
        assert Conversation.objects.count() == before
        if response.status_code == 302:
            assert not re.fullmatch(r"/c/\d+/", response["Location"])


def test_a_hidden_proposition_gives_the_user_a_visible_reason():
    hidden = K.make_topic("Hidden proposition text.", created_by=K.make_user(), hidden=True)
    response = K.client_for(K.make_user()).post(enter_url(hidden), {"side": "pro"}, follow=True)
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert H.alert_text(response).strip(), "a refusal must be shown to the user, not swallowed"


def test_too_many_open_conversations_is_explained_on_the_page(settings):
    from forum.models import Conversation

    settings.MAX_OPEN_CONVERSATIONS = 5  # off by default; the capacity to switch the limit back on stays

    user = K.make_user()
    for i in range(5):
        K.enter(user, new_topic(f"Open one {i}."), "pro")
    before = Conversation.objects.count()
    response = K.client_for(user).post(enter_url(new_topic("The sixth.")), {"side": "con"}, follow=True)
    assert response.status_code == 200
    alert = H.alert_text(response)
    assert "5 open conversations" in alert and "End one before starting another." in alert
    assert Conversation.objects.count() == before


def test_the_open_conversation_limit_in_the_message_comes_from_settings(settings):
    settings.MAX_OPEN_CONVERSATIONS = 2
    user = K.make_user()
    for i in range(2):
        K.enter(user, new_topic(f"Open one {i}."), "pro")
    response = K.client_for(user).post(enter_url(new_topic("The third.")), {"side": "pro"}, follow=True)
    assert "2 open conversations" in H.alert_text(response)


def test_returning_to_your_own_conversation_is_allowed_even_at_the_open_limit(settings):
    settings.MAX_OPEN_CONVERSATIONS = 5
    user = K.make_user()
    topics = [new_topic(f"Open one {i}.") for i in range(5)]
    convs = [K.enter(user, t, "pro") for t in topics]
    response = K.enter_view(K.client_for(user), topics[2], "pro")
    assert response.status_code == 302 and K.conv_id(response) == convs[2].pk


def test_entering_a_missing_proposition_is_a_404_with_or_without_a_side():
    client = K.client_for(K.make_user())
    assert client.post(reverse("forum:enter", args=[987654]), {"side": "pro"}).status_code == 404
    assert client.post(reverse("forum:enter", args=[987654])).status_code == 404


def test_by_default_there_is_no_limit_on_open_conversations():
    from django.conf import settings as live

    assert live.MAX_OPEN_CONVERSATIONS is None
    user = K.make_user()
    for i in range(8):
        assert K.enter_view(K.client_for(user), new_topic(f"Unlimited open one {i}."), "pro").status_code == 302
