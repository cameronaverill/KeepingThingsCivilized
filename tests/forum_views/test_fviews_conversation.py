"""7b: two browsers hold a conversation: the page, posting, refusals, moderator posts, ended and closed states."""
import re

import pytest
from django.db import connection
from django.urls import reverse

import fviews_html as H
import fviews_kit as K


def say(client, duo, field_names, text, **extra):
    return client.post(duo.post_url, {field_names["message"]: text}, **extra)


def page(client, duo):
    response = client.get(duo.url)
    assert response.status_code == 200
    return response


def composer(response, duo):
    return H.form_with_action(H.doc(response), duo.post_url)


def norm(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def speaker_in_block(response, token):
    """'You' or 'other' for the message block holding `token`, from the speaker label inside that block."""
    root = H.doc(response)
    block = K.find_block(root, token, ["You", "The other participant"])
    assert block is not None, f"{token!r} is not shown next to a speaker label"
    text = block.text().replace(token, "")
    has_you = re.search(r"\bYou\b", text) is not None
    has_other = "The other participant" in text
    assert has_you != has_other, f"the block for {token!r} must carry exactly one speaker label: {text!r}"
    return "you" if has_you else "other"


# --- the page for an active conversation ------------------------------------------------------------------------------

def test_the_active_page_shows_the_proposition_and_a_labelled_composer_with_the_limit(field_names):
    duo = K.Duo("Cats make better pets than dogs.")
    response = page(duo.ca, duo)
    assert "Cats make better pets than dogs." in H.unescape(response.content.decode())
    form = composer(response, duo)
    assert form is not None and form.get("method", "").lower() == "post"
    assert H.hidden_csrf(form) and H.has_button(form)
    control = H.text_control(form)
    assert control.tag == "textarea"
    assert "maxlength" not in control.attrs
    assert not H.unlabelled_controls(H.doc(response))
    assert "3,000" in H.doc(response).text()
    counters = H.counter_nodes(H.doc(response))
    assert counters, "counter markup is required next to the composer"


def test_the_composer_limit_comes_from_settings(settings):
    settings.MAX_MESSAGE_CHARS = 1234
    duo = K.Duo()
    text = H.doc(page(duo.ca, duo)).text()
    assert "1,234" in text and "3,000" not in text


def test_the_sidebar_states_the_limits_from_settings(settings):
    settings.MIN_SECONDS_BETWEEN_MESSAGES = 45
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 12
    duo = K.Duo()
    text = H.doc(page(duo.ca, duo)).text()
    assert "45 seconds" in text
    assert re.search(r"\b12\b", text)
    assert "30 seconds" not in text


def test_the_sidebar_counts_the_messages_used(field_names):
    duo = K.Duo()
    for i, participant in enumerate([duo.pa, duo.pb, duo.pa]):
        duo.seed(participant, f"seed {i}", minutes_ago=100 - i)
    text = H.doc(page(duo.ca, duo)).text()
    assert re.search(r"\b3\b\D{0,20}\b30\b", text), "the sidebar shows how many of the 30 messages are used"


def test_messages_carry_you_and_the_other_participant_per_viewer_in_order():
    duo = K.Duo()
    duo.seed(duo.pa, "Token-alpha-one", 50)
    duo.seed(duo.pb, "Token-bravo-two", 40)
    duo.seed(duo.pa, "Token-charlie-three", 30)
    for client, expected in ((duo.ca, ["you", "other", "you"]), (duo.cb, ["other", "you", "other"])):
        response = page(client, duo)
        assert [speaker_in_block(response, t) for t in ("Token-alpha-one", "Token-bravo-two", "Token-charlie-three")] == expected
        body = response.content.decode()
        assert body.index("Token-alpha-one") < body.index("Token-bravo-two") < body.index("Token-charlie-three")


def test_a_message_is_escaped_not_run():
    duo = K.Duo()
    duo.seed(duo.pa, "<script>alert(1)</script> <b>bold</b> & more")
    for client in (duo.ca, duo.cb):
        body = page(client, duo).content.decode()
        assert "<script>alert(1)</script>" not in body and "<b>bold</b>" not in body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_the_end_control_is_a_post_button_on_an_active_page():
    duo = K.Duo()
    form = H.form_with_action(H.doc(page(duo.ca, duo)), duo.end_url)
    assert form is not None and form.get("method", "").lower() == "post"
    assert H.hidden_csrf(form) and H.has_button(form)


def test_the_page_shows_no_moderator_notice_when_moderation_is_fine():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "hello")
    K.set_last_run(duo.conv, trigger, "done")
    text = H.doc(page(duo.ca, duo)).text()
    assert "still posted" not in text


