"""evaluation.blinding.blinded_view: only the proposition, the message text and minimal context (earlier messages'
text, labelled only "earlier message"). Every forbidden item is planted with a recognisable sentinel and the returned
structure is walked (keys and values, however deep) for it. Reading chosen where the brief is silent: the topic title,
description, ids and the planted ground truth are not on the allowed list, so they must not appear either; an act
target's view carries the act's own text (that is what is rated) but nothing else the moderator produced."""
import json

import evalmodels_testkit as kit
import pytest

from evaluation.blinding import blinded_view

pytestmark = pytest.mark.django_db

PROPOSITION = "Cities should ban private cars downtown. PROPOSITION_SENTINEL"
M1 = "Traffic downtown is heavy every morning and it hurts small shops."
M2 = "Shops also benefit from delivery trucks that need road access."
M3 = "Banning cars would only move the congestion to the side streets."
LATER = "LATER_MESSAGE_SENTINEL text that nobody had written yet."
MOD_POST = "Could you point to a source for the figure you quoted? MODPOST_SENTINEL"
ACT_TEXT = "ACT_TEXT_SENTINEL Please share the source of that number."

# recognisable values that must never reach a rater
USER_A, USER_B = "ZuluAlice91", "KiloBob77"
FORBIDDEN_ALWAYS = [
    USER_A, USER_B, f"{USER_A}@example.com", f"{USER_B}@example.com", "@example.com",
    "TITLE_SENTINEL", "DESCRIPTION_SENTINEL", "LEANS_SENTINEL", "LEANS_RATIONALE_SENTINEL",
    "EXPERIMENT_NAME_SENTINEL", "EXPERIMENT_DESC_SENTINEL", "EXPERIMENT_CONFIG_SENTINEL",
    "VARIANT_SENTINEL", "PAIR_ID_SENTINEL", "TRANSCRIPT_ID_SENTINEL", "987654321",
    "ISSUE_EXPLANATION_SENTINEL", "REJECTION_SENTINEL", "RATIONALE_SENTINEL", "CONFIG_SNAPSHOT_SENTINEL",
    "DISCUSSION_MAP_SENTINEL", "DISPOSITION_SENTINEL", "RATER_NAME_SENTINEL", "FINDING_DETAIL_SENTINEL",
    "ANNOTATION_SENTINEL", "CONSENSUS_SENTINEL",
    "participant", "unsupported_claim", "no_intervention", "intervene",
    "planted", "abusiveness", "factual_accuracy", "PLANTED_PHRASE_SENTINEL", LATER,
]  # fmt: skip
FORBIDDEN_MODERATOR_OUTPUT = ["request_information", "correct_factual_error", "firm", "gentle", "enforce_conduct"]


