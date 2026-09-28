"""Step 20b, Group C's forum-layer slice (docs/step20b_brief.md item 6): the "Provide factual background" button on
an eligible act, the click endpoint (`forum:request_research`), and the pending/done states on the conversation page.

Button eligibility was widened 2026-09-27 (owner decision, recorded in the brief): the button is not `offer_research`
-only any more. It is available on any VALID act whose `act_type` is `offer_research`, `correct_factual_error`,
`provide_information`, or `request_information` -- any act that addresses a checkable factual claim, whether or not
the Master flagged `needs_verification`. `request_information` was added in a second pass the same day (an owner
report: a real "Could a source or study be given for..." message had no button) -- it fits the same rationale even
more directly than the first three, since it is the Intervenor explicitly signalling it has no confident answer of
its own and is asking someone else for one. The endpoint, the `ModerationRun(kind="research")` it creates, and the
pending/done states work identically regardless of which of the four eligible types triggered them (the brief:
"`research.py` needs no change for this -- it already builds its query from the act's cited issue(s), not from which
act type triggered it").

Written from the contract in the brief, against `forum/urls.py`, `forum/views.py`, `forum/viewmodels.py` and
`forum/templates/forum/_message.html` as they are SPECIFIED there -- a coding agent builds those concurrently in this
same workspace, so these tests may fail until that lands. That is expected; it is not a signal to change this file.

Conventions follow tests/forum_views/test_fviews_check.py (the closest analog: a small POST endpoint on a conversation,
gated the same way, answering JSON) and fviews_kit.py's `add_moderator_post` (adapted here to allow any act type and
non-default validity, which that helper does not support)."""
import json

import pytest
from django.urls import reverse

import fviews_html as H
import fviews_kit as K

BUTTON_TEXT = "Provide factual background"
PENDING_TEXT = "Checking — this may take a moment"
BANNED_WORDS = ("cost", "spend", "budget", "spending", "running cost", "api cost")

# The four act types the button (and the endpoint) must treat alike (widened 2026-09-27, twice).
ELIGIBLE_ACT_TYPES = ("offer_research", "correct_factual_error", "provide_information", "request_information")
# A representative sample of the act types that must never show the button or accept a click.
INELIGIBLE_ACT_TYPES = ("request_clarification", "enforce_conduct", "improve_argumentation")


# --- building blocks for this file only (fviews_kit.py's add_moderator_post hardcodes act_type="request_clarification"
#     and always validity="valid", neither of which fits every scenario needed here) -------------------------------------

def post_with_act(conv, trigger, *, act_type="offer_research", validity="valid", marker="research-marker",
                   source_messages=None, order=1):
    """A moderator message replying to `trigger`, its `done` `live` run, and one `InterventionAct` on it. Returns
    (message, act). Mirrors K.add_moderator_post's shape but lets the act_type/validity/sources vary."""
    from forum.models import Message
    from moderation.models import InterventionAct, ModerationRun

    run = ModerationRun.objects.create(
        conversation=conv, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live", status="pending"
    )
    mod = Message.objects.create(
        conversation=conv, author_type="moderator", content=f"{marker}: an independent check could be requested.",
        in_reply_to=trigger,
    )
    run.status, run.decision, run.posted_message = "done", "intervene", mod
    run.save()
    kwargs = {}
    if validity == "rejected":
        kwargs["rejection_reason"] = "not chosen for this run"
    act = InterventionAct.objects.create(
        run=run, order=order, act_type=act_type, tone="neutral", text=mod.content,
        addressee="all", subject="none", validity=validity, **kwargs,
    )
    act.source_messages.set(source_messages if source_messages is not None else [trigger])
    return mod, act


def make_research_run(act, *, status="pending", posted_message=None, requested_by=None):
    """A `ModerationRun(kind="research")` for `act`, written straight into the database (Group A/B's fields), so the
    template's pending/done rendering (Group C) can be tested without depending on the worker actually running."""
    from moderation.models import ModerationRun

    trigger = act.source_messages.order_by("seq_no").first()
    return ModerationRun.objects.create(
        conversation=act.run.conversation, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="research",
        source_act=act, requested_by=requested_by, status=status, posted_message=posted_message,
    )