# --- a real conversation between two sessions -------------------------------------------------------------------------

def test_two_sessions_alternate_and_repeat_posts_with_no_turn_rule(field_names, clock):
    duo = K.Duo()
    assert say(duo.ca, duo, field_names, "Token-a1").status_code == 302
    assert say(duo.cb, duo, field_names, "Token-b1").status_code == 302  # B answers at once: their own gap is clear
    clock.advance(31)
    assert say(duo.ca, duo, field_names, "Token-a2").status_code == 302  # A again, B has not replied since
    clock.advance(31)
    assert say(duo.ca, duo, field_names, "Token-a3").status_code == 302  # and again: no "not your turn"
    assert say(duo.cb, duo, field_names, "Token-b2").status_code == 302
    clock.advance(31)
    assert say(duo.cb, duo, field_names, "Token-b3").status_code == 302
    contents = list(K.user_messages(duo.conv).values_list("content", flat=True))
    assert contents == ["Token-a1", "Token-b1", "Token-a2", "Token-a3", "Token-b2", "Token-b3"]
    seqs = list(K.user_messages(duo.conv).values_list("seq_no", flat=True))
    assert seqs == sorted(seqs) and len(set(seqs)) == 6
    for client, expected in ((duo.ca, "you"), (duo.cb, "other")):
        assert speaker_in_block(page(client, duo), "Token-a2") == expected


def test_a_successful_post_redirects_back_to_the_conversation_and_saves_one_pending_live_run(field_names):
    duo = K.Duo()
    response = say(duo.ca, duo, field_names, "First message from A.")
    assert response.status_code == 302 and response["Location"] == duo.url
    msg = K.user_messages(duo.conv).get()
    assert msg.participant == duo.pa and msg.content == "First message from A."
    run = K.runs(duo.conv).get()
    assert (run.trigger_message, run.kind, run.status, run.snapshot_seq) == (msg, "live", "pending", msg.seq_no)
    assert "First message from A." in page(duo.ca, duo).content.decode()


def test_the_other_persons_page_shows_the_new_message(field_names):
    duo = K.Duo()
    say(duo.ca, duo, field_names, "Visible-to-B-token")
    assert "Visible-to-B-token" in page(duo.cb, duo).content.decode()


def test_posting_twice_inside_the_gap_is_refused_with_the_seconds_left_and_the_text_kept(field_names, clock):
    duo = K.Duo()
    say(duo.ca, duo, field_names, "one")
    clock.advance(10)
    response = say(duo.ca, duo, field_names, "second, too soon")
    assert response.status_code == 200
    alert = H.alert_text(response)
    assert "Please wait 20 more seconds" in alert
    assert "30 seconds" in alert
    assert H.control_value(H.text_control(composer(response, duo))) == "second, too soon"
    assert K.user_messages(duo.conv).count() == 1 and K.runs(duo.conv).count() == 1


def test_the_wait_is_counted_from_your_own_previous_message_not_the_others(field_names, clock):
    duo = K.Duo()
    say(duo.ca, duo, field_names, "a1")
    clock.advance(5)
    say(duo.cb, duo, field_names, "b1")
    clock.advance(24)  # 29 s after A's own message, 24 s after B's
    refused = say(duo.ca, duo, field_names, "a2 too early")
    assert refused.status_code == 200 and "Please wait 1 more" in H.alert_text(refused)
    clock.advance(2)  # 31 s
    assert say(duo.ca, duo, field_names, "a2 on time").status_code == 302


