"""7b: nobody but the two participants can read a conversation, and no page names a person or shows an A/B label."""
import re

import pytest
from django.urls import reverse

import fviews_html as H
import fviews_kit as K


def html_of(response):
    return response.content.decode()


def assert_clean(response, viewer, other, where, own_name_in_header_ok=False, other_name_ok=True):
    """Revision 5: the other participant's username may be shown to a participant; emails, the viewer's own name on
    conversation pages, the word 'Participant' and label letters may not."""
    problems = K.leaks(html_of(response), viewer, other, own_name_in_header_ok, other_name_ok)
    assert problems == [], f"{where}: {problems}"


def busy_duo():
    """An active conversation with messages and moderator posts of every heading kind."""
    duo = K.Duo("Trains are better than planes.")
    la, lb = duo.pa.label, duo.pb.label
    m1 = duo.seed(duo.pa, "Hello from the first person.", 50)
    m2 = duo.seed(duo.pb, "Hello from the second person.", 49)
    K.add_moderator_post(duo.conv, m1, "Mod one", [(la, la, [m1])])
    K.add_moderator_post(duo.conv, m2, "Mod two", [("all", "both", [m2])])
    m3 = duo.seed(duo.pa, "Third.", 40)
    K.add_moderator_post(duo.conv, m3, "Mod three", [(lb, "none", [])])
    return duo


def viewers(duo):
    return ((duo.ca, duo.ua, duo.ub, "A"), (duo.cb, duo.ub, duo.ua, "B"))


def test_the_active_page_shows_no_email_no_own_name_and_no_label():
    duo = busy_duo()
    for client, viewer, other, who in viewers(duo):
        assert_clean(client.get(duo.url), viewer, other, f"active page for {who}")


def test_the_waiting_page_names_nobody():
    user = K.make_user(K.NAME_A, K.EMAIL_A)
    conv = K.enter(user, K.make_topic("Waiting proposition text.", created_by=user))
    response = K.client_for(user).get(reverse("forum:conversation", args=[conv.pk]))
    assert_clean(response, user, K.make_user(K.NAME_B, K.EMAIL_B), "waiting page")


def test_ended_and_closed_pages_name_nobody_and_say_the_other_participant():
    duo = busy_duo()
    duo.ca.post(duo.end_url)
    for client, viewer, other, who in viewers(duo):
        response = client.get(duo.url)
        assert_clean(response, viewer, other, f"closed page for {who}")
    assert "The other participant ended this conversation." in H.unescape(html_of(duo.cb.get(duo.url)))


def test_a_full_and_auto_closed_page_names_nobody(field_names, clock):
    duo = K.Duo()
    for i in range(29):
        duo.seed(duo.pa if i % 2 else duo.pb, f"seed {i}", 600 - i)
    clock.advance(60)
    duo.ca.post(duo.post_url, {field_names["message"]: "The thirtieth."})
    for client, viewer, other, who in viewers(duo):
        assert_clean(client.get(duo.url), viewer, other, f"full page for {who}")


def test_refusal_pages_name_nobody(field_names, clock):
    duo = busy_duo()
    for text in ("", "x" * 3001):
        assert_clean(duo.ca.post(duo.post_url, {field_names["message"]: text}), duo.ua, duo.ub, "refusal")
    duo.ca.post(duo.post_url, {field_names["message"]: "fine"})
    assert_clean(duo.ca.post(duo.post_url, {field_names["message"]: "too soon"}), duo.ua, duo.ub, "too soon")
    duo.ca.post(duo.end_url)
    assert_clean(duo.cb.post(duo.post_url, {field_names["message"]: "closed"}), duo.ub, duo.ua, "closed refusal")


def test_moderation_notice_pages_name_nobody():
    duo = busy_duo()
    trigger = duo.seed(duo.pa, "trigger", 5)
    for status, reason in (("failed", "boom"), ("skipped_disabled", ""), ("skipped_budget", "site_total")):
        K.set_last_run(duo.conv, trigger, status, reason)
        for client, viewer, other, who in viewers(duo):
            assert_clean(client.get(duo.url), viewer, other, f"{status} page for {who}")


