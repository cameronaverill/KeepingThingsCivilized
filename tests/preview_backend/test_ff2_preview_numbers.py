"""Front-end fixes 2, fix B, draft preview (docs/frontend_fixes_brief2.md): `_run_agents` renumbers "message N" in every act's
text with the shared helper. The map is the transcript's ids -> seq_no, and the draft's placeholder -1 -> snapshot_seq + 1.
Stored `note_texts` and `intervenor_output` carry the renumbered text; the Master output does not."""
import pipeline_run_kit as prk
import preview_kit as pk
import pytest

pytestmark = pytest.mark.django_db


def skewed(specs=None, offset=2):
    """A world whose message pks differ from seq_no (`offset` messages exist elsewhere first)."""
    for _ in range(offset):
        prk.build([("A", "A message elsewhere.")])
    w = pk.world(specs)
    assert all(m.pk != m.seq_no for m in w.msgs)
    return w


def checked(fake, w, texts, who="B", tune_cap=None):
    fake(*pk.concern_script(pk.PLACEHOLDER, texts=texts))
    return pk.check(w, who)[1]


def test_a_cited_window_id_becomes_its_seq_no_in_the_notes_and_the_stored_output(fake):
    w = skewed()
    check = checked(fake, w, (f"Could a source be given for the claim in message {w[2].pk}?",))
    want = "Could a source be given for the claim in message 2?"
    assert check.outcome == "concern" and check.note_texts == [want]
    assert [a["text"] for a in check.intervenor_output["acts"]] == [want]


def test_ids_and_the_placeholder_are_renumbered_together(fake):
    w = skewed()
    check = checked(fake, w, (f"Message {w[1].pk} makes the claim that message -1 disputes; see messages {w[3].pk} and -1.",))
    n = len(w.msgs) + 1
    assert n == 4
    assert check.note_texts == [f"Message 1 makes the claim that message {n} disputes; see messages 3 and {n}."]


def test_the_hash_form_for_ids_and_the_placeholder(fake):
    w = skewed()
    check = checked(fake, w, (f"Compare message #{w[2].pk} with message #-1.",))
    assert check.note_texts == ["Compare message #2 with message #4."]


def test_a_positional_citation_and_unknown_ids_stay(fake):
    w = skewed()
    unknown = max(m.pk for m in w.msgs) + 500
    check = checked(fake, w, (f"Message 1 and message {unknown} are not window ids; message {w[3].pk} is.",))
    assert check.note_texts == [f"Message 1 and message {unknown} are not window ids; message 3 is."]


def test_a_list_is_renumbered_throughout(fake):
    w = skewed()
    a, b, c = (w[i].pk for i in (1, 2, 3))
    check = checked(fake, w, (f"Messages {a}, {b} and {c} or {a} & -1.",))
    assert check.note_texts == ["Messages 1, 2 and 3 or 1 & 4."]


def test_one_pass_no_chaining_when_pks_and_seq_overlap(fake):
    """pk of message k is k+1 (one message exists elsewhere), so the pks 2,3,4 map to 1,2,3 and a chained rewrite would
    turn message 3 -> 2 -> 1."""
    w = skewed(offset=1)
    assert [m.pk for m in w.msgs] == [2, 3, 4] and [m.seq_no for m in w.msgs] == [1, 2, 3]
    check = checked(fake, w, ("Messages 4 and 3 and 2 differ; message 3 is long.",))
    assert check.note_texts == ["Messages 3 and 2 and 1 differ; message 2 is long."]


def test_every_act_is_renumbered(fake, tune):
    tune(MAX_ACTS_PER_INTERVENTION=3)
    w = skewed()
    check = checked(fake, w, (f"Is message {w[2].pk} sourced?", f"Does message {w[3].pk} or message -1 cite a figure?"))
    assert check.note_texts == ["Is message 2 sourced?", "Does message 3 or message 4 cite a figure?"]
    assert [a["text"] for a in check.intervenor_output["acts"]] == check.note_texts


def test_the_number_of_the_draft_follows_the_conversation_length(fake):
    from pipeline_run_kit import DEFAULT_SPECS

    specs = DEFAULT_SPECS + [("B", "Rents are capped in many cities."), ("A", "That is not what the data says.")]
    w = skewed(specs)
    check = checked(fake, w, (f"Message {w[5].pk} and message -1.",))
    assert check.note_texts == ["Message 5 and message 6."]


def test_master_output_is_not_rewritten(fake):
    w = skewed()
    cited = w[2].pk
    explanation = f"message {cited} and message -1 and messages {w[1].pk}, {cited}."
    master = prk.master_d(prk.issue_d("i1", pk.PLACEHOLDER, "unsupported_claim", pk.QUOTE, explanation=explanation))
    act = prk.act_d(f"Is message {cited} or message -1 sourced?", issues=["i1"], messages=[pk.PLACEHOLDER],
                    addressee="all", subject="none")
    fake(master, prk.interv_d(dispositions=[prk.disp_d("i1")], acts=[act], rationale=f"Because of message {cited}."))
    check = pk.check(w, "B")[1]
    assert check.note_texts == ["Is message 2 or message 4 sourced?"]
    assert check.master_output["issues"][0]["explanation"] == explanation
    assert check.intervenor_output["rationale"] == f"Because of message {cited}."