def test_the_first_message_of_a_person_is_never_rate_limited_by_the_other(field_names, clock):
    duo = K.Duo()
    say(duo.ca, duo, field_names, "a1")
    assert say(duo.cb, duo, field_names, "b1 straight away").status_code == 302


def test_the_gap_in_the_alert_comes_from_settings(field_names, clock, settings):
    settings.MIN_SECONDS_BETWEEN_MESSAGES = 90
    duo = K.Duo()
    say(duo.ca, duo, field_names, "one")
    clock.advance(30)
    alert = H.alert_text(say(duo.ca, duo, field_names, "two"))
    assert "Please wait 60 more seconds" in alert and "90 seconds" in alert


def test_no_alert_ever_talks_about_turns(field_names, clock):
    duo = K.Duo()
    say(duo.ca, duo, field_names, "one")
    refused = say(duo.ca, duo, field_names, "two")
    assert not re.search(r"(?i)\byour turn\b|wait for the other|take turns", H.alert_text(refused))


def test_an_empty_message_is_refused_and_creates_nothing(field_names):
    duo = K.Duo()
    for text in ("", "   ", "\n \t\r\n"):
        response = say(duo.ca, duo, field_names, text)
        assert response.status_code == 200
        assert "Your message is empty." in H.alert_text(response)
    assert K.user_messages(duo.conv).count() == 0 and K.runs(duo.conv).count() == 0


def test_a_message_of_exactly_3000_characters_is_accepted_even_with_padding(field_names):
    duo = K.Duo()
    text = "  " + "m" * 3000 + " \r\n"
    assert say(duo.ca, duo, field_names, text).status_code == 302
    assert K.user_messages(duo.conv).get().content.strip() == "m" * 3000


def test_a_message_of_3001_characters_is_refused_with_the_count_limit_and_reason_and_the_text_kept(field_names):
    duo = K.Duo()
    text = "line one\r\n" + "n" * 2991 + " é"  # 10 + 2991 + 2 = 3003? computed below
    text = "n" * 2999 + "\r\n" + "z"  # 2999 + 1 (newline) + 1 = 3001 characters
    response = say(duo.ca, duo, field_names, text)
    assert response.status_code == 200
    alert = H.alert_text(response)
    assert "3,001" in alert and "3,000" in alert
    assert re.search(r"shorten it by 1 character", alert)
    assert "keep the AI moderator's running costs low" in alert
    assert norm(H.control_value(H.text_control(composer(response, duo)))) == norm(text)
    assert K.user_messages(duo.conv).count() == 0 and K.runs(duo.conv).count() == 0


def test_the_length_in_the_alert_counts_emoji_as_one_and_trims(field_names):
    duo = K.Duo()
    response = say(duo.ca, duo, field_names, "  " + "\U0001f600" * 3001 + "  ")
    alert = H.alert_text(response)
    assert "3,001" in alert and "shorten it by 1 character" in alert


def test_a_hand_made_post_without_the_field_is_answered_like_an_empty_message(field_names):
    duo = K.Duo()
    response = duo.ca.post(duo.post_url, {})
    assert response.status_code == 200
    assert "Your message is empty." in H.alert_text(response)


def test_the_message_limit_in_the_alert_comes_from_settings(field_names, settings):
    settings.MAX_MESSAGE_CHARS = 100
    duo = K.Duo()
    alert = H.alert_text(say(duo.ca, duo, field_names, "p" * 150))
    assert "150" in alert and "100" in alert and "3,000" not in alert and "shorten it by 50 characters" in alert


def test_the_thirtieth_message_is_accepted_and_closes_the_conversation(field_names, clock):
    duo = K.Duo()
    for i in range(29):
        duo.seed(duo.pa if i % 2 else duo.pb, f"seed {i}", minutes_ago=600 - i)
    clock.advance(60)
    response = say(duo.ca, duo, field_names, "The thirtieth.")
    assert response.status_code == 302
    assert K.user_messages(duo.conv).count() == 30
    assert K.fresh(duo.conv).status == "closed"
    closed_page = page(duo.ca, duo)
    assert not composer(closed_page, duo)
    assert "closed" in H.doc(closed_page).text().lower()


