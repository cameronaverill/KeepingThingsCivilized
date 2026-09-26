"""What the agents send: the rendered input, privacy, the topic, no truncation and no database writes."""
import copy
import json
import re

import pytest

import pipeline_agents_kit as kit

pytestmark = pytest.mark.usefixtures("llm_ready")


def _tuples(transcript):
    return [(m["id"], m["label"], m["text"]) for m in transcript]


def _sent_user_text(fake, index=0):
    return fake.calls[index]["messages"][0]["content"]


def _master_candidates(transcript, topic, **kwargs):
    """The acceptable exact inputs: the topic title is optional ("when present"); everything else is fixed."""
    from moderation import prompting

    return {
        prompting.render_master_input(_tuples(transcript), topic_title=title, proposition=topic.proposition, **kwargs)
        for title in (topic.title, None)
    }


def _intervenor_candidates(transcript, topic, issues, discussion_map):
    """Acceptable exact inputs: title optional; the issue id shown is the stored issue's pk or its local_id."""
    from moderation import prompting

    as_dicts = [
        {
            "id": i.local_id, "message_id": i.message_id, "issue_type": i.issue_type, "quote": i.quote,
            "explanation": i.explanation, "confidence": i.confidence, "intensity": i.intensity,
        }
        for i in issues
    ]  # fmt: skip
    out = set()
    for title in (topic.title, None):
        for shown in (list(issues), as_dicts):
            out.add(
                prompting.render_intervenor_input(
                    _tuples(transcript), shown, topic_title=title, proposition=topic.proposition, discussion_map=discussion_map
                )
            )
    return out


# --- The Master's input ---------------------------------------------------------------------------------------------

def test_master_input_is_the_rendered_transcript_and_proposition(install_fake):
    sc = kit.make_human_scenario()
    fake = install_fake(kit.master_out())
    kit.agents().call_master(sc.run, sc.transcript, topic=sc.topic)
    sent = _sent_user_text(fake)
    assert sent in _master_candidates(sc.transcript, sc.topic)
    # spelled out: ids, labels, text and the proposition
    for m in sc.transcript:
        assert f'id="{m["id"]}"' in sent
    assert 'participant="Participant A"' in sent and 'participant="Participant B"' in sent
    assert 'participant="Moderator"' in sent
    assert "Cities should cap annual rent increases" in sent
    assert "Caps reduce the supply of housing over time." in sent


def test_master_input_lists_messages_oldest_first_in_the_order_given(install_fake):
    sc = kit.make_human_scenario()
    fake = install_fake(kit.master_out())
    kit.agents().call_master(sc.run, sc.transcript, topic=sc.topic)
    sent = _sent_user_text(fake)
    positions = [sent.index(f'<message id="{m["id"]}"') for m in sc.transcript]
    assert positions == sorted(positions)
    assert f'<newest_message id="{sc.transcript[-1]["id"]}"' in sent


def test_only_the_proposition_and_title_of_the_topic_reach_the_prompt(install_fake):
    sc = kit.make_human_scenario()
    fake = install_fake(kit.master_out(), kit.intervenor_out())
    issue = kit.make_issue(sc.run, sc.msgs[0])
    ag = kit.agents()
    ag.call_master(sc.run, sc.transcript, topic=sc.topic)
    ag.call_intervenor(sc.run, sc.transcript, topic=sc.topic, valid_issues=[issue])
    everything = kit.all_request_text(fake.calls)
    assert kit.TOPIC_DESCRIPTION_MARKER not in everything
    assert "LEANS-MARKER" not in everything and "compass" not in everything
    for call in fake.calls:
        user = call["messages"][0]["content"]
        assert "LEANS-MARKER" not in user and "compass" not in user
        assert kit.TOPIC_DESCRIPTION_MARKER not in user
        assert "Cities should cap annual rent increases for existing tenants." in user


def test_master_input_never_carries_a_timestamp_or_seq_or_author_type(install_fake):
    sc = kit.make_human_scenario()
    fake = install_fake(kit.master_out())
    kit.agents().call_master(sc.run, sc.transcript, topic=sc.topic)
    sent = _sent_user_text(fake)
    assert "2031" not in sent  # the year of every created_at in the kit
    assert "created_at" not in sent and "author_type" not in sent and "seq_no" not in sent


