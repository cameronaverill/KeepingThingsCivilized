"""Step 7c: the AI moderator gets only the neutral claim (Topic.proposition): never a side, an opposing position, a label
meaning or a role. Checked on what the pipeline actually hands to the (fake) model, for conversations made through the
services (people who chose sides, a conversation still waiting, a seeded topic that carries both wordings)."""
import re

import pytest

from fsvc_testkit import make_user, svc, uniq

pytestmark = pytest.mark.django_db

OPPOSING = "OPPOSINGWORDING zebra crossings should be removed everywhere"
CLAIM = "Zebra crossings should be kept everywhere"


@pytest.fixture(autouse=True)
def _llm(settings, monkeypatch, clock):
    from moderation import llm

    settings.LLM_ENABLED = True
    settings.ANTHROPIC_API_KEY = "moderator-input-dummy-key"  # secret-scan: allow
    settings.LLM_FORBID_ATOMIC_CALLS = False

    def blocked(*args, **kwargs):
        raise AssertionError("a real Anthropic client was built in a test")

    monkeypatch.setattr(llm, "_build_real_client", blocked)
    llm.reset_client()
    yield
    llm.reset_client()


def seeded_topic():
    from forum.models import Topic

    return Topic.objects.create(title=uniq("Crossings"), proposition=CLAIM, opposing_position=OPPOSING)


def run_moderator(run):
    """Run the pipeline with a FakeLLM that finds nothing to say; return (fake, requests as one string, user inputs)."""
    from moderation import llm
    from moderation.fake_llm import FakeLLM
    from moderation.pipeline import run_moderation

    client = FakeLLM([{"issues": [], "discussion_map": {"agreements": [], "disagreements": []}}] * 3)
    llm.set_client(client)
    run_moderation(run)
    assert client.calls, "the pipeline made no model call"
    inputs = [c["messages"][0]["content"] for c in client.calls]
    return client, inputs


def pending_run(conv):
    from moderation.models import ModerationRun

    return ModerationRun.objects.filter(conversation=conv, status="pending").order_by("-id").first()


def paired(topic):
    first, second = make_user(), make_user()
    conv = svc().enter_proposition(first, topic, "pro")
    svc().enter_proposition(second, topic, "con")
    return conv, first, second


# The words a prompt could use to tell the model which side someone is on, or what their role is.
SIDE_WORDS = re.compile(r"\b(pro|con|side|sides|stance|role|opposing|opposite|opponent|proponent|disagree[sd]?|disagreement with this position)\b", re.I)


def test_the_moderator_input_contains_the_claim_and_never_the_opposing_wording(clock):
    conv, first, second = paired(seeded_topic())
    svc().post_message(first, conv, "crossings keep walkers safe on busy roads")
    clock.advance(5)
    svc().post_message(second, conv, "walkers are safer with lights and bridges instead")
    _, inputs = run_moderator(pending_run(conv))
    for text in inputs:
        assert CLAIM in text
        assert "OPPOSINGWORDING" not in text and OPPOSING not in text


def test_no_side_or_role_wording_appears_in_what_is_rendered_from_the_conversation(clock):
    conv, first, second = paired(seeded_topic())
    svc().post_message(first, conv, "crossings keep walkers safe on busy roads")
    clock.advance(5)
    svc().post_message(second, conv, "walkers are safer with lights and bridges instead")
    _, inputs = run_moderator(pending_run(conv))
    for text in inputs:
        assert not SIDE_WORDS.search(text), SIDE_WORDS.search(text).group(0)
        assert "My position is that" not in text
        assert "I disagree" not in text


def test_the_labels_seen_by_the_moderator_are_the_neutral_participant_labels_not_sides(clock):
    conv, first, second = paired(seeded_topic())
    svc().post_message(first, conv, "crossings keep walkers safe on busy roads")
    _, inputs = run_moderator(pending_run(conv))
    rendered = re.findall(r'participant="([^"]*)"', inputs[0])
    assert rendered and set(rendered) <= {"Participant A", "Participant B"}


