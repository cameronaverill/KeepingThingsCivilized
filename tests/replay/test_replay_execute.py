"""execute_runs: creating and executing kind="replay" runs one at a time (docs/step13_brief.md)."""
from decimal import Decimal

import replay_kit as kit

NAME = "exp-exec"
QUOTE = "stated plainly and civilly"


def prepared(transcripts, *, assignments="both", replicates=1):
    experiment_plan = kit.load(NAME, transcripts, assignments=assignments, replicates=replicates)
    return kit.plan(experiment_plan, replicates=replicates)


def run_all(fake, transcripts, *, assignments="both", replicates=1, max_usd=50, input_tokens=None):
    specs = prepared(transcripts, assignments=assignments, replicates=replicates)
    client = fake(*kit.no_issue_script(len(specs), input_tokens=input_tokens))
    report = kit.execute(specs, max_usd=max_usd)
    return specs, client, report


def messages_snapshot(exp):
    return [
        (m.pk, m.seq_no, m.author_type, m.content)
        for conv in kit.conversations(exp)
        for m in kit.messages_of(conv)
    ]


class TestRunRows:
    def test_each_spec_gets_one_done_replay_run(self, fake):
        specs, _client, _report = run_all(fake, kit.pair("rows"))
        runs = kit.replay_runs(kit.experiment(NAME))
        assert (len(runs), {r.kind for r in runs}, {r.status for r in runs}) == (4, {"replay"}, {"done"})

    def test_replicates_are_numbered_and_each_gets_its_own_run(self, fake):
        run_all(fake, [kit.transcript("reps")], replicates=3)
        runs = kit.replay_runs(kit.experiment(NAME))
        assert sorted((r.conversation_id, r.replicate) for r in runs) == sorted(
            (c.pk, n) for c in kit.conversations(kit.experiment(NAME)) for n in (1, 2, 3)
        )

    def test_the_run_is_triggered_by_the_files_trigger_message_with_the_right_snapshot(self, fake):
        data = kit.transcript("trigrun", authors="ABABA", trigger_seq=3)
        run_all(fake, [data])
        runs = kit.replay_runs(kit.experiment(NAME))
        assert sorted((r.trigger_message.seq_no, r.snapshot_seq) for r in runs) == [(3, 3), (3, 3)]

    def test_the_trigger_is_a_user_message_of_the_runs_own_conversation(self, fake):
        run_all(fake, kit.pair("trigown"))
        runs = kit.replay_runs(kit.experiment(NAME))
        assert [(r.trigger_message.author_type, r.trigger_message.conversation_id == r.conversation_id) for r in runs] == [
            ("user", True)
        ] * 4

    def test_runs_are_executed_in_plan_order(self, fake):
        specs, _client, _report = run_all(fake, kit.pair("ordered"), replicates=2)
        runs = kit.replay_runs(kit.experiment(NAME))
        assert [(r.conversation_id, r.trigger_message_id, r.replicate) for r in runs] == [kit.spec_key(s) for s in specs]

    def test_the_config_snapshot_holds_the_factors_computed_from_the_transcript(self, fake):
        from moderation import replay

        run_all(fake, [kit.transcript("snapfx", authors="ABBBB")])
        runs = kit.replay_runs(kit.experiment(NAME))
        assert [r.config_snapshot["factors"] for r in runs] == [replay.factors(r.conversation) for r in runs]
        assert [r.config_snapshot["factors"]["longest_consecutive_run"] for r in runs] == [4, 4]

    def test_the_run_row_already_holds_its_factors_when_it_is_created(self, monkeypatch):
        from moderation import replay

        seen = []

        def stand_in(run):
            seen.append(dict(run.config_snapshot))
            return run

        monkeypatch.setattr(replay.pipeline, "run_moderation", stand_in)
        specs = prepared([kit.transcript("atcreation", authors="ABBBB")], assignments="as-is")
        kit.execute(specs, max_usd=50)
        assert [snapshot["factors"] for snapshot in seen] == [replay.factors(specs[0].conversation)]

    def test_the_config_snapshot_keeps_what_the_pipeline_records(self, fake):
        run_all(fake, [kit.transcript("snappipe")], assignments="as-is")
        (run,) = kit.replay_runs(kit.experiment(NAME))
        assert {"models", "prompts", "factors"} <= set(run.config_snapshot)

    def test_on_result_is_called_once_per_executed_run_in_order(self, fake):
        specs = prepared(kit.pair("callback"))
        fake(*kit.no_issue_script(len(specs)))
        seen = []
        kit.execute(specs, max_usd=50, on_result=seen.append)
        assert [(r.spec.replicate, r.status) for r in seen] == [(1, "done")] * 4


