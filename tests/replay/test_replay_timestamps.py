"""Fixed message times (architect ruling, docs/step13_brief.md amendments): the same for every side and assignment."""
from datetime import timedelta

import replay_kit as kit

NAME = "exp-times"


def times(conv):
    return [m.created_at for m in kit.messages_of(conv)]


def gaps(conv):
    stamps = times(conv)
    return [(later - earlier).total_seconds() for earlier, later in zip(stamps, stamps[1:])]


class TestFixedTimes:
    def test_the_default_gap_is_ninety_seconds(self, settings):
        assert settings.REPLAY_MESSAGE_GAP_SECONDS == 90

    def test_consecutive_messages_are_the_gap_apart(self):
        data = kit.transcript("gapdefault", authors="ABABAB")
        kit.load(NAME, [data], assignments="as-is")
        assert gaps(kit.conv_for(kit.experiment(NAME), data)) == [90.0] * 5

    def test_message_i_is_at_the_fixed_base_time_plus_i_gaps(self):
        from datetime import datetime, timezone

        data = kit.transcript("basetime", authors="ABAB")
        kit.load(NAME, [data], assignments="as-is")
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert times(kit.conv_for(kit.experiment(NAME), data)) == [base + timedelta(seconds=90 * i) for i in range(4)]

    def test_the_gap_is_read_from_the_tunable(self, tune):
        tune(REPLAY_MESSAGE_GAP_SECONDS=30)
        data = kit.transcript("gapshort", authors="ABAB")
        kit.load(NAME, [data], assignments="as-is")
        assert gaps(kit.conv_for(kit.experiment(NAME), data)) == [30.0] * 3

    def test_scripted_moderator_messages_are_spaced_like_the_others(self):
        data = kit.transcript("gapmod", authors="ABMA")
        kit.load(NAME, [data], assignments="as-is")
        assert gaps(kit.conv_for(kit.experiment(NAME), data)) == [90.0] * 3

    def test_both_assignments_of_a_transcript_have_identical_times(self):
        data = kit.transcript("assignsame", authors="ABAB")
        kit.load(NAME, [data], assignments="both")
        exp = kit.experiment(NAME)
        assert times(kit.conv_for(exp, data)) == times(kit.conv_for(exp, data, swapped=True))

    def test_the_left_and_right_variants_of_a_pair_have_identical_times(self):
        left, right = kit.pair("sides")
        kit.load(NAME, [left, right], assignments="as-is")
        exp = kit.experiment(NAME)
        assert times(kit.conv_for(exp, left)) == times(kit.conv_for(exp, right))

    def test_unrelated_transcripts_of_equal_length_have_identical_times(self):
        first, second = kit.transcript("unrel1", authors="ABAB"), kit.transcript("unrel2", authors="ABAB")
        kit.load(NAME, [first, second], assignments="as-is")
        exp = kit.experiment(NAME)
        assert times(kit.conv_for(exp, first)) == times(kit.conv_for(exp, second))

    def test_the_times_do_not_depend_on_when_the_transcript_was_loaded(self):
        data = kit.transcript("whenloaded", authors="ABAB")
        kit.load("exp-times-first", [data], assignments="as-is")
        kit.load("exp-times-second", [data], assignments="as-is")
        first = times(kit.conv_for(kit.experiment("exp-times-first"), data))
        second = times(kit.conv_for(kit.experiment("exp-times-second"), data))
        assert first == second

    def test_the_times_are_not_the_load_time(self):
        from django.utils import timezone

        data = kit.transcript("notnow", authors="AB")
        kit.load(NAME, [data], assignments="as-is")
        (first, _second) = times(kit.conv_for(kit.experiment(NAME), data))
        assert abs(timezone.now() - first) > timedelta(days=1)

    def test_reloading_does_not_change_the_times(self):
        data = kit.transcript("reloadtimes", authors="ABAB")
        kit.load(NAME, [data], assignments="both")
        exp = kit.experiment(NAME)
        before = [times(c) for c in kit.conversations(exp)]
        kit.load(NAME, [data], assignments="both")
        assert [times(c) for c in kit.conversations(exp)] == before

    def test_running_the_replay_does_not_change_the_times(self, fake):
        data = kit.transcript("runtimes", authors="ABAB")
        experiment_plan = kit.load(NAME, [data], assignments="both")
        exp = kit.experiment(NAME)
        before = [times(c) for c in kit.conversations(exp)]
        specs = kit.plan(experiment_plan, replicates=1)
        fake(*kit.no_issue_script(len(specs)))
        kit.execute(specs, max_usd=50)
        assert [times(c) for c in kit.conversations(exp)] == before


class TestTheMasterSeesTheFixedGap:
    def test_the_seconds_between_the_last_two_messages_is_the_gap_for_both_assignments(self, fake):
        specs = kit.plan(kit.load(NAME, [kit.transcript("factgap", authors="ABAB")], assignments="both"), replicates=1)
        client = fake(*kit.no_issue_script(2))
        kit.execute(specs, max_usd=50)
        contents = [c["messages"][0]["content"] for c in client.calls]
        assert [_gap_fact(content) for content in contents] == ["90.0", "90.0"]

    def test_the_fact_follows_the_tunable(self, fake, tune):
        tune(REPLAY_MESSAGE_GAP_SECONDS=45)
        specs = kit.plan(kit.load(NAME, [kit.transcript("factgap2", authors="ABAB")], assignments="as-is"), replicates=1)
        client = fake(*kit.no_issue_script(1))
        kit.execute(specs, max_usd=50)
        assert _gap_fact(client.calls[0]["messages"][0]["content"]) == "45.0"


def _gap_fact(content):
    import re

    found = re.findall(r'<fact name="seconds_between_last_two_messages">([^<]*)</fact>', content)
    return found[0] if found else None
