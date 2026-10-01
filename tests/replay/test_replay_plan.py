"""plan_runs: one run spec per conversation, trigger and replicate; factors(conversation) (docs/step13_brief.md)."""
import replay_kit as kit

NAME = "exp-plan"


def specs_for(transcripts, *, assignments="both", replicates=1):
    experiment_plan = kit.load(NAME, transcripts, assignments=assignments, replicates=replicates)
    return kit.plan(experiment_plan, replicates=replicates)


class TestSpecs:
    def test_one_spec_per_conversation_for_one_replicate(self):
        specs = specs_for(kit.pair("one"))
        assert len(specs) == 4

    def test_replicates_multiply_the_specs(self):
        specs = specs_for(kit.pair("reps"), replicates=3)
        assert len(specs) == 12

    def test_replicates_are_numbered_from_one_for_each_conversation(self):
        specs = specs_for([kit.transcript("numbered")], replicates=3)
        assert sorted(sorted(v) for v in kit.replicates_by_conversation(specs).values()) == [[1, 2, 3], [1, 2, 3]]

    def test_every_spec_key_is_unique(self):
        specs = specs_for(kit.pair("uniq"), replicates=2)
        keys = [kit.spec_key(s) for s in specs]
        assert len(set(keys)) == len(keys) == 8

    def test_the_trigger_is_the_files_trigger_seq_message_of_that_conversation(self):
        data = kit.transcript("trig", authors="ABABA", trigger_seq=3)
        specs = specs_for([data])
        assert [(kit.spec_trigger(s).seq_no, kit.spec_trigger(s).conversation_id == kit.spec_conversation(s).pk) for s in specs] == [
            (3, True),
            (3, True),
        ]

    def test_the_trigger_is_not_simply_the_last_message(self):
        data = kit.transcript("trig_mid", authors="ABABAB", trigger_seq=2)
        specs = specs_for([data], assignments="as-is")
        assert [kit.spec_trigger(s).seq_no for s in specs] == [2]

    def test_the_trigger_is_a_user_message(self):
        data = kit.transcript("trig_user", authors="ABMA")
        specs = specs_for([data])
        assert {kit.spec_trigger(s).author_type for s in specs} == {"user"}

    def test_the_trigger_of_a_swapped_conversation_is_the_same_seq_by_the_mirrored_participant(self):
        data = kit.transcript("trig_swap", authors="ABBA")
        specs = specs_for([data])
        exp = kit.experiment(NAME)
        swapped = kit.conv_for(exp, data, swapped=True)
        (spec,) = [s for s in specs if kit.spec_conversation(s).pk == swapped.pk]
        assert (kit.spec_trigger(spec).seq_no, kit.participant_label_of(kit.spec_trigger(spec))) == (4, "B")

    def test_snapshot_seq_is_the_trigger_seq_no(self):
        specs = specs_for([kit.transcript("snap", authors="ABABA", trigger_seq=4)])
        assert [(kit.spec_snapshot_seq(s), kit.spec_trigger(s).seq_no) for s in specs] == [(4, 4), (4, 4)]

    def test_a_singles_trigger_can_be_an_early_message(self):
        data = kit.transcript("single_trig", authors="AB", trigger_seq=1)
        specs = specs_for([data], assignments="as-is")
        assert [kit.spec_trigger(s).seq_no for s in specs] == [1]

    def test_planning_creates_no_run_and_no_ledger_row(self):
        from moderation.models import LLMCall, ModerationRun

        specs_for(kit.pair("nowrite"), replicates=2)
        assert (ModerationRun.objects.count(), LLMCall.objects.count()) == (0, 0)


class TestDeterministicOrder:
    def test_planning_twice_gives_the_same_specs_in_the_same_order(self):
        experiment_plan = kit.load(NAME, kit.pair("det") + [kit.transcript("det_single")], assignments="both")
        first = [kit.spec_key(s) for s in kit.plan(experiment_plan, replicates=2)]
        second = [kit.spec_key(s) for s in kit.plan(experiment_plan, replicates=2)]
        assert first == second

    def test_the_order_is_replicate_major(self):
        keys = [kit.spec_key(s) for s in specs_for(kit.pair("grouped"), replicates=3)]
        assert [k[2] for k in keys] == [1] * 4 + [2] * 4 + [3] * 4

    def test_within_a_replicate_the_order_is_pair_then_variant_then_assignment(self):
        transcripts = kit.pair("bb") + kit.pair("aa")  # given out of order on purpose
        specs = specs_for(transcripts, replicates=2)
        got = [
            (kit.spec_replicate(s), kit.spec_conversation(s).pair_id, kit.spec_conversation(s).variant, kit.spec_assignment(s))
            for s in specs
        ]
        one_round = [
            (pair, variant, assignment)
            for pair in ("aa", "bb")
            for variant in ("left", "right")
            for assignment in ("as-is", "swapped")
        ]
        assert got == [(1, *row) for row in one_round] + [(2, *row) for row in one_round]

    def test_the_same_specs_are_executed_in_that_order(self, fake):
        specs = specs_for(kit.pair("cc"), replicates=2)
        fake(*kit.no_issue_script(len(specs)))
        kit.execute(specs, max_usd=50)
        runs = kit.replay_runs(kit.experiment(NAME))
        assert [(r.conversation_id, r.trigger_message_id, r.replicate) for r in runs] == [kit.spec_key(s) for s in specs]

    def test_a_fresh_load_of_the_same_transcripts_plans_the_same_order(self):
        transcripts = kit.pair("fresh") + [kit.transcript("fresh_single", authors="ABMA")]
        first = [kit.spec_key(s) for s in specs_for(transcripts)]
        second = [kit.spec_key(s) for s in specs_for(transcripts)]
        assert first == second

    def test_the_order_does_not_depend_on_the_order_the_transcripts_are_given_in(self):
        transcripts = kit.pair("shuffle") + [kit.transcript("shuffle_single")]
        forward = [kit.spec_key(s) for s in specs_for(transcripts)]
        backward = [kit.spec_key(s) for s in specs_for(list(reversed(transcripts)))]
        assert forward == backward