def test_already_raised_issues_are_rendered_with_their_outcomes(install_fake):
    from moderation import prompting

    sc = kit.make_human_scenario()
    already = [
        {"id": "i7", "message_id": sc.msgs[0].pk, "issue_type": "possible_factual_error", "quote": "12 percent",
         "explanation": "The figure looks too high.", "outcome": "acted"},
        {"id": "i8", "message_id": sc.msgs[1].pk, "issue_type": "unsupported_claim", "quote": "reduce the supply",
         "explanation": "No source is given.", "outcome": "declined"},
    ]  # fmt: skip
    fake = install_fake(kit.master_out())
    kit.agents().call_master(sc.run, sc.transcript, topic=sc.topic, already_raised=already)
    sent = _sent_user_text(fake)
    assert sent in _master_candidates(sc.transcript, sc.topic, already_raised=already)
    assert "<already_raised_issues>" in sent
    assert "<outcome>acted</outcome>" in sent and "<outcome>declined</outcome>" in sent
    assert "The figure looks too high." in sent
    assert prompting  # the module is what rendered the expectation


def test_no_already_raised_block_by_default(install_fake):
    sc = kit.make_human_scenario()
    fake = install_fake(kit.master_out())
    kit.agents().call_master(sc.run, sc.transcript, topic=sc.topic)
    assert "<already_raised_issues>" not in _sent_user_text(fake)


def test_process_facts_are_rendered_when_given_and_absent_otherwise(install_fake):
    sc = kit.make_human_scenario()
    facts = {"message_count": 4, "current_run_length": 1, "seconds_between_last_two_messages": 30.0}
    fake = install_fake(kit.master_out(), kit.master_out())
    ag = kit.agents()
    ag.call_master(sc.run, sc.transcript, topic=sc.topic, process_facts=facts)
    ag.call_master(sc.run, sc.transcript, topic=sc.topic)
    with_facts, without = _sent_user_text(fake, 0), _sent_user_text(fake, 1)
    assert with_facts in _master_candidates(sc.transcript, sc.topic, process_facts=facts)
    assert '<fact name="message_count">4</fact>' in with_facts
    assert "<process_facts>" not in without
    assert without in _master_candidates(sc.transcript, sc.topic)


def test_real_process_facts_flow_into_the_master_input(install_fake):
    sc = kit.make_human_scenario()
    facts = kit.features().process_facts(sc.transcript)
    fake = install_fake(kit.master_out())
    kit.agents().call_master(sc.run, sc.transcript, topic=sc.topic, process_facts=facts)
    sent = _sent_user_text(fake)
    for key in facts:
        assert f'<fact name="{key}">' in sent


# --- The Intervenor's input -----------------------------------------------------------------------------------------

def test_intervenor_input_has_transcript_issues_and_map(install_fake):
    sc = kit.make_human_scenario()
    issues = [
        kit.make_issue(sc.run, sc.msgs[0], "i1", quote="12 percent", quote_start=11, quote_end=21, explanation="Rent figure is off."),
        kit.make_issue(sc.run, sc.msgs[1], "i2", issue_type="unsupported_claim", intensity=None, quote="the supply", quote_start=12, quote_end=22, explanation="No source."),
    ]  # fmt: skip
    dmap = {
        "agreements": ["Rents have risen."],
        "disagreements": [{"summary": "Whether caps reduce supply.", "kind": "factual"}],
    }
    fake = install_fake(kit.intervenor_out())
    kit.agents().call_intervenor(sc.run, sc.transcript, topic=sc.topic, valid_issues=issues, discussion_map=dmap)
    sent = _sent_user_text(fake)
    assert sent in _intervenor_candidates(sc.transcript, sc.topic, issues, dmap)
    assert "Rent figure is off." in sent and "No source." in sent
    assert "<discussion_map>" in sent and "Whether caps reduce supply." in sent
    assert '<disagreement kind="factual">' in sent
    # each issue is rendered under a distinct id and carries its own message id and type
    ids = re.findall(r'<issue id="([^"]+)">', sent)
    assert len(ids) == 2 and len(set(ids)) == 2
    assert set(ids) in ({"i1", "i2"}, {str(i.pk) for i in issues})


