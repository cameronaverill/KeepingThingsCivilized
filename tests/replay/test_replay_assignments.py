"""Label assignments (as-is, swapped, both) and idempotent loading (docs/step13_brief.md)."""
import pytest
import replay_kit as kit
from django.core.management.base import CommandError

NAME = "exp-assign"
REJECTED = (ValueError, CommandError)


def counts(exp):
    from forum.models import Message, Participant

    convs = kit.conversations(exp)
    return (
        len(convs),
        Participant.objects.filter(conversation__in=convs).count(),
        Message.objects.filter(conversation__in=convs).count(),
    )


class TestAssignments:
    def test_as_is_makes_one_conversation_per_transcript(self):
        kit.load(NAME, kit.pair("aa"), assignments="as-is")
        assert counts(kit.experiment(NAME)) == (2, 4, 8)

    def test_swapped_makes_one_conversation_per_transcript(self):
        kit.load(NAME, kit.pair("ss"), assignments="swapped")
        assert counts(kit.experiment(NAME)) == (2, 4, 8)

    def test_both_makes_two_conversations_per_transcript(self):
        kit.load(NAME, kit.pair("bb"), assignments="both")
        assert counts(kit.experiment(NAME)) == (4, 8, 16)

    def test_the_default_assignment_is_as_is(self):
        kit.load(NAME, kit.pair("dd"))
        assert counts(kit.experiment(NAME)) == (2, 4, 8)

    def test_an_unknown_assignment_is_refused_and_writes_nothing(self):
        from forum.models import Experiment

        with pytest.raises((ValueError, kit_command_error())):
            kit.load(NAME, kit.pair("uu"), assignments="sideways")
        assert Experiment.objects.count() == 0

    def test_as_is_authorship_is_the_files(self):
        data = kit.transcript("asis", authors="ABBA")
        kit.load(NAME, [data], assignments="as-is")
        conv = kit.conv_for(kit.experiment(NAME), data)
        assert kit.authors_of(conv) == ["A", "B", "B", "A"]

    def test_swapped_authorship_is_the_mirror_of_the_files(self):
        data = kit.transcript("mirror", authors="ABBA")
        kit.load(NAME, [data], assignments="swapped")
        (conv,) = kit.conversations(kit.experiment(NAME))
        assert kit.authors_of(conv) == ["B", "A", "A", "B"]

    def test_swapped_keeps_every_text_and_every_seq_no_unchanged(self):
        data = kit.transcript("texts", authors="ABBA")
        kit.load(NAME, [data], assignments="both")
        exp = kit.experiment(NAME)
        as_is, swapped = kit.conv_for(exp, data), kit.conv_for(exp, data, swapped=True)
        assert [(m.seq_no, m.content) for m in kit.messages_of(as_is)] == [
            (m.seq_no, m.content) for m in kit.messages_of(swapped)
        ]

    def test_swapped_keeps_planted_char_counts_and_author_types(self):
        data = kit.with_planted(kit.transcript("keeps", authors="ABMA"), 4)
        kit.load(NAME, [data], assignments="both")
        exp = kit.experiment(NAME)
        as_is, swapped = kit.conv_for(exp, data), kit.conv_for(exp, data, swapped=True)
        assert shape(as_is) == shape(swapped)

    def test_a_scripted_moderator_message_stays_a_moderator_message_when_swapped(self):
        data = kit.transcript("modswap", authors="ABMA")
        kit.load(NAME, [data], assignments="both")
        swapped = kit.conv_for(kit.experiment(NAME), data, swapped=True)
        assert kit.authors_of(swapped) == ["B", "A", None, "B"]

    def test_both_assignments_of_a_transcript_share_its_pair_id_and_variant(self):
        (left, right) = kit.pair("shared")
        kit.load(NAME, [left, right], assignments="both")
        exp = kit.experiment(NAME)
        rows = [(c.pair_id, c.variant) for c in kit.conversations(exp)]
        assert sorted(rows) == [("shared", "left"), ("shared", "left"), ("shared", "right"), ("shared", "right")]

    def test_the_two_assignments_are_separate_conversations_with_their_own_seed(self):
        data = kit.transcript("seeds")
        kit.load(NAME, [data], assignments="both")
        exp = kit.experiment(NAME)
        first, second = kit.conv_for(exp, data), kit.conv_for(exp, data, swapped=True)
        assert (first.pk != second.pk, first.label_seed is not None, second.label_seed is not None) == (True, True, True)
        assert first.label_seed != second.label_seed

    def test_each_conversation_has_its_own_participants(self):
        data = kit.transcript("ownparts")
        kit.load(NAME, [data], assignments="both")
        exp = kit.experiment(NAME)
        first, second = kit.conv_for(exp, data), kit.conv_for(exp, data, swapped=True)
        assert set(first.participants.values_list("pk", flat=True)).isdisjoint(second.participants.values_list("pk", flat=True))

    def test_the_seeds_do_not_change_when_more_transcripts_are_loaded(self):
        first = kit.transcript("stable1")
        kit.load(NAME, [first], assignments="both")
        exp = kit.experiment(NAME)
        before = sorted((c.pk, c.label_seed) for c in kit.conversations(exp))
        kit.load(NAME, [first, kit.transcript("stable2")], assignments="both")
        after = sorted((c.pk, c.label_seed) for c in kit.conversations(exp) if c.pk in dict(before))
        assert after == before