def test_a_conversation_still_waiting_gives_the_moderator_the_same_kind_of_input(clock):
    topic = seeded_topic()
    user = make_user()
    conv = svc().enter_proposition(user, topic, "con")  # the con holder waits
    svc().post_message(user, conv, "crossings are a waste of paint and time")
    _, inputs = run_moderator(pending_run(conv))
    for text in inputs:
        assert CLAIM in text and OPPOSING not in text
        assert not SIDE_WORDS.search(text), SIDE_WORDS.search(text).group(0)


def test_a_user_created_claim_is_sent_as_stored_with_no_side_wording(clock):
    topic = svc().create_proposition(make_user(), "my position is that trains should be free")
    first, second = make_user(), make_user()
    conv = svc().enter_proposition(first, topic, "pro")
    svc().enter_proposition(second, topic, "con")
    svc().post_message(first, conv, "free trains would cut traffic in the city")
    _, inputs = run_moderator(pending_run(conv))
    for text in inputs:
        assert "Trains should be free" in text
        assert "My position is that" not in text
        assert not SIDE_WORDS.search(text), SIDE_WORDS.search(text).group(0)


def test_the_moderator_input_is_the_same_whichever_side_the_first_speaker_holds(clock):
    """Swap who is pro and who is con with identical texts: the rendered inputs must be identical (labels are random,
    so compare after replacing the two labels by placeholders)."""
    texts = []
    for first_side, second_side in (("pro", "con"), ("con", "pro")):
        topic = seeded_topic()
        first, second = make_user(), make_user()
        conv = svc().enter_proposition(first, topic, first_side)
        svc().enter_proposition(second, topic, second_side)
        svc().post_message(first, conv, "crossings keep walkers safe on busy roads")
        run = pending_run(conv)
        _, inputs = run_moderator(run)
        label = conv.participants.get(user=first).label
        normal = re.sub(r'id="\d+"', 'id="N"', inputs[0])
        normal = re.sub(r"<title>.*?</title>", "<title>T</title>", normal)
        normal = normal.replace(f"Participant {label}", "Participant X")
        texts.append(re.sub(r"Participant [AB]", "Participant Y", normal))
    assert texts[0] == texts[1]


def test_the_topic_block_holds_only_the_claim_and_its_title(clock):
    conv, first, _ = paired(seeded_topic())
    svc().post_message(first, conv, "crossings keep walkers safe on busy roads")
    _, inputs = run_moderator(pending_run(conv))
    block = re.search(r"<topic>.*?</topic>", inputs[0], re.S).group(0)
    assert CLAIM in block
    assert "OPPOSING" not in block


# --- step 7c revision 5: usernames, blocks and sides never reach the moderation code --------------------------------------------------------

NAME_A, NAME_B, NAME_C = "zebulonquist", "quillfeather", "marigoldvane"


def named(name):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(username=name, email=f"{name}@mailbox.example")


def master_and_intervenor_scripts(message):
    quote = "cross wherever they like"
    return [
        {
            "issues": [
                {
                    "id": "i1", "message_id": message.pk, "issue_type": "unsupported_claim", "quote": quote,
                    "explanation": "The claim is stated without support.", "confidence": 0.8, "intensity": None,
                    "time_sensitive": False,
                }
            ],
            "discussion_map": {"agreements": [], "disagreements": []},
        },
        {
            "decision": "intervene",
            "rationale": "A source would help both readers.",
            "issue_dispositions": [{"issue_id": "i1", "disposition": "acted", "reason": "It matters here."}],
            "acts": [
                {
                    "type": "request_information", "addressee": "all", "subject": "none", "source_issue_ids": ["i1"],
                    "source_message_ids": [message.pk], "tone": "neutral", "text": "Could a source be given for that claim?",
                }
            ],
        },
    ]


def full_run(first, second, topic, extra_setup=None):
    """Two named people talk; the first says something the master flags; returns (client, run, conversation)."""
    from moderation import llm
    from moderation.fake_llm import FakeLLM
    from moderation.pipeline import run_moderation

    conv = svc().enter_proposition(first, topic, "pro")
    svc().enter_proposition(second, topic, "con")
    svc().post_message(second, conv, "pedestrians are safer with lights and bridges instead")
    message = svc().post_message(first, conv, "people should cross wherever they like on any road")
    if extra_setup:
        extra_setup(conv)
    client = FakeLLM(master_and_intervenor_scripts(message))
    llm.set_client(client)
    run = pending_run(conv)
    run_moderation(run)
    return client, run, conv