def walk(node):
    """Every key and every scalar of a nested structure, as lower-case strings."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key).lower()
            yield from walk(value)
    elif isinstance(node, (list, tuple, set, frozenset)):
        for item in node:
            yield from walk(item)
    else:
        yield str(node).lower()


def _values(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _values(value)
    elif isinstance(node, (list, tuple, set, frozenset)):
        for item in node:
            yield from _values(item)
    else:
        yield node


def says_automated(view):
    """Some VALUE (not just a key name) says the text is an automated reply: True, or a string that says so."""
    return any(v is True or (isinstance(v, str) and "automated" in v.lower()) for v in _values(view))


def flat(view):
    return "\n".join(walk(view))


def dump(view):
    return json.dumps(view, sort_keys=True, default=str)


def assert_no(view, forbidden):
    text = flat(view)
    leaked = [item for item in forbidden if item.lower() in text]
    assert leaked == [], f"leaked into the blinded view: {leaked}"


class World:
    pass


@pytest.fixture
def world():
    from forum.models import Experiment, Topic

    w = World()
    w.alice, w.bob = kit.make_user(USER_A), kit.make_user(USER_B)
    topic = Topic.objects.create(
        title="TITLE_SENTINEL Cars downtown", description="DESCRIPTION_SENTINEL about cars", proposition=PROPOSITION,
        leans={"left": {"pro": {"LEANS_SENTINEL": "LEANS_RATIONALE_SENTINEL"}}},
    )  # fmt: skip
    experiment = Experiment.objects.create(
        name="EXPERIMENT_NAME_SENTINEL", kind="paired", description="EXPERIMENT_DESC_SENTINEL", config={"k": "EXPERIMENT_CONFIG_SENTINEL"}
    )  # fmt: skip
    from forum.models import Conversation, Message, Participant

    w.conv = Conversation.objects.create(
        topic=topic, source="human", experiment=experiment, pair_id="PAIR_ID_SENTINEL", variant="VARIANT_SENTINEL",
        transcript_id="TRANSCRIPT_ID_SENTINEL", label_seed=987654321,
    )  # fmt: skip
    w.pa = Participant.objects.create(conversation=w.conv, user=w.alice, label="A", join_order=1)
    w.pb = Participant.objects.create(conversation=w.conv, user=w.bob, label="B", join_order=2)
    w.m1 = Message.objects.create(conversation=w.conv, author_type="user", participant=w.pa, content=M1)
    w.m2 = Message.objects.create(conversation=w.conv, author_type="user", participant=w.pb, content=M2, in_reply_to=w.m1)
    w.m3 = Message.objects.create(
        conversation=w.conv, author_type="user", participant=w.pa, content=M3, in_reply_to=w.m2,
        planted=[{"dimension": "abusiveness", "phrase": "PLANTED_PHRASE_SENTINEL", "intensity": 4}],
    )  # fmt: skip
    w.mod = Message.objects.create(conversation=w.conv, author_type="moderator", participant=None, content=MOD_POST, in_reply_to=w.m3)
    w.later = Message.objects.create(conversation=w.conv, author_type="user", participant=w.pb, content=LATER, in_reply_to=w.m3)
    return w


def add_moderation_and_rater_output(w):
    """Everything a rater must never see: the run, its issues, acts, decision, dispositions, other raters' work."""
    from evaluation.models import Annotation

    run = kit.make_run(
        w.m3, decision="intervene", rationale="RATIONALE_SENTINEL", config_snapshot={"x": "CONFIG_SNAPSHOT_SENTINEL"},
        discussion_map={"y": "DISCUSSION_MAP_SENTINEL"}, posted_message=w.mod, status="done",
    )  # fmt: skip
    issue = kit.make_issue(
        run, w.m3, explanation="ISSUE_EXPLANATION_SENTINEL", validity="rejected", rejection_reason="REJECTION_SENTINEL"
    )
    from moderation.models import IssueDisposition

    issue2 = kit.make_issue(run, w.m3, issue_type="abusive_language", explanation="ISSUE_EXPLANATION_SENTINEL")
    IssueDisposition.objects.create(issue=issue2, disposition="acted", reason="DISPOSITION_SENTINEL")
    act = kit.make_act(run, text=ACT_TEXT, act_type="request_information", tone="firm")
    other = kit.make_rater("llm", name="RATER_NAME_SENTINEL")
    rating = kit.make_rating(other, w.m3)
    kit.make_finding(rating, 0, 7, detail={"note": "FINDING_DETAIL_SENTINEL"})
    Annotation.objects.create(target_type="message", target_id=w.m3.pk, dimension="stance", value="ANNOTATION_SENTINEL", source="rater:RATER_NAME_SENTINEL", rating=rating)
    panel = kit.make_panel([other])
    kit.make_consensus(panel, w.m3, dimension="factual_accuracy", start=0, end=7)
    return act


# --- a user message ----------------------------------------------------------------------------------------------------
def test_the_view_is_a_dict_and_carries_proposition_message_and_the_preceding_message(world):
    view = blinded_view(world.m3)
    assert isinstance(view, dict)
    text = flat(view)
    assert PROPOSITION.lower() in text
    assert M3.lower() in text
    assert M2.lower() in text


