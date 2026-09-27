"""forum/admin.py: the Topic admin and its hide / unhide actions are exactly as they were (step 7a); Conversation, Message
and Participant are added: conversation columns, message content, the author_type filter, participants read-only."""
import re

from django.contrib import admin
from django.test import RequestFactory

import adm_kit as K

TOPICS = K.url("forum", "topic", "changelist")


def topic_admin():
    from forum.models import Topic

    return admin.site._registry[Topic]


def request_for(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


# --- the Topic admin is unchanged -------------------------------------------------------------------------------------

def test_the_topic_admin_configuration_is_unchanged():
    ma = topic_admin()
    assert type(ma).__name__ == "TopicAdmin" and type(ma).__module__ == "forum.admin"
    assert ma.list_display == ("proposition_text", "created_by", "hidden", "created_at")
    assert ma.list_filter == ("hidden",)
    assert ma.search_fields == ("proposition", "title")
    assert ma.raw_id_fields == ("created_by",)
    assert ma.readonly_fields == ("created_at",)
    assert ma.ordering == ("-created_at", "-id")
    assert ma.actions == ["hide_selected_propositions", "unhide_selected_propositions"]


def test_the_topic_admin_offers_exactly_hide_unhide_and_the_standard_delete(superuser):
    assert sorted(topic_admin().get_actions(request_for(superuser))) == [
        "delete_selected", "hide_selected_propositions", "unhide_selected_propositions",
    ]


def test_the_topic_admin_is_not_read_only(root, world):
    ma = topic_admin()
    request = request_for(K.make_superuser())
    assert (ma.has_add_permission(request), ma.has_change_permission(request), ma.has_delete_permission(request)) == (True, True, True)
    assert root.get(K.url("forum", "topic", "add")).status_code == 200
    assert root.get(K.url("forum", "topic", "delete", world.topic.pk)).status_code == 200


def test_the_topic_change_form_still_saves(root, world):
    response = root.post(
        K.url("forum", "topic", "change", world.seeded_topic.pk),
        {"title": "Trains", "description": "seeded", "proposition": "Trains should be free, always.", "leans": '{"left": {"x": 1}}',
         "hidden": "on", "_save": "Save"},
    )
    assert response.status_code == 302
    world.seeded_topic.refresh_from_db()
    assert (world.seeded_topic.proposition, world.seeded_topic.hidden) == ("Trains should be free, always.", True)


def test_the_topic_list_shows_the_propositions_creator_and_hidden_flag(root, world):
    html = K.page(root.get(TOPICS))
    assert "Cats make better pets than dogs." in html and "Trains should be free." in html
    assert "zelda_mox" in html
    assert {"proposition_text", "created_by", "hidden", "created_at"} - K.column_classes(html) == set()


def test_topic_search_by_proposition_and_by_title_still_works(root, world):
    assert K.listed_pks(root.get(TOPICS, {"q": "Cats make"}), "forum", "topic") == [world.topic.pk]
    assert K.listed_pks(root.get(TOPICS, {"q": "Trains"}), "forum", "topic") == [world.seeded_topic.pk]


def test_the_topic_hidden_filter_still_works(root, world):
    from forum.models import Topic

    Topic.objects.filter(pk=world.topic.pk).update(hidden=True)
    assert K.listed_pks(root.get(TOPICS, {"hidden__exact": "1"}), "forum", "topic") == [world.topic.pk]
    assert K.listed_pks(root.get(TOPICS, {"hidden__exact": "0"}), "forum", "topic") == [world.seeded_topic.pk]


def action_value(root, description):
    html = K.page(root.get(TOPICS))
    values = {re.sub(r"\s+", " ", d).strip(): v for v, d in re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', html) if v}
    return values[description]


def test_the_hide_action_hides_the_selected_topics_only_and_keeps_their_conversations(root, world):
    from forum.models import Conversation, Topic

    conversations = Conversation.objects.count()
    response = root.post(
        TOPICS,
        {"action": action_value(root, "Hide selected propositions"), "_selected_action": [world.topic.pk]},
        follow=True,
    )
    assert "Hid 1 proposition(s). Their conversations are kept." in K.page(response)
    assert dict(Topic.objects.values_list("pk", "hidden")) == {world.topic.pk: True, world.seeded_topic.pk: False}
    assert Conversation.objects.count() == conversations


def test_the_unhide_action_unhides_the_selected_topics(root, world):
    from forum.models import Topic

    Topic.objects.update(hidden=True)
    response = root.post(
        TOPICS,
        {"action": action_value(root, "Unhide selected propositions"), "_selected_action": [world.seeded_topic.pk]},
        follow=True,
    )
    assert "Unhid 1 proposition(s)." in K.page(response)
    assert dict(Topic.objects.values_list("pk", "hidden")) == {world.topic.pk: True, world.seeded_topic.pk: False}


def test_hiding_every_topic_at_once_reports_the_count(root, world):
    response = root.post(
        TOPICS,
        {"action": action_value(root, "Hide selected propositions"), "_selected_action": [world.topic.pk, world.seeded_topic.pk]},
        follow=True,
    )
    assert "Hid 2 proposition(s)." in K.page(response)


# --- Conversation ------------------------------------------------------------------------------------------------------

def test_the_conversation_list_has_the_contract_columns(root, world):
    columns = K.column_classes(K.page(root.get(K.url("forum", "conversation", "changelist"))))
    assert {"id", "topic", "status", "source", "experiment", "created_at", "ended_by"} - columns == set()


def test_the_conversation_list_shows_each_conversation_with_its_ended_by(root, world):
    html = K.page(root.get(K.url("forum", "conversation", "changelist")))
    assert "Participant B of conversation 8002" in html


def test_the_conversation_change_page_shows_its_fields(root, world):
    html = K.page(root.get(K.url("forum", "conversation", "change", world.conv2.pk)))
    assert K.readonly_text(html, "status") == "closed"
    assert K.readonly_text(html, "source") == "synthetic"
    assert K.readonly_text(html, "transcript_id") == "t-1"
    assert "Participant B of conversation 8002" in K.field_html(html, "ended_by")


# --- Message ------------------------------------------------------------------------------------------------------------

def test_the_message_change_page_shows_content_author_and_sequence(root, world):
    html = K.page(root.get(K.url("forum", "message", "change", world.mod.pk)))
    assert "Moderator note: could you say where that figure comes from?" in html
    assert K.readonly_text(html, "author_type") == "moderator"
    assert K.readonly_text(html, "seq_no") == str(world.mod.seq_no)


def test_a_long_message_is_shown_in_full_on_its_page(root, world):
    from forum.models import Message

    text = "long words " * 60 + "ENDOFLONGMESSAGE"
    msg = Message.objects.create(conversation=world.conv, author_type="user", participant=world.pa, content=text)
    assert "ENDOFLONGMESSAGE" in K.page(root.get(K.url("forum", "message", "change", msg.pk)))


def test_the_message_content_is_escaped_on_the_admin_pages(root, world):
    from forum.models import Message

    msg = Message.objects.create(
        conversation=world.conv, author_type="user", participant=world.pa, content="<script>alert('XSSMARK')</script>"
    )
    for target in (K.url("forum", "message", "change", msg.pk), K.url("forum", "message", "changelist")):
        html = K.page(root.get(target))
        assert "<script>alert('XSSMARK')" not in html


# --- Participant --------------------------------------------------------------------------------------------------------

def test_the_participant_list_shows_label_conversation_and_user(root, world):
    html = K.page(root.get(K.url("forum", "participant", "changelist")))
    assert "zelda_mox" in html and "quincy_ray" in html
    assert "Conversation 7001" in html and "Conversation 8002" in html


def test_the_participant_of_a_synthetic_conversation_renders_without_a_user(root, world):
    assert root.get(K.url("forum", "participant", "change", world.qa.pk)).status_code == 200
