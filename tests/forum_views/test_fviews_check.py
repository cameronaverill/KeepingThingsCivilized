"""Step 19 composer, page half: the two endpoints (c/<id>/check/ and c/<id>/check/<check_id>/edit/), the data-preview attribute
and the post with check_id. Model answers are scripted through the real moderation.preview.check_draft (fake LLM only)."""
import json
import re

import pytest
from django.test import Client
from django.urls import reverse

import fviews_html as H
import fviews_kit as K
import fviews_llm as L

ALLOWED_KEYS = {"status", "check_id", "notes", "code", "message", "retry_after"}
OUTCOMES = {"no_concern", "concern", "unavailable"}


def check_url(conv):
    return f"/c/{conv.pk}/check/"


def edit_url(conv, check_id):
    return f"/c/{conv.pk}/check/{check_id}/edit/"


def check(client, conv, text=L.DRAFT, **extra):
    return client.post(check_url(conv), {"text": text}, **extra)


def body(response):
    assert response["Content-Type"].startswith("application/json"), response["Content-Type"]
    return json.loads(response.content)


def rows(conv=None):
    from moderation.models import PreviewCheck

    qs = PreviewCheck.objects.all()
    return list(qs.filter(conversation=conv.pk) if conv is not None else qs)


def duo_with_history():
    duo = K.Duo("Cities should cap how much landlords can raise rents each year.")
    duo.seed(duo.pa, "I think rent control reduces the supply of housing, which is a well known fact.", 50)
    duo.seed(duo.pb, "The supply of homes barely changes when rents are capped, and tenants gain a lot from that.", 40)
    return duo


def table_counts():
    from forum.models import Message
    from moderation.models import InterventionAct, Issue, ModerationRun

    return (Message.objects.count(), ModerationRun.objects.count(), Issue.objects.count(), InterventionAct.objects.count())


# --- URL rules ------------------------------------------------------------------------------------------------------------

def test_the_paths():
    duo = K.Duo()
    assert check_url(duo.conv) == f"/c/{duo.conv.pk}/check/"
    from django.urls import resolve

    assert resolve(check_url(duo.conv)).func is not None
    assert resolve(edit_url(duo.conv, 5)).func is not None


def test_only_post_is_allowed_on_both_urls(fake):
    duo = K.Duo()
    fake()
    for url in (check_url(duo.conv), edit_url(duo.conv, 1)):
        for method in ("get", "put", "patch", "delete"):
            assert getattr(duo.ca, method)(url).status_code == 405, (method, url)
    assert rows() == []


def test_login_is_required_and_nothing_happens(fake):
    duo = K.Duo()
    client = fake()
    for url in (check_url(duo.conv), edit_url(duo.conv, 1)):
        response = Client().post(url, {"text": L.DRAFT})
        assert response.status_code == 302 and reverse("accounts:login") in response["Location"]
    assert rows() == [] and client.calls == []


def test_csrf_is_enforced_on_both_urls(fake):
    duo = K.Duo(csrf=True)
    client = fake(*L.concern())
    assert duo.ca.post(check_url(duo.conv), {"text": L.DRAFT}).status_code == 403
    assert duo.ca.post(check_url(duo.conv), {"text": L.DRAFT, "csrfmiddlewaretoken": "x" * 64}).status_code == 403
    assert duo.ca.post(edit_url(duo.conv, 1)).status_code == 403
    assert rows() == [] and client.calls == []
    ok = K.csrf_post(duo.ca, check_url(duo.conv), {"text": L.DRAFT})
    assert ok.status_code == 200 and body(ok)["status"] == "concern"


