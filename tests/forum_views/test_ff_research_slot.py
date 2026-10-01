"""Front-end fixes 2 and 3 (docs/frontend_fixes_brief.md): the research slot markup per state, the state mapping, the retry
of a failed research run, and the `research` key of the poll payload. Written from the brief; fails until the builder is done."""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

import fviews_html as H
import fviews_kit as K
import ff_kit as F

ALL_STATUSES = ("pending", "running", "done", "failed", "skipped_budget", "skipped_disabled")
STATE_OF = {"pending": "pending", "running": "pending", "failed": "failed", "skipped_budget": "failed",
            "skipped_disabled": "failed", "done": "done"}
ELIGIBLE = ("offer_research", "correct_factual_error", "provide_information", "request_information")


def world(state=None, act_type="offer_research", marker="ff-slot", requested_by=None, names=None):
    duo = K.Duo(names=names) if names else K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    mod, act = F.post_with_act(duo.conv, trigger, act_type=act_type, marker=marker)
    run = F.make_state(act, state, requested_by=requested_by or duo.pa) if state else None
    return duo, trigger, mod, act, run


def page_slot(duo, act, client=None):
    root = H.doc((client or duo.ca).get(duo.url))
    return root, F.slot_for(root, act)


# --- markup per state ---------------------------------------------------------------------------------------------------

def test_none_state_slot_wraps_the_offer_button():
    duo, _, _, act, _ = world()
    root, slot = page_slot(duo, act)
    assert slot is not None and slot.tag == "div"
    assert slot.get("data-state") == "none" and slot.get("data-act-id") == str(act.pk)
    offer = F.descendants(slot, "div", "research-offer")
    assert len(offer) == 1
    forms = F.descendants(offer[0], "form", "research-form")
    assert len(forms) == 1 and forms[0].get("action") == F.url_for(duo.conv, act)
    assert forms[0].get("method", "").lower() == "post"
    assert H.hidden_csrf(forms[0])
    buttons = F.descendants(forms[0], "button")
    assert [b.text() for b in buttons] == [F.OFFER_BUTTON]
    assert not F.descendants(slot, "p", "research-pending") and not F.descendants(slot, "p", "research-failed")


def test_pending_state_slot_wraps_the_spinner_text_and_no_form():
    duo, _, _, act, _ = world("pending")
    root, slot = page_slot(duo, act)
    assert slot.get("data-state") == "pending"
    pending = F.descendants(slot, "p", "research-pending")
    assert len(pending) == 1 and has_class(pending[0], "msg-meta")
    assert F.PENDING_TEXT in pending[0].text()
    assert [n for n in pending[0].walk() if n.tag == "span" and has_class(n, "spinner")]
    assert not F.descendants(slot, "form") and not F.descendants(slot, "button")


def has_class(node, name):
    return F.has_class(node, name)


def test_failed_state_slot_shows_the_alert_and_a_try_again_form():
    duo, _, _, act, _ = world("failed")
    root, slot = page_slot(duo, act)
    assert slot.get("data-state") == "failed"
    failed = F.descendants(slot, "p", "research-failed")
    assert len(failed) == 1
    assert has_class(failed[0], "msg-meta") and failed[0].get("role") == "alert"
    assert failed[0].text() == F.FAILED_TEXT
    forms = F.descendants(slot, "form", "research-form")
    assert len(forms) == 1 and forms[0].get("action") == F.url_for(duo.conv, act)
    assert forms[0].get("method", "").lower() == "post" and H.hidden_csrf(forms[0])
    assert [b.text() for b in F.descendants(forms[0], "button")] == [F.RETRY_BUTTON]
    assert F.OFFER_BUTTON not in slot.text() and F.PENDING_TEXT not in slot.text()
    # the alert comes before the form
    order = [n for n in slot.walk() if n is failed[0] or n is forms[0]]
    assert order[0] is failed[0]


def test_done_state_slot_is_present_but_empty():
    duo, _, _, act, _ = world("done")
    root, slot = page_slot(duo, act)
    assert slot is not None and slot.get("data-state") == "done"
    assert slot.text() == "" and not [n for n in slot.walk()], "nothing inside a done slot"


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_every_run_status_maps_to_the_right_slot_state(status):
    duo = K.Duo(names=(f"ffs_a_{status}", f"ffs_b_{status}"))
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = F.post_with_act(duo.conv, trigger, marker="ff-map")
    posted = F.note_message(duo.conv, trigger) if status == "done" else None
    F.make_run(act, status=status, posted_message=posted, requested_by=duo.pa)
    _, slot = page_slot(duo, act)
    assert slot.get("data-state") == STATE_OF[status]