def test_a_full_conversation_refuses_the_next_post_and_saves_nothing(field_names, clock):
    duo = K.Duo()
    for i in range(30):
        duo.seed(duo.pa if i % 2 else duo.pb, f"seed {i}", minutes_ago=600 - i)
    clock.advance(60)
    response = say(duo.cb, duo, field_names, "The thirty-first.")
    assert response.status_code == 200
    alert = H.alert_text(response)
    assert "This conversation has reached its limit of 30 messages and is closed. You can start a new one." in alert
    assert K.user_messages(duo.conv).count() == 30 and K.runs(duo.conv).count() == 0


def test_the_full_limit_in_the_alert_comes_from_settings(field_names, clock, settings):
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 4
    duo = K.Duo()
    for i in range(4):
        duo.seed(duo.pa if i % 2 else duo.pb, f"seed {i}", minutes_ago=600 - i)
    clock.advance(60)
    assert "limit of 4 messages" in H.alert_text(say(duo.ca, duo, field_names, "fifth"))


def test_moderator_posts_do_not_count_toward_the_limit(field_names, clock):
    duo = K.Duo()
    duo.seed(duo.pa, "t", minutes_ago=100)
    for i in range(5):
        K.add_moderator_post(duo.conv, duo.seed(duo.pb, f"b{i}", minutes_ago=90 - i), f"Mod-{i}", [("all", "both", [])])
    clock.advance(60)
    assert say(duo.ca, duo, field_names, "still allowed").status_code == 302


# --- the ended and closed states --------------------------------------------------------------------------------------

def test_ending_closes_it_for_both_and_names_who_ended_it():
    duo = K.Duo()
    duo.seed(duo.pa, "Token-kept-for-the-record", 20)
    response = duo.ca.post(duo.end_url)
    assert response.status_code == 302 and response["Location"] == duo.url
    conv = K.fresh(duo.conv)
    assert conv.status == "closed" and conv.ended_by == duo.pa and conv.ended_at is not None
    a_page, b_page = page(duo.ca, duo), page(duo.cb, duo)
    a_text, b_text = H.unescape(a_page.content.decode()), H.unescape(b_page.content.decode())
    assert K.YOU_ENDED in a_text and K.OTHER_ENDED not in a_text
    assert K.OTHER_ENDED in b_text and K.YOU_ENDED not in b_text
    for closed_page in (a_page, b_page):
        assert composer(closed_page, duo) is None
        assert H.form_with_action(H.doc(closed_page), duo.end_url) is None
        assert "Token-kept-for-the-record" in closed_page.content.decode()
        assert reverse("forum:home") in H.links(H.doc(closed_page)), "the closed page says what to do next: a way back"


def test_ending_by_the_second_person_names_them_as_the_one_who_ended_it():
    duo = K.Duo()
    duo.cb.post(duo.end_url)
    assert K.OTHER_ENDED in H.unescape(page(duo.ca, duo).content.decode())
    assert K.YOU_ENDED in H.unescape(page(duo.cb, duo).content.decode())


def test_posting_to_a_closed_conversation_is_refused_with_who_ended_it(field_names):
    duo = K.Duo()
    duo.ca.post(duo.end_url)
    a = H.unescape(H.alert_text(say(duo.ca, duo, field_names, "after the end")))
    b = H.unescape(H.alert_text(say(duo.cb, duo, field_names, "after the end")))
    assert K.CLOSED_TEXT in a and K.YOU_ENDED in a
    assert K.CLOSED_TEXT in b and K.OTHER_ENDED in b
    assert K.user_messages(duo.conv).count() == 0 and K.runs(duo.conv).count() == 0


