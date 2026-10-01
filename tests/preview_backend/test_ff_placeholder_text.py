"""Front-end fix 1 (docs/frontend_fixes_brief.md): a preview note never says "message -1". `_run_agents` rewrites a reference to the
draft's placeholder id inside every act's text to the draft's number (snapshot_seq + 1) before the notes are derived and before
the Intervenor output is stored, so the concern panel, `note_texts`, the stored `intervenor_output` and a reusing live run all say
"message N". Only "message"/"messages" (any case), optional "#", then -1 as a whole token is rewritten; Master output is left alone."""
import pipeline_run_kit as prk
import preview_kit as pk
import pytest

pytestmark = pytest.mark.django_db

LONG_SPECS = pk.SPECS + [("B", "Rents are capped in many cities and supply there is stable."),
                         ("A", "That is not what the data says about the long run.")]  # 5 messages: the draft is message 6

REWRITES = [
    ("Could a source be given for the claim in message -1?", "Could a source be given for the claim in message {n}?"),
    ("Message -1 gives no source.", "Message {n} gives no source."),
    ("MESSAGE -1 gives no source.", "MESSAGE {n} gives no source."),
    ("mEsSaGe -1 gives no source.", "mEsSaGe {n} gives no source."),
    ("Messages -1 and the earlier ones lack sources.", "Messages {n} and the earlier ones lack sources."),
    ("The claim in message #-1 needs a source.", "The claim in message #{n} needs a source."),
    ("The claim in Message #-1 needs a source.", "The claim in Message #{n} needs a source."),
    ("The claim in messages #-1 needs a source.", "The claim in messages #{n} needs a source."),
    ("What supports message -1, exactly?", "What supports message {n}, exactly?"),
    ("Is the figure in (message -1) sourced?", "Is the figure in (message {n}) sourced?"),
    ("Is it sourced in message -1.", "Is it sourced in message {n}."),
    ("Both message -1 and, later, message -1 lack a source.", "Both message {n} and, later, message {n} lack a source."),
    ("Does message  -1 cite a source?", "Does message  {n} cite a source?"),
]

UNCHANGED = [
    "Could a source be given for the -1% change in the claim?",
    "Does message 3-1 cite a source?",
    "The index fell to -1 over the period; is there a source?",
    "Does message -10 cite a source for the figure?",
    "Does message -12 cite a source for the figure?",
    "The messaged -1 value needs a source.",
    "Is a source available for the premessage -1 value?",
    "Could a source be given for the claim in message 3?",
    "Could a source be given for the claim in message 2 or message 3?",
    "Does the word message alone or -1 alone change anything here?",
    "Is there a source for 1-1 and for -1-1 style figures?",
]


def checked(fake, text, specs=None, who="B"):
    w = pk.world(specs)
    n = len(w.msgs) + 1
    fake(*pk.concern_script(pk.PLACEHOLDER, texts=(text,)))
    check = pk.check(w, who)[1]
    return w, n, check


@pytest.mark.parametrize("given,expected", REWRITES)
def test_the_placeholder_reference_becomes_the_drafts_number_everywhere_it_is_stored(fake, given, expected):
    w, n, check = checked(fake, given)
    assert n == 4
    want = expected.format(n=n)
    assert check.outcome == "concern"
    assert check.note_texts == [want]
    acts = check.intervenor_output["acts"]
    assert [a["text"] for a in acts] == [want]


@pytest.mark.parametrize("given", UNCHANGED)
def test_everything_else_is_left_alone(fake, given):
    w, n, check = checked(fake, given)
    assert check.outcome == "concern", "the note must still be accepted"
    assert check.note_texts == [given]
    assert [a["text"] for a in check.intervenor_output["acts"]] == [given]


@pytest.mark.parametrize("specs", [None, LONG_SPECS], ids=["draft is message 4", "draft is message 6"])
def test_the_number_is_snapshot_seq_plus_one_for_any_conversation_length(fake, specs):
    w, n, check = checked(fake, "Could a source be given for the claim in message -1?", specs)
    assert n == check.snapshot_seq + 1
    assert check.note_texts == [f"Could a source be given for the claim in message {n}?"]


def test_the_rewrite_is_per_act_for_every_act(fake, tune):
    tune(MAX_ACTS_PER_INTERVENTION=3)
    w = pk.world()
    fake(*pk.concern_script(pk.PLACEHOLDER, texts=("Is message -1 sourced?", "Does message #-1 cite a figure?")))
    check = pk.check(w, "B")[1]
    assert check.note_texts == ["Is message 4 sourced?", "Does message #4 cite a figure?"]
    assert [a["text"] for a in check.intervenor_output["acts"]] == check.note_texts


def test_the_rewrite_reaches_the_concern_panel_json_of_the_check_view(fake):
    """What the composer shows is `note_texts`; make sure no other field of the stored check still says -1 for the draft."""
    w, n, check = checked(fake, "Is the claim in message -1 sourced?")
    assert "message -1" not in str(check.note_texts) and "message -1" not in str(check.intervenor_output)


def test_a_note_without_a_reference_is_identical_to_before(fake):
    w, n, check = checked(fake, pk.NOTE)
    assert check.note_texts == [pk.NOTE] and check.intervenor_output["acts"][0]["text"] == pk.NOTE


def test_master_output_is_never_rewritten(fake):
    w = pk.world()
    master = prk.master_d(prk.issue_d("i1", pk.PLACEHOLDER, "unsupported_claim", pk.QUOTE,
                                      explanation="message -1 states a claim and Message #-1 lacks a source."))
    acts = [prk.act_d("Is message -1 sourced?", issues=["i1"], messages=[pk.PLACEHOLDER], addressee="all", subject="none")]
    fake(master, prk.interv_d(dispositions=[prk.disp_d("i1")], acts=acts))
    check = pk.check(w, "B")[1]
    assert check.note_texts == ["Is message 4 sourced?"]
    explanation = check.master_output["issues"][0]["explanation"]
    assert explanation == "message -1 states a claim and Message #-1 lacks a source."


