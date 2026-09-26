"""What the pipeline hands to the agents and what it records about its own configuration (plan sections 6 and 7; brief "5b
details" steps 2 and 6): no identities in prompts, the proposition, earlier issues, process facts, the config snapshot."""
import json

import pipeline_run_kit as kit
import pytest

pytestmark = pytest.mark.django_db


class TestNoIdentitiesInPrompts:
    def test_usernames_and_emails_never_reach_a_prompt_the_ledger_or_the_stored_rows(self, fake):
        from moderation.models import LLMCall

        world = kit.build(human=True, names={"A": "zebulonquist", "B": "quillfeather"})
        run = kit.new_run(world.last)
        client = fake(*kit.simple_success(world))
        _, stored = kit.go(run)
        assert stored.status == "done"
        sent = kit.whole_request_text(client)
        ledger_text = json.dumps(
            [[r.request, r.raw_response, r.parsed, r.error] for r in LLMCall.objects.all()], default=str
        )
        stored_text = json.dumps(
            [stored.error, stored.rationale, stored.config_snapshot, stored.discussion_map, stored.posted_message.content], default=str
        )
        for identity in ("zebulonquist", "quillfeather", "zebulonquist@example.org", "quillfeather@example.org", "example.org"):
            assert identity not in sent
            assert identity not in ledger_text
            assert identity not in stored_text

    def test_the_prompts_carry_labels_message_ids_and_text(self, fake):
        world = kit.build(human=True, names={"A": "zebulonquist", "B": "quillfeather"})
        run = kit.new_run(world.last)
        client = fake(*kit.simple_success(world))
        kit.go(run)
        master_input = kit.user_input(client.calls[0])
        assert kit.rendered_messages(client.calls[0]) == [(m.pk, f"Participant {m.participant.label}") for m in world.msgs]
        assert world.last.content in master_input

    def test_the_proposition_is_sent_to_both_agents(self, fake):
        world = kit.build(proposition="Cities should ban private cars downtown.")
        run = kit.new_run(world.last)
        client = fake(*kit.simple_success(world))
        kit.go(run)
        assert "Cities should ban private cars downtown." in kit.user_input(client.calls[0])
        assert "Cities should ban private cars downtown." in kit.user_input(client.calls[1])


class TestMasterExtras:
    def test_the_master_gets_process_facts_computed_in_code(self, fake):
        world = kit.build([("A", "One."), ("A", "Two."), ("A", "Three."), ("B", "Four.")])
        run = kit.new_run(world.last)
        client = fake(kit.master_d())
        kit.go(run)
        facts = kit.block(kit.user_input(client.calls[0]), "process_facts")
        assert facts is not None
        assert "Participant A" in facts and "Participant B" in facts

    def test_earlier_valid_issues_are_shown_as_already_raised_with_their_outcome_and_rejected_ones_are_not(self, fake):
        from moderation.models import Issue, IssueDisposition

        world = kit.build([("A", "The moon is made of green cheese, everybody knows that."), ("B", "Not at all."), ("A", "It is, and nobody disagrees on that.")])
        earlier = kit.new_run(world[1], status="done")
        good = Issue.objects.create(
            run=earlier, local_id="i1", message=world[1], issue_type="possible_factual_error", quote="green cheese",
            quote_start=world[1].content.index("green cheese"), quote_end=world[1].content.index("green cheese") + 12,
            quote_match="exact", explanation="The moon is not cheese.", confidence=0.9, intensity=4,
        )  # fmt: skip
        IssueDisposition.objects.create(issue=good, disposition="acted", reason="Clear error.")
        Issue.objects.create(
            run=earlier, local_id="r1", message=world[1], issue_type="unsupported_claim", quote="unique rejected phrase",
            quote_match="not_found", explanation="unique rejected explanation", confidence=0.4, validity="rejected",
            rejection_reason="quote_not_found",
        )  # fmt: skip
        run = kit.new_run(world[3])
        client = fake(kit.master_d())
        kit.go(run)
        text = kit.user_input(client.calls[0])
        raised = kit.block(text, "already_raised_issues")
        assert raised is not None
        assert "green cheese" in raised
        assert "acted" in raised
        assert "unique rejected phrase" not in text
        assert "unique rejected explanation" not in text

    @pytest.mark.parametrize("status", ["failed", "pending", "running", "skipped_budget"])
    def test_issues_of_earlier_runs_that_did_not_finish_are_not_shown_as_already_raised(self, fake, status):
        from moderation.models import Issue

        world = kit.build([("A", "The moon is made of green cheese, everybody knows that."), ("B", "Not at all."), ("A", "It is, honestly.")])
        earlier = kit.new_run(world[1], status=status)
        Issue.objects.create(
            run=earlier, local_id="i1", message=world[1], issue_type="unsupported_claim", quote="green cheese",
            quote_start=world[1].content.index("green cheese"), quote_end=world[1].content.index("green cheese") + 12,
            quote_match="exact", explanation="unfinished run explanation", confidence=0.9,
        )  # fmt: skip
        client = fake(kit.master_d())
        kit.go(kit.new_run(world[3]))
        assert "unfinished run explanation" not in kit.user_input(client.calls[0])

    def test_the_intervenor_sees_each_issue_under_the_masters_own_local_id(self, fake):
        world = kit.build()
        master = kit.master_d(kit.issue_d("first_issue", world.last), kit.issue_d("second_issue", world.last, "fallacy", kit.QUOTE_2))
        client = fake(master, kit.interv_d("no_intervention", "Nothing.", [kit.disp_d("first_issue", "declined"), kit.disp_d("second_issue", "declined")]))
        kit.go(kit.new_run(world.last))
        shown = kit.block(kit.user_input(client.calls[1]), "issues")
        assert '<issue id="first_issue">' in shown
        assert '<issue id="second_issue">' in shown

    def test_a_first_run_has_no_already_raised_block(self, fake):
        world = kit.build()
        run = kit.new_run(world.last)
        client = fake(kit.master_d())
        kit.go(run)
        assert kit.block(kit.user_input(client.calls[0]), "already_raised_issues") is None


