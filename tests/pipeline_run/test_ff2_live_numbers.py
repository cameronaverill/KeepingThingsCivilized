"""Front-end fixes 2, fix B, live pipeline (docs/frontend_fixes_brief2.md): "message N" in the Intervenor's act text is
renumbered from the transcript id (database pk) to the number the page shows (seq_no) before the text is stored or posted.
Database pks are made different from seq_no by creating other conversations first."""
import sys
from pathlib import Path

import pipeline_run_kit as kit
import pytest
from django.test import Client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "forum_views"))
import fviews_html as H  # noqa: E402

pytestmark = pytest.mark.django_db


def skewed(specs=None, human=False):
    """A world whose message pks differ from their seq_no (other conversations were created before it)."""
    outside = kit.other_world_message()
    kit.build()
    kit.build()
    world = kit.build(specs, human=human)
    assert all(m.pk != m.seq_no for m in world.msgs)
    return world, outside


def run_with(fake, world, texts, *, tune_cap=None, rationale=None, explanation=None, acts_extra=None):
    trigger = world.last
    master = kit.master_d(kit.issue_d("i1", trigger, "unsupported_claim", kit.QUOTE_1,
                                      **({"explanation": explanation} if explanation else {})))
    acts = [kit.act_d(t, issues=["i1"], messages=[trigger], addressee="all", subject="none") for t in texts]
    fake(master, kit.interv_d(dispositions=[kit.disp_d("i1")], acts=acts,
                              **({"rationale": rationale} if rationale else {})))
    return kit.go(kit.new_run(trigger))[1]


def posted_text(run):
    return run.posted_message.content


# --- the main behaviour --------------------------------------------------------------------------------------------------

def test_a_cited_id_becomes_the_displayed_number_in_the_stored_act_and_the_posted_message(fake):
    world, _ = skewed()
    cited = world[2].pk
    run = run_with(fake, world, [f"Could a source be given for the claim in message {cited}?"])
    want = "Could a source be given for the claim in message 2?"
    (act,) = kit.acts_of(run)
    assert act.text == want and act.validity == "valid"
    assert posted_text(run) == want


def test_the_trigger_itself_is_renumbered(fake):
    world, _ = skewed()
    run = run_with(fake, world, [f"Message #{world[3].pk} cites nothing."])
    assert posted_text(run) == "Message #3 cites nothing."


def test_a_list_form_is_renumbered_throughout(fake):
    world, _ = skewed()
    a, b, c = world[1].pk, world[2].pk, world[3].pk
    run = run_with(fake, world, [f"Messages {a}, {b} and {c} disagree; messages {c} & {a} too."])
    assert posted_text(run) == "Messages 1, 2 and 3 disagree; messages 3 & 1 too."
    assert kit.acts_of(run)[0].text == posted_text(run)


def test_a_positional_citation_stays(fake):
    world, _ = skewed()
    assert world[1].pk > 1 and world[2].pk > 2
    run = run_with(fake, world, ["Could a source be given for the claim in message 1?"])
    assert posted_text(run) == "Could a source be given for the claim in message 1?"


def test_a_citation_of_an_id_outside_the_window_stays(fake, tune):
    tune(TRANSCRIPT_MAX_MESSAGES=2)  # window = messages 2 and 3
    world, outside = skewed()
    out_of_window = world[1].pk
    other_conv = outside.pk
    run = run_with(fake, world, [f"Compare message {out_of_window} with message {other_conv} and message {world[3].pk}."])
    assert posted_text(run) == f"Compare message {out_of_window} with message {other_conv} and message 3."


def test_two_acts_in_one_message_are_both_renumbered_and_match(fake, tune):
    tune(MAX_ACTS_PER_INTERVENTION=3)
    world, _ = skewed()
    t1 = f"Could a source be given for message {world[3].pk}?"
    t2 = f"Message {world[2].pk} and message {world[3].pk} cite no figures."
    run = run_with(fake, world, [t1, t2])
    acts = kit.acts_of(run)
    assert [a.text for a in acts] == ["Could a source be given for message 3?", "Message 2 and message 3 cite no figures."]
    assert posted_text(run) == "\n\n".join(a.text for a in acts)