class TestNothingIsPosted:
    def test_a_replay_that_finds_an_issue_and_intervenes_posts_nothing(self, fake):
        specs = prepared(kit.pair("noposting"), assignments="as-is")
        script = [item for spec in specs for item in (kit.issue_master_answer(spec, QUOTE), kit.intervene_answer(spec))]
        fake(*script)
        before = messages_snapshot(kit.experiment(NAME))
        kit.execute(specs, max_usd=50)
        runs = kit.replay_runs(kit.experiment(NAME))
        assert (messages_snapshot(kit.experiment(NAME)), [(r.decision, r.posted_message_id) for r in runs]) == (
            before,
            [("intervene", None)] * 2,
        )

    def test_an_intervening_replay_still_stores_its_issues_and_acts(self, fake):
        specs = prepared([kit.transcript("stores")], assignments="as-is")
        fake(kit.issue_master_answer(specs[0], QUOTE), kit.intervene_answer(specs[0]))
        kit.execute(specs, max_usd=50)
        (run,) = kit.replay_runs(kit.experiment(NAME))
        assert ([(i.local_id, i.validity) for i in run.issues.all()], [(a.order, a.validity) for a in run.acts.all()]) == (
            [("i1", "valid")],
            [(1, "valid")],
        )

    def test_the_stored_transcript_is_unchanged_after_a_full_run(self, fake):
        specs = prepared(kit.pair("unchanged") + [kit.transcript("unchanged_mod", authors="ABMA")])
        before = messages_snapshot(kit.experiment(NAME))
        fake(*kit.no_issue_script(len(specs)))
        kit.execute(specs, max_usd=50)
        assert messages_snapshot(kit.experiment(NAME)) == before

    def test_no_moderator_message_exists_after_the_run_except_the_scripted_one(self, fake):
        from forum.models import Message

        run_all(fake, [kit.transcript("onlyscripted", authors="ABMA")])
        assert Message.objects.filter(author_type="moderator").count() == 2


class TestLiveRunsAreLeftAlone:
    def test_no_live_run_is_created(self, fake):
        from moderation.models import ModerationRun

        run_all(fake, kit.pair("nolive"))
        assert ModerationRun.objects.filter(kind="live").count() == 0

    def test_an_existing_live_run_is_not_touched(self, fake):
        from moderation.models import ModerationRun

        live = kit.human_live_run()
        run_all(fake, kit.pair("besidelive"))
        live = ModerationRun.objects.get(pk=live.pk)
        assert (live.status, live.attempts, live.posted_message_id, live.decision) == ("pending", 0, None, "")

    def test_a_live_run_on_a_scripted_trigger_is_not_touched_and_does_not_count_as_done(self, fake):
        from moderation.models import ModerationRun

        specs = prepared([kit.transcript("livetrig")], assignments="as-is")
        trigger = kit.spec_trigger(specs[0])
        live = ModerationRun.objects.create(
            conversation=trigger.conversation, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live"
        )
        ModerationRun.objects.filter(pk=live.pk).update(status="done", decision="no_intervention")
        fake(*kit.no_issue_script(1))
        report = kit.execute(specs, max_usd=50)
        assert (len(kit.replay_runs()), kit.report_counts(report)) == (1, {"done": 1})
        assert ModerationRun.objects.get(pk=live.pk).attempts == 0

    def test_a_human_conversation_cannot_be_replayed(self, fake):
        import pytest
        from django.core.management.base import CommandError

        from moderation import replay

        live = kit.human_live_run()
        spec = replay.RunSpec(
            conversation=live.conversation, trigger_message=live.trigger_message, snapshot_seq=live.snapshot_seq,
            replicate=1, transcript_id="x", assignment="as-is",
        )
        client = fake(*kit.no_issue_script(1))
        with pytest.raises((CommandError, ValueError)):
            kit.execute([spec], max_usd=50)
        assert (client.calls, kit.replay_runs()) == ([], [])


