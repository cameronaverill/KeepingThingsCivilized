"""The real golden directory: all 42 transcripts, both assignments, replayed with FakeLLM (docs/step13_brief.md)."""
from decimal import Decimal

import pytest
import replay_kit as kit

NAME = "exp-golden"
GOLDEN = kit.load_golden()
ALL = list(GOLDEN.values())


def load_all(**kwargs):
    return kit.load(NAME, ALL, assignments="both", **kwargs)


def authorship_mismatches(exp):
    """Ids of golden transcripts whose as-is or swapped conversation does not have the file's (or its mirror's) authorship."""
    bad = []
    for data in ALL:
        as_is = kit.conv_for(exp, data)
        swapped = kit.conv_for(exp, data, swapped=True)
        texts = [m["text"].strip() for m in data["messages"]]
        if kit.authors_of(as_is) != kit.file_labels(data) or kit.authors_of(swapped) != kit.flipped(kit.file_labels(data)):
            bad.append(data["id"])
        if [m.content.strip() for m in kit.messages_of(as_is)] != texts or [m.content.strip() for m in kit.messages_of(swapped)] != texts:
            bad.append(data["id"])
    return bad


def planted_mismatches(exp):
    return [
        data["id"]
        for data in ALL
        for conv in (kit.conv_for(exp, data), kit.conv_for(exp, data, swapped=True))
        if [m.planted for m in kit.messages_of(conv)] != [m["planted"] for m in data["messages"]]
    ]


def factor_mismatches(exp):
    from moderation import replay
    from moderation.series import compute_features

    return [
        data["id"]
        for data in ALL
        for conv in (kit.conv_for(exp, data), kit.conv_for(exp, data, swapped=True))
        if replay.factors(conv) != compute_features(data["messages"], data["trigger_seq"])
    ]


class TestThePremise:
    def test_the_golden_directory_holds_42_transcripts(self):
        assert len(ALL) == 42

    def test_the_real_transcripts_all_validate_as_the_spike_checks_them(self):
        from moderation.management.commands import spike

        assert len(spike.load_transcripts()) == 42


class TestLoadingEverything:
    def test_both_assignments_of_all_42_make_84_synthetic_conversations(self):
        load_all()
        convs = kit.conversations(kit.experiment(NAME))
        assert (len(convs), {c.source for c in convs}, {c.status for c in convs}) == (84, {"synthetic"}, {"closed"})

    def test_every_message_and_participant_is_stored_and_no_user_is_created(self):
        from django.contrib.auth import get_user_model

        from forum.models import Message, Participant

        load_all()
        assert (
            Message.objects.count(),
            Participant.objects.count(),
            get_user_model().objects.count(),
            Participant.objects.filter(user__isnull=False).count(),
        ) == (2 * sum(len(t["messages"]) for t in ALL), 168, 0, 0)

    def test_authorship_is_the_files_and_its_mirror_with_identical_text(self):
        load_all()
        assert authorship_mismatches(kit.experiment(NAME)) == []

    def test_planted_items_are_stored_for_both_assignments(self):
        load_all()
        assert planted_mismatches(kit.experiment(NAME)) == []

    def test_pair_ids_and_variants_come_from_the_files(self):
        load_all()
        stored = sorted((c.pair_id, c.variant) for c in kit.conversations(kit.experiment(NAME)) if c.pair_id)
        expected = sorted((t["pair_id"], t["variant"]) for t in ALL if t["pair_id"] for _ in range(2))
        assert stored == expected

    def test_there_are_nine_pairs_and_each_has_four_conversations(self):
        from collections import Counter

        load_all()
        counts = Counter(c.pair_id for c in kit.conversations(kit.experiment(NAME)) if c.pair_id)
        assert (len(counts), set(counts.values())) == (9, {4})

    def test_every_conversation_has_a_seed_and_the_two_assignments_differ(self):
        load_all()
        seeds = [c.label_seed for c in kit.conversations(kit.experiment(NAME))]
        assert (None in seeds, len(set(seeds))) == (False, 84)

    def test_topics_are_shared_not_duplicated(self):
        from forum.models import Topic

        load_all()
        assert Topic.objects.count() == len({(t["topic"]["title"], t["topic"]["proposition"]) for t in ALL})

    def test_loading_twice_creates_nothing_new(self):
        load_all()
        before = kit.table_counts()
        load_all()
        assert kit.table_counts() == before

    def test_the_experiment_config_names_all_42_transcripts(self):
        import json

        load_all()
        config_text = json.dumps(kit.experiment(NAME).config)
        assert [tid for tid in GOLDEN if tid not in config_text] == []


