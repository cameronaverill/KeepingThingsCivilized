"""7c: a waiting conversation is a usable conversation: banner and pill wording, the composer works with every limit
of an active conversation, the second person sees everything, polling works while waiting."""
import json
import re

import pytest
from django.db import connection
from django.urls import reverse

import fviews_html as H
import fviews_js as J
import fviews_kit as K

pytestmark = []


def waiting(proposition="Cats make better pets than dogs.", side="pro", name=K.NAME_A, opposing=""):
    user = K.make_user(name, f"{name}@leakcheck.example")
    topic = K.make_topic(proposition, created_by=user, opposing=opposing)
    conv = K.enter(user, topic, side)
    client = K.client_for(user)
    return user, topic, conv, client, reverse("forum:conversation", args=[conv.pk])


def post_url(conv):
    return reverse("forum:post", args=[conv.pk])


def say(client, conv, field_names, text):
    return client.post(post_url(conv), {field_names["message"]: text})


def text_of(response):
    return H.unescape(H.doc(response).text())


def composer(response, conv):
    return H.form_with_action(H.doc(response), post_url(conv))


# --- the page: banner, pill, composer, end card ------------------------------------------------------------------------

def test_the_waiting_banner_has_the_final_title_and_text():
    _, _, conv, client, url = waiting()
    response = client.get(url)
    assert response.status_code == 200
    text = text_of(response)
    assert K.WAITING_BODY in text
    root = H.doc(response)
    titles = [n for n in root.walk() if n.text() == K.WAITING_TITLE]
    assert titles, "an element whose whole text is the banner title"
    assert text.count(K.WAITING_TITLE) >= 2, "the title in the banner and the status pill say the same"


def test_the_status_pill_of_a_waiting_conversation_reads_the_final_wording():
    _, _, conv, client, url = waiting()
    root = H.doc(client.get(url))
    pills = [n for n in root.walk() if "pill" in (n.get("class", "") + " " + n.get("id", ""))]
    assert pills and any(p.text() == K.WAITING_TITLE for p in pills), [p.text() for p in pills]


def test_the_banner_and_pill_are_gone_once_someone_joins():
    user, topic, conv, client, url = waiting()
    K.enter(K.make_user(K.NAME_B, K.EMAIL_B), topic, "con")
    for c in (client,):
        text = text_of(c.get(url))
        assert K.WAITING_TITLE not in text and K.WAITING_BODY not in text


def test_the_waiting_page_has_a_working_composer_not_a_replacement_box(field_names):
    _, _, conv, client, url = waiting()
    response = client.get(url)
    form = composer(response, conv)
    assert form is not None and form.get("method", "").lower() == "post"
    assert H.hidden_csrf(form) and H.has_button(form)
    control = H.text_control(form)
    assert control.tag == "textarea" and "maxlength" not in control.attrs
    assert not H.unlabelled_controls(H.doc(response))
    assert H.counter_nodes(H.doc(response)), "the character counter is there while waiting"
    assert "3,000" in H.doc(response).text()


def test_the_waiting_page_keeps_the_end_conversation_card():
    _, _, conv, client, url = waiting()
    response = client.get(url)
    form = H.form_with_action(H.doc(response), reverse("forum:end", args=[conv.pk]))
    assert form is not None and form.get("method", "").lower() == "post" and H.hidden_csrf(form)
    assert "End conversation" in text_of(response)
    assert "Ending closes this conversation before anyone joins it." in text_of(response)


def test_the_old_waiting_wording_is_gone_from_every_state_of_every_page(field_names, clock):
    user, topic, conv, client, url = waiting(name="olduser_one")
    pages = [client.get(url), client.get("/"), client.get("/propose/"), client.get("/how-it-works/"),
             say(client, conv, field_names, "")]
    duo = K.Duo("Another proposition entirely.", names=("olduser_two", "olduser_three"))
    duo.ca.post(duo.end_url)
    pages += [duo.ca.get(duo.url), duo.cb.get(duo.url)]
    for response in pages:
        text = text_of(response)
        for phrase in K.OLD_PHRASES:
            assert phrase not in text, f"old wording still on a page: {phrase!r}"
        assert not re.search(r"(?i)told which side|which side (the other|you take)", response.content.decode())


# --- posting while waiting ---------------------------------------------------------------------------------------------

