"""Step 7c (revision 2): the opening-message preview is gone; own_conversations and own_sides (the home page's "You
already have a conversation here" and the side the viewer holds) remain."""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from fsvc_testkit import make_prop, make_user, make_waiting, svc

pytestmark = pytest.mark.django_db


def enter_as(user, topic, side):
    return svc().enter_proposition(user, topic, side)


def waiting_on(topic, text="I hold this position and here is why", side="pro", user=None):
    user = user or make_user()
    conv = enter_as(user, topic, side)
    if text is not None:
        svc().post_message(user, conv, text)
    return conv, user


@pytest.fixture(autouse=True)
def _frozen(clock):
    return clock


# --- the preview is gone ------------------------------------------------------------------------------------------------------


def test_waiting_previews_no_longer_exists():
    import forum.services as services

    assert not hasattr(services, "waiting_previews")
    assert "waiting_previews" not in getattr(services, "__all__", [])


def test_the_preview_tunable_is_gone(settings):
    from config import tunables

    assert not hasattr(tunables, "WAITING_PREVIEW_CHARS")
    assert not hasattr(settings, "WAITING_PREVIEW_CHARS")


def test_no_forum_service_or_view_model_module_mentions_a_preview_of_the_opening_message():
    import inspect

    import forum.services as services
    import forum.viewmodels as viewmodels

    for module in (services, viewmodels):
        source = inspect.getsource(module)
        assert "WAITING_PREVIEW" not in source and "waiting_previews" not in source


def test_the_view_model_of_a_waiting_conversation_shows_nothing_of_it_to_others():
    """Nobody but the two participants can read a conversation, waiting or not."""
    from forum.viewmodels import conversation_view

    topic = make_prop()
    conv, _ = waiting_on(topic, "my private opening words")
    from fsvc_testkit import rejection

    assert rejection(conversation_view, make_user(), conv).code == "not_participant"


# --- own_conversations / own_sides ------------------------------------------------------------------------------------------


def test_own_conversations_lists_the_topics_where_the_viewer_has_an_open_or_active_conversation():
    waiting_topic, active_topic, closed_topic, none_topic = make_prop(), make_prop(), make_prop(), make_prop()
    viewer = make_user()
    enter_as(viewer, waiting_topic, "pro")
    other = make_user()
    enter_as(other, active_topic, "pro")
    enter_as(viewer, active_topic, "con")
    closed = enter_as(viewer, closed_topic, "pro")
    svc().end_conversation(viewer, closed)
    topics = [waiting_topic, active_topic, closed_topic, none_topic]
    assert svc().own_conversations(topics, viewer) == {waiting_topic.pk, active_topic.pk}


def test_someone_elses_conversations_are_not_the_viewers():
    topic = make_prop()
    waiting_on(topic, "their words")
    assert svc().own_conversations([topic], make_user()) == set()
    assert svc().own_sides([topic], make_user()) == {}


def test_own_sides_gives_the_side_the_viewer_holds():
    a, b, c = make_prop(), make_prop(), make_prop()
    viewer = make_user()
    enter_as(viewer, a, "pro")
    enter_as(viewer, b, "con")
    assert svc().own_sides([a, b, c], viewer) == {a.pk: "pro", b.pk: "con"}


def test_own_sides_reports_pro_for_a_legacy_blank_side():
    topic = make_prop()
    user = make_user()
    make_waiting(topic, user=user)
    assert svc().own_sides([topic], user) == {topic.pk: "pro"}
    assert svc().own_conversations([topic], user) == {topic.pk}


def test_own_sides_is_limited_to_the_topics_asked_about():
    a, b = make_prop(), make_prop()
    viewer = make_user()
    enter_as(viewer, a, "pro")
    enter_as(viewer, b, "pro")
    assert svc().own_sides([a], viewer) == {a.pk: "pro"}