def test_ending_twice_keeps_the_first_ender_and_shows_a_reason(field_names):
    duo = K.Duo()
    duo.ca.post(duo.end_url)
    response = duo.cb.post(duo.end_url, follow=True)
    assert response.status_code == 200
    assert "closed" in H.doc(response).text().lower()
    assert K.fresh(duo.conv).ended_by == duo.pa


def test_the_creator_of_a_waiting_conversation_can_end_it(field_names):
    user = K.make_user(K.NAME_A, K.EMAIL_A)
    topic = K.make_topic("Waiting to be ended.", created_by=user)
    conv = K.enter(user, topic)
    client = K.client_for(user)
    response = client.post(reverse("forum:end", args=[conv.pk]))
    assert response.status_code == 302
    assert K.fresh(conv).status == "closed"
    landing = client.get(reverse("forum:conversation", args=[conv.pk]))
    assert K.YOU_ENDED in H.unescape(landing.content.decode())
    later = K.enter(K.make_user(), topic)
    assert later.pk != conv.pk and later.status == "open", "nobody can join an ended conversation"


def test_a_closed_conversation_stays_readable_after_its_proposition_is_hidden():
    duo = K.Duo()
    duo.seed(duo.pa, "Token-still-readable", 20)
    duo.topic.hidden = True
    duo.topic.save()
    assert "Token-still-readable" in page(duo.ca, duo).content.decode()
    assert "Token-still-readable" in page(duo.cb, duo).content.decode()
    assert duo.topic.proposition not in duo.ca.get("/").content.decode()


# --- the AI moderator: cards, headings, notices -----------------------------------------------------------------------

def scripted_conversation():
    """Six user messages and six moderator posts, one for each way a heading can be worked out."""
    duo = K.Duo()
    la, lb = duo.pa.label, duo.pb.label
    m1 = duo.seed(duo.pa, "User-one-by-A", 100)
    m2 = duo.seed(duo.pb, "User-two-by-B", 99)
    m3 = duo.seed(duo.pa, "User-three-by-A", 98)
    m4 = duo.seed(duo.pb, "User-four-by-B", 97)
    m5 = duo.seed(duo.pa, "User-five-by-A", 96)
    m6 = duo.seed(duo.pb, "User-six-by-B", 95)
    K.add_moderator_post(duo.conv, m1, "Mod-token-1", [(la, la, [m1])])
    K.add_moderator_post(duo.conv, m2, "Mod-token-2", [(lb, lb, [m2])])
    K.add_moderator_post(duo.conv, m3, "Mod-token-3", [("all", "both", [m3])])
    K.add_moderator_post(duo.conv, m4, "Mod-token-4", [(la, "none", [])])
    K.add_moderator_post(duo.conv, m5, "Mod-token-5", [(lb, "both", [m5])])
    K.add_moderator_post(duo.conv, m6, "Mod-token-6", [(la, lb, [m6])])
    return duo, (m1, m2, m3, m4, m5, m6)


HEADINGS = ["About your message", "About the other participant's message", "For both of you", "About the conversation"]


def heading_in_block(response, token):
    root = H.doc(response)
    block = K.find_block(root, token, HEADINGS)
    assert block is not None, f"no heading next to {token!r}"
    text = H.unescape(block.text())
    present = [h for h in HEADINGS if h in text]
    assert len(present) == 1, f"one heading per card, found {present} in {text!r}"
    return re.search(re.escape(present[0]) + r"(?: \d+)?", text).group(0)


def test_each_moderator_post_shows_the_heading_worked_out_for_the_viewer():
    duo, _ = scripted_conversation()
    a = page(duo.ca, duo)
    b = page(duo.cb, duo)
    expected_a = {1: "About your message 1", 2: "About the other participant's message 2", 3: "For both of you",
                  4: "About the conversation", 5: "For both of you", 6: "About the other participant's message 6"}
    expected_b = {1: "About the other participant's message 1", 2: "About your message 2", 3: "For both of you",
                  4: "About the conversation", 5: "For both of you", 6: "About your message 6"}
    for n in range(1, 7):
        assert heading_in_block(a, f"Mod-token-{n}") == expected_a[n], f"A, card {n}"
        assert heading_in_block(b, f"Mod-token-{n}") == expected_b[n], f"B, card {n}"