def test_a_finished_run_that_posted_no_note_counts_as_failed():
    """Reading of the brief: 'a finished run that did not produce a note counts as failed' -> done without posted_message."""
    duo, _, _, act, _ = world()
    F.make_run(act, status="done", posted_message=None, requested_by=duo.pa)
    _, slot = page_slot(duo, act)
    assert slot.get("data-state") == "failed"


@pytest.mark.parametrize("act_type", ELIGIBLE)
def test_the_slot_exists_for_every_eligible_type_and_state(act_type):
    for state in ("none", "pending", "failed", "done"):
        duo = K.Duo(names=(f"ffe_a_{act_type[:6]}{state}", f"ffe_b_{act_type[:6]}{state}"))
        trigger = duo.seed(duo.pa, "A claim worth checking.")
        _, act = F.post_with_act(duo.conv, trigger, act_type=act_type)
        F.make_state(act, state, requested_by=duo.pa)
        _, slot = page_slot(duo, act)
        assert slot is not None and slot.get("data-state") == state, (act_type, state)


@pytest.mark.parametrize("act_type", ("request_clarification", "enforce_conduct", "improve_argumentation"))
def test_no_slot_for_an_act_without_research_info(act_type):
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    F.post_with_act(duo.conv, trigger, act_type=act_type)
    root = H.doc(duo.ca.get(duo.url))
    assert F.slots(root) == []


def test_no_slot_for_a_rejected_act():
    from moderation.models import InterventionAct

    duo, _, _, act, _ = world()
    InterventionAct.objects.filter(pk=act.pk).update(validity="rejected", rejection_reason="x")
    root = H.doc(duo.ca.get(duo.url))
    assert F.slots(root) == []


def test_slots_live_only_inside_moderator_articles_one_per_researchable_paragraph():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    duo.seed(duo.pb, "Another user message with offer text.")
    mod, acts = F.post_with_acts(duo.conv, trigger, ["offer_research", "request_clarification", "provide_information"])
    root = H.doc(duo.ca.get(duo.url))
    found = F.slots(root)
    assert [s.get("data-act-id") for s in found] == [str(acts[0].pk), str(acts[2].pk)]
    for s in found:
        article = next(a for a in s.ancestors() if a.tag == "article")
        assert F.has_class(article, "msg-moderator")
    # slot follows its own paragraph, not another one
    article = found[0].parent
    kids = [c for c in article.children if not isinstance(c, str)]
    idx = kids.index(found[0])
    assert kids[idx - 1].tag == "p" and "paragraph number 0" in kids[idx - 1].text()


def test_two_acts_have_independent_states():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, (a1, a2) = F.post_with_acts(duo.conv, trigger, ["offer_research", "offer_research"])
    F.make_state(a1, "failed", requested_by=duo.pa)
    root = H.doc(duo.ca.get(duo.url))
    assert F.slot_for(root, a1).get("data-state") == "failed"
    assert F.slot_for(root, a2).get("data-state") == "none"


def test_no_cost_wording_in_any_slot_state_or_the_retry_reply():
    for state in ("none", "pending", "failed", "done"):
        duo = K.Duo(names=(f"ffw_a_{state}", f"ffw_b_{state}"))
        trigger = duo.seed(duo.pa, "A claim worth checking.")
        _, act = F.post_with_act(duo.conv, trigger, marker="ffw")
        F.make_state(act, state, requested_by=duo.pa)
        _, slot = page_slot(duo, act)
        inner = slot.text().lower()
        for word in F.BANNED:
            assert word not in inner, (state, word)
    for status, reason in (("skipped_budget", "budget"), ("skipped_disabled", "disabled"), ("failed", "internal_error")):
        duo = K.Duo(names=(f"ffw2_a_{status}", f"ffw2_b_{status}"))
        trigger = duo.seed(duo.pa, "A claim worth checking.")
        _, act = F.post_with_act(duo.conv, trigger, marker="ffw2")
        F.make_run(act, status=status, requested_by=duo.pa, failure_reason=reason, error=f"{reason} exceeded spend limit")
        _, slot = page_slot(duo, act)
        assert F.descendants(slot, "p", "research-failed")[0].text() == F.FAILED_TEXT, "a fixed text, whatever the internal reason"
        reply = K.csrf_post(duo.ca, F.url_for(duo.conv, act)).content.decode().lower()
        for word in F.BANNED:
            assert word not in reply