def test_the_first_person_can_post_while_waiting_and_a_pending_live_run_is_created(field_names, clock):
    user, topic, conv, client, url = waiting()
    response = say(client, conv, field_names, "First words while waiting.")
    assert response.status_code == 302 and response["Location"] == url
    msg = K.user_messages(conv).get()
    assert msg.content == "First words while waiting." and msg.participant.user == user
    run = K.runs(conv).get()
    assert (run.trigger_message, run.kind, run.status, run.snapshot_seq) == (msg, "live", "pending", msg.seq_no)
    assert K.fresh(conv).status == "open", "posting does not make it active"
    assert "First words while waiting." in client.get(url).content.decode()


def test_own_messages_show_as_you_and_several_in_a_row_are_fine(field_names, clock):
    _, _, conv, client, url = waiting()
    for i in range(3):
        assert say(client, conv, field_names, f"Waiting-token-{i}").status_code == 302
        clock.advance(31)
    body = client.get(url).content.decode()
    root = H.doc(client.get(url))
    for i in range(3):
        block = K.find_block(root, f"Waiting-token-{i}", ["You", "The other participant"])
        assert block is not None and re.search(r"\bYou\b", block.text()) and "The other participant" not in block.text()
    assert body.index("Waiting-token-0") < body.index("Waiting-token-1") < body.index("Waiting-token-2")


def test_the_thirty_second_gap_applies_while_waiting(field_names, clock):
    _, _, conv, client, url = waiting()
    say(client, conv, field_names, "one")
    clock.advance(10)
    response = say(client, conv, field_names, "two, too soon")
    assert response.status_code == 200
    alert = H.alert_text(response)
    assert "Please wait 20 more seconds" in alert
    assert H.control_value(H.text_control(composer(response, conv))) == "two, too soon"
    assert K.user_messages(conv).count() == 1 and K.runs(conv).count() == 1


def test_empty_and_too_long_are_refused_while_waiting_with_the_text_kept(field_names):
    _, _, conv, client, url = waiting()
    empty = say(client, conv, field_names, "   ")
    assert empty.status_code == 200 and "Your message is empty." in H.alert_text(empty)
    text = "n" * 3001
    long = say(client, conv, field_names, text)
    assert long.status_code == 200
    alert = H.alert_text(long)
    assert "3,001" in alert and "3,000" in alert and re.search(r"shorten it by 1 character", alert)
    assert H.control_value(H.text_control(composer(long, conv))) == text
    assert K.user_messages(conv).count() == 0 and K.runs(conv).count() == 0


def test_exactly_3000_characters_is_accepted_while_waiting(field_names):
    _, _, conv, client, url = waiting()
    assert say(client, conv, field_names, "m" * 3000).status_code == 302


def test_the_thirty_message_cap_counts_the_waiting_persons_messages(field_names, clock):
    user, topic, conv, client, url = waiting()
    part = K.participant_of(conv, user)
    for i in range(29):
        K.seed_message(conv, part, f"seed {i}", minutes_ago=600 - i)
    clock.advance(60)
    assert say(client, conv, field_names, "The thirtieth.").status_code == 302
    assert K.fresh(conv).status == "closed"
    refused = say(client, conv, field_names, "The thirty-first.")
    assert refused.status_code == 200 and K.CLOSED_TEXT in H.alert_text(refused)
    assert K.user_messages(conv).count() == 30


def test_a_full_waiting_conversation_says_so(field_names, clock):
    user, topic, conv, client, url = waiting()
    part = K.participant_of(conv, user)
    for i in range(30):
        K.seed_message(conv, part, f"seed {i}", minutes_ago=600 - i)
    clock.advance(60)
    refused = say(client, conv, field_names, "one too many")
    assert refused.status_code == 200
    assert "This conversation has reached its limit of 30 messages and is closed. You can start a new one." in H.alert_text(refused)


def test_a_waiting_conversation_that_was_ended_refuses_posts_and_says_you_ended_it(field_names):
    _, _, conv, client, url = waiting()
    client.post(reverse("forum:end", args=[conv.pk]))
    refused = say(client, conv, field_names, "after the end")
    alert = H.unescape(H.alert_text(refused))
    assert K.CLOSED_TEXT in alert and K.YOU_ENDED in alert
    assert K.user_messages(conv).count() == 0


def test_a_stranger_cannot_read_or_post_in_a_waiting_conversation(field_names):
    _, _, conv, client, url = waiting()
    say(client, conv, field_names, "Private waiting words.")
    stranger = K.client_for(K.make_user())
    assert stranger.get(url).status_code == 404
    response = say(stranger, conv, field_names, "let me in")
    assert response.status_code == 404 and "Private waiting words." not in response.content.decode()
    assert K.user_messages(conv).count() == 1