def test_intervenor_discussion_map_may_be_a_dict_or_the_pydantic_model(install_fake):
    from moderation.schemas import DiscussionMap

    sc = kit.make_human_scenario()
    issue = kit.make_issue(sc.run, sc.msgs[0])
    raw = {"agreements": ["a1"], "disagreements": [{"summary": "s1", "kind": "normative"}]}
    fake = install_fake(kit.intervenor_out(), kit.intervenor_out())
    ag = kit.agents()
    ag.call_intervenor(sc.run, sc.transcript, topic=sc.topic, valid_issues=[issue], discussion_map=raw)
    ag.call_intervenor(sc.run, sc.transcript, topic=sc.topic, valid_issues=[issue], discussion_map=DiscussionMap.model_validate(raw))
    assert _sent_user_text(fake, 0) == _sent_user_text(fake, 1)
    assert '<disagreement kind="normative">s1</disagreement>' in _sent_user_text(fake, 0)


def test_intervenor_without_a_map_has_no_map_block(install_fake):
    sc = kit.make_human_scenario()
    issue = kit.make_issue(sc.run, sc.msgs[0])
    fake = install_fake(kit.intervenor_out())
    kit.agents().call_intervenor(sc.run, sc.transcript, topic=sc.topic, valid_issues=[issue])
    assert "<discussion_map>" not in _sent_user_text(fake)


def test_intervenor_issue_intensity_none_is_rendered_as_none(install_fake):
    sc = kit.make_human_scenario()
    issue = kit.make_issue(sc.run, sc.msgs[1], "i9", issue_type="unsupported_claim", intensity=None)
    fake = install_fake(kit.intervenor_out())
    kit.agents().call_intervenor(sc.run, sc.transcript, topic=sc.topic, valid_issues=[issue])
    assert "<intensity>none</intensity>" in _sent_user_text(fake)


def test_intervenor_sends_no_issue_that_was_not_given(install_fake):
    """A rejected issue (or one of another run) must not appear unless the caller passes it: only valid_issues are sent."""
    sc = kit.make_human_scenario()
    given = kit.make_issue(sc.run, sc.msgs[0], "i1", explanation="GIVEN-ISSUE-TEXT")
    kit.make_issue(
        sc.run, sc.msgs[1], "i2", issue_type="unsupported_claim", intensity=None, quote="", quote_start=None, quote_end=None,
        quote_match="not_found", validity="rejected", rejection_reason="quote_not_found", explanation="REJECTED-ISSUE-TEXT",
    )  # fmt: skip
    fake = install_fake(kit.intervenor_out())
    kit.agents().call_intervenor(sc.run, sc.transcript, topic=sc.topic, valid_issues=[given])
    sent = _sent_user_text(fake)
    assert "GIVEN-ISSUE-TEXT" in sent and "REJECTED-ISSUE-TEXT" not in sent


# --- Privacy --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("agent", ("master", "intervenor"))
def test_no_username_or_email_reaches_the_prompt_the_request_or_the_ledger(agent, install_fake):
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    # the users really exist and are the participants of this conversation
    assert {p.user.username for p in sc.parts.values()} == {kit.USER_A["username"], kit.USER_B["username"]}
    issue = kit.make_issue(sc.run, sc.msgs[0])
    fake = install_fake(kit.master_out() if agent == "master" else kit.intervenor_out())
    ag = kit.agents()
    if agent == "master":
        ag.call_master(sc.run, sc.transcript, topic=sc.topic, process_facts=kit.features().process_facts(sc.transcript))
    else:
        ag.call_intervenor(sc.run, sc.transcript, topic=sc.topic, valid_issues=[issue], discussion_map={"agreements": [], "disagreements": []})
    sent = kit.all_request_text(fake.calls)
    ledger_text = json.dumps(list(LLMCall.objects.values("request", "raw_response", "parsed", "error")), default=str)
    for needle in kit.FORBIDDEN_STRINGS:
        assert needle not in sent, needle
        assert needle not in ledger_text, needle
    assert "Participant A" in sent and "Participant B" in sent  # the labels are what identifies people