def test_the_failed_text_never_leaks_the_internal_reason_or_error():
    duo, _, _, act, _ = world()
    F.make_run(act, status="failed", requested_by=duo.pa, failure_reason="provider_error", error="SecretTrace123")
    raw = duo.ca.get(duo.url).content.decode()
    assert "SecretTrace123" not in raw and "provider_error" not in raw
    _, data = K.poll_json(duo.ca, duo.conv)
    assert "SecretTrace123" not in str(data) and "provider_error" not in str(data)


# --- the same partial on the page and in the poll -------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["none", "pending", "failed", "done"])
def test_poll_html_is_the_same_markup_as_the_page_slot_content(state):
    duo, _, _, act, _ = world(state if state != "none" else None, names=(f"ffp_a_{state}", f"ffp_b_{state}"))
    if state == "none":
        # `none` is never in the poll payload; compare through the other three only
        assert F.item_for(F.research_payload(duo.ca, duo.conv), act) is None
        return
    root, slot = page_slot(duo, act)
    item = F.item_for(F.research_payload(duo.ca, duo.conv), act)
    assert item is not None and item["state"] == state
    frag = F.fragment(item["html"])
    assert frag.text() == slot.text()
    page_tags = [(n.tag, n.get("class"), n.get("role"), n.get("action")) for n in slot.walk()]
    frag_tags = [(n.tag, n.get("class"), n.get("role"), n.get("action")) for n in frag.walk()]
    assert page_tags == frag_tags


# --- the retry --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status", F.FAILED_STATUSES)
def test_a_click_on_a_failed_run_requeues_the_same_row(status):
    duo = K.Duo(names=(f"ffr_a_{status}", f"ffr_b_{status}"))
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = F.post_with_act(duo.conv, trigger, marker="ff-retry")
    old = F.make_run(act, status=status, requested_by=duo.pa, attempts=3, failure_reason="timeout", error="boom")
    response = K.csrf_post(duo.cb, F.url_for(duo.conv, act))
    assert response.status_code == 200
    assert F.body(response) == {"status": "pending", "act_id": act.pk, "run_id": old.pk}
    rows = F.research_runs(act)
    assert [r.pk for r in rows] == [old.pk], "no second run: still one research run per act"
    run = rows[0]
    assert run.status == "pending" and run.attempts == 0
    assert run.failure_reason == "" and run.error == ""
    assert run.requested_by_id == duo.pb.pk, "requested_by becomes the retrying participant"
    assert run.trigger_message_id == trigger.pk and run.kind == "research" and run.source_act_id == act.pk
    assert run.posted_message_id is None


def test_after_the_retry_the_page_and_poll_show_pending():
    duo, _, _, act, _ = world("failed")
    K.csrf_post(duo.ca, F.url_for(duo.conv, act))
    _, slot = page_slot(duo, act)
    assert slot.get("data-state") == "pending" and F.PENDING_TEXT in slot.text()
    assert F.item_for(F.research_payload(duo.cb, duo.conv), act)["state"] == "pending"


def test_a_requeued_run_is_claimable_by_the_worker():
    from moderation import worker

    duo, _, _, act, run = world("failed")
    K.csrf_post(duo.ca, F.url_for(duo.conv, act))
    claimed = worker.claim_next_run()
    assert claimed is not None and claimed.pk == run.pk and claimed.status == "running"


def test_the_retry_makes_no_model_call():
    from moderation.models import LLMCall

    duo, _, _, act, _ = world("failed")
    before = LLMCall.objects.count()
    K.csrf_post(duo.ca, F.url_for(duo.conv, act))
    assert LLMCall.objects.count() == before


def test_a_double_click_requeues_exactly_once():
    duo, _, _, act, run = world("failed")
    url = F.url_for(duo.conv, act)
    first = K.csrf_post(duo.ca, url)
    # the worker picks the run up between the two clicks
    from moderation.models import ModerationRun
    ModerationRun.objects.filter(pk=run.pk).update(status="running", attempts=1)
    second = K.csrf_post(duo.ca, url)
    assert first.status_code == second.status_code == 200
    assert F.body(first)["status"] == "pending" and F.body(second)["status"] == "pending"
    after = F.reload_run(run)
    assert (after.status, after.attempts) == ("running", 1), "the second click must not reset a run in flight"
    assert len(F.research_runs(act)) == 1