def test_the_preceding_messages_are_labelled_earlier_message(world):
    view = blinded_view(world.m3)
    assert "earlier message" in flat(view)


def test_the_view_leaks_nothing_forbidden(world):
    add_moderation_and_rater_output(world)
    assert_no(blinded_view(world.m3), FORBIDDEN_ALWAYS + FORBIDDEN_MODERATOR_OUTPUT + [ACT_TEXT, MOD_POST])


def test_the_view_leaks_nothing_forbidden_even_before_any_moderation_exists(world):
    assert_no(blinded_view(world.m3), FORBIDDEN_ALWAYS + FORBIDDEN_MODERATOR_OUTPUT)


@pytest.mark.parametrize("which", ["m1", "m2"])
def test_earlier_user_messages_are_blinded_too(world, which):
    add_moderation_and_rater_output(world)
    view = blinded_view(getattr(world, which))
    assert_no(view, FORBIDDEN_ALWAYS + FORBIDDEN_MODERATOR_OUTPUT + [ACT_TEXT, MOD_POST])
    assert PROPOSITION.lower() in flat(view)


def test_only_preceding_messages_appear_never_later_ones(world):
    text = flat(blinded_view(world.m2))
    assert M1.lower() in text
    assert M3.lower() not in text
    assert LATER.lower() not in text and MOD_POST.lower() not in text


def test_the_first_message_has_no_context(world):
    text = flat(blinded_view(world.m1))
    assert M1.lower() in text
    assert M2.lower() not in text and M3.lower() not in text and "earlier message" not in text


def test_a_user_messages_view_does_not_say_it_is_automated(world):
    """Keys are allowed to name the flag (automated_reply: False); no VALUE may claim the message is automated."""
    view = blinded_view(world.m3)
    assert not says_automated(view)


def test_an_earlier_moderator_message_is_never_part_of_the_context(world):
    """The moderator's posted text is moderator output; the later user message sits after it in the conversation."""
    text = flat(blinded_view(world.later))
    assert MOD_POST.lower() not in text and "modpost_sentinel" not in text
    assert M3.lower() in text


def test_the_view_is_deterministic(world):
    assert dump(blinded_view(world.m3)) == dump(blinded_view(world.m3))
    assert blinded_view(world.m3) == blinded_view(world.m3)


def test_moderation_and_rater_output_added_later_does_not_change_the_view(world):
    before = {name: dump(blinded_view(getattr(world, name))) for name in ("m1", "m2", "m3", "mod")}
    add_moderation_and_rater_output(world)
    after = {name: dump(blinded_view(getattr(world, name))) for name in ("m1", "m2", "m3", "mod")}
    assert before == after


def test_a_later_message_does_not_change_an_earlier_messages_view(world):
    from forum.models import Message

    before = dump(blinded_view(world.m2))
    Message.objects.create(conversation=world.conv, author_type="user", participant=world.pa, content="one more message, written afterwards")
    assert dump(blinded_view(world.m2)) == before


def test_the_view_depends_only_on_the_conversation_text_not_on_who_wrote_it(world):
    """Same words under other users, another topic title, no experiment: the view is the same."""
    from forum.models import Conversation, Message, Participant, Topic

    other_topic = Topic.objects.create(title="a different title", proposition=PROPOSITION, leans={"other": "shape"})
    other = Conversation.objects.create(topic=other_topic, source="synthetic")
    pa = Participant.objects.create(conversation=other, label="A", join_order=1)
    pb = Participant.objects.create(conversation=other, label="B", join_order=2)
    n1 = Message.objects.create(conversation=other, author_type="user", participant=pb, content=M1)
    n2 = Message.objects.create(conversation=other, author_type="user", participant=pa, content=M2, in_reply_to=n1)
    n3 = Message.objects.create(conversation=other, author_type="user", participant=pb, content=M3, in_reply_to=n2)
    assert dump(blinded_view(n3)) == dump(blinded_view(world.m3))