def research_url(conv, act):
    return reverse("forum:request_research", args=[conv.pk, act.pk])


def body(response):
    assert response["Content-Type"].startswith("application/json"), response["Content-Type"]
    return json.loads(response.content)


def block_for(root, marker, needles):
    found = K.find_block(root, marker, needles)
    return found


def research_run_count(act=None):
    from moderation.models import ModerationRun

    qs = ModerationRun.objects.filter(kind="research")
    return qs.filter(source_act=act).count() if act is not None else qs.count()


# --- the URL ---------------------------------------------------------------------------------------------------------

def test_the_url_resolves_with_conversation_and_act_ids():
    from django.urls import resolve

    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="url-check")
    assert resolve(research_url(duo.conv, act)).func is not None


# --- the button: renders only for a valid act of an eligible type with no existing run ----------------------------------

@pytest.mark.parametrize("act_type", ELIGIBLE_ACT_TYPES)
def test_the_button_renders_for_a_valid_eligible_act_with_no_research_run(act_type):
    duo = K.Duo(f"Claim for {act_type}.")
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    post_with_act(duo.conv, trigger, act_type=act_type, marker="button-valid")
    root = H.doc(duo.ca.get(duo.url))
    block = block_for(root, "button-valid", [BUTTON_TEXT])
    assert block is not None, f"the button should render for a valid, unrequested {act_type} act"


@pytest.mark.parametrize("act_type", ELIGIBLE_ACT_TYPES)
def test_the_button_does_not_render_for_a_rejected_act_of_an_eligible_type(act_type):
    duo = K.Duo(f"Claim for rejected {act_type}.")
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    post_with_act(duo.conv, trigger, act_type=act_type, marker="button-rejected", validity="rejected")
    root = H.doc(duo.ca.get(duo.url))
    block = block_for(root, "button-rejected", [BUTTON_TEXT])
    assert block is None, f"a rejected {act_type} act must never offer the button"


@pytest.mark.parametrize("act_type", INELIGIBLE_ACT_TYPES)
def test_the_button_does_not_render_for_any_ineligible_act_type(act_type):
    duo = K.Duo(f"Claim for {act_type}.")
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    post_with_act(duo.conv, trigger, act_type=act_type, marker="button-othertype")
    root = H.doc(duo.ca.get(duo.url))
    block = block_for(root, "button-othertype", [BUTTON_TEXT])
    assert block is None, f"{act_type} must never show the research button"


def test_both_participants_see_the_button_since_the_act_addresses_both():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    post_with_act(duo.conv, trigger, marker="button-both")
    for client in (duo.ca, duo.cb):
        root = H.doc(client.get(duo.url))
        assert block_for(root, "button-both", [BUTTON_TEXT]) is not None


# --- posting: creates exactly one ModerationRun(kind="research") with the right fields ----------------------------------

def test_posting_creates_exactly_one_research_run_with_the_right_fields():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="post-fields")
    response = K.csrf_post(duo.ca, research_url(duo.conv, act))
    assert response.status_code == 200
    data = body(response)
    assert "status" in data
    from moderation.models import ModerationRun

    runs = list(ModerationRun.objects.filter(kind="research", source_act=act))
    assert len(runs) == 1
    run = runs[0]
    assert run.source_act_id == act.pk
    assert run.requested_by_id == duo.pa.pk
    assert run.trigger_message_id == trigger.pk


@pytest.mark.parametrize("act_type", ["correct_factual_error", "provide_information"])
def test_posting_works_identically_for_the_other_two_eligible_act_types(act_type):
    """Same endpoint, same ModerationRun creation, regardless of which eligible act_type triggered the click."""
    duo = K.Duo(f"Claim for {act_type} posting.")
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, act_type=act_type, marker="post-fields-widened")
    response = K.csrf_post(duo.ca, research_url(duo.conv, act))
    assert response.status_code == 200
    data = body(response)
    assert "status" in data
    from moderation.models import ModerationRun

    runs = list(ModerationRun.objects.filter(kind="research", source_act=act))
    assert len(runs) == 1
    run = runs[0]
    assert run.source_act_id == act.pk
    assert run.requested_by_id == duo.pa.pk
    assert run.trigger_message_id == trigger.pk