class TestFactorsOfTheRealSeries:
    def test_the_factors_of_every_conversation_equal_compute_features_on_the_file(self):
        load_all()
        assert factor_mismatches(kit.experiment(NAME)) == []

    def test_the_factors_of_every_series_member_equal_its_declared_computed_block(self):
        from moderation import replay

        load_all()
        exp = kit.experiment(NAME)
        members = [t for t in ALL if t.get("series")]
        declared = [t["computed"] for t in members]
        assert len(members) == 20
        assert [replay.factors(kit.conv_for(exp, t)) for t in members] == declared

    def test_the_flooding_transcripts_have_a_run_of_four_the_repetitions_a_repeat_and_the_questions_go_unanswered(self):
        from moderation import replay

        load_all()
        exp = kit.experiment(NAME)
        got = {
            "flood": replay.factors(kit.conv_for(exp, GOLDEN["flooding_left"]))["longest_consecutive_run"],
            "repeat": replay.factors(kit.conv_for(exp, GOLDEN["repetition_right"], swapped=True))["repeated_sentence_across_messages"],
            "question": replay.factors(kit.conv_for(exp, GOLDEN["unanswered_question_left"]))[
                "unanswered_question_followed_by_two_replies"
            ],
        }
        assert got == {"flood": 4, "repeat": True, "question": True}


class TestPlanAndRuns:
    def test_the_plan_has_one_run_per_conversation_with_the_files_trigger(self):
        specs = kit.plan(load_all(), replicates=1)
        triggers = sorted((spec.transcript_id.split(":")[0], kit.spec_trigger(spec).seq_no) for spec in specs)
        assert (len(specs), triggers) == (84, sorted((t["id"], t["trigger_seq"]) for t in ALL for _ in range(2)))

    def test_the_plan_order_is_the_same_on_every_call(self):
        experiment_plan = load_all()
        assert [kit.spec_key(s) for s in kit.plan(experiment_plan, replicates=2)] == [
            kit.spec_key(s) for s in kit.plan(experiment_plan, replicates=2)
        ]

    def test_all_84_runs_finish_done_with_a_plain_no_issue_master_answer(self, fake):
        specs = kit.plan(load_all(), replicates=1)
        client = fake(*kit.no_issue_script(len(specs)))
        report = kit.execute(specs, max_usd=50)
        runs = kit.replay_runs(kit.experiment(NAME))
        assert (kit.report_counts(report), len(client.calls), {r.kind for r in runs}, {r.decision for r in runs}) == (
            {"done": 84},
            84,
            {"replay"},
            {"no_intervention"},
        )

    def test_nothing_is_posted_and_the_scripted_transcripts_are_unchanged(self, fake):
        from forum.models import Message

        specs = kit.plan(load_all(), replicates=1)
        before = Message.objects.count()
        fake(*kit.no_issue_script(len(specs)))
        kit.execute(specs, max_usd=50)
        assert (Message.objects.count(), Message.objects.filter(author_type="moderator").count(), kit.replay_runs()[0].posted_message_id) == (
            before,
            0,
            None,
        )

    def test_every_run_carries_the_factors_of_its_conversation(self, fake):
        from moderation import replay

        specs = kit.plan(load_all(), replicates=1)
        fake(*kit.no_issue_script(len(specs)))
        kit.execute(specs, max_usd=50)
        runs = kit.replay_runs(kit.experiment(NAME))
        assert [r.config_snapshot["factors"] for r in runs] == [replay.factors(r.conversation) for r in runs]

    def test_every_run_snapshot_matches_the_files_trigger(self, fake):
        specs = kit.plan(load_all(), replicates=1)
        fake(*kit.no_issue_script(len(specs)))
        kit.execute(specs, max_usd=50)
        runs = kit.replay_runs(kit.experiment(NAME))
        expected = sorted(t["trigger_seq"] for t in ALL for _ in range(2))
        assert sorted(r.snapshot_seq for r in runs) == expected

    def test_the_ledger_holds_one_replay_row_per_run(self, fake):
        specs = kit.plan(load_all(), replicates=1)
        fake(*kit.no_issue_script(len(specs), input_tokens=1000))
        kit.execute(specs, max_usd=50)
        assert (len(kit.ledger()), {row.purpose for row in kit.ledger()}, kit.replay_spend()) == (
            84,
            {"replay"},
            84 * Decimal("0.002500"),
        )

    def test_a_second_full_execution_costs_nothing(self, fake):
        specs = kit.plan(load_all(), replicates=1)
        fake(*kit.no_issue_script(len(specs)))
        kit.execute(specs, max_usd=50)
        before = kit.table_counts()
        client = fake()
        kit.execute(specs, max_usd=50)
        assert (kit.table_counts(), client.calls) == (before, [])