def shape(conv):
    return [(m.planted, m.char_count, m.author_type) for m in kit.messages_of(conv)]


def kit_command_error():
    from django.core.management.base import CommandError

    return CommandError


class TestIdempotentLoading:
    def test_loading_twice_with_the_same_arguments_creates_nothing_new(self):
        from forum.models import Experiment, Topic

        transcripts = kit.pair("twice") + [kit.transcript("twice_single", authors="ABMA")]
        kit.load(NAME, transcripts, assignments="both")
        exp = kit.experiment(NAME)
        before = (counts(exp), Experiment.objects.count(), Topic.objects.count(), sorted(c.pk for c in kit.conversations(exp)))
        kit.load(NAME, transcripts, assignments="both")
        after = (counts(exp), Experiment.objects.count(), Topic.objects.count(), sorted(c.pk for c in kit.conversations(exp)))
        assert after == before

    def test_the_second_load_returns_a_plan_over_the_same_conversations(self):
        transcripts = kit.pair("samepk")
        first = kit.plan(kit.load(NAME, transcripts, assignments="both"), replicates=1)
        second = kit.plan(kit.load(NAME, transcripts, assignments="both"), replicates=1)
        assert [kit.spec_key(s) for s in first] == [kit.spec_key(s) for s in second]

    def test_a_reload_leaves_stored_messages_unchanged(self):
        data = kit.transcript("frozen")
        kit.load(NAME, [data], assignments="both")
        exp = kit.experiment(NAME)
        before = [(m.pk, m.content, m.char_count) for c in kit.conversations(exp) for m in kit.messages_of(c)]
        kit.load(NAME, [data], assignments="both")
        after = [(m.pk, m.content, m.char_count) for c in kit.conversations(exp) for m in kit.messages_of(c)]
        assert after == before

    def test_a_reload_after_runs_creates_nothing(self, fake):
        transcripts = kit.pair("afterrun")
        experiment_plan = kit.load(NAME, transcripts, assignments="both")
        specs = kit.plan(experiment_plan, replicates=1)
        fake(*kit.no_issue_script(len(specs)))
        kit.execute(specs, max_usd=10)
        before = kit.table_counts()
        kit.load(NAME, transcripts, assignments="both")
        assert kit.table_counts() == before

    def test_asking_for_more_assignments_later_adds_only_the_missing_conversations(self):
        transcripts = kit.pair("grow")
        kit.load(NAME, transcripts, assignments="as-is")
        exp = kit.experiment(NAME)
        original = {c.pk for c in kit.conversations(exp)}
        kit.load(NAME, transcripts, assignments="both")
        now = {c.pk for c in kit.conversations(exp)}
        assert (len(now), original <= now) == (4, True)

    def test_a_different_experiment_name_gets_its_own_conversations(self):
        transcripts = kit.pair("names")
        kit.load("exp-one", transcripts, assignments="as-is")
        kit.load("exp-two", transcripts, assignments="as-is")
        one, two = kit.conversations(kit.experiment("exp-one")), kit.conversations(kit.experiment("exp-two"))
        assert (len(one), len(two), {c.pk for c in one} & {c.pk for c in two}) == (2, 2, set())

    def test_a_new_transcript_added_to_an_existing_experiment_is_loaded(self):
        kit.load(NAME, [kit.transcript("early")], assignments="as-is")
        kit.load(NAME, [kit.transcript("early"), kit.transcript("late")], assignments="as-is")
        assert len(kit.conversations(kit.experiment(NAME))) == 2


