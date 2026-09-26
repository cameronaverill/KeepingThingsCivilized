"""7b: the JSON polling endpoint /c/<id>/messages/ (docs/step7_brief.md: ?after=<seq_no>, status, no-store)."""
import json

import pytest
from django.test import Client
from django.urls import reverse

import fviews_html as H
import fviews_kit as K


def poll(client, duo, after=None):
    return K.poll_json(client, duo.poll_url, after)


def seqs(data):
    return [m["seq_no"] for m in data["messages"]]


def test_the_endpoint_answers_json_with_no_store_headers():
    duo = K.Duo()
    response, data = poll(duo.ca, duo)
    assert response["Content-Type"].startswith("application/json")
    assert "no-store" in response["Cache-Control"]
    assert isinstance(data["messages"], list)


def test_status_is_the_conversation_status():
    duo = K.Duo()
    assert poll(duo.ca, duo)[1]["status"] == "active"
    duo.ca.post(duo.end_url)
    assert poll(duo.cb, duo)[1]["status"] == "closed"


def test_a_waiting_conversation_reports_open_and_no_messages():
    user = K.make_user()
    conv = K.enter(user, K.make_topic("Waiting proposition.", created_by=user))
    response, data = K.poll_json(K.client_for(user), conv)
    assert data["status"] == "open" and data["messages"] == []
    assert "no-store" in response["Cache-Control"]


def test_all_messages_come_back_without_after_and_each_has_the_fields_the_page_needs():
    duo = K.Duo()
    duo.seed(duo.pa, "first", 30)
    duo.seed(duo.pb, "second", 29)
    _, data = poll(duo.ca, duo)
    assert seqs(data) == [1, 2]
    for message in data["messages"]:
        assert {"seq_no", "kind", "text", "created_at"} <= set(message)
    assert [m["text"] for m in data["messages"]] == ["first", "second"]


def test_after_returns_only_newer_messages():
    duo = K.Duo()
    for i in range(5):
        duo.seed(duo.pa if i % 2 == 0 else duo.pb, f"msg {i}", 30 - i)
    assert seqs(poll(duo.ca, duo, after=0)[1]) == [1, 2, 3, 4, 5]
    assert seqs(poll(duo.ca, duo, after=2)[1]) == [3, 4, 5]
    assert seqs(poll(duo.ca, duo, after=5)[1]) == []
    assert seqs(poll(duo.ca, duo, after=99)[1]) == []
    assert poll(duo.ca, duo, after=5)[1]["status"] == "active"


def test_kinds_are_worked_out_per_viewer():
    duo = K.Duo()
    duo.seed(duo.pa, "by A", 30)
    duo.seed(duo.pb, "by B", 29)
    assert [m["kind"] for m in poll(duo.ca, duo)[1]["messages"]] == ["you", "other"]
    assert [m["kind"] for m in poll(duo.cb, duo)[1]["messages"]] == ["other", "you"]


def test_a_new_post_shows_up_in_the_other_persons_next_poll(field_names):
    duo = K.Duo()
    duo.seed(duo.pa, "old", 30)
    before = poll(duo.cb, duo)[1]
    last = max(seqs(before))
    duo.ca.post(duo.post_url, {field_names["message"]: "brand new"})
    after = poll(duo.cb, duo, after=last)[1]
    assert [m["text"] for m in after["messages"]] == ["brand new"]
    assert after["messages"][0]["kind"] == "other"


def test_the_moderator_post_is_picked_up_with_the_heading_for_each_viewer():
    duo = K.Duo()
    m1 = duo.seed(duo.pa, "by A", 30)
    _, seen = poll(duo.cb, duo)
    last = max(seqs(seen))
    K.add_moderator_post(duo.conv, m1, "Moderator says hello", [(duo.pa.label, duo.pa.label, [m1])])
    _, for_b = poll(duo.cb, duo, after=last)
    _, for_a = poll(duo.ca, duo, after=last)
    assert [(m["kind"], m["text"], m["heading"]) for m in for_b["messages"]] == [
        ("moderator", "Moderator says hello", "About the other participant's message 1")]
    assert [(m["kind"], m["text"], m["heading"]) for m in for_a["messages"]] == [
        ("moderator", "Moderator says hello", "About your message 1")]


def test_the_json_never_names_a_person_or_a_label():
    duo = K.Duo()
    m1 = duo.seed(duo.pa, "by A", 30)
    duo.seed(duo.pb, "by B", 29)
    K.add_moderator_post(duo.conv, m1, "Mod text", [(duo.pa.label, duo.pa.label, [m1])])
    for client, viewer, other in ((duo.ca, duo.ua, duo.ub), (duo.cb, duo.ub, duo.ua)):
        _, data = poll(client, duo)
        assert K.json_leaks(data, viewer, other) == []


def test_a_closed_conversation_reports_closed_with_its_messages():
    duo = K.Duo()
    duo.seed(duo.pa, "kept", 30)
    duo.cb.post(duo.end_url)
    _, data = poll(duo.ca, duo)
    assert data["status"] == "closed" and [m["text"] for m in data["messages"]] == ["kept"]


@pytest.mark.parametrize("bad", ["abc", "-3", "1.5", "", "99999999999999999999999", "0x10", "1;2", "%00"])
def test_a_bad_after_never_causes_a_server_error(bad):
    duo = K.Duo()
    duo.seed(duo.pa, "one", 30)
    response = duo.ca.get(duo.poll_url, {"after": bad})
    assert response.status_code in (200, 400)
    if response.status_code == 200:
        json.loads(response.content)


def test_polling_is_read_only_and_creates_no_run():
    duo = K.Duo()
    duo.seed(duo.pa, "one", 30)
    for _ in range(3):
        poll(duo.ca, duo)
    assert K.runs(duo.conv).count() == 0 and K.user_messages(duo.conv).count() == 1


def test_a_non_participant_and_a_missing_conversation_get_the_same_404():
    duo = K.Duo()
    duo.seed(duo.pa, "secret words", 30)
    stranger = K.client_for(K.make_user())
    hidden = stranger.get(duo.poll_url)
    missing = stranger.get(reverse("forum:messages", args=[987654]))
    assert hidden.status_code == missing.status_code == 404
    assert H.normalise_page(hidden) == H.normalise_page(missing)
    assert b"secret words" not in hidden.content


def test_anonymous_polling_is_sent_to_login():
    duo = K.Duo()
    response = Client().get(duo.poll_url)
    assert response.status_code == 302 and reverse("accounts:login") in response["Location"]
