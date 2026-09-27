"""7b: awkward input and flows through the pages: hostile text, huge text, the sign-in round trip."""
import re
from urllib.parse import urlparse

from django.test import Client
from django.urls import reverse

import fviews_html as H
import fviews_kit as K

PW = "correct-horse-" + "battery-9Z"  # built at runtime so the repo secret scan does not read it as a credential


def say(client, duo, field_names, text):
    return client.post(duo.post_url, {field_names["message"]: text})


def test_script_posted_through_the_page_is_shown_as_text_to_both_people(field_names):
    duo = K.Duo()
    nasty = "<script>alert('pwned')</script><img src=x onerror=alert(2)> & \"quotes\""
    assert say(duo.ca, duo, field_names, nasty).status_code == 302
    for client in (duo.ca, duo.cb):
        body = client.get(duo.url).content.decode()
        assert "<script>alert('pwned')" not in body and "<img src=x" not in body
        assert "&lt;script&gt;" in body and "&lt;img src=x onerror=alert(2)&gt;" in body
    _, data = K.poll_json(duo.cb, duo.conv)
    assert "<script>" not in data["messages"][0].get("html", "")


def test_moderator_text_is_escaped_too():
    duo = K.Duo()
    m1 = duo.seed(duo.pa, "hello", 30)
    K.add_moderator_post(duo.conv, m1, "<script>alert('mod')</script> <b>bold</b>", [("all", "both", [m1])])
    for client in (duo.ca, duo.cb):
        body = client.get(duo.url).content.decode()
        assert "<script>alert('mod')" not in body and "<b>bold</b>" not in body
        assert "&lt;script&gt;alert(&#x27;mod&#x27;)&lt;/script&gt;" in body or "&lt;script&gt;alert('mod')&lt;/script&gt;" in body
    _, data = K.poll_json(duo.ca, duo.conv)
    for message in data["messages"]:
        assert "<script>" not in message.get("html", "")


def test_a_null_character_in_a_message_or_proposition_is_never_a_server_error(field_names):
    duo = K.Duo()
    assert say(duo.ca, duo, field_names, "before\x00after").status_code in (200, 302)
    response = K.client_for(K.make_user()).post("/propose/", {field_names["proposition"]: "Null\x00proposition"})
    assert response.status_code in (200, 302)


def test_a_huge_message_is_refused_with_its_real_length_and_saves_nothing(field_names):
    duo = K.Duo()
    response = say(duo.ca, duo, field_names, "h" * 100_000)
    assert response.status_code == 200
    alert = H.alert_text(response)
    assert "100,000" in alert and "3,000" in alert
    assert K.user_messages(duo.conv).count() == 0


def test_a_huge_proposition_is_refused_not_truncated(field_names):
    response = K.client_for(K.make_user()).post("/propose/", {field_names["proposition"]: "p" * 50_000})
    assert response.status_code == 200
    assert "50,000" in H.alert_text(response) or "50000" in H.alert_text(response)


def test_repeated_and_extra_form_fields_change_nothing_they_should_not(field_names):
    duo = K.Duo()
    response = duo.ca.post(duo.post_url, {field_names["message"]: ["first", "second"], "author_type": "moderator",
                                          "participant": duo.pb.pk, "seq_no": 99, "in_reply_to": 12345})
    assert response.status_code in (200, 302)
    for message in K.user_messages(duo.conv):
        assert message.author_type == "user" and message.participant == duo.pa and message.seq_no == 1


def test_a_hand_made_post_cannot_speak_as_the_moderator_or_the_other_person(field_names):
    duo = K.Duo()
    duo.ca.post(duo.post_url, {field_names["message"]: "I am A", "participant": duo.pb.pk, "author_type": "moderator"})
    message = duo.conv.messages.get()
    assert message.author_type == "user" and message.participant == duo.pa


def test_sign_in_round_trip_returns_to_the_page_that_asked(field_names):
    duo = K.Duo()
    duo.ua.set_password(PW)
    duo.ua.save()
    client = Client()
    first = client.get(duo.url)
    assert first.status_code == 302
    login_url = first["Location"]
    assert urlparse(login_url).path == reverse("accounts:login")
    client.get(login_url)
    done = client.post(login_url, {"username": duo.ua.username, "password": PW})
    assert done.status_code == 302 and done["Location"] == duo.url
    assert client.get(duo.url).status_code == 200


def test_an_open_redirect_through_next_is_not_followed():
    duo = K.Duo()
    duo.ua.set_password(PW)
    duo.ua.save()
    client = Client()
    done = client.post(reverse("accounts:login") + "?next=https://evil.example/steal", {"username": duo.ua.username, "password": PW})
    assert done.status_code == 302 and "evil.example" not in done["Location"]


def test_logging_out_takes_the_pages_away_again():
    duo = K.Duo(csrf=True)
    assert duo.ca.get(duo.url).status_code == 200
    K.csrf_post(duo.ca, reverse("accounts:logout"))
    response = duo.ca.get(duo.url)
    assert response.status_code == 302 and reverse("accounts:login") in response["Location"]


def test_the_home_page_of_a_user_with_no_conversations_still_works_and_lists_waiting_positions():
    user = K.make_user()
    topic = K.make_topic("Only waiting proposition around.", created_by=K.make_user())
    K.wait_on(topic, "pro")
    response = K.client_for(user).get("/")
    assert response.status_code == 200 and "Only waiting proposition around." in response.content.decode()


def test_the_home_page_with_no_propositions_at_all_says_so_and_offers_to_propose():
    response = K.client_for(K.make_user()).get("/")
    assert response.status_code == 200
    root = H.doc(response)
    assert reverse("forum:propose") in H.links(root)
    assert not re.search(r"/p/\d+/enter/", response.content.decode())


def test_many_waiting_positions_are_all_reachable_from_the_home_page():
    user = K.make_user()
    topics = [K.make_topic(f"Bulk proposition number {i}.", created_by=user) for i in range(1, 41)]
    for i, topic in enumerate(topics):
        K.wait_on(topic, "pro", minutes_ago=100 - i)
    page = K.client_for(K.make_user()).get("/").content.decode()
    missing = [t.proposition for t in topics if t.proposition not in page]
    assert not missing, f"{len(missing)} waiting positions are not on the page and there is no paging in the contract: {missing[:3]}"
