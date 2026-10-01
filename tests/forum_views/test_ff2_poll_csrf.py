"""Front-end fixes 2, fix A (docs/frontend_fixes_brief2.md): the poll endpoint renders each message with the request, so the
research form of a poll-appended moderator message carries a real CSRF token for the viewer. Django masks the token per render,
so the tests do not compare strings: they POST the research request with the token found in the poll html, through a client
that enforces CSRF checks."""
import re

import pytest
from django.urls import reverse

import ff_kit as F
import fviews_html as H
import fviews_kit as K

TOKEN_RE = re.compile(r'name="csrfmiddlewaretoken"\s+value="([^"]*)"')


def tokens(html):
    return TOKEN_RE.findall(html)


def world(state="none", act_type="offer_research", csrf=True):
    duo = K.Duo(csrf=csrf)
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    mod, act = F.post_with_act(duo.conv, trigger, act_type=act_type, marker="ff2-csrf")
    F.make_state(act, state, requested_by=duo.pa)
    return duo, trigger, mod, act


def moderator_item(data):
    found = [m for m in data["messages"] if m["kind"] == "moderator"]
    assert found
    return found[0]  # the offer message (a "done" run adds a second, note, message after it)


@pytest.mark.parametrize("act_type", ["offer_research", "correct_factual_error", "provide_information",
                                                    "request_information"])
def test_poll_appended_moderator_message_carries_a_nonempty_token(act_type):
    duo, trigger, mod, act = world(act_type=act_type)
    _, data = K.poll_json(duo.ca, duo.conv, after=trigger.seq_no)
    html = moderator_item(data)["html"]
    found = tokens(html)
    assert len(found) == 1 and found[0], html
    assert len(found[0]) == 64  # a masked token


def test_the_token_validates_when_the_research_request_is_posted_with_it():
    duo, trigger, mod, act = world()
    _, data = K.poll_json(duo.ca, duo.conv, after=trigger.seq_no)
    token = tokens(moderator_item(data)["html"])[0]
    response = duo.ca.post(F.url_for(duo.conv, act), {"csrfmiddlewaretoken": token})
    assert response.status_code != 403
    assert len(F.research_runs(act)) == 1


def test_a_client_whose_first_request_is_the_poll_gets_a_cookie_and_a_matching_token():
    """No page was loaded before: the poll alone must start the CSRF cookie that goes with the token."""
    duo, trigger, mod, act = world()
    assert "csrftoken" not in duo.ca.cookies
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    assert "csrftoken" in duo.ca.cookies
    token = tokens(moderator_item(data)["html"])[0]
    assert duo.ca.post(F.url_for(duo.conv, act), {"csrfmiddlewaretoken": token}).status_code != 403
    assert len(F.research_runs(act)) == 1


def test_the_token_also_works_after_the_page_was_loaded_first_and_matches_the_pages_forms():
    duo, trigger, mod, act = world()
    page_html = duo.ca.get(duo.url).content.decode()
    page_token = tokens(page_html)
    assert page_token and all(page_token)
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    poll_token = tokens(moderator_item(data)["html"])[0]
    # both validate against the same cookie, whatever the masking
    for token in (page_token[0], poll_token):
        r = duo.ca.post(F.url_for(duo.conv, act), {"csrfmiddlewaretoken": token})
        assert r.status_code != 403


def test_every_poll_render_is_a_valid_token_even_though_they_differ_in_masking():
    duo, trigger, mod, act = world()
    seen = set()
    for _ in range(3):
        _, data = K.poll_json(duo.ca, duo.conv, after=0)
        seen.add(tokens(moderator_item(data)["html"])[0])
    for token in seen:
        # a validating (non-403) answer; the first POST creates the run, later ones are answered too (never 403)
        assert duo.ca.post(F.url_for(duo.conv, act), {"csrfmiddlewaretoken": token}).status_code != 403


def test_the_token_is_the_viewers_own_not_another_sessions():
    duo, trigger, mod, act = world()
    K.csrf_token(duo.ca)
    _, data = K.poll_json(duo.cb, duo.conv, after=0)  # B's cookie
    b_token = tokens(moderator_item(data)["html"])[0]
    before = len(F.research_runs(act))
    assert duo.ca.post(F.url_for(duo.conv, act), {"csrfmiddlewaretoken": b_token}).status_code == 403
    assert len(F.research_runs(act)) == before