def test_extra_keys_in_transcript_dicts_are_never_sent(install_fake):
    """The transcript's dicts are plain and only id, label and text may be used; a stray key must not leak."""
    sc = kit.make_human_scenario()
    transcript = copy.deepcopy(sc.transcript)
    for m in transcript:
        m["username"] = "quillfeather_zx"
        m["email"] = "quillfeather.zx@hidden-mail.example"
        m["user"] = sc.users["A"]
    fake = install_fake(kit.master_out())
    kit.agents().call_master(sc.run, transcript, topic=sc.topic)
    sent = kit.all_request_text(fake.calls)
    for needle in kit.FORBIDDEN_STRINGS:
        assert needle not in sent, needle


def test_moderator_messages_are_labelled_moderator(install_fake):
    sc = kit.make_human_scenario()
    fake = install_fake(kit.master_out())
    kit.agents().call_master(sc.run, sc.transcript, topic=sc.topic)
    sent = _sent_user_text(fake)
    mod = next(m for m in sc.transcript if m["author_type"] == "moderator")
    assert f'<message id="{mod["id"]}" participant="Moderator">' in sent


# --- The agents reject nothing and truncate nothing -----------------------------------------------------------------

@pytest.mark.parametrize("agent", ("master", "intervenor"))
def test_the_whole_transcript_given_is_sent_even_beyond_the_window(agent, install_fake, tune):
    """Truncation to TRANSCRIPT_MAX_MESSAGES is the caller's job: 30 messages given are 30 messages sent."""
    tune(TRANSCRIPT_MAX_MESSAGES=5)
    sc = kit.make_human_scenario()
    transcript = kit.hand_transcript("AB" * 15, start_id=1000)
    assert len(transcript) == 30
    issue = kit.make_issue(sc.run, sc.msgs[0])
    fake = install_fake(kit.master_out() if agent == "master" else kit.intervenor_out())
    ag = kit.agents()
    if agent == "master":
        ag.call_master(sc.run, transcript, topic=sc.topic)
    else:
        ag.call_intervenor(sc.run, transcript, topic=sc.topic, valid_issues=[issue])
    sent = _sent_user_text(fake)
    assert sent.count("<message id=") == 30
    assert sent.count("</message>") == 30
    assert '<message id="1000"' in sent and '<message id="1029"' in sent
    assert '<newest_message id="1029"' in sent


@pytest.mark.parametrize("agent", ("master", "intervenor"))
def test_a_short_transcript_is_not_padded(agent, install_fake):
    sc = kit.make_human_scenario()
    transcript = sc.transcript[:2]
    issue = kit.make_issue(sc.run, sc.msgs[0])
    fake = install_fake(kit.master_out() if agent == "master" else kit.intervenor_out())
    ag = kit.agents()
    if agent == "master":
        ag.call_master(sc.run, transcript, topic=sc.topic)
    else:
        ag.call_intervenor(sc.run, transcript, topic=sc.topic, valid_issues=[issue])
    assert _sent_user_text(fake).count("<message id=") == 2


def test_a_moderator_authored_newest_message_is_not_rejected_by_the_agent(install_fake):
    """Plan section 2 layer 3 (the pipeline's job): the agent module itself does not judge who wrote the newest message."""
    sc = kit.make_human_scenario()
    transcript = sc.transcript[:3]  # ends with the moderator message
    assert transcript[-1]["author_type"] == "moderator"
    install_fake(kit.master_out())
    result = kit.agents().call_master(sc.run, transcript, topic=sc.topic)
    assert result.issues == []