class TestWhatTheModelSees:
    def test_the_master_sees_only_messages_up_to_the_trigger(self, fake):
        data = kit.transcript("upto", authors="ABABA", trigger_seq=3)
        _specs, client, _report = run_all(fake, [data], assignments="as-is")
        content = client.calls[0]["messages"][0]["content"]
        assert ("[upto] message 3" in content, "[upto] message 4" in content, "[upto] message 5" in content) == (True, False, False)

    def test_a_scripted_moderator_message_before_the_trigger_reaches_the_master_as_context(self, fake):
        data = kit.transcript("modctx", authors="ABMA")
        _specs, client, _report = run_all(fake, [data], assignments="as-is")
        assert "[modctx] message 3" in client.calls[0]["messages"][0]["content"]

    def test_the_swapped_conversation_reaches_the_master_with_the_labels_swapped(self, fake):
        data = kit.transcript("labels", authors="AB")
        specs = prepared([data], assignments="both")
        client = fake(*kit.no_issue_script(2))
        kit.execute(specs, max_usd=50)
        contents = [c["messages"][0]["content"] for c in client.calls]
        as_is, swapped = contents
        assert (
            'participant="Participant A">\n[labels] message 1' in as_is.replace("\r", ""),
            'participant="Participant B">\n[labels] message 1' in swapped.replace("\r", ""),
        ) == (True, True)