def test_home_and_propose_and_how_it_works_name_nobody_else():
    duo = busy_duo()
    for client, viewer, other, who in viewers(duo):
        for name in ("forum:home", "forum:propose", "forum:how_it_works"):
            assert_clean(client.get(reverse(name)), viewer, other, f"{name} for {who}", own_name_in_header_ok=True, other_name_ok=False)


def test_the_page_html_holds_no_label_in_attributes_scripts_or_comments():
    duo = busy_duo()
    html = html_of(duo.ca.get(duo.url))
    assert "<!--" not in html or not re.search(r"<!--.*?(label|Participant|zelda|quincy).*?-->", html, re.S | re.I)
    for participant in (duo.pa, duo.pb):
        assert f'"{participant.label}"' not in html, "an internal label leaked into an attribute or script"
        assert f"'{participant.label}'" not in html


# --- strangers --------------------------------------------------------------------------------------------------------

def stranger_client():
    return K.client_for(K.make_user("stranger_sam", "stranger_sam@example.com"))


def norm_404(response):
    return H.normalise_page(response)


def test_a_stranger_and_a_missing_id_get_the_identical_404_page_with_the_contract_text():
    duo = busy_duo()
    stranger = stranger_client()
    real = stranger.get(duo.url)
    missing = stranger.get(reverse("forum:conversation", args=[987654]))
    assert real.status_code == missing.status_code == 404
    assert K.NOT_FOUND_TEXT in H.unescape(html_of(real))
    assert norm_404(real) == norm_404(missing)
    for secret in ("Trains are better than planes.", "Hello from the first person.", "Mod one", K.NAME_A, K.NAME_B):
        assert secret not in html_of(real)


def test_a_stranger_cannot_post_end_or_poll_and_learns_nothing(field_names):
    duo = busy_duo()
    stranger = stranger_client()
    before_msgs, before_runs = duo.conv.messages.count(), K.runs(duo.conv).count()
    for method, name in (("post", "forum:post"), ("post", "forum:end"), ("get", "forum:messages")):
        real = getattr(stranger, method)(reverse(name, args=[duo.conv.pk]), {field_names["message"]: "let me in"} if method == "post" else {})
        missing = getattr(stranger, method)(reverse(name, args=[987654]), {field_names["message"]: "let me in"} if method == "post" else {})
        assert real.status_code == missing.status_code == 404, name
        assert H.normalise_page(real) == H.normalise_page(missing), f"{name}: a stranger must not be able to tell the two apart"
    assert duo.conv.messages.count() == before_msgs and K.runs(duo.conv).count() == before_runs
    assert K.fresh(duo.conv).status == "active"


def test_a_participant_of_another_conversation_is_a_stranger_here():
    duo = busy_duo()
    other = K.Duo("A different proposition entirely.", names=("other_olga", "other_omar"))
    response = other.ca.get(duo.url)
    assert response.status_code == 404 and K.NOT_FOUND_TEXT in H.unescape(html_of(response))


def test_a_third_person_entering_the_same_proposition_cannot_read_the_conversation():
    duo = busy_duo()
    third = K.client_for(K.make_user("third_tim", "third_tim@example.com"))
    conv_id = int(re.search(r"/c/(\d+)/", third.post(reverse("forum:enter", args=[duo.topic.pk]), {"side": "pro"})["Location"]).group(1))
    assert conv_id != duo.conv.pk
    assert third.get(duo.url).status_code == 404
    assert "Hello from the first person." not in html_of(third.get(reverse("forum:conversation", args=[conv_id])))


def test_the_404_page_leaks_no_internals():
    body = html_of(stranger_client().get(reverse("forum:conversation", args=[987654])))
    for bad in ("Traceback", "Exception", "django", "/workspace", "conversation_id", "DoesNotExist", "Http404"):
        assert bad not in body, bad