def test_the_moderator_reviews_the_waiting_persons_message_and_the_card_shows_for_them(field_names):
    user, topic, conv, client, url = waiting()
    part = K.participant_of(conv, user)
    m1 = K.seed_message(conv, part, "Words for review.", minutes_ago=5)
    K.add_moderator_post(conv, m1, "Moderator-review-words", [(part.label, part.label, [m1])])
    response = client.get(url)
    root = H.doc(response)
    block = K.find_block(root, "Moderator-review-words", ["About your message", "For both of you", "About the conversation"])
    assert block is not None and "About your message 1" in block.text()
    assert K.leaks(response.content.decode(), user, K.make_user("other_obs", "other_obs@leakcheck.example")) == []


@pytest.mark.django_db(transaction=True)
def test_sync_mode_runs_the_pipeline_for_a_post_made_while_waiting(field_names, settings, monkeypatch):
    settings.MODERATION_RUN_MODE = "sync"
    user, topic, conv, client, url = waiting()
    seen = {}

    def moderate(run):
        seen["atomic"] = connection.in_atomic_block
        seen["participants"] = run.conversation.participants.count()

    calls = K.install_fake_pipeline(monkeypatch, moderate)
    assert say(client, conv, field_names, "Please review while I wait.").status_code == 302
    assert len(calls) == 1 and calls[0].trigger_message.content == "Please review while I wait."
    assert seen == {"atomic": False, "participants": 1}


# --- the second person joins -------------------------------------------------------------------------------------------

def test_the_second_person_sees_everything_posted_so_far_and_can_post_at_once(field_names, clock):
    user, topic, conv, client, url = waiting("Cats make better pets than dogs.", name=K.NAME_A)
    for i in range(3):
        say(client, conv, field_names, f"Earlier-token-{i}")
        clock.advance(31)
    joiner = K.make_user(K.NAME_B, K.EMAIL_B)
    jc = K.client_for(joiner)
    entered = K.enter_view(jc, topic, "con")
    assert K.conv_id(entered) == conv.pk
    response = jc.get(entered["Location"])
    body = response.content.decode()
    assert body.index("Earlier-token-0") < body.index("Earlier-token-1") < body.index("Earlier-token-2")
    root = H.doc(response)
    for i in range(3):
        block = K.find_block(root, f"Earlier-token-{i}", ["You", K.NAME_A])
        assert K.NAME_A in block.text() and not re.search(r"\bYou\b", block.text().replace(K.NAME_A, ""))
    assert K.WAITING_TITLE not in text_of(response)
    assert composer(response, conv) is not None
    assert K.leaks(body, joiner, user, other_name_ok=True) == []
    assert say(jc, conv, field_names, "Joiner answers at once.").status_code == 302
    seen = client.get(url).content.decode()
    assert "Joiner answers at once." in seen and "Earlier-token-2" in seen


def test_the_first_person_is_not_asked_to_wait_again_and_their_page_shows_the_other_side(field_names):
    user, topic, conv, client, url = waiting(opposing="Cats make worse pets than dogs.")
    K.enter(K.make_user(K.NAME_B, K.EMAIL_B), topic, "con")
    text = text_of(client.get(url))
    assert "Your position: Cats make better pets than dogs." in re.sub(r"(position:)(\S)", r"\1 \2", text)
    assert K.WAITING_TITLE not in text


# --- polling while waiting ---------------------------------------------------------------------------------------------

def test_the_polling_endpoint_serves_a_waiting_conversation_with_the_persons_own_messages(field_names, clock):
    user, topic, conv, client, url = waiting()
    say(client, conv, field_names, "Poll-me-one")
    clock.advance(31)
    say(client, conv, field_names, "Poll-me-two")
    response, data = K.poll_json(client, conv)
    assert data["status"] == "open"
    assert [(m["kind"], m["text"]) for m in data["messages"]] == [("you", "Poll-me-one"), ("you", "Poll-me-two")]
    assert [m["seq_no"] for m in K.poll_json(client, conv, after=1)[1]["messages"]] == [2]
    assert "no-store" in response["Cache-Control"]
    other = K.make_user("obs_two", "obs_two@leakcheck.example")
    assert K.json_leaks(data, user, other) == []