class TestResume:
    def test_running_the_same_specs_again_makes_no_call_and_no_row(self, fake):
        specs, _client, _report = run_all(fake, kit.pair("resume"))
        before = kit.table_counts()
        client = fake()
        again = kit.execute(specs, max_usd=50)
        assert (kit.table_counts(), client.calls, kit.report_counts(again)) == (before, [], {})

    def test_the_second_report_lists_the_skipped_specs_as_already_done(self, fake):
        specs, _client, _report = run_all(fake, kit.pair("alreadydone"))
        fake()
        again = kit.execute(specs, max_usd=50)
        assert (len(again.already_done), kit.report_not_run(again), kit.report_total_cost(again)) == (4, [], Decimal("0"))

    def test_only_the_missing_replicate_is_run_when_more_replicates_are_asked_for(self, fake):
        run_all(fake, [kit.transcript("morereps")], assignments="as-is", replicates=1)
        specs = prepared([kit.transcript("morereps")], assignments="as-is", replicates=3)
        client = fake(*kit.no_issue_script(2))
        kit.execute(specs, max_usd=50)
        runs = kit.replay_runs(kit.experiment(NAME))
        assert (len(client.calls), sorted(r.replicate for r in runs)) == (2, [1, 2, 3])

    def test_a_run_that_was_skipped_is_run_for_real_on_the_next_call(self, fake, settings):
        specs = prepared([kit.transcript("skipped")], assignments="as-is")
        settings.LLM_ENABLED = False
        first = kit.execute(specs, max_usd=50)
        settings.LLM_ENABLED = True
        client = fake(*kit.no_issue_script(1))
        second = kit.execute(specs, max_usd=50)
        done = [r for r in kit.replay_runs(kit.experiment(NAME)) if r.status == "done"]
        assert (kit.report_counts(first), kit.report_counts(second), len(done), len(client.calls)) == (
            {"skipped_disabled": 1},
            {"done": 1},
            1,
            1,
        )

    def test_a_pending_replay_run_left_by_an_interrupted_call_is_reused_not_duplicated(self, fake):
        from moderation.models import ModerationRun

        specs = prepared([kit.transcript("leftover")], assignments="as-is")
        spec = specs[0]
        ModerationRun.objects.create(
            conversation=spec.conversation, trigger_message=kit.spec_trigger(spec), snapshot_seq=kit.spec_snapshot_seq(spec),
            kind="replay", replicate=1, status="pending",
        )
        fake(*kit.no_issue_script(1))
        kit.execute(specs, max_usd=50)
        runs = kit.replay_runs(kit.experiment(NAME))
        assert [r.status for r in runs] == ["done"]

    def test_a_done_run_of_another_replicate_does_not_make_this_one_done(self, fake):
        from moderation.models import ModerationRun

        specs = prepared([kit.transcript("otherrep")], assignments="as-is", replicates=2)
        first = specs[0]
        ModerationRun.objects.create(
            conversation=first.conversation, trigger_message=kit.spec_trigger(first), snapshot_seq=kit.spec_snapshot_seq(first),
            kind="replay", replicate=1, status="done", decision="no_intervention",
        )
        client = fake(*kit.no_issue_script(1))
        kit.execute(specs, max_usd=50)
        assert (len(client.calls), sorted(r.replicate for r in kit.replay_runs())) == (1, [1, 2])


class TestSwitchedOff:
    def test_with_the_kill_switch_off_every_run_is_skipped_disabled_and_nothing_is_called(self, settings):
        settings.LLM_ENABLED = False
        specs = prepared(kit.pair("off"))
        report = kit.execute(specs, max_usd=50)
        runs = kit.replay_runs(kit.experiment(NAME))
        assert (kit.report_counts(report), {r.status for r in runs}, len(runs)) == ({"skipped_disabled": 4}, {"skipped_disabled"}, 4)

    def test_with_the_kill_switch_off_no_money_is_spent_and_no_call_row_is_ok(self, settings):
        settings.LLM_ENABLED = False
        report = kit.execute(prepared(kit.pair("offmoney")), max_usd=50)
        assert (kit.replay_spend(), [row.status for row in kit.ledger() if row.status == "ok"], kit.report_total_cost(report)) == (
            Decimal("0"),
            [],
            Decimal("0"),
        )

    def test_the_skipped_runs_still_carry_their_factors(self, settings):
        settings.LLM_ENABLED = False
        kit.execute(prepared([kit.transcript("offfx", authors="ABBBB")], assignments="as-is"), max_usd=50)
        (run,) = kit.replay_runs(kit.experiment(NAME))
        assert run.config_snapshot["factors"]["longest_consecutive_run"] == 4


class TestModelOverrides:
    def test_an_override_changes_the_model_of_that_agents_calls(self, fake):
        specs = prepared([kit.transcript("override")], assignments="as-is")
        client = fake(*kit.no_issue_script(1))
        kit.execute(specs, max_usd=50, model_overrides={"master": "claude-haiku-4-5"})
        assert ([c["model"] for c in client.calls], [row.model for row in kit.ledger()]) == (
            ["claude-haiku-4-5"],
            ["claude-haiku-4-5"],
        )

    def test_without_overrides_the_configured_models_are_used(self, fake, settings):
        specs = prepared([kit.transcript("nooverride")], assignments="as-is")
        client = fake(*kit.no_issue_script(1))
        kit.execute(specs, max_usd=50)
        assert [c["model"] for c in client.calls] == [settings.MASTER_MODEL]