class TestConfigSnapshot:
    def finished(self, fake, tune, **tuned):
        tune(**tuned)
        world = kit.build()
        run = kit.new_run(world.last)
        fake(*kit.simple_success(world))
        _, stored = kit.go(run)
        assert stored.status == "done"
        return stored

    def test_the_snapshot_records_each_agents_model_and_max_tokens(self, fake, tune):
        stored = self.finished(
            fake, tune, MASTER_MODEL="claude-haiku-4-5", INTERVENOR_MODEL="claude-sonnet-5",
            MASTER_MAX_TOKENS=1234, INTERVENOR_MAX_TOKENS=777,
        )  # fmt: skip
        snapshot = stored.config_snapshot
        assert any("master" in p for p in kit.paths_of(snapshot, "claude-haiku-4-5"))
        assert any("intervenor" in p for p in kit.paths_of(snapshot, "claude-sonnet-5"))
        assert not any("intervenor" in p for p in kit.paths_of(snapshot, "claude-haiku-4-5"))
        assert any("master" in p for p in kit.paths_of(snapshot, 1234))
        assert any("intervenor" in p for p in kit.paths_of(snapshot, 777))

    def test_the_snapshot_records_the_prompt_names_and_hashes(self, fake, tune):
        from moderation import agents

        stored = self.finished(fake, tune)
        fingerprint = agents.prompt_fingerprint()
        for agent in ("master", "intervenor"):
            for field in ("name", "sha256"):
                paths = kit.paths_of(stored.config_snapshot, fingerprint[agent][field])
                assert any(agent in p for p in paths), (agent, field)

    def test_the_snapshot_hashes_are_the_sha256_of_the_prompt_files(self, fake, tune):
        import hashlib

        from moderation import prompting

        stored = self.finished(fake, tune)
        for agent in ("master", "intervenor"):
            prompt = prompting.load_prompt(agent)
            digest = hashlib.sha256((prompting.PROMPTS_DIR / f"{prompt.name}.md").read_bytes()).hexdigest()
            assert any(agent in p for p in kit.paths_of(stored.config_snapshot, digest))

    def test_the_snapshot_records_the_tunables_that_shape_the_run(self, fake, tune):
        stored = self.finished(fake, tune, TRANSCRIPT_MAX_MESSAGES=7, MAX_ACTS_PER_INTERVENTION=2)
        assert any("transcript" in p for p in kit.paths_of(stored.config_snapshot, 7))
        assert any("acts" in p for p in kit.paths_of(stored.config_snapshot, 2))

    def test_the_snapshot_is_recorded_when_there_is_no_valid_issue_too(self, fake):
        from moderation import agents

        world = kit.build()
        run = kit.new_run(world.last)
        fake(kit.master_d())
        _, stored = kit.go(run)
        assert kit.paths_of(stored.config_snapshot, agents.prompt_fingerprint()["master"]["sha256"])
        assert kit.paths_of(stored.config_snapshot, "claude-sonnet-5")