def test_text_without_a_reference_is_stored_and_posted_unchanged(fake):
    world, _ = skewed()
    run = run_with(fake, world, [kit.CLEAN_TEXT_2])
    assert kit.acts_of(run)[0].text == kit.CLEAN_TEXT_2 and posted_text(run) == kit.CLEAN_TEXT_2


def test_the_model_input_is_unchanged_the_transcript_still_carries_the_pks(fake):
    world, _ = skewed()
    cited = world[2].pk
    master = kit.master_d(kit.issue_d("i1", world.last, "unsupported_claim", kit.QUOTE_1))
    client = fake(master, kit.interv_d(dispositions=[kit.disp_d("i1")],
                                       acts=[kit.act_d(f"message {cited}?", issues=["i1"], messages=[world.last])]))
    kit.go(kit.new_run(world.last))
    for agent in ("master", "intervenor"):
        (call,) = kit.calls_of(client, agent)
        assert [i for i, _ in kit.rendered_messages(call)] == [m.pk for m in world.msgs]
        assert 'message id="1"' not in kit.user_input(call) or world[1].pk == 1


def test_an_act_citing_a_different_pk_per_message_uses_each_messages_own_seq(fake):
    world, _ = skewed([("A", "I think rent control reduces supply."), ("B", "No it does not."), ("A", "Yes it does."),
                       ("B", "Prove it."), ("A", "Landlords always leave the market, nobody disagrees on that.")])
    texts = " ".join(f"message {m.pk}=>{m.seq_no};" for m in world.msgs)
    expected = " ".join(f"message {m.seq_no}=>{m.seq_no};" for m in world.msgs)
    run = run_with(fake, world, [texts])
    # "message 7=>2;" : the "=>" ends each reference's number list
    assert posted_text(run) == expected


# --- what is not rewritten -------------------------------------------------------------------------------------------------

def test_master_output_issue_explanation_quote_and_rationale_are_not_rewritten(fake):
    world, _ = skewed()
    cited = world[2].pk
    explanation = f"message {cited} states a claim and messages {world[1].pk} and {cited} lack a source."
    rationale = f"A reply to message {cited} would help."
    run = run_with(fake, world, [f"Is message {cited} sourced?"], explanation=explanation, rationale=rationale)
    assert posted_text(run) == "Is message 2 sourced?"
    (issue,) = kit.issues_of(run)
    assert issue.explanation == explanation
    assert run.rationale == rationale


def test_a_quote_that_looks_like_a_reference_is_not_rewritten(fake):
    from forum.models import Message

    specs = [("A", "Rent control matters."), ("B", "Fine."), ("A", "As I said, nobody disagrees on that.")]
    world, _ = skewed(specs)
    Message.objects.filter(pk=world[3].pk).update(content=f"As I said in message {world[1].pk}, nobody disagrees on that.")
    quote = f"in message {world[1].pk}, nobody"
    master = kit.master_d(kit.issue_d("i1", world[3], "unsupported_claim", quote))
    fake(master, kit.interv_d(dispositions=[kit.disp_d("i1")],
                              acts=[kit.act_d(f"Message {world[1].pk}?", issues=["i1"], messages=[world[3]])]))
    run = kit.go(kit.new_run(world[3]))[1]
    (issue,) = kit.issues_of(run)
    assert issue.quote == quote and issue.quote_match != "not_found"
    assert posted_text(run) == "Message 1?"
    world[3].refresh_from_db()
    assert f"message {world[1].pk}," in world[3].content  # the user's own message is never touched


def test_user_messages_are_never_rewritten(fake):
    from forum.models import Message

    world, _ = skewed()
    before = list(Message.objects.filter(conversation=world.conv, author_type="user").order_by("seq_no")
                  .values_list("content", flat=True))
    run_with(fake, world, [f"message {world[2].pk}?"])
    after = list(Message.objects.filter(conversation=world.conv, author_type="user").order_by("seq_no")
                 .values_list("content", flat=True))
    assert before == after


