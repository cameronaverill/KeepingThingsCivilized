"""The exact arguments each agent passes to llm.call (checked at the FakeLLM client and in the LLMCall ledger)."""
import pytest

import pipeline_agents_kit as kit

pytestmark = pytest.mark.usefixtures("llm_ready")

AGENTS = ("master", "intervenor")


def _run_agent(agent, sc, fake_script_out=None, **overrides):
    """Call the named agent on the human scenario and return its result."""
    ag = kit.agents()
    if agent == "master":
        return ag.call_master(sc.run, sc.transcript, topic=sc.topic, **overrides)
    issue = kit.make_issue(sc.run, sc.msgs[0])
    overrides.setdefault("valid_issues", [issue])
    return ag.call_intervenor(sc.run, sc.transcript, topic=sc.topic, **overrides)


def _valid(agent):
    return kit.master_out() if agent == "master" else kit.intervenor_out()


def _schema(agent):
    from moderation.schemas import IntervenorOutput, MasterOutput

    return MasterOutput if agent == "master" else IntervenorOutput


def _prompt_bytes(agent):
    return (kit.PROMPTS / f"{kit.latest_prompt_name(agent)}.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("agent", AGENTS)
def test_fake_client_receives_the_contracted_request(agent, install_fake, settings):
    sc = kit.make_human_scenario()
    fake = install_fake(_valid(agent))
    _run_agent(agent, sc)
    assert len(fake.calls) == 1
    sent = fake.calls[0]
    model = settings.MASTER_MODEL if agent == "master" else settings.INTERVENOR_MODEL
    max_tokens = settings.MASTER_MAX_TOKENS if agent == "master" else settings.INTERVENOR_MAX_TOKENS
    assert sent["model"] == model
    assert sent["max_tokens"] == max_tokens
    assert sent["output_format"] is _schema(agent)
    # cache_system=True: the system prompt is one text block with an explicit cache breakpoint
    assert sent["system"] == [{"type": "text", "text": _prompt_bytes(agent), "cache_control": {"type": "ephemeral"}}]
    assert isinstance(sent["messages"], list) and len(sent["messages"]) == 1
    assert sent["messages"][0]["role"] == "user"
    assert isinstance(sent["messages"][0]["content"], str) and sent["messages"][0]["content"].strip()
    assert "extra_body" not in sent  # no temperature is sent


@pytest.mark.parametrize("agent", AGENTS)
def test_ledger_row_records_purpose_agent_ids_model_and_prompt(agent, install_fake, settings):
    sc = kit.make_human_scenario()
    install_fake(_valid(agent))
    _run_agent(agent, sc)
    rows = kit.ledger(sc.run)
    assert len(rows) == 1
    row = rows[0]
    model = settings.MASTER_MODEL if agent == "master" else settings.INTERVENOR_MODEL
    max_tokens = settings.MASTER_MAX_TOKENS if agent == "master" else settings.INTERVENOR_MAX_TOKENS
    assert row.purpose == "moderation"
    assert row.agent == agent
    assert row.attempt == 1
    assert row.model == model
    assert row.max_tokens == max_tokens
    assert row.run_id == sc.run.pk
    assert row.conversation_id == sc.run.conversation_id == sc.conv.pk
    assert row.status == "ok"
    assert row.temperature is None
    assert row.request["cache_system"] is True
    # architect ruling: prompt_version is the prompt's file name, exactly as the spike records it
    assert row.prompt_version == kit.latest_prompt_name(agent)
    assert len(row.prompt_sha256) == 64


@pytest.mark.parametrize("agent", AGENTS)
def test_model_and_max_tokens_come_from_tunables_not_constants(agent, install_fake, tune):
    sc = kit.make_human_scenario()
    tune(MASTER_MODEL="claude-haiku-4-5", INTERVENOR_MODEL="claude-haiku-4-5", MASTER_MAX_TOKENS=1234, INTERVENOR_MAX_TOKENS=777)
    fake = install_fake(_valid(agent))
    _run_agent(agent, sc)
    expected_tokens = 1234 if agent == "master" else 777
    assert fake.calls[0]["model"] == "claude-haiku-4-5"
    assert fake.calls[0]["max_tokens"] == expected_tokens
    row = kit.ledger(sc.run)[0]
    assert (row.model, row.max_tokens) == ("claude-haiku-4-5", expected_tokens)


def test_the_two_agents_use_their_own_tunables(install_fake, tune):
    """A mix-up (the Master using the Intervenor's model or limit) shows only when the four values all differ."""
    sc = kit.make_human_scenario()
    tune(MASTER_MODEL="claude-sonnet-5", INTERVENOR_MODEL="claude-haiku-4-5", MASTER_MAX_TOKENS=1111, INTERVENOR_MAX_TOKENS=555)
    fake = install_fake(kit.master_out(), kit.intervenor_out())
    _run_agent("master", sc)
    _run_agent("intervenor", sc)
    assert (fake.calls[0]["model"], fake.calls[0]["max_tokens"]) == ("claude-sonnet-5", 1111)
    assert (fake.calls[1]["model"], fake.calls[1]["max_tokens"]) == ("claude-haiku-4-5", 555)


@pytest.mark.parametrize("agent", AGENTS)
def test_run_and_conversation_ids_come_from_the_run(agent, install_fake):
    """Two conversations and runs with different pks: the ledger row follows the run it was given."""
    first = kit.make_human_scenario(human=False)
    second = kit.make_human_scenario()
    assert first.run.pk != second.run.pk and first.conv.pk != second.conv.pk
    install_fake(_valid(agent))
    _run_agent(agent, second)
    assert [r.run_id for r in kit.ledger(second.run)] == [second.run.pk]
    assert kit.ledger(first.run) == []
    from moderation.models import LLMCall

    assert LLMCall.objects.get().conversation_id == second.conv.pk


@pytest.mark.parametrize("agent", AGENTS)
def test_system_prompt_is_the_loaded_prompt_file(agent, install_fake):
    from moderation import prompting

    sc = kit.make_human_scenario()
    fake = install_fake(_valid(agent))
    _run_agent(agent, sc)
    system_text = fake.calls[0]["system"][0]["text"]
    assert system_text == prompting.load_prompt(agent).text == _prompt_bytes(agent)


@pytest.mark.parametrize("agent", AGENTS)
def test_result_is_the_parsed_pydantic_output(agent, install_fake):
    sc = kit.make_human_scenario()
    if agent == "master":
        out = kit.master_out([kit.issue_d(message_id=sc.msgs[0].pk, quote="12 percent", intensity=None)], agreements=["prices rose"])
    else:
        out = kit.intervenor_out("intervene", "a source is needed", [kit.disposition_d("i1")], [kit.act_d(source_issue_ids=["i1"])])
    install_fake(out)
    result = _run_agent(agent, sc)
    assert isinstance(result, _schema(agent))
    assert result == _schema(agent).model_validate(out)


def test_call_master_signature_defaults(install_fake):
    """already_raised and process_facts are optional and keyword-only; topic is required and keyword-only."""
    sc = kit.make_human_scenario()
    ag = kit.agents()
    install_fake(kit.master_out())
    with pytest.raises(TypeError):
        ag.call_master(sc.run, sc.transcript, sc.topic)  # topic must be a keyword
    with pytest.raises(TypeError):
        ag.call_master(sc.run, sc.transcript)  # topic is required
    with pytest.raises(TypeError):
        ag.call_intervenor(sc.run, sc.transcript, topic=sc.topic)  # valid_issues is required