def test_the_trigger_message_is_the_earliest_of_the_acts_source_messages():
    duo = K.Duo()
    earlier = duo.seed(duo.pa, "The earlier claim.", minutes_ago=10)
    later = duo.seed(duo.pb, "The later claim.", minutes_ago=5)
    _, act = post_with_act(duo.conv, later, marker="post-earliest", source_messages=[later, earlier])
    assert list(act.source_messages.order_by("seq_no")) == [earlier, later]
    response = K.csrf_post(duo.ca, research_url(duo.conv, act))
    assert response.status_code == 200
    from moderation.models import ModerationRun

    run = ModerationRun.objects.get(kind="research", source_act=act)
    assert run.trigger_message_id == earlier.pk, "the earliest cited message (by seq_no) is the claim being verified"


def test_a_second_click_never_creates_a_duplicate_run_and_is_not_an_error():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="post-second-click")
    first = K.csrf_post(duo.ca, research_url(duo.conv, act))
    second = K.csrf_post(duo.ca, research_url(duo.conv, act))
    assert first.status_code == 200 and second.status_code == 200, "a second click is success, not an error"
    assert research_run_count(act) == 1, "the second click must not create a second research run"
    assert "status" in body(first) and "status" in body(second)


def test_a_second_click_by_the_other_participant_also_does_not_duplicate():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="post-second-click-other")
    K.csrf_post(duo.ca, research_url(duo.conv, act))
    response = K.csrf_post(duo.cb, research_url(duo.conv, act))
    assert response.status_code == 200
    assert research_run_count(act) == 1


# --- who may click -----------------------------------------------------------------------------------------------------

def test_a_non_participant_gets_404_and_creates_nothing():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="post-stranger")
    stranger = K.client_for(K.make_user("research_stranger"))
    response = K.csrf_post(stranger, research_url(duo.conv, act))
    assert response.status_code == 404
    assert K.NOT_FOUND_TEXT in K.page_text(response)
    assert research_run_count(act) == 0


def test_a_missing_conversation_gets_the_same_404():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="post-missing-conv")
    response = K.csrf_post(duo.ca, f"/c/987654/research/{act.pk}/")
    assert response.status_code == 404
    assert K.NOT_FOUND_TEXT in K.page_text(response)


def test_an_act_belonging_to_another_conversation_is_404_here():
    duo, other = K.Duo(), K.Duo("Elsewhere.", names=("res_else_one", "res_else_two"))
    trigger = other.seed(other.pa, "A claim worth checking, elsewhere.")
    _, other_act = post_with_act(other.conv, trigger, marker="post-cross-conv")
    response = K.csrf_post(duo.ca, research_url(duo.conv, other_act))
    assert response.status_code == 404
    assert research_run_count(other_act) == 0


@pytest.mark.parametrize("act_type", INELIGIBLE_ACT_TYPES)
def test_an_act_of_an_ineligible_type_is_404(act_type):
    duo = K.Duo(f"Claim for {act_type} 404.")
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, act_type=act_type, marker="post-wrong-type")
    response = K.csrf_post(duo.ca, research_url(duo.conv, act))
    assert response.status_code == 404
    assert research_run_count(act) == 0


@pytest.mark.parametrize("act_type", ELIGIBLE_ACT_TYPES)
def test_a_rejected_act_of_an_eligible_type_is_404(act_type):
    duo = K.Duo(f"Claim for rejected {act_type} 404.")
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, act_type=act_type, validity="rejected", marker="post-rejected")
    response = K.csrf_post(duo.ca, research_url(duo.conv, act))
    assert response.status_code == 404
    assert research_run_count(act) == 0


def test_a_missing_act_id_is_404():
    duo = K.Duo()
    response = K.csrf_post(duo.ca, f"/c/{duo.conv.pk}/research/987654/")
    assert response.status_code == 404


def test_login_is_required():
    from django.test import Client

    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="post-login")
    response = Client().post(research_url(duo.conv, act))
    assert response.status_code == 302 and reverse("accounts:login") in response["Location"]
    assert research_run_count(act) == 0