def whole_request_text(client):
    import json

    return json.dumps([{"system": c.get("system"), "messages": c.get("messages")} for c in client.calls], default=str)


def ledger_text():
    import json

    from moderation.models import LLMCall

    return json.dumps([[r.request, r.raw_response, r.parsed, r.error] for r in LLMCall.objects.all()], default=str)


LEAKS = (NAME_A, NAME_B, NAME_C, f"{NAME_A}@", f"{NAME_B}@", "mailbox.example")


def test_no_username_or_email_reaches_a_prompt_or_the_ledger_even_with_blocks_and_sides(clock):
    first, second, third = named(NAME_A), named(NAME_B), named(NAME_C)
    svc().block_user(third, first)  # blocks exist around the conversation
    svc().block_user(second, third)
    topic = seeded_topic()
    client, run, conv = full_run(first, second, topic)
    assert len(client.calls) == 2  # master and intervenor both ran
    sent = whole_request_text(client)
    ledger = ledger_text()
    for leak in LEAKS:
        assert leak not in sent, leak
        assert leak not in ledger, leak


def test_block_words_and_side_words_are_never_in_what_the_moderator_receives(clock):
    first, second = named(NAME_A), named(NAME_B)
    topic = seeded_topic()
    client, run, conv = full_run(first, second, topic)
    for call in client.calls:
        user_input = call["messages"][0]["content"]
        assert not re.search(r"\b(block|blocked|blocks|blocking|unblock)\b", user_input, re.I)
        assert not SIDE_WORDS.search(user_input), SIDE_WORDS.search(user_input).group(0)
        assert OPPOSING not in user_input


def test_a_block_made_after_the_message_changes_nothing_in_the_moderator_input(clock):
    """Same conversation, run once with and once without a later block: the rendered inputs are identical."""
    inputs = []
    for do_block in (False, True):
        first, second = named(f"{NAME_A}{int(do_block)}"), named(f"{NAME_B}{int(do_block)}")
        topic = seeded_topic()

        def setup(conv, first=first, second=second, do_block=do_block):
            if do_block:
                svc().block_user(first, second)

        client, run, conv = full_run(first, second, topic, setup)
        text = client.calls[0]["messages"][0]["content"]
        label = conv.participants.get(user=first).label
        text = re.sub(r'id="\d+"', 'id="N"', text)
        text = re.sub(r"<title>.*?</title>", "<title>T</title>", text)
        text = re.sub(r"Participant [AB]", "Participant X", text)
        inputs.append(text)
        assert label in ("A", "B")
    assert inputs[0] == inputs[1]


def test_the_moderators_posted_message_and_stored_rows_carry_no_username(clock):
    from forum.models import Message
    from moderation.models import ModerationRun

    first, second = named(NAME_A), named(NAME_B)
    client, run, conv = full_run(first, second, seeded_topic())
    run = ModerationRun.objects.get(pk=run.pk)
    assert run.status == "done"
    posted = Message.objects.get(pk=run.posted_message_id)
    import json

    stored = json.dumps([posted.content, run.rationale, run.error, run.config_snapshot, run.discussion_map], default=str)
    for leak in LEAKS:
        assert leak not in stored, leak


def test_the_page_heading_is_worked_out_in_the_view_not_in_the_stored_moderator_message(clock):
    """The moderator's text never contains the name; the heading names people only in conversation_view."""
    from forum.models import Message
    from forum.viewmodels import conversation_view
    from moderation.models import ModerationRun

    first, second = named(NAME_A), named(NAME_B)
    client, run, conv = full_run(first, second, seeded_topic())
    posted = Message.objects.get(pk=ModerationRun.objects.get(pk=run.pk).posted_message_id)
    assert NAME_A not in posted.content and NAME_B not in posted.content
    result = conversation_view(first, conv)
    (post,) = [m for m in result["messages"] if m["kind"] == "moderator"]
    assert "heading" in post