def test_after_someone_joins_the_poll_reports_active_and_keeps_the_earlier_messages():
    user, topic, conv, client, url = waiting()
    part = K.participant_of(conv, user)
    K.seed_message(conv, part, "Kept-for-the-joiner", minutes_ago=10)
    K.enter(K.make_user(K.NAME_B, K.EMAIL_B), topic, "con")
    _, data = K.poll_json(client, conv)
    assert data["status"] == "active" and [m["text"] for m in data["messages"]] == ["Kept-for-the-joiner"]


def waiting_page(settings, messages=2):
    settings.POLL_SECONDS = 7
    user, topic, conv, client, url = waiting()
    part = K.participant_of(conv, user)
    for i in range(messages):
        K.seed_message(conv, part, f"Own-token-{i + 1}", minutes_ago=60 - i)
    page = J.Page(client.get(url).content.decode(), pathname=url)
    textarea = H.text_control(H.form_with_action(page.root, post_url(conv)))
    return user, topic, conv, client, page, page.eid(textarea)


def real(client, conv, after):
    return {"status": 200, "json": json.loads(client.get(reverse("forum:messages", args=[conv.pk]), {"after": after}).content)}


pytestmark_js = pytest.mark.skipif(J.NODE is None, reason="Node is not installed")


@pytestmark_js
def test_polling_a_waiting_page_never_repeats_the_persons_own_messages(settings):
    user, topic, conv, client, page, ta = waiting_page(settings)
    page.add(op="load", script=J.script_path("poll.js"))
    for after in (0, 0, 2):  # a server that repeats itself, then the normal poll
        page.add(op="queue_fetch", responses=[real(client, conv, after)])
        page.add(op="run_timer")
    K.seed_message(conv, K.participant_of(conv, user), "Own-token-3", minutes_ago=1)
    page.add(op="queue_fetch", responses=[real(client, conv, 2)])
    page.add(op="run_timer")
    body = page.add(op="dom_text", eid=page.eid(page.root.find("body")))
    errors = page.add(op="errors")
    results = page.run()
    text = results[body]
    for token in ("Own-token-1", "Own-token-2", "Own-token-3"):
        assert text.count(token) == 1, f"{token} shown {text.count(token)} times"
    assert results[errors] == []


@pytestmark_js
def test_polling_a_waiting_page_keeps_going_while_nothing_changes(settings):
    user, topic, conv, client, page, ta = waiting_page(settings)
    page.add(op="load", script=J.script_path("poll.js"))
    for _ in range(3):
        page.add(op="queue_fetch", responses=[real(client, conv, 2)])
        page.add(op="run_timer")
    timers = page.add(op="timers")
    reloads = page.add(op="reloads")
    results = page.run()
    assert results[reloads] == 0 and results[timers], "no reload and still polling"
    assert min(t["delay"] for t in results[timers]) == 7000


@pytestmark_js
@pytest.mark.parametrize("typed", ["", "half typed reply"])
def test_when_someone_joins_an_empty_box_reloads_and_a_typed_box_is_left_alone(settings, typed):
    user, topic, conv, client, page, ta = waiting_page(settings)
    page.add(op="load", script=J.script_path("poll.js"))
    page.add(op="focus", eid=ta)
    page.add(op="set_value", eid=ta, value=typed)
    K.enter(K.make_user("joiner_person", "joiner_person@leakcheck.example"), topic, "con")
    page.add(op="queue_fetch", responses=[real(client, conv, 2)])
    page.add(op="run_timer")
    reloads = page.add(op="reloads")
    value = page.add(op="value", eid=ta)
    connected = page.add(op="connected", eid=ta)
    timers = page.add(op="timers")
    errors = page.add(op="errors")
    notice = next((n for n in page.root.walk() if "Reload" in n.text() and n.tag not in ("html", "body", "main")
                   and not any("Reload" in c.text() for c in n.walk())), None)
    notice_hidden = page.add(op="attr", eid=page.eid(notice.parent if notice is not None and notice.tag == "a" else notice), name="hidden") \
        if notice is not None else None
    results = page.run()
    assert results[errors] == []
    if typed:
        if notice is not None:
            assert results[notice_hidden] is None, "the reload notice is shown when text is being typed"
        assert results[reloads] == 0, "typed text is never thrown away by a reload"
        assert results[value] == typed and results[connected] is True
        assert results[timers], "polling continues after the notice"
    else:
        assert results[reloads] == 1, "an empty box reloads to show the new state"