# --- only POST is allowed, like check/check_edit ------------------------------------------------------------------------

def test_only_post_is_allowed():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="post-methods")
    url = research_url(duo.conv, act)
    for method in ("get", "put", "patch", "delete"):
        response = getattr(duo.ca, method)(url)
        assert response.status_code == 405, (method, response.status_code)
    assert research_run_count(act) == 0


# --- the pending state on the page --------------------------------------------------------------------------------------

def test_the_pending_text_shows_and_the_button_is_gone_once_a_run_exists():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="pending-shows")
    make_research_run(act, status="pending", requested_by=duo.pa)
    root = H.doc(duo.ca.get(duo.url))
    assert block_for(root, "pending-shows", [PENDING_TEXT]) is not None
    assert block_for(root, "pending-shows", [BUTTON_TEXT]) is None


@pytest.mark.parametrize("act_type", ["correct_factual_error", "provide_information"])
def test_the_pending_state_works_identically_for_the_other_two_eligible_act_types(act_type):
    duo = K.Duo(f"Claim for {act_type} pending.")
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, act_type=act_type, marker="pending-shows-widened")
    make_research_run(act, status="pending", requested_by=duo.pa)
    root = H.doc(duo.ca.get(duo.url))
    assert block_for(root, "pending-shows-widened", [PENDING_TEXT]) is not None
    assert block_for(root, "pending-shows-widened", [BUTTON_TEXT]) is None


@pytest.mark.parametrize("status", ["pending", "running"])
def test_the_pending_text_shows_for_every_in_flight_status(status):
    duo = K.Duo(f"Claim for {status}.", names=(f"pend_a_{status}", f"pend_b_{status}"))
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="pending-status")
    make_research_run(act, status=status, requested_by=duo.pa)
    root = H.doc(duo.ca.get(duo.url))
    assert block_for(root, "pending-status", [PENDING_TEXT]) is not None


def test_no_button_reappears_once_a_run_exists_even_if_it_later_fails():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="pending-failed")
    make_research_run(act, status="failed", requested_by=duo.pa)
    root = H.doc(duo.ca.get(duo.url))
    assert block_for(root, "pending-failed", [BUTTON_TEXT]) is None, "any existing run (any status) hides the button"


def test_the_pending_text_clears_once_the_run_is_done_and_the_note_shows_normally():
    from forum.models import Message

    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="pending-done")
    note = Message.objects.create(
        conversation=duo.conv, author_type="moderator", content="research-note-marker: here is the sourced answer.",
        in_reply_to=trigger,
    )
    make_research_run(act, status="done", posted_message=note, requested_by=duo.pa)
    root = H.doc(duo.ca.get(duo.url))
    assert block_for(root, "pending-done", [PENDING_TEXT]) is None, "no pending UI persists once the run is done"
    assert block_for(root, "pending-done", [BUTTON_TEXT]) is None
    assert "research-note-marker" in K.page_text(duo.ca.get(duo.url))


def test_the_done_note_also_appears_through_polling_like_any_moderator_message():
    from forum.models import Message

    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="pending-poll")
    note = Message.objects.create(
        conversation=duo.conv, author_type="moderator", content="research-note-poll-marker: sourced answer.",
        in_reply_to=trigger,
    )
    make_research_run(act, status="done", posted_message=note, requested_by=duo.pa)
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    blob = json.dumps(data)
    assert "research-note-poll-marker" in blob
    assert PENDING_TEXT not in blob


# --- no cost language anywhere in this flow -------------------------------------------------------------------------------

def test_no_cost_wording_in_the_button_page_or_json_replies():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    _, act = post_with_act(duo.conv, trigger, marker="wording-marker")
    page_before = duo.ca.get(duo.url).content.decode().lower()
    response = K.csrf_post(duo.ca, research_url(duo.conv, act))
    reply = response.content.decode().lower()
    page_after = duo.ca.get(duo.url).content.decode().lower()
    for word in BANNED_WORDS:
        assert word not in page_before, f"the button page mentions {word!r}"
        assert word not in reply, f"the click reply mentions {word!r}"
        assert word not in page_after, f"the pending page mentions {word!r}"