def test_two_participants_clicking_in_turn_requeue_once_and_the_first_keeps_requested_by():
    duo, _, _, act, run = world("failed")
    url = F.url_for(duo.conv, act)
    r1 = K.csrf_post(duo.ca, url)
    r2 = K.csrf_post(duo.cb, url)
    assert F.body(r1) == F.body(r2) == {"status": "pending", "act_id": act.pk, "run_id": run.pk}
    after = F.reload_run(run)
    assert after.status == "pending" and after.requested_by_id == duo.pa.pk, "the loser does not overwrite requested_by"


def test_a_failed_run_requeued_twice_over_its_life_is_still_one_row():
    from moderation.models import ModerationRun

    duo, _, _, act, run = world("failed")
    url = F.url_for(duo.conv, act)
    K.csrf_post(duo.ca, url)
    ModerationRun.objects.filter(pk=run.pk).update(status="failed", attempts=2, failure_reason="timeout", error="x")
    resp = K.csrf_post(duo.cb, url)
    assert F.body(resp)["run_id"] == run.pk
    assert len(F.research_runs(act)) == 1 and F.reload_run(run).requested_by_id == duo.pb.pk


@pytest.mark.parametrize("status", ["pending", "running"])
def test_a_click_on_an_in_flight_run_changes_nothing(status):
    duo = K.Duo(names=(f"ffi_a_{status}", f"ffi_b_{status}"))
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = F.post_with_act(duo.conv, trigger, marker="ff-inflight")
    run = F.make_run(act, status=status, requested_by=duo.pa, attempts=1)
    response = K.csrf_post(duo.cb, F.url_for(duo.conv, act))
    assert F.body(response) == {"status": "pending", "act_id": act.pk, "run_id": run.pk}
    after = F.reload_run(run)
    assert (after.status, after.attempts, after.requested_by_id) == (status, 1, duo.pa.pk)


def test_a_click_on_a_done_run_returns_done_and_changes_nothing():
    duo, trigger, _, act, run = world("done")
    before = F.reload_run(run)
    response = K.csrf_post(duo.cb, F.url_for(duo.conv, act))
    body = F.body(response)
    assert body["status"] == "done" and body["act_id"] == act.pk and body["run_id"] == run.pk
    after = F.reload_run(run)
    assert (after.status, after.requested_by_id, after.posted_message_id, after.attempts) == (
        before.status, before.requested_by_id, before.posted_message_id, before.attempts)


def test_a_first_click_without_a_run_still_creates_one_pending_run():
    duo, trigger, _, act, _ = world()
    response = K.csrf_post(duo.ca, F.url_for(duo.conv, act))
    rows = F.research_runs(act)
    assert len(rows) == 1 and rows[0].status == "pending" and rows[0].requested_by_id == duo.pa.pk
    assert F.body(response) == {"status": "pending", "act_id": act.pk, "run_id": rows[0].pk}


def test_retry_keeps_the_404s_and_creates_nothing():
    duo, trigger, _, act, run = world("failed")
    url = F.url_for(duo.conv, act)
    stranger = K.client_for(K.make_user("ff_stranger"))
    response = K.csrf_post(stranger, url)
    assert response.status_code == 404 and K.NOT_FOUND_TEXT in K.page_text(response)
    assert F.reload_run(run).status == "failed", "a non-participant cannot re-queue"
    assert F.reload_run(run).failure_reason == "internal_error"
    assert K.csrf_post(duo.ca, f"/c/987654/research/{act.pk}/").status_code == 404
    assert K.csrf_post(duo.ca, f"/c/{duo.conv.pk}/research/987654/").status_code == 404
    other = K.Duo("Elsewhere.", names=("ffr_else_1", "ffr_else_2"))
    assert K.csrf_post(other.ca, F.url_for(other.conv, act)).status_code == 404
    assert F.reload_run(run).status == "failed"


@pytest.mark.parametrize("act_type", ("request_clarification", "enforce_conduct"))
def test_retry_on_an_ineligible_act_is_still_404(act_type):
    duo = K.Duo(names=(f"ffr_i_{act_type[:5]}_a", f"ffr_i_{act_type[:5]}_b"))
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = F.post_with_act(duo.conv, trigger, act_type=act_type)
    assert K.csrf_post(duo.ca, F.url_for(duo.conv, act)).status_code == 404