class TestPairIdGroupingOrder:
    """plan_runs orders by (pair_id or source_id, variant, assignment-index, source_id): a transcript's pair_id need not
    be a prefix of its own id, so alphabetical-by-id ordering must not be mistaken for pair_id grouping."""

    def test_conversations_are_grouped_by_pair_id_not_by_alphabetical_transcript_id(self):
        zeta_left = kit.transcript("aaa_left", pair_id="zeta", variant="left")
        zeta_right = kit.transcript("aaa_right", pair_id="zeta", variant="right")
        alpha_solo = kit.transcript("zzz_solo", pair_id="alpha")
        experiment_plan = kit.load(NAME, [zeta_left, zeta_right, alpha_solo], assignments="as-is")

        specs = kit.plan(experiment_plan, replicates=1)

        got = [kit.spec_conversation(s).transcript_id for s in specs]
        assert got == ["zzz_solo", "aaa_left", "aaa_right"]


class TestFactors:
    def test_factors_equal_compute_features_on_the_transcript(self):
        from moderation import replay
        from moderation.transcripts import compute_features

        data = kit.transcript("fx", authors="ABBBB")
        kit.load(NAME, [data], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        assert replay.factors(conv) == compute_features(data["messages"], data["trigger_seq"])

    def test_factors_are_computed_from_the_stored_messages_not_the_file(self):
        from forum.models import Message
        from moderation import replay

        data = kit.transcript("fx_stored", authors="ABAB")
        kit.load(NAME, [data], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        third = Message.objects.get(conversation=conv, seq_no=3)
        Message.objects.filter(pk=third.pk).update(participant=conv.participants.get(label="B"))
        assert replay.factors(conv)["longest_consecutive_run"] == 3

    def test_factors_report_a_flood_a_repeat_and_an_unanswered_question(self):
        from moderation import replay

        flood = kit.transcript("fx_flood", authors="ABBBB")
        repeat_messages = [
            kit.message("fx_rep", 1, "Participant A", text="The rent cap is unfair to owners today. Please answer."),
            kit.message("fx_rep", 2, "Participant B", text="I disagree with that view, for reasons of my own."),
            kit.message("fx_rep", 3, "Participant A", text="The rent cap is unfair to owners today. Also another point."),
        ]
        repeat = kit.transcript("fx_rep", messages=repeat_messages)
        question_messages = [
            kit.message("fx_q", 1, "Participant A", text="What evidence supports that claim?"),
            kit.message("fx_q", 2, "Participant B", text="Here is one thing I believe."),
            kit.message("fx_q", 3, "Participant B", text="Here is another thing I believe."),
        ]
        question = kit.transcript("fx_q", messages=question_messages)
        kit.load(NAME, [flood, repeat, question], assignments="as-is")
        exp = kit.experiment(NAME)
        got = {
            "flood": replay.factors(kit.conv_for(exp, flood))["longest_consecutive_run"],
            "repeat": replay.factors(kit.conv_for(exp, repeat))["repeated_sentence_across_messages"],
            "question": replay.factors(kit.conv_for(exp, question))["unanswered_question_followed_by_two_replies"],
        }
        assert got == {"flood": 4, "repeat": True, "question": True}

    def test_the_factors_of_a_swapped_conversation_equal_those_of_the_file(self):
        from moderation import replay
        from moderation.transcripts import compute_features

        data = kit.transcript("fx_swap", authors="ABBBB")
        kit.load(NAME, [data], assignments="both")
        exp = kit.experiment(NAME)
        swapped = kit.conv_for(exp, data, swapped=True)
        assert replay.factors(swapped) == compute_features(data["messages"], data["trigger_seq"])

    def test_factors_count_only_messages_up_to_the_trigger(self):
        from moderation import replay
        from moderation.transcripts import compute_features

        data = kit.transcript("fx_trig", authors="ABBBBA", trigger_seq=3)
        kit.load(NAME, [data], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        factors = replay.factors(conv)
        assert (factors["longest_consecutive_run"], factors) == (2, compute_features(data["messages"], 3))

    def test_a_scripted_moderator_message_counts_as_its_own_author_in_the_factors(self):
        from moderation import replay
        from moderation.transcripts import compute_features

        data = kit.transcript("fx_mod", authors="ABBMBA", trigger_seq=6)
        kit.load(NAME, [data], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        assert replay.factors(conv) == compute_features(data["messages"], data["trigger_seq"])

    def test_the_factors_are_json_serializable(self):
        import json

        from moderation import replay

        kit.load(NAME, [kit.transcript("fx_json")], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        assert json.loads(json.dumps(replay.factors(conv))) == replay.factors(conv)