def test_structured_ids_are_not_renumbered(fake):
    w = skewed()
    check = checked(fake, w, (f"Message {w[2].pk}?",))
    assert check.intervenor_output["acts"][0]["source_message_ids"] == [pk.PLACEHOLDER]
    assert check.master_output["issues"][0]["message_id"] == pk.PLACEHOLDER


def test_a_rejected_act_in_the_stored_output_is_also_renumbered(fake, tune):
    tune(MAX_ACTS_PER_INTERVENTION=3)
    w = skewed()
    check = checked(fake, w, (f"Participant B, cite message {w[2].pk}.", f"Is message {w[2].pk} sourced?"))
    assert check.note_texts == ["Is message 2 sourced?"]
    assert [a["text"] for a in check.intervenor_output["acts"]] == [f"Participant B, cite message 2.",
                                                                  "Is message 2 sourced?"]


def test_the_agree_disagree_act_is_still_dropped(fake, tune):
    tune(MAX_ACTS_PER_INTERVENTION=3)
    w = skewed()
    master = prk.master_d(prk.issue_d("i1", pk.PLACEHOLDER, "unsupported_claim", pk.QUOTE))
    acts = [prk.act_d(f"Is message {w[2].pk} sourced?", issues=["i1"], messages=[pk.PLACEHOLDER], addressee="all",
                      subject="none"),
            prk.act_d("Both agree.", type="identify_agreement_disagreement", issues=["i1"], messages=[pk.PLACEHOLDER],
                      addressee="all", subject="both")]
    fake(master, prk.interv_d(dispositions=[prk.disp_d("i1")], acts=acts))
    check = pk.check(w, "B")[1]
    assert check.note_texts == ["Is message 2 sourced?"]


def test_the_agents_still_see_the_ids_unchanged(fake):
    w = skewed()
    client = fake(*pk.concern_script(pk.PLACEHOLDER, texts=("x?",)))
    pk.check(w, "B")
    for agent in ("master", "intervenor"):
        (call,) = prk.calls_of(client, agent)
        assert [i for i, _ in prk.rendered_messages(call)] == [m.pk for m in w.msgs]  # the regex reads positive ids
        assert f'<message id="{pk.PLACEHOLDER}"' in prk.user_input(call)


@pytest.mark.parametrize("given", ["The -1% change needs a source.", "Does message 3-1 cite a source?",
                                   "Does message -10 cite a source?", "The messaged -1 value."])
def test_fix_1_behaviours_still_hold(fake, given):
    w = skewed()
    check = checked(fake, w, (given,))
    assert check.note_texts == [given]


def test_name_the_draft_if_kept_still_names_only_the_draft():
    from moderation import preview

    if not hasattr(preview, "_name_the_draft"):
        pytest.skip("the builder removed the wrapper")
    assert preview._name_the_draft("message #-1 and message 3", 5) == "message #5 and message 3"


# --- a live run that reuses the check ---------------------------------------------------------------------------------------

def test_a_reusing_live_run_posts_the_same_numbers_as_the_check_showed(fake):
    w = skewed()
    text = f"Message {w[2].pk} says one thing and message -1 another."
    check = checked(fake, w, (text,))
    assert check.note_texts == ["Message 2 says one thing and message 4 another."]
    client = fake()
    message, run = pk.post(w, "B", pk.DRAFT)
    _, stored = prk.go(run)
    assert client.calls == []
    assert stored.config_snapshot["preview_check_id"] == check.pk
    assert stored.posted_message.content == check.note_texts[0] == f"Message 2 says one thing and message {message.seq_no} another."
    assert prk.acts_of(stored)[0].text == stored.posted_message.content


def test_reuse_never_renumbers_a_second_time(fake):
    """pk = seq + 1 here: renumbering "message 2" once more would give "message 1"."""
    w = skewed(offset=1)
    check = checked(fake, w, (f"Is message {w[2].pk} sourced?",))  # pk 3 -> seq 2
    assert check.note_texts == ["Is message 2 sourced?"]
    fake()
    message, run = pk.post(w, "B", pk.DRAFT)
    _, stored = prk.go(run)
    assert stored.posted_message.content == "Is message 2 sourced?"


def test_the_reused_run_equals_a_fresh_run_whose_model_cited_the_ids(fake):
    w = skewed()
    cited = w[2].pk
    fake(*pk.concern_script(pk.PLACEHOLDER, texts=(f"Is message {cited} sourced, and message -1?",)))
    pk.check(w, "B")
    fake()
    message, run = pk.post(w, "B", pk.DRAFT)
    _, reused = prk.go(run)
    assert reused.posted_message.content == f"Is message 2 sourced, and message {message.seq_no}?"