class TestTheCommandOnTheRealDirectory:
    def test_a_dry_run_with_no_directory_reads_the_golden_folder_and_writes_nothing(self):
        result = kit.run_command("--experiment", NAME, "--dry-run")
        numbers = kit.integers_in(result.out)
        assert (result.exc, 42 in numbers, 84 in numbers, kit.table_counts()["Experiment"]) == (None, True, True, 0)

    def test_the_dry_run_estimate_is_the_spikes_worst_case_for_every_run(self):
        from django.conf import settings

        from moderation import prompting
        from moderation.management.commands import spike

        master, intervenor = prompting.load_prompt("master"), prompting.load_prompt("intervenor")
        expected = sum(
            (sum(spike.estimate_worst_case(t, settings.MASTER_MODEL, master, intervenor)) * 2 for t in GOLDEN.values()),
            Decimal("0"),
        )
        assert spike.usd(expected) in kit.run_command("--experiment", NAME, "--dry-run").out

    def test_the_flooding_series_can_be_selected_alone_although_its_base_is_elsewhere(self, fake):
        fake(*kit.no_issue_script(4))
        result = kit.run_command("--experiment", NAME, "--set", "flooding_*", "--max-usd", "1", "--live", "--yes")
        assert (result.exc, len(kit.conversations(kit.experiment(NAME))), len(kit.replay_runs())) == (None, 4, 4)

    def test_a_glob_selects_every_transcript_whose_id_matches(self, fake):
        fake(*kit.no_issue_script(19))
        result = kit.run_command(
            "--experiment", NAME, "--set", "*_left", "--assignments", "as-is", "--max-usd", "1", "--live", "--yes",
        )  # fmt: skip
        assert (result.exc, len(kit.conversations(kit.experiment(NAME)))) == (None, 19)

    def test_the_right_variants_are_selected_just_as_the_left_ones_are(self, fake):
        fake(*kit.no_issue_script(19))
        result = kit.run_command(
            "--experiment", NAME, "--set", "*_right", "--assignments", "as-is", "--max-usd", "1", "--live", "--yes",
        )  # fmt: skip
        assert (result.exc, len(kit.conversations(kit.experiment(NAME)))) == (None, 19)

    def test_the_full_set_replays_through_the_command_with_replicates(self, fake):
        fake(*kit.no_issue_script(168))
        result = kit.run_command("--experiment", NAME, "--replicates", "2", "--max-usd", "5", "--live", "--yes")
        runs = kit.replay_runs(kit.experiment(NAME))
        assert (result.exc, len(runs), {r.status for r in runs}, sorted({r.replicate for r in runs})) == (None, 168, {"done"}, [1, 2])

    def test_without_live_the_full_set_is_recorded_skipped_disabled(self):
        result = kit.run_command("--experiment", NAME, "--max-usd", "1")
        runs = kit.replay_runs(kit.experiment(NAME))
        assert (result.exc, len(runs), {r.status for r in runs}) == (None, 84, {"skipped_disabled"})


@pytest.mark.parametrize("swap_pair", ["rent_factual_obvious", "drugs_abusive"])
def test_the_label_swap_series_members_are_loaded_as_their_own_conversations(swap_pair):
    """The label-swap series member (already swapped in its file) is a separate transcript, not a duplicate of the base."""
    load_all()
    exp = kit.experiment(NAME)
    member = GOLDEN[f"{swap_pair}_swapped_left"]
    base = GOLDEN[f"{swap_pair}_left"]
    assert (
        kit.authors_of(kit.conv_for(exp, member)),
        kit.authors_of(kit.conv_for(exp, base, swapped=True)),
    ) == (kit.file_labels(member), kit.flipped(kit.file_labels(base)))