def test_retry_keeps_login_and_post_only():
    from django.test import Client

    duo, _, _, act, run = world("failed")
    url = F.url_for(duo.conv, act)
    response = Client().post(url)
    assert response.status_code == 302
    assert F.reload_run(run).status == "failed"
    assert duo.ca.get(url).status_code == 405


def test_the_retry_reply_is_json_with_no_store():
    duo, _, _, act, _ = world("failed")
    response = K.csrf_post(duo.ca, F.url_for(duo.conv, act))
    assert response["Content-Type"].startswith("application/json") and "no-store" in response["Cache-Control"]


# --- the poll payload ---------------------------------------------------------------------------------------------------------

def test_research_key_is_an_empty_list_when_no_act_has_a_run():
    duo, _, _, act, _ = world()
    items = F.research_payload(duo.ca, duo.conv)
    assert items == []


def test_the_poll_lists_pending_failed_and_done_but_never_none():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    t2 = duo.seed(duo.pb, "A second claim worth checking.")
    _, first = F.post_with_acts(duo.conv, trigger, ["offer_research"] * 3)
    _, second = F.post_with_acts(duo.conv, t2, ["offer_research"], marker="ff-second")
    acts = first + second
    for act, state in zip(acts, ("none", "pending", "failed", "done")):
        F.make_state(act, state, requested_by=duo.pa)
    for client in (duo.ca, duo.cb):
        items = F.research_payload(client, duo.conv)
        assert sorted((i["act_id"], i["state"]) for i in items) == sorted(
            [(acts[1].pk, "pending"), (acts[2].pk, "failed"), (acts[3].pk, "done")])
        for item in items:
            assert set(item) == {"act_id", "state", "html"}
            assert isinstance(item["act_id"], int) and isinstance(item["state"], str) and isinstance(item["html"], str)


def test_the_poll_html_per_state():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, acts = F.post_with_acts(duo.conv, trigger, ["offer_research"] * 3)
    for act, state in zip(acts, ("pending", "failed", "done")):
        F.make_state(act, state, requested_by=duo.pa)
    items = F.research_payload(duo.ca, duo.conv)
    by = {i["state"]: i for i in items}
    assert F.PENDING_TEXT in F.fragment(by["pending"]["html"]).text()
    failed = F.fragment(by["failed"]["html"])
    assert failed.text().startswith(F.FAILED_TEXT) and F.RETRY_BUTTON in failed.text()
    form = failed.find("form")
    assert form is not None and form.get("action") == F.url_for(duo.conv, acts[1]) and form.get("class") == "research-form"
    assert by["done"]["html"].strip() == ""
    lowered = by["failed"]["html"].lower()
    for word in F.BANNED:
        assert word not in lowered


def test_the_poll_research_does_not_depend_on_after():
    duo, trigger, mod, act, _ = world("failed")
    for after in (0, mod.seq_no, 999):
        _, data = K.poll_json(duo.ca, duo.conv, after=after)
        assert [(i["act_id"], i["state"]) for i in data["research"]] == [(act.pk, "failed")]
    assert K.poll_json(duo.ca, duo.conv, after=999)[1]["messages"] == []


def test_the_poll_keeps_every_existing_key():
    duo, trigger, mod, act, _ = world("pending")
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    for key in ("status", "messages", "message_count", "waiting", "can_post", "moderation_notice", "cannot_post_reason"):
        assert key in data, key
    assert [m["seq_no"] for m in data["messages"]] == [1, 2]
    moderator = data["messages"][1]
    assert F.PENDING_TEXT in moderator["html"] and "research-slot" in moderator["html"]


def test_the_poll_only_lists_this_conversations_acts():
    duo, _, _, act, _ = world("failed")
    other = K.Duo("Elsewhere.", names=("ffq_else_1", "ffq_else_2"))
    t2 = other.seed(other.pa, "Another claim.")
    _, act2 = F.post_with_act(other.conv, t2, marker="ff-else")
    F.make_state(act2, "failed", requested_by=other.pa)
    ids = [i["act_id"] for i in F.research_payload(duo.ca, duo.conv)]
    assert ids == [act.pk]


def test_the_poll_skips_acts_that_are_not_eligible():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = F.post_with_act(duo.conv, trigger, act_type="request_clarification")
    F.make_run(act, status="failed", requested_by=duo.pa)
    assert F.research_payload(duo.ca, duo.conv) == []