def test_other_users_and_conversations_never_appear(world):
    from forum.models import Conversation, Message, Participant, Topic

    other = Conversation.objects.create(topic=Topic.objects.create(title="elsewhere", proposition="OTHER_PROPOSITION_SENTINEL"), source="synthetic")
    pa = Participant.objects.create(conversation=other, label="A", join_order=1)
    Message.objects.create(conversation=other, author_type="user", participant=pa, content="OTHER_CONVERSATION_SENTINEL says hello")
    text = flat(blinded_view(world.m3))
    assert "other_proposition_sentinel" not in text and "other_conversation_sentinel" not in text


# --- a moderator message -----------------------------------------------------------------------------------------------
def test_a_moderator_messages_view_says_it_is_an_automated_reply(world):
    assert says_automated(blinded_view(world.mod))


def test_a_moderator_messages_view_says_only_that_it_is_automated_and_withholds_its_words(world):
    """Reading chosen: 'the view says only that it is an automated reply' means the moderator's words are not shown
    (a rater rates them through the intervention act target); the proposition and the user context remain."""
    text = flat(blinded_view(world.mod))
    assert MOD_POST.lower() not in text and "modpost_sentinel" not in text
    assert PROPOSITION.lower() in text


def test_a_moderator_messages_view_has_no_moderator_output_beyond_the_message(world):
    add_moderation_and_rater_output(world)
    view = blinded_view(world.mod)
    assert_no(view, FORBIDDEN_ALWAYS + FORBIDDEN_MODERATOR_OUTPUT + [ACT_TEXT, MOD_POST])


def test_a_moderator_messages_view_names_no_issue_type_dimension_tone_or_decision(world):
    add_moderation_and_rater_output(world)
    text = flat(blinded_view(world.mod))
    for word in ("issue", "decision", "tone", "act_type", "rationale", "disposition", "run"):
        assert word not in text.replace("automated reply", "").split(), word


# --- an intervention act -----------------------------------------------------------------------------------------------
def test_an_acts_view_carries_the_acts_text_and_the_proposition(world):
    act = add_moderation_and_rater_output(world)
    text = flat(blinded_view(act))
    assert ACT_TEXT.lower() in text and PROPOSITION.lower() in text
    assert says_automated(blinded_view(act))


def test_an_acts_view_leaks_nothing_else(world):
    act = add_moderation_and_rater_output(world)
    assert_no(blinded_view(act), FORBIDDEN_ALWAYS + FORBIDDEN_MODERATOR_OUTPUT + [MOD_POST])


def test_an_acts_view_is_deterministic(world):
    act = add_moderation_and_rater_output(world)
    assert dump(blinded_view(act)) == dump(blinded_view(act))


def test_the_message_being_rated_appears_once_not_again_as_context(world):
    assert flat(blinded_view(world.m3)).count(M3.lower()) == 1


def test_context_is_minimal_and_ends_with_the_immediately_preceding_message():
    from forum.models import Message

    conv, parts = kit.make_conversation()
    messages = [kit.user_msg(conv, parts, "AB"[i % 2], f"CONTEXT_MESSAGE_{i:03d} says something") for i in range(60)]
    target = kit.user_msg(conv, parts, "A", "The message that is being rated now.")
    text = flat(blinded_view(target))
    assert messages[-1].content.lower() in text
    assert messages[0].content.lower() not in text
    assert text.count("earlier message") < 60 and Message.objects.count() == 61


def test_an_acts_context_includes_the_message_that_triggered_it_but_nothing_after(world):
    act = add_moderation_and_rater_output(world)
    text = flat(blinded_view(act))
    assert M3.lower() in text and LATER.lower() not in text
