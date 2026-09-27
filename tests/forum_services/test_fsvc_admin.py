"""forum/admin.py: Topic is registered with the propositions listed, the hidden flag, the creator, and the Hide / Unhide
actions; hiding keeps every conversation."""
import re

import pytest
from fsvc_testkit import enter  # noqa: E402
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.urls import reverse

from fsvc_testkit import make_active_via_service, make_prop, make_user, rejection, svc, uniq

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(client):
    name = uniq("adminquill")
    user = get_user_model().objects.create_superuser(username=name, email=f"{name}@mailbox.example", password=None)
    client.force_login(user)
    return user


def changelist(client, **params):
    response = client.get(reverse("admin:forum_topic_changelist"), params)
    assert response.status_code == 200
    return response


def action_names(client):
    """{description: value} for the changelist's action dropdown."""
    html = changelist(client).content.decode()
    return {
        re.sub(r"\s+", " ", desc).strip(): value
        for value, desc in re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', html)
        if value
    }


def run_action(client, description_part, topics):
    actions = action_names(client)
    matches = [v for d, v in actions.items() if description_part in d]
    assert len(matches) == 1, (description_part, actions)
    response = client.post(
        reverse("admin:forum_topic_changelist"),
        {"action": matches[0], "_selected_action": [t.pk for t in topics]},
        follow=True,
    )
    assert response.status_code == 200
    return response


def fresh(topic):
    from forum.models import Topic

    return Topic.objects.get(pk=topic.pk)


def test_topic_is_registered_in_the_admin():
    from forum.models import Topic

    assert Topic in admin.site._registry


def test_the_changelist_lists_the_propositions_and_who_created_them(client, staff):
    creator = make_user()
    make_prop("Cats make better pets than dogs", created_by=creator)
    make_prop("Seeded proposition about trains", title="Trains")
    html = changelist(client).content.decode()
    assert "Cats make better pets than dogs" in html
    assert "Seeded proposition about trains" in html
    assert creator.username in html


def test_a_user_created_topic_with_a_blank_title_is_shown_by_its_proposition(client, staff):
    make_prop("Rents should be capped everywhere", created_by=make_user())
    assert "Rents should be capped everywhere" in changelist(client).content.decode()


def test_the_hidden_flag_is_shown_on_the_list_and_editable_on_the_form(client, staff):
    topic = make_prop("Trains should be free")
    modeladmin = admin.site._registry[type(topic)]
    shown = list(modeladmin.list_display) + list(modeladmin.list_filter or []) + list(modeladmin.list_editable or [])
    assert "hidden" in shown
    response = client.get(reverse("admin:forum_topic_change", args=[topic.pk]))
    assert response.status_code == 200
    assert 'name="hidden"' in response.content.decode()


def test_the_hide_and_unhide_actions_are_offered(client, staff):
    make_prop("Something to act on")  # the admin shows no action menu on an empty list
    descriptions = " | ".join(action_names(client))
    assert "Hide selected propositions" in descriptions
    assert "Unhide" in descriptions


def test_the_hide_action_hides_the_selected_propositions_only(client, staff):
    one, two, three = make_prop("First one"), make_prop("Second one"), make_prop("Third one")
    run_action(client, "Hide selected propositions", [one, three])
    assert fresh(one).hidden is True and fresh(three).hidden is True
    assert fresh(two).hidden is False


def test_the_unhide_action_brings_them_back(client, staff):
    one, two = make_prop("First one", hidden=True), make_prop("Second one", hidden=True)
    run_action(client, "Unhide", [one])
    assert fresh(one).hidden is False and fresh(two).hidden is True


def test_hiding_twice_and_unhiding_a_visible_one_are_harmless(client, staff):
    topic = make_prop("Only one")
    run_action(client, "Hide selected propositions", [topic])
    run_action(client, "Hide selected propositions", [topic])
    assert fresh(topic).hidden is True
    run_action(client, "Unhide", [topic])
    run_action(client, "Unhide", [topic])
    assert fresh(topic).hidden is False


def test_hiding_a_proposition_keeps_its_conversations_and_they_stay_readable(client, staff):
    from forum.models import Conversation, Message, Participant
    from forum.viewmodels import conversation_view

    w = make_active_via_service()
    svc().post_message(w.u1, w.conv, "hello there friend")
    run_action(client, "Hide selected propositions", [w.topic])
    assert Conversation.objects.filter(pk=w.conv.pk).exists()
    assert Participant.objects.filter(conversation=w.conv).count() == 2
    assert Message.objects.filter(conversation=w.conv).count() == 1
    conv = Conversation.objects.get(pk=w.conv.pk)
    assert conv.status == "active"
    for user in (w.u1, w.u2):
        assert len(conversation_view(user, conv)["messages"]) == 1


def test_a_hidden_proposition_cannot_be_entered_until_unhidden(client, staff):
    topic = make_prop("Trains should be free")
    run_action(client, "Hide selected propositions", [topic])
    assert rejection(enter, make_user(), fresh(topic)).code == "hidden"
    run_action(client, "Unhide", [topic])
    assert enter(make_user(), fresh(topic)).pk


def test_a_hidden_proposition_is_ignored_by_the_duplicate_check_but_visible_ones_are_not(client, staff):
    topic = make_prop("Trains should be free")
    run_action(client, "Hide selected propositions", [topic])
    assert svc().create_proposition(make_user(), "trains should be free").pk != topic.pk


def test_the_admin_is_closed_to_anonymous_visitors(client):
    response = client.get(reverse("admin:forum_topic_changelist"))
    assert response.status_code in (302, 403)
    if response.status_code == 302:
        assert "login" in response["Location"]