def test_the_scripted_master_output_stored_with_the_run_keeps_the_ids(fake):
    """Issue.message_id points at the real message; nothing structural is renumbered."""
    world, _ = skewed()
    run = run_with(fake, world, [f"message {world[3].pk}?"])
    (issue,) = kit.issues_of(run)
    assert issue.message_id == world[3].pk
    (act,) = kit.acts_of(run)
    assert [m.pk for m in act.source_messages.all()] == [world[3].pk]


# --- the page agrees with what was stored ---------------------------------------------------------------------------------

def paragraphs(root):
    return [n.text() for n in root.walk() if n.tag == "p" and "msg-text" in (n.get("class") or "").split()]


def slots(root):
    return [n for n in root.walk() if n.tag == "div" and "research-slot" in (n.get("class") or "").split()]


def open_page(world):
    from django.urls import reverse

    client = Client()
    client.force_login(world.users["A"], backend="django.contrib.auth.backends.ModelBackend")
    conv = world.conv
    return client, reverse("forum:conversation", args=[conv.pk]), reverse("forum:messages", args=[conv.pk])


def test_page_poll_and_stored_text_agree_and_research_slots_still_match(fake, tune):
    tune(MAX_ACTS_PER_INTERVENTION=3)
    world, _ = skewed(human=True)
    t1 = f"Could a source be given for message {world[3].pk}?"
    t2 = f"Message {world[2].pk} and message {world[3].pk} cite no figures."
    run = run_with(fake, world, [t1, t2])
    acts = kit.acts_of(run)
    assert [a.text for a in acts] == ["Could a source be given for message 3?", "Message 2 and message 3 cite no figures."]
    client, page_url, poll_url = open_page(world)
    page = H.doc(client.get(page_url))
    texts = paragraphs(page)
    mod = [t for t in texts if "cite no figures" in t or "source be given" in t]
    assert mod == [a.text for a in acts]
    found = slots(page)
    # request_information is research-eligible: one slot per paragraph, linked to the act of the same position
    assert [s.get("data-act-id") for s in found] == [str(a.pk) for a in acts]
    for s in found:
        assert s.get("data-state") == "none"
    import json

    data = json.loads(client.get(poll_url + "?after=0").content)
    item = [m for m in data["messages"] if m["kind"] == "moderator"][0]
    assert item["text"] == posted_text(run)
    assert [p["text"] for p in item["paragraphs"]] == [a.text for a in acts]
    assert [p["research"]["act_id"] for p in item["paragraphs"]] == [a.pk for a in acts]


# --- replays ------------------------------------------------------------------------------------------------------------------

def test_a_replay_renumbers_the_same_way_and_never_posts(fake):
    from forum.models import Message

    world, _ = skewed()
    live = kit.new_run(world.last)
    replay = kit.new_run(world.last, kind="replay", replay_of=live, replicate=1)
    master = kit.master_d(kit.issue_d("i1", world.last, "unsupported_claim", kit.QUOTE_1))
    fake(master, kit.interv_d(dispositions=[kit.disp_d("i1")],
                              acts=[kit.act_d(f"Is message {world[2].pk} sourced?", issues=["i1"], messages=[world.last])]))
    count = Message.objects.filter(conversation=world.conv).count()
    _, stored = kit.go(replay)
    assert stored.posted_message is None
    assert Message.objects.filter(conversation=world.conv).count() == count
    assert kit.acts_of(stored)[0].text == "Is message 2 sourced?"


def test_a_live_run_and_a_replay_of_the_same_answer_store_the_same_text(fake):
    world, _ = skewed()

    def answer():
        master = kit.master_d(kit.issue_d("i1", world.last, "unsupported_claim", kit.QUOTE_1))
        return [master, kit.interv_d(dispositions=[kit.disp_d("i1")],
                                     acts=[kit.act_d(f"Messages {world[1].pk} and {world[3].pk}.", issues=["i1"],
                                                     messages=[world.last])])]

    live = kit.new_run(world.last)
    fake(*answer())
    _, live_done = kit.go(live)
    replay = kit.new_run(world.last, kind="replay", replay_of=live_done, replicate=1)
    fake(*answer())
    _, replay_done = kit.go(replay)
    assert kit.acts_of(live_done)[0].text == kit.acts_of(replay_done)[0].text == "Messages 1 and 3."