def test_structured_ids_still_carry_the_placeholder_for_reuse(fake):
    """Only the text changes: source_message_ids keep -1 so that the reuse can remap them to the real message id."""
    w, n, check = checked(fake, "Is message -1 sourced?")
    assert check.intervenor_output["acts"][0]["source_message_ids"] == [pk.PLACEHOLDER]
    assert check.master_output["issues"][0]["message_id"] == pk.PLACEHOLDER


def test_the_rewrite_does_not_change_the_number_of_model_calls_or_the_outcome(fake):
    w = pk.world()
    client = fake(*pk.concern_script(pk.PLACEHOLDER, texts=("Is message -1 sourced?",)))
    check = pk.check(w, "B")[1]
    assert len(client.calls) == 2 and check.outcome == "concern"


def test_a_declined_check_and_a_quiet_check_are_unaffected(fake):
    w = pk.world()
    fake(*pk.declined_script())
    assert pk.check(w, "B")[1].outcome == "no_concern"
    w2 = pk.world()
    fake(pk.quiet_master())
    assert pk.check(w2, "B")[1].outcome == "no_concern"


def test_the_agree_disagree_act_is_still_dropped_and_the_others_rewritten(fake, tune):
    tune(MAX_ACTS_PER_INTERVENTION=3)
    w = pk.world()
    master = prk.master_d(prk.issue_d("i1", pk.PLACEHOLDER, "unsupported_claim", pk.QUOTE))
    acts = [
        prk.act_d("Is message -1 sourced?", issues=["i1"], messages=[pk.PLACEHOLDER], addressee="all", subject="none"),
        prk.act_d("Both agree on message -1.", type="identify_agreement_disagreement", issues=["i1"],
                  messages=[pk.PLACEHOLDER], addressee="all", subject="both"),
    ]
    fake(master, prk.interv_d(dispositions=[prk.disp_d("i1")], acts=acts))
    check = pk.check(w, "B")[1]
    assert check.note_texts == ["Is message 4 sourced?"]
    assert [a["text"] for a in check.intervenor_output["acts"]] == ["Is message 4 sourced?"]


# --- reuse by the live run -------------------------------------------------------------------------------------------------------

def _offset_ids():
    """Create other conversations first so that message primary keys differ from seq_no in the next world."""
    pk.world()
    pk.world()


@pytest.mark.parametrize("specs", [None, LONG_SPECS], ids=["draft 4", "draft 6"])
def test_a_reusing_live_run_posts_message_n_with_n_the_real_seq_no(fake, specs):
    _offset_ids()
    w = pk.world(specs)
    fake(*pk.concern_script(pk.PLACEHOLDER, texts=("Could a source be given for the claim in message -1?",)))
    check = pk.check(w, "B")[1]
    client = fake()  # no model call may happen on reuse
    message, run = pk.post(w, "B", pk.DRAFT)
    _, stored = prk.go(run)
    assert client.calls == []
    assert stored.config_snapshot["preview_check_id"] == check.pk
    posted = stored.posted_message
    assert posted.content == f"Could a source be given for the claim in message {message.seq_no}?"
    assert message.seq_no == check.snapshot_seq + 1 and message.pk != message.seq_no
    assert "-1" not in posted.content
    (act,) = prk.acts_of(stored)
    assert act.text == posted.content
    assert [m.pk for m in act.source_messages.all()] == [message.pk]


def test_the_reused_run_equals_a_fresh_run_whose_model_wrote_the_real_number(fake):
    w = pk.world()
    fake(*pk.concern_script(pk.PLACEHOLDER, texts=("Is the claim in message -1 sourced?",)))
    pk.check(w, "B")
    fake()
    message, run = pk.post(w, "B", pk.DRAFT)
    _, reused = prk.go(run)

    def factory(world, draft_id):
        return pk.concern_script(draft_id, texts=(f"Is the claim in message {world.last.seq_no} sourced?",))

    _, _, fresh = pk.run_live(fake, factory)
    assert pk.fingerprint(reused) == pk.fingerprint(fresh)


def test_a_reused_note_with_hash_form_keeps_the_hash(fake):
    w = pk.world()
    fake(*pk.concern_script(pk.PLACEHOLDER, texts=("The claim in message #-1 needs a source.",)))
    pk.check(w, "B")
    fake()
    message, run = pk.post(w, "B", pk.DRAFT)
    _, stored = prk.go(run)
    assert stored.posted_message.content == f"The claim in message #{message.seq_no} needs a source."


def test_a_note_that_never_mentioned_the_placeholder_is_posted_unchanged_on_reuse(fake):
    w = pk.world()
    fake(*pk.concern_script(pk.PLACEHOLDER, texts=(pk.NOTE,)))
    pk.check(w, "B")
    fake()
    message, run = pk.post(w, "B", pk.DRAFT)
    _, stored = prk.go(run)
    assert stored.posted_message.content == pk.NOTE


def test_every_stored_act_is_rewritten_including_one_the_validator_rejects(fake, tune):
    tune(MAX_ACTS_PER_INTERVENTION=3)
    w = pk.world()
    fake(*pk.concern_script(pk.PLACEHOLDER, texts=("Participant B, please cite a source for message -1.", "Is message -1 sourced?")))
    check = pk.check(w, "B")[1]
    assert check.note_texts == ["Is message 4 sourced?"]
    stored = [a["text"] for a in check.intervenor_output["acts"]]
    assert stored == ["Participant B, please cite a source for message 4.", "Is message 4 sourced?"]