def test_the_csrf_token_may_come_in_the_header_like_a_script_sends_it(fake):
    duo = K.Duo(csrf=True)
    fake(*L.concern())
    token = K.csrf_token(duo.ca)
    response = duo.ca.post(check_url(duo.conv), {"text": L.DRAFT}, HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200 and body(response)["status"] == "concern"


# --- who may check ----------------------------------------------------------------------------------------------------------

def test_a_non_participant_and_a_missing_conversation_get_the_same_404_and_nothing_is_created(fake):
    duo = duo_with_history()
    client = fake(*L.concern())
    stranger = K.client_for(K.make_user("stranger_sam"))
    real, missing = check(stranger, duo.conv), stranger.post("/c/987654/check/", {"text": L.DRAFT})
    assert real.status_code == missing.status_code == 404
    assert K.NOT_FOUND_TEXT in H.norm(real.content.decode())
    assert H.normalise_page(real) == H.normalise_page(missing)
    assert rows() == [] and client.calls == []


def test_a_participant_of_another_conversation_is_a_stranger_here(fake):
    duo, other = duo_with_history(), K.Duo("Elsewhere.", names=("else_one", "else_two"))
    client = fake(*L.concern())
    assert check(other.ca, duo.conv).status_code == 404
    assert client.calls == [] and rows() == []


def test_a_waiting_conversation_can_be_checked_by_its_only_participant(fake):
    user = K.make_user()
    conv = K.enter(user, K.make_topic("Waiting claim.", created_by=user), "pro")
    fake(*L.no_concern())
    response = check(K.client_for(user), conv)
    assert response.status_code == 200 and body(response)["status"] == "no_concern"


# --- the refusal JSON: the same rules and words as a post -------------------------------------------------------------------

def post_refusal_alert(client, duo, field_names, text):
    response = client.post(duo.post_url, {field_names["message"]: text})
    assert response.status_code == 200
    return H.norm(H.alert_text(response))


def assert_refused(response, code, post_alert):
    data = body(response)
    assert response.status_code == 200
    assert set(data) == {"status", "code", "message", "retry_after"}
    assert data["status"] == "refused" and data["code"] == code
    assert H.norm(data["message"]) in post_alert, "the check says exactly what the post would say"
    return data


def test_empty_is_refused_with_the_post_wording(fake, field_names):
    duo = duo_with_history()
    client = fake()
    for text in ("", "   ", "\n\t\r\n"):
        data = assert_refused(check(duo.ca, duo.conv, text), "empty", post_refusal_alert(duo.ca, duo, field_names, text))
        assert data["message"] == "Your message is empty." and data["retry_after"] is None
    assert client.calls == [] and rows() == []


def test_too_long_is_refused_with_the_count_the_limit_and_the_reason(fake, field_names):
    duo = duo_with_history()
    client = fake()
    text = "n" * 3001
    data = assert_refused(check(duo.ca, duo.conv, text), "too_long", post_refusal_alert(duo.ca, duo, field_names, text))
    assert "3,001" in data["message"] and "3,000" in data["message"] and "shorten it by 1 character" in data["message"]
    assert body(check(duo.ca, duo.conv, "m" * 3001))["status"] == "refused"
    assert client.calls == [] and rows() == []
    assert body(check(duo.ca, duo.conv, "m" * 3000))["status"] != "refused", "exactly at the limit is not refused"


def test_too_fast_is_refused_with_the_seconds_left(fake, field_names, clock):
    duo = duo_with_history()
    fake()
    duo.ca.post(duo.post_url, {field_names["message"]: "my first message"})
    clock.advance(10)
    data = assert_refused(check(duo.ca, duo.conv, "another"), "too_fast", post_refusal_alert(duo.ca, duo, field_names, "another"))
    assert data["retry_after"] == 20 and "Please wait 20 more seconds" in data["message"]
    clock.advance(21)
    fake(*L.no_concern())
    assert body(check(duo.ca, duo.conv, "another"))["status"] == "no_concern"


def test_a_closed_conversation_is_refused_and_says_who_ended_it(fake, field_names):
    duo = duo_with_history()
    duo.ca.post(duo.end_url)
    client = fake()
    a = assert_refused(check(duo.ca, duo.conv), "closed", post_refusal_alert(duo.ca, duo, field_names, L.DRAFT))
    b = assert_refused(check(duo.cb, duo.conv), "closed", post_refusal_alert(duo.cb, duo, field_names, L.DRAFT))
    assert K.CLOSED_TEXT in a["message"] and K.YOU_ENDED in a["message"] and K.OTHER_ENDED in b["message"]
    assert client.calls == [] and rows() == []


def test_a_full_conversation_is_refused(fake, field_names, clock):
    duo = K.Duo()
    for i in range(30):
        duo.seed(duo.pa if i % 2 else duo.pb, f"seed {i}", 600 - i)
    clock.advance(60)
    fake()
    data = assert_refused(check(duo.ca, duo.conv), "conversation_full", post_refusal_alert(duo.ca, duo, field_names, L.DRAFT))
    assert "limit of 30 messages" in data["message"]


def test_the_refusal_order_is_the_posting_order_closed_before_empty(fake):
    duo = duo_with_history()
    duo.ca.post(duo.end_url)
    fake()
    assert body(check(duo.cb, duo.conv, ""))["code"] == "closed"


def test_a_missing_text_field_is_answered_like_an_empty_message(fake):
    duo = duo_with_history()
    fake()
    response = duo.ca.post(check_url(duo.conv), {})
    assert response.status_code == 200 and body(response)["code"] == "empty"


def test_the_refusal_numbers_come_from_settings(fake, settings, clock):
    duo = duo_with_history()
    settings.MAX_MESSAGE_CHARS = 50
    fake()
    data = body(check(duo.ca, duo.conv, "x" * 80))
    assert "80" in data["message"] and "50" in data["message"] and "3,000" not in data["message"]


# --- the three outcomes through the real check_draft ------------------------------------------------------------------------

def test_a_concern_returns_the_note_and_a_check_id_and_calls_the_model_twice(fake):
    duo = duo_with_history()
    client = fake(*L.concern())
    response = check(duo.ca, duo.conv)
    data = body(response)
    assert set(data) == {"status", "check_id", "notes"}
    assert data["status"] == "concern" and data["notes"] == [L.NOTE] and isinstance(data["check_id"], int)
    assert len(client.calls) == 2
    [row] = rows(duo.conv)
    participant_id = getattr(row, "participant_id", row.participant)  # a plain id or a foreign key, whichever the model uses
    assert (row.pk, participant_id, row.outcome, row.action) == (data["check_id"], duo.pa.pk, "concern", "")
    assert row.draft_text == L.DRAFT


def test_several_notes_come_back_in_order(fake):
    duo = duo_with_history()
    fake(*L.concern(texts=(L.NOTE, L.NOTE_2)))
    assert body(check(duo.ca, duo.conv))["notes"] == [L.NOTE, L.NOTE_2]


def test_no_concern_returns_a_check_id_and_no_notes(fake):
    duo = duo_with_history()
    client = fake(*L.no_concern())
    data = body(check(duo.ca, duo.conv))
    assert data["status"] == "no_concern" and isinstance(data["check_id"], int) and data.get("notes", []) == []
    assert set(data) <= ALLOWED_KEYS and len(client.calls) == 1


def test_a_declined_issue_is_no_concern(fake):
    duo = duo_with_history()
    fake(*L.declined())
    data = body(check(duo.ca, duo.conv))
    assert data["status"] == "no_concern" and data.get("notes", []) == []


def test_unavailable_when_the_kill_switch_is_off_never_an_error(fake, settings):
    duo = duo_with_history()
    client = fake()
    settings.LLM_ENABLED = False
    response = check(duo.ca, duo.conv)
    data = body(response)
    assert response.status_code == 200 and data["status"] == "unavailable" and data.get("notes", []) == []
    assert data.get("check_id") is None or isinstance(data["check_id"], int)
    assert client.calls == []


def test_unavailable_when_the_provider_fails(fake):
    from moderation.fake_llm import FakeProviderError

    duo = duo_with_history()
    fake(FakeProviderError(500, "api_error", "upstream exploded"), FakeProviderError(500, "api_error", "upstream exploded"))
    response = check(duo.ca, duo.conv)
    data = body(response)
    assert response.status_code == 200 and data["status"] == "unavailable" and "upstream exploded" not in json.dumps(data)


def test_unavailable_when_the_model_call_raises_something_unexpected(fake):
    duo = duo_with_history()

    def boom(kwargs):
        raise RuntimeError("secret-internal-xyz")

    fake(boom, boom, boom)
    response = check(duo.ca, duo.conv)
    assert response.status_code == 200 and body(response)["status"] == "unavailable"
    assert "secret-internal-xyz" not in response.content.decode() and "Traceback" not in response.content.decode()


def test_unavailable_when_the_preview_is_switched_off_for_the_conversation_and_no_model_call(fake, tune):
    tune(PREVIEW_SHARE=0.0)
    duo = duo_with_history()
    client = fake()
    data = body(check(duo.ca, duo.conv))
    assert data["status"] == "unavailable" and client.calls == []


def test_unavailable_when_the_per_minute_cap_is_reached(fake, tune):
    tune(PREVIEW_MAX_CHECKS_PER_MINUTE=2)
    duo = duo_with_history()
    client = fake(*L.no_concern(), *L.no_concern(), *L.no_concern())
    statuses = [body(check(duo.ca, duo.conv))["status"] for _ in range(3)]
    assert statuses == ["no_concern", "no_concern", "unavailable"]
    assert len(client.calls) == 2


def test_a_failure_inside_check_draft_itself_is_unavailable(fake, monkeypatch):
    duo = duo_with_history()
    fake(*L.concern())

    def broken(*args, **kwargs):
        raise RuntimeError("secret-internal-xyz")

    monkeypatch.setattr("moderation.preview.check_draft", broken)
    monkeypatch.setattr("forum.views.check_draft", broken, raising=False)
    monkeypatch.setattr("forum.services.check_draft", broken, raising=False)
    response = check(duo.ca, duo.conv)
    assert response.status_code == 200 and body(response)["status"] == "unavailable"
    assert "secret-internal-xyz" not in response.content.decode()


# --- what comes back: keys, privacy, headers --------------------------------------------------------------------------------

@pytest.mark.parametrize("script, status", [(L.concern(), "concern"), (L.no_concern(), "no_concern")])
def test_only_the_allowed_keys_and_nothing_about_the_check_internals(fake, script, status):
    duo = duo_with_history()
    fake(*script)
    response = check(duo.ca, duo.conv)
    data = body(response)
    assert data["status"] == status and set(data) <= ALLOWED_KEYS
    text = response.content.decode()
    for leak in ("mode", "reason", "master", "intervenor", "output", "draft", "sha", "llm_call", "snapshot", "participant",
                 "label", "username", "email", L.MARKER, duo.ua.username, duo.ub.username):
        assert leak not in text, f"{leak!r} in the check reply"
    assert not re.search(r"rate_limited|llm_disabled|budget|breaker|structural|api_error", text)


def test_every_reply_is_uncached_json(fake, field_names):
    duo = duo_with_history()
    fake(*L.concern(), *L.no_concern())
    for response in (check(duo.ca, duo.conv), check(duo.ca, duo.conv, ""), check(duo.ca, duo.conv, "x" * 4000)):
        assert "no-store" in response["Cache-Control"] and response["Content-Type"].startswith("application/json")
    cid = body(check(duo.ca, duo.conv))["check_id"]
    edit = duo.ca.post(edit_url(duo.conv, cid))
    assert "no-store" in edit["Cache-Control"] and edit["Content-Type"].startswith("application/json")


def test_notes_come_back_as_plain_text_in_json_never_html(fake):
    note = "Could <b>you</b> cite it? <img src=x onerror=alert(1)> & more"
    duo = duo_with_history()
    fake(*L.concern(texts=(note,)))
    response = check(duo.ca, duo.conv)
    assert response["Content-Type"].startswith("application/json")
    assert body(response)["notes"] == [note], "the JSON carries the note text as it is; the page inserts it as text"


def test_a_check_changes_no_message_run_issue_or_act_and_shows_nothing_to_the_other_person(fake):
    duo = duo_with_history()
    fake(*L.concern())
    before = table_counts()
    check(duo.ca, duo.conv)
    assert table_counts() == before
    _, poll = K.poll_json(duo.cb, duo.conv)
    page = duo.cb.get(duo.url).content.decode() + duo.ca.get(duo.url).content.decode() + json.dumps(poll)
    for secret in (L.MARKER, L.NOTE, "draft"):
        assert secret not in page, secret
    assert set(poll) and not [k for k in poll if re.search(r"(?i)check|preview|draft", k)]


# --- the edit endpoint ------------------------------------------------------------------------------------------------------

def make_check(fake, duo, script=None, who=None):
    fake(*(script or L.concern()))
    return body(check(who or duo.ca, duo.conv))["check_id"]


def test_the_owner_can_record_an_edit_and_it_is_idempotent(fake):
    duo = duo_with_history()
    cid = make_check(fake, duo)
    response = duo.ca.post(edit_url(duo.conv, cid))
    assert response.status_code == 200 and body(response) == {"ok": True}
    [row] = rows(duo.conv)
    assert row.action == "edited" and row.resolved_at is not None
    assert duo.ca.post(edit_url(duo.conv, cid)).status_code == 200


def test_another_persons_check_and_a_missing_check_are_404_and_untouched(fake):
    duo = duo_with_history()
    cid = make_check(fake, duo)
    stranger = K.client_for(K.make_user("stranger_sam"))
    for client in (duo.cb, stranger):
        assert client.post(edit_url(duo.conv, cid)).status_code == 404
    assert duo.ca.post(edit_url(duo.conv, 987654)).status_code == 404
    assert duo.ca.post(f"/c/987654/check/{cid}/edit/").status_code == 404
    assert rows(duo.conv)[0].action == ""


def test_a_check_from_another_conversation_is_404_here(fake):
    duo, other = duo_with_history(), K.Duo("Elsewhere.", names=("else_one", "else_two"))
    fake(*L.concern())
    cid = body(check(other.ca, other.conv))["check_id"]
    assert duo.ca.post(edit_url(duo.conv, cid)).status_code == 404
    assert rows(other.conv)[0].action == ""


def test_the_edit_of_a_check_already_posted_as_written_is_never_a_server_error(fake, field_names):
    duo = duo_with_history()
    cid = make_check(fake, duo)
    duo.ca.post(duo.post_url, {field_names["message"]: L.DRAFT, "check_id": cid})
    response = duo.ca.post(edit_url(duo.conv, cid))
    assert response.status_code < 500
    assert rows(duo.conv)[0].action == "posted_as_written"


# --- the post with check_id -------------------------------------------------------------------------------------------------

def post_with(client, duo, field_names, text, check_id):
    return client.post(duo.post_url, {field_names["message"]: text, "check_id": check_id})


def test_the_composer_form_has_no_check_id_until_the_script_adds_one(field_names):
    duo = duo_with_history()
    root = H.doc(duo.ca.get(duo.url))
    assert not [n for n in root.walk() if n.get("name") == "check_id"]


def test_posting_the_checked_text_records_posted_as_written_with_the_message_id(fake, field_names):
    duo = duo_with_history()
    cid = make_check(fake, duo)
    response = post_with(duo.ca, duo, field_names, L.DRAFT, cid)
    assert response.status_code == 302
    message = K.user_messages(duo.conv).filter(participant=duo.pa).order_by("-seq_no").first()
    assert message.content == L.DRAFT
    [row] = rows(duo.conv)
    assert (row.action, row.resulting_message_id) == ("posted_as_written", message.pk) and row.resolved_at is not None


def test_the_match_uses_the_normalised_text(fake, field_names):
    duo = duo_with_history()
    cid = make_check(fake, duo)
    text = "\n  " + L.DRAFT.replace(" ", " ") + "  \r\n"
    assert post_with(duo.ca, duo, field_names, text.replace("\n", "\r\n"), cid).status_code == 302
    assert rows(duo.conv)[0].action == "posted_as_written"


def test_a_different_text_is_posted_and_the_check_is_left_alone(fake, field_names):
    duo = duo_with_history()
    cid = make_check(fake, duo)
    assert post_with(duo.ca, duo, field_names, L.DRAFT + " and one more thing", cid).status_code == 302
    assert K.user_messages(duo.conv).filter(participant=duo.pa).count() == 2  # the seeded one and this post
    assert rows(duo.conv)[0].action == "" and rows(duo.conv)[0].resulting_message_id is None


def test_someone_elses_check_is_ignored_silently(fake, field_names):
    duo = duo_with_history()
    cid = make_check(fake, duo)  # A's check
    response = post_with(duo.cb, duo, field_names, L.DRAFT, cid)  # B posts the same text with A's check id
    assert response.status_code == 302
    assert rows(duo.conv)[0].action == ""
    assert K.user_messages(duo.conv).filter(participant=duo.pb).count() == 2, "the post itself went through"


def test_a_check_of_another_conversation_is_ignored(fake, field_names):
    duo, other = duo_with_history(), K.Duo("Elsewhere.", names=("else_one", "else_two"))
    fake(*L.concern())
    cid = body(check(other.ca, other.conv))["check_id"]
    # the same person (A of the other conversation) posts in the other conversation's twin: use one user in both
    both = K.make_user("both_bob")
    conv2 = K.enter(both, K.make_topic("Twin claim.", created_by=both), "pro")
    client = K.client_for(both)
    response = client.post(reverse("forum:post", args=[conv2.pk]), {field_names["message"]: L.DRAFT, "check_id": cid})
    assert response.status_code == 302 and rows(other.conv)[0].action == ""


@pytest.mark.parametrize("bad", ["abc", "-1", "0", "", "9" * 40, "1;2", "1.5", " 1", "<script>", "1\x00"])
def test_a_garbage_check_id_never_changes_the_post_outcome(fake, field_names, bad):
    duo = duo_with_history()
    fake()
    response = post_with(duo.ca, duo, field_names, "A perfectly good message.", bad)
    assert response.status_code == 302
    assert K.user_messages(duo.conv).filter(participant=duo.pa).count() == 2  # the seeded one and this post


def test_a_check_that_was_edited_stays_edited_and_the_post_still_succeeds(fake, field_names):
    duo = duo_with_history()
    cid = make_check(fake, duo)
    duo.ca.post(edit_url(duo.conv, cid))
    response = post_with(duo.ca, duo, field_names, L.DRAFT, cid)
    assert response.status_code == 302 and K.user_messages(duo.conv).filter(participant=duo.pa).count() == 2
    assert rows(duo.conv)[0].action == "edited"


def test_a_refused_post_resolves_nothing_and_keeps_the_text(fake, field_names, clock):
    duo = duo_with_history()
    duo.ca.post(duo.post_url, {field_names["message"]: "first"})
    clock.advance(31)
    cid = make_check(fake, duo)
    clock.advance(-25)  # back inside the gap: the post is refused
    response = post_with(duo.ca, duo, field_names, L.DRAFT, cid)
    assert response.status_code == 200 and "Please wait" in H.alert_text(response)
    assert H.control_value(H.text_control(H.form_with_action(H.doc(response), duo.post_url))) == L.DRAFT
    assert rows(duo.conv)[0].action == ""


def test_a_failure_while_recording_the_check_never_changes_whether_the_post_succeeds(fake, field_names, monkeypatch):
    duo = duo_with_history()
    cid = make_check(fake, duo)

    def broken(*args, **kwargs):
        raise RuntimeError("secret-internal-xyz")

    monkeypatch.setattr("moderation.preview.resolve_check", broken)
    monkeypatch.setattr("forum.views.resolve_check", broken, raising=False)
    monkeypatch.setattr("forum.services.resolve_check", broken, raising=False)
    response = post_with(duo.ca, duo, field_names, L.DRAFT, cid)
    assert response.status_code == 302
    assert K.user_messages(duo.conv).filter(participant=duo.pa).count() == 2  # the seeded one and this post


def test_posting_without_a_check_id_is_the_old_post(fake, field_names):
    duo = duo_with_history()
    client = fake()
    response = duo.ca.post(duo.post_url, {field_names["message"]: "Plain post."})
    assert response.status_code == 302 and client.calls == []
    assert rows() == []


# --- data-preview on the composer -------------------------------------------------------------------------------------------

def composer(response, duo):
    return H.form_with_action(H.doc(response), duo.post_url)


def test_the_composer_says_on_or_off_from_the_conversations_mode(tune):
    for share, expected in ((1.0, "on"), (0.0, "off")):
        tune(PREVIEW_SHARE=share)
        duo = K.Duo(f"Claim for share {share}.", names=(f"mode_a{int(share)}", f"mode_b{int(share)}"))
        form = composer(duo.ca.get(duo.url), duo)
        assert form.get("data-preview") == expected
        assert composer(duo.cb.get(duo.url), duo).get("data-preview") == expected, "both people share the mode"


def test_the_mode_is_stable_across_page_loads_and_tunable_changes(tune):
    tune(PREVIEW_SHARE=1.0)
    duo = K.Duo()
    assert composer(duo.ca.get(duo.url), duo).get("data-preview") == "on"
    tune(PREVIEW_SHARE=0.0)
    for _ in range(3):
        assert composer(duo.ca.get(duo.url), duo).get("data-preview") == "on"


def test_a_closed_conversation_has_no_data_preview_and_never_asks_for_the_mode(monkeypatch):
    duo = K.Duo()
    duo.ca.post(duo.end_url)
    calls = []
    import moderation.preview as preview

    real = preview.preview_mode
    monkeypatch.setattr(preview, "preview_mode", lambda cid: calls.append(cid) or real(cid))
    monkeypatch.setattr("forum.views.preview_mode", lambda cid: calls.append(cid) or real(cid), raising=False)
    monkeypatch.setattr("forum.viewmodels.preview_mode", lambda cid: calls.append(cid) or real(cid), raising=False)
    for client in (duo.ca, duo.cb):
        response = client.get(duo.url)
        assert "data-preview" not in response.content.decode()
    assert calls == [], "preview_mode is called only for a participant of an open or active conversation"


def test_a_non_participant_never_reaches_preview_mode(monkeypatch):
    duo = K.Duo()
    calls = []
    import moderation.preview as preview

    monkeypatch.setattr(preview, "preview_mode", lambda cid: calls.append(cid) or "on")
    monkeypatch.setattr("forum.views.preview_mode", lambda cid: calls.append(cid) or "on", raising=False)
    assert K.client_for(K.make_user()).get(duo.url).status_code == 404
    assert calls == []


def test_a_failing_back_end_gives_off(monkeypatch):
    duo = K.Duo()

    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("secret-internal-xyz")

    import moderation.preview as preview
    from moderation.models import PreviewMode

    def broken(cid):
        raise RuntimeError("secret-internal-xyz")

    monkeypatch.setattr(preview, "preview_mode", broken)
    monkeypatch.setattr("forum.views.preview_mode", broken, raising=False)
    monkeypatch.setattr("forum.viewmodels.preview_mode", broken, raising=False)
    monkeypatch.setattr(PreviewMode, "objects", Boom())
    response = duo.ca.get(duo.url)
    assert response.status_code == 200
    assert composer(response, duo).get("data-preview") == "off"
    assert "secret-internal-xyz" not in response.content.decode()


def test_the_check_timeout_is_exposed_from_the_tunable(tune):
    duo = K.Duo()
    form = composer(duo.ca.get(duo.url), duo)
    assert form.get("data-check-timeout") in ("25", "25000")
    tune(PREVIEW_CLIENT_TIMEOUT_SECONDS=7)
    assert composer(duo.ca.get(duo.url), duo).get("data-check-timeout") in ("7", "7000")


def test_the_composer_is_still_an_ordinary_form_without_script(field_names):
    duo = K.Duo()
    form = composer(duo.ca.get(duo.url), duo)
    assert form.get("method", "").lower() == "post" and form.get("action") == duo.post_url and H.hidden_csrf(form)
    assert [b.get("type", "submit") for b in form.find_all("button")].count("submit") == 1, "one submit button"
    assert all(b.get("type") == "button" for b in form.find_all("button") if b.get("type", "submit") != "submit")


def test_the_polling_json_never_mentions_previews_checks_or_drafts(fake):
    duo = duo_with_history()
    fake(*L.concern())
    check(duo.ca, duo.conv)
    for client in (duo.ca, duo.cb):
        _, data = K.poll_json(client, duo.conv)
        assert not re.search(r"(?i)preview|check_id|draft", json.dumps(data))


def test_no_cost_or_debate_wording_in_the_script_the_panel_or_any_check_reply(fake):
    import fviews_js as J

    js = J.script_path("compose.js")
    text = open(js, encoding="utf-8").read().lower()
    for word in ("running cost", "api cost", "spending", "budget", "debate"):
        assert word not in text, f"compose.js says {word!r}"
    duo = duo_with_history()
    page = H.norm(H.doc(duo.ca.get(duo.url)).text()).lower()
    fake(*L.concern())
    reply_text = check(duo.ca, duo.conv).content.decode().lower()
    for word in ("running cost", "api cost", "spending", "budget", "debat"):
        assert word not in page and word not in reply_text