def test_unusable_but_schema_valid_content_is_returned_untouched(install_fake):
    """Ids and quotes that make no sense are the pipeline's to reject; the Master's output comes back as it was."""
    sc = kit.make_human_scenario()
    out = kit.master_out(
        [
            kit.issue_d("i1", message_id=987654, quote="text that is nowhere", issue_type="possible_factual_error", intensity=2),
            kit.issue_d("i1", message_id=sc.msgs[2].pk, quote="dup id on a moderator message"),
            kit.issue_d("i3", message_id=sc.msgs[0].pk, quote="x", issue_type="unsupported_claim", intensity=4),
        ],
        agreements=["something"],
        disagreements=[{"summary": "sum", "kind": "factual"}],
    )
    install_fake(out)
    result = kit.agents().call_master(sc.run, sc.transcript, topic=sc.topic)
    assert [i.id for i in result.issues] == ["i1", "i1", "i3"]
    assert result.issues[0].message_id == 987654
    assert result.issues[2].intensity == 4  # an intensity on a type with no dimension: not the agent's business
    assert result.discussion_map.agreements == ["something"]


def test_intervenor_output_with_unknown_ids_and_over_cap_acts_is_returned_untouched(install_fake):
    sc = kit.make_human_scenario()
    issue = kit.make_issue(sc.run, sc.msgs[0])
    acts = [kit.act_d(source_issue_ids=["nope"], source_message_ids=[424242], addressee="Z", subject="Q", text=f"act {i} names Participant A") for i in range(5)]
    out = kit.intervenor_out("intervene", "r", [kit.disposition_d("unknown"), kit.disposition_d("unknown")], acts)
    install_fake(out)
    result = kit.agents().call_intervenor(sc.run, sc.transcript, topic=sc.topic, valid_issues=[issue])
    assert len(result.acts) == 5 and len(result.issue_dispositions) == 2
    assert result.acts[0].text == "act 0 names Participant A"


def test_empty_issue_list_and_empty_transcript_are_not_rejected_by_the_agent(install_fake):
    sc = kit.make_human_scenario()
    fake = install_fake(kit.intervenor_out(), kit.master_out())
    ag = kit.agents()
    ag.call_intervenor(sc.run, sc.transcript, topic=sc.topic, valid_issues=[])
    ag.call_master(sc.run, [], topic=sc.topic)
    assert len(fake.calls) == 2


# --- The agents write no domain rows --------------------------------------------------------------------------------

@pytest.mark.parametrize("agent", ("master", "intervenor"))
def test_agents_write_only_ledger_rows(agent, install_fake):
    from moderation.models import LLMCall

    sc = kit.make_human_scenario()
    issue = kit.make_issue(sc.run, sc.msgs[0])
    before = kit.db_snapshot()
    ledger_before = LLMCall.objects.count()
    install_fake(kit.master_out([kit.issue_d(message_id=sc.msgs[0].pk)]) if agent == "master" else kit.intervenor_out("intervene", "r", [kit.disposition_d("i1")], [kit.act_d(source_issue_ids=["i1"])]))
    ag = kit.agents()
    if agent == "master":
        ag.call_master(sc.run, sc.transcript, topic=sc.topic)
    else:
        ag.call_intervenor(sc.run, sc.transcript, topic=sc.topic, valid_issues=[issue])
    assert kit.db_snapshot() == before  # no Issue, disposition, act, message, run change
    assert LLMCall.objects.count() == ledger_before + 1
    sc.run.refresh_from_db()
    assert (sc.run.status, sc.run.attempts, sc.run.decision, sc.run.posted_message_id) == ("pending", 0, "", None)


def test_agents_do_not_modify_their_arguments(install_fake):
    sc = kit.make_human_scenario()
    transcript = copy.deepcopy(sc.transcript)
    already = [{"id": "i1", "message_id": 1, "issue_type": "unsupported_claim", "quote": "q", "explanation": "e", "outcome": "acted"}]
    facts = {"message_count": 4, "messages_per_label": {"Participant A": 2}}
    dmap = {"agreements": ["a"], "disagreements": [{"summary": "s", "kind": "factual"}]}
    before = copy.deepcopy((transcript, already, facts, dmap))
    install_fake(kit.master_out(), kit.intervenor_out())
    ag = kit.agents()
    issue = kit.make_issue(sc.run, sc.msgs[0])
    ag.call_master(sc.run, transcript, topic=sc.topic, already_raised=already, process_facts=facts)
    ag.call_intervenor(sc.run, transcript, topic=sc.topic, valid_issues=[issue], discussion_map=dmap)
    assert (transcript, already, facts, dmap) == before