def test_without_the_token_the_same_post_is_refused():
    duo, trigger, mod, act = world()
    K.poll_json(duo.ca, duo.conv, after=0)
    assert duo.ca.post(F.url_for(duo.conv, act), {}).status_code == 403
    assert F.research_runs(act) == []


def test_the_failed_state_message_carries_a_valid_token_for_the_try_again_form():
    duo, trigger, mod, act = world("failed")
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    html = moderator_item(data)["html"]
    found = tokens(html)
    assert len(found) == 1 and found[0]
    assert F.RETRY_BUTTON in html
    assert duo.ca.post(F.url_for(duo.conv, act), {"csrfmiddlewaretoken": found[0]}).status_code != 403


@pytest.mark.parametrize("state", ["pending", "done"])
def test_states_without_a_form_have_no_token_and_no_form(state):
    duo, trigger, mod, act = world(state)
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    html = moderator_item(data)["html"]
    assert "<form" not in html and tokens(html) == []


def test_two_research_eligible_paragraphs_each_get_a_token():
    duo = K.Duo(csrf=True)
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    mod, acts = F.post_with_acts(duo.conv, trigger, ["offer_research", "request_information"])
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    found = tokens(moderator_item(data)["html"])
    assert len(found) == 2 and all(found)
    for act, token in zip(acts, found):
        assert duo.ca.post(F.url_for(duo.conv, act), {"csrfmiddlewaretoken": token}).status_code != 403


def test_the_other_participant_also_gets_a_working_token():
    duo, trigger, mod, act = world()
    _, data = K.poll_json(duo.cb, duo.conv, after=0)
    token = tokens(moderator_item(data)["html"])[0]
    assert token
    assert duo.cb.post(F.url_for(duo.conv, act), {"csrfmiddlewaretoken": token}).status_code != 403


# --- nothing else changes ------------------------------------------------------------------------------------------------

def test_user_messages_have_no_form_and_no_token():
    duo, trigger, mod, act = world()
    _, data = K.poll_json(duo.cb, duo.conv, after=0)
    users = [m for m in data["messages"] if m["kind"] != "moderator"]
    assert users
    for m in users:
        assert "<form" not in m["html"] and tokens(m["html"]) == [] and "csrf" not in m["html"].lower()
        assert "A claim worth checking." in m["html"]


def test_user_message_html_is_escaped_as_before():
    duo = K.Duo(csrf=True)
    duo.seed(duo.pa, "<b>bold</b> & more")
    _, data = K.poll_json(duo.cb, duo.conv, after=0)
    html = data["messages"][0]["html"]
    assert "&lt;b&gt;bold&lt;/b&gt; &amp; more" in html and "<b>bold" not in html


def test_payload_keys_are_unchanged():
    from forum import viewmodels

    duo, trigger, mod, act = world()
    view = viewmodels.conversation_view(duo.ua, duo.conv, after_seq=0)
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    assert set(data) == set(view) | {"research"}
    assert len(data["messages"]) == len(view["messages"])
    for got, want in zip(data["messages"], view["messages"]):
        assert set(got) == set(want) | {"html"}
        assert got["seq_no"] == want["seq_no"] and got["kind"] == want["kind"]
    assert set(data["research"][0]) == {"act_id", "state", "html"} if data["research"] else True


def test_moderator_html_shape_is_unchanged_apart_from_the_token():
    duo, trigger, mod, act = world()
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    html = moderator_item(data)["html"]
    root = H.parse(f"<html><body>{html}</body></html>")
    arts = [n for n in root.walk() if n.tag == "article"]
    assert len(arts) == 1 and F.has_class(arts[0], "msg-moderator")
    assert arts[0].get("data-seq") == str(mod.seq_no)
    slot = F.slot_for(root, act)
    assert slot is not None and slot.get("data-state") == "none"
    assert [b.text() for b in F.descendants(slot, "button")] == [F.OFFER_BUTTON]
    forms = F.descendants(slot, "form", "research-form")
    assert forms[0].get("action") == F.url_for(duo.conv, act)


def test_the_research_list_html_is_not_affected():
    duo, trigger, mod, act = world("failed")
    items = F.research_payload(duo.ca, duo.conv)
    assert len(items) == 1 and tokens(items[0]["html"])