class TestAChangedTranscriptIsNeverSilentlyReused:
    def test_a_changed_message_text_under_the_same_experiment_name_is_refused_and_changes_nothing(self):
        import copy

        original = kit.transcript("changed")
        kit.load(NAME, [original], assignments="both")
        before = kit.table_counts()
        edited = copy.deepcopy(original)
        edited["messages"][1]["text"] = "A completely different second message."
        with pytest.raises(REJECTED):
            kit.load(NAME, [edited], assignments="both")
        assert kit.table_counts() == before

    def test_a_changed_planted_item_is_refused_too(self):
        original = kit.transcript("changedplant")
        kit.load(NAME, [original], assignments="as-is")
        with pytest.raises(REJECTED):
            kit.load(NAME, [kit.with_planted(kit.transcript("changedplant"), 4)], assignments="as-is")

    def test_a_changed_pair_id_is_refused_too(self):
        kit.load(NAME, [kit.transcript("changedpair", pair_id="p1", variant="left")], assignments="as-is")
        with pytest.raises(REJECTED):
            kit.load(NAME, [kit.transcript("changedpair", pair_id="p2", variant="left")], assignments="as-is")

    def test_the_same_transcript_under_a_new_experiment_name_may_differ(self):
        import copy

        original = kit.transcript("newname")
        kit.load(NAME, [original], assignments="as-is")
        edited = copy.deepcopy(original)
        edited["messages"][1]["text"] = "A completely different second message."
        kit.load("exp-assign-other", [edited], assignments="as-is")
        assert len(kit.conversations(kit.experiment("exp-assign-other"))) == 1


class TestArgumentChecks:
    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_replicate_count_below_one_is_refused_when_loading_and_writes_nothing(self, bad):
        with pytest.raises(REJECTED):
            kit.load(NAME, [kit.transcript("badreps")], replicates=bad)
        assert kit.table_counts()["Experiment"] == 0

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_replicate_count_below_one_is_refused_when_planning(self, bad):
        experiment_plan = kit.load(NAME, [kit.transcript("badreps2")])
        with pytest.raises(REJECTED):
            kit.plan(experiment_plan, replicates=bad)

    def test_an_empty_experiment_name_is_refused(self):
        with pytest.raises(REJECTED):
            kit.load("  ", [kit.transcript("noname")])
        assert kit.table_counts()["Experiment"] == 0

    def test_an_empty_set_of_transcripts_is_refused(self):
        with pytest.raises(REJECTED):
            kit.load(NAME, [])
        assert kit.table_counts()["Experiment"] == 0

    def test_an_unknown_kind_is_refused_and_writes_nothing(self):
        with pytest.raises(REJECTED):
            kit.load(NAME, [kit.transcript("badkind")], kind="observed")
        assert kit.table_counts()["Experiment"] == 0