def test_a_non_participant_still_gets_the_404_not_a_research_list():
    duo, _, _, act, _ = world("failed")
    stranger = K.client_for(K.make_user("ff_poll_stranger"))
    response = stranger.get(duo.poll_url)
    assert response.status_code == 404 and b"research" not in response.content.lower().replace(b"researching", b"")


def test_the_poll_json_leaks_nothing_new():
    duo, _, _, act, _ = world("failed")
    for client, viewer, other in ((duo.ca, duo.ua, duo.ub), (duo.cb, duo.ub, duo.ua)):
        _, data = K.poll_json(client, duo.conv)
        assert K.json_leaks(data, viewer, other) == []


def _queries_for(n_acts, with_runs=True, after=None, tag=""):
    duo = K.Duo(names=(f"ffc_a_{n_acts}{with_runs}{after}{tag}", f"ffc_b_{n_acts}{with_runs}{after}{tag}"))
    for i in range(n_acts):
        trigger = duo.seed(duo.pa, f"A claim worth checking {i}.")
        mod, act = F.post_with_act(duo.conv, trigger, marker=f"ff-q{i}")
        if with_runs:
            F.make_state(act, ("pending", "failed", "done")[i % 3], requested_by=duo.pa)
    with CaptureQueriesContext(connection) as ctx:
        _, data = K.poll_json(duo.ca, duo.conv, after=after)
    assert "research" in data
    assert len(data["research"]) == (n_acts if with_runs else 0)
    return len(ctx)


def test_the_research_list_costs_a_constant_number_of_queries_not_one_per_act():
    """With ?after= past every message the page-model work is the same, so any growth is the research lookup."""
    counts = [_queries_for(n, after=999, tag="c") for n in (1, 4, 9)]
    assert counts[0] == counts[1] == counts[2], counts


def test_the_conversation_page_still_renders_every_state_for_the_other_participant_too():
    duo, _, _, act, _ = world("failed")
    root, slot = page_slot(duo, act, duo.cb)
    assert slot.get("data-state") == "failed" and F.RETRY_BUTTON in slot.text()


def test_a_finished_run_without_a_note_is_requeued_by_the_try_again_click():
    """It shows `failed` (Try again), so the click has to do something: re-queue it, not answer `done` and loop."""
    duo, _, _, act, _ = world()
    run = F.make_run(act, status="done", posted_message=None, requested_by=duo.pa, attempts=1)
    response = K.csrf_post(duo.cb, F.url_for(duo.conv, act))
    assert F.body(response) == {"status": "pending", "act_id": act.pk, "run_id": run.pk}
    after = F.reload_run(run)
    assert (after.status, after.attempts, after.requested_by_id) == ("pending", 0, duo.pb.pk)


def test_a_click_after_the_requeued_run_finished_with_a_note_changes_nothing():
    from moderation.models import ModerationRun

    duo, trigger, _, act, run = world("failed")
    url = F.url_for(duo.conv, act)
    K.csrf_post(duo.ca, url)
    note = F.note_message(duo.conv, trigger)
    ModerationRun.objects.filter(pk=run.pk).update(status="done", posted_message=note.pk, attempts=1)
    response = K.csrf_post(duo.cb, url)
    assert F.body(response)["status"] == "done"
    after = F.reload_run(run)
    assert (after.status, after.attempts, after.posted_message_id, after.requested_by_id) == ("done", 1, note.pk, duo.pa.pk)
    _, slot = page_slot(duo, act)
    assert slot.get("data-state") == "done" and slot.text() == ""


def test_the_page_slot_and_the_poll_item_agree_on_every_state_of_the_same_act():
    from moderation.models import ModerationRun

    duo, trigger, _, act, run = world("pending")
    for status, posted in (("pending", None), ("running", None), ("failed", None), ("skipped_budget", None),
                           ("skipped_disabled", None), ("done", "note")):
        values = {"status": status, "posted_message": F.note_message(duo.conv, trigger).pk if posted else None}
        if posted:
            ModerationRun.objects.filter(pk=run.pk).update(posted_message=None)
        ModerationRun.objects.filter(pk=run.pk).update(**values)
        _, slot = page_slot(duo, act)
        item = F.item_for(F.research_payload(duo.ca, duo.conv), act)
        assert item["state"] == slot.get("data-state") == STATE_OF[status], status