def test_moderator_posts_are_labelled_as_the_ai_moderator_and_distinct_from_participants():
    duo, _ = scripted_conversation()
    response = page(duo.ca, duo)
    root = H.doc(response)
    block = K.find_block(root, "Mod-token-1", ["moderator", "Moderator"])
    assert block is not None and "moderator" in block.text().lower()
    speaker = block.text()
    assert "The other participant" not in speaker.replace("About the other participant's", "")
    user_block = K.find_block(root, "User-one-by-A", ["You", "The other participant"])
    assert user_block is not block and "moderator" not in user_block.text().lower().replace("ai moderator", "")


def test_a_moderator_post_is_never_addressed_by_a_label_letter_or_username_in_the_page():
    duo, _ = scripted_conversation()
    for client, viewer, other in ((duo.ca, duo.ua, duo.ub), (duo.cb, duo.ub, duo.ua)):
        assert K.leaks(page(client, duo).content.decode(), viewer, other) == []


def test_moderation_paused_notices_are_plain_and_say_messages_are_still_posted(field_names):
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "hello", 30)
    cases = [
        ("skipped_disabled", "", "AI moderation is switched off right now; your messages are still posted."),
        ("failed", "internal_error_code_42", "The AI moderator ran into a problem on the last message; your messages are still posted."),
    ]
    for status, reason, sentence in cases:
        K.set_last_run(duo.conv, trigger, status, reason)
        response = page(duo.ca, duo)
        assert sentence in H.unescape(response.content.decode()), status
        assert "internal_error_code_42" not in response.content.decode()
        assert composer(response, duo) is not None, "moderation being paused never blocks posting"


def test_a_budget_pause_names_the_spending_limit_and_that_posting_still_works():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "hello", 30)
    K.set_last_run(duo.conv, trigger, "skipped_budget", "site_total_cap")
    body = H.unescape(page(duo.ca, duo).content.decode())
    assert re.search(r"spending limit", body) and "still posted" in body
    assert "site_total_cap" not in body


# --- the run mode: sync calls the pipeline after commit ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_sync_mode_runs_the_pipeline_after_commit_and_the_moderator_post_reaches_both_pollers(field_names, settings, monkeypatch):
    settings.MODERATION_RUN_MODE = "sync"
    duo = K.Duo()
    seen = {}

    def moderate(run):
        seen["in_atomic_block"] = connection.in_atomic_block
        seen["visible"] = type(run).objects.filter(pk=run.pk, status="pending").exists()
        trigger = run.trigger_message
        label = trigger.participant.label
        K.add_moderator_post(run.conversation, trigger, "Mod-from-pipeline", [(label, label, [trigger])])

    calls = K.install_fake_pipeline(monkeypatch, moderate)
    response = say(duo.ca, duo, field_names, "Please moderate this.")
    assert response.status_code == 302
    assert len(calls) == 1 and calls[0].trigger_message.content == "Please moderate this."
    assert seen == {"in_atomic_block": False, "visible": True}
    _, for_b = K.poll_json(duo.cb, duo.conv, after=0)
    kinds = [(m["kind"], m["text"]) for m in for_b["messages"]]
    assert kinds == [("other", "Please moderate this."), ("moderator", "Mod-from-pipeline")]
    assert [m["heading"] for m in for_b["messages"] if m["kind"] == "moderator"] == ["About the other participant's message 1"]
    _, for_a = K.poll_json(duo.ca, duo.conv, after=1)
    assert [(m["kind"], m["heading"]) for m in for_a["messages"]] == [("moderator", "About your message 1")]


def test_worker_mode_leaves_the_run_pending_and_calls_nothing(field_names, monkeypatch):
    calls = K.install_fake_pipeline(monkeypatch)
    duo = K.Duo()
    say(duo.ca, duo, field_names, "worker mode")
    assert calls == [] and K.runs(duo.conv).get().status == "pending"
