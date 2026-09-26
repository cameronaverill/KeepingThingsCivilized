"""prompt_fingerprint(): the name and sha256 of the two prompt files, for the run's config snapshot."""
import hashlib
import json

import pipeline_agents_kit as kit


def _file_sha(name):
    return hashlib.sha256((kit.PROMPTS / f"{name}.md").read_bytes()).hexdigest()


def test_fingerprint_has_both_agents_with_name_and_sha256():
    fp = kit.agents().prompt_fingerprint()
    assert set(fp) == {"master", "intervenor"}
    for agent in ("master", "intervenor"):
        assert {"name", "sha256"} <= set(fp[agent])
        assert fp[agent]["name"] == f"{agent}_v1"


def test_fingerprint_sha256_is_the_sha256_of_the_prompt_file_bytes():
    fp = kit.agents().prompt_fingerprint()
    assert fp["master"]["sha256"] == _file_sha("master_v1")
    assert fp["intervenor"]["sha256"] == _file_sha("intervenor_v1")
    assert fp["master"]["sha256"] != fp["intervenor"]["sha256"]


def test_fingerprint_matches_load_prompt():
    from moderation import prompting

    fp = kit.agents().prompt_fingerprint()
    for agent in ("master", "intervenor"):
        p = prompting.load_prompt(agent)
        assert (fp[agent]["name"], fp[agent]["sha256"]) == (p.name, p.sha256)


def test_fingerprint_is_json_serializable_and_stable():
    ag = kit.agents()
    first = ag.prompt_fingerprint()
    assert json.loads(json.dumps(first)) == first
    assert ag.prompt_fingerprint() == first


def test_fingerprint_of_a_run_matches_the_prompt_the_agent_sent(llm_ready, install_fake):
    """The fingerprint describes what the agents really send: the hash of the system text of an actual call."""
    sc = kit.make_human_scenario()
    fake = install_fake(kit.master_out())
    kit.agents().call_master(sc.run, sc.transcript, topic=sc.topic)
    sent = fake.calls[0]["system"][0]["text"]
    assert hashlib.sha256(sent.encode("utf-8")).hexdigest() == kit.agents().prompt_fingerprint()["master"]["sha256"]
