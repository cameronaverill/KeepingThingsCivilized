"""seeding.arms.check_pair: same fact, same count, same authors, lengths within GENERATOR_LENGTH_TOLERANCE, each base valid."""
import gen_kit as kit
import pytest


def check(left, right):
    from seeding import arms

    return arms.check_pair(left, right)


def base_error():
    from seeding import arms

    return arms.BaseError


LENGTHS = [200, 200, 200, 200]


class TestAValidPair:
    def test_the_stub_pair_passes(self):
        assert check(*kit.pair()) is None

    def test_identical_lengths_pass(self):
        assert check(kit.with_lengths("left", LENGTHS), kit.with_lengths("right", LENGTHS)) is None

    @pytest.mark.parametrize("count", [4, 6])
    def test_either_length_of_conversation_passes_when_the_range_allows_it(self, count, tune):
        tune(GENERATOR_MIN_MESSAGES=4, GENERATOR_MAX_MESSAGES=6)
        assert check(*kit.pair(count=count)) is None


class TestSameFactAndShape:
    def test_a_different_fact_id_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.base("left", "one_fact"), kit.base("right", "other_fact"))

    def test_a_different_message_count_is_refused(self, tune):
        tune(GENERATOR_MIN_MESSAGES=4, GENERATOR_MAX_MESSAGES=6)
        with pytest.raises(base_error()):
            check(kit.base("left", count=4), kit.base("right", count=6))

    def test_a_different_message_count_is_refused_even_when_the_shared_messages_match_in_length(self, tune):
        tune(GENERATOR_MIN_MESSAGES=4, GENERATOR_MAX_MESSAGES=6)
        with pytest.raises(base_error()):
            check(kit.with_lengths("left", [200] * 4), kit.with_lengths("right", [200] * 6))

    def test_a_different_message_count_is_refused_the_other_way_round(self, tune):
        tune(GENERATOR_MIN_MESSAGES=4, GENERATOR_MAX_MESSAGES=6)
        with pytest.raises(base_error()):
            check(kit.with_lengths("left", [200] * 6), kit.with_lengths("right", [200] * 4))


class TestEachBaseIsValid:
    def test_an_invalid_left_base_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.with_message(kit.base("left"), 3, text="No marker."), kit.base("right"))

    def test_an_invalid_right_base_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.base("left"), kit.with_message(kit.base("right"), 3, text="No marker."))

    def test_an_empty_text_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.base("left"), kit.with_message(kit.base("right"), 0, text=""))


class TestLengthTolerance:
    """Tolerance is 0.25 of the longer text: 100 against 75 is exactly at the limit, 100 against 74 is over it."""

    @pytest.mark.parametrize("index", [0, 1, 2, 3])
    def test_a_difference_exactly_at_the_tolerance_passes_at_every_index(self, index):
        right = list(LENGTHS)
        right[index] = 150
        left = list(LENGTHS)
        left[index] = 200
        assert check(kit.with_lengths("left", left), kit.with_lengths("right", right)) is None

    @pytest.mark.parametrize("index", [0, 1, 2, 3])
    def test_a_difference_just_over_the_tolerance_is_refused_at_every_index(self, index):
        right = list(LENGTHS)
        right[index] = 149
        with pytest.raises(base_error()):
            check(kit.with_lengths("left", LENGTHS), kit.with_lengths("right", right))

    @pytest.mark.parametrize("index", [0, 3])
    def test_the_check_is_the_same_the_other_way_round(self, index):
        right = list(LENGTHS)
        right[index] = 149
        with pytest.raises(base_error()):
            check(kit.with_lengths("left", right), kit.with_lengths("right", LENGTHS))

    def test_the_shorter_one_may_be_on_either_side_at_the_boundary(self):
        shorter = [150, 200, 200, 200]
        assert check(kit.with_lengths("left", shorter), kit.with_lengths("right", LENGTHS)) is None
        assert check(kit.with_lengths("left", LENGTHS), kit.with_lengths("right", shorter)) is None

    def test_the_tolerance_is_a_share_of_the_longer_not_the_shorter(self):
        # 100 against 75 is 25 apart: exactly 25% of the longer, but 33% of the shorter.
        assert check(kit.with_lengths("left", [100, 100, 100, 100]), kit.with_lengths("right", [75, 100, 100, 100])) is None

    def test_the_marker_counts_as_its_own_length_in_the_last_message(self):
        left = kit.with_lengths("left", [100, 100, 100, 100])
        right = kit.with_lengths("right", [100, 100, 100, 74])
        with pytest.raises(base_error()):
            check(left, right)

    def test_the_tolerance_is_read_from_the_tunables_at_call_time(self, tune):
        left, right = kit.with_lengths("left", LENGTHS), kit.with_lengths("right", [100, 200, 200, 200])
        with pytest.raises(base_error()):
            check(left, right)
        tune(GENERATOR_LENGTH_TOLERANCE=0.5)
        assert check(left, right) is None

    def test_a_zero_tolerance_needs_equal_lengths(self, tune):
        tune(GENERATOR_LENGTH_TOLERANCE=0.0)
        assert check(kit.with_lengths("left", LENGTHS), kit.with_lengths("right", LENGTHS)) is None
        with pytest.raises(base_error()):
            check(kit.with_lengths("left", LENGTHS), kit.with_lengths("right", [199, 200, 200, 200]))


def pair_with_ratio(index, keep):
    """A pair identical in shape whose message `index` has SequenceMatcher ratio keep/100: the two texts share `keep` characters
    (equal length 100, so the length check is not in play); every other message is a different letter, so its ratio is 0."""
    lengths = [100, 100, 100, 100]
    left, right = kit.with_lengths("left", lengths), kit.with_lengths("right", lengths)
    if index == 3:
        tail = ". [[CLAIM]]."
        head = 100 - len(tail)
        shared = keep - len(tail)
        left["messages"][3]["text"] = "x" * shared + "y" * (head - shared) + tail
        right["messages"][3]["text"] = "x" * shared + "z" * (head - shared) + tail
    else:
        left["messages"][index]["text"] = "x" * keep + "y" * (100 - keep)
        right["messages"][index]["text"] = "x" * keep + "z" * (100 - keep)
    return left, right


class TestSimilarityGuard:
    """A mirrored message must not be a near copy: difflib ratio must be BELOW GENERATOR_MAX_SIMILARITY (0.6)."""

    @pytest.mark.parametrize("index", [0, 1, 2, 3])
    def test_a_ratio_just_below_the_limit_passes_at_every_index(self, index):
        assert check(*pair_with_ratio(index, 59)) is None

    @pytest.mark.parametrize("index", [0, 1, 2, 3])
    def test_a_ratio_exactly_at_the_limit_is_refused_at_every_index(self, index):
        with pytest.raises(base_error()):
            check(*pair_with_ratio(index, 60))

    @pytest.mark.parametrize("index", [0, 3])
    def test_a_ratio_above_the_limit_is_refused(self, index):
        with pytest.raises(base_error()):
            check(*pair_with_ratio(index, 90))

    def test_identical_bases_are_refused(self):
        with pytest.raises(base_error()):
            check(kit.base("left"), kit.edited(kit.base("left"), side="right"))

    def test_the_limit_is_read_from_the_tunables_at_call_time(self, tune):
        pair = pair_with_ratio(0, 60)
        with pytest.raises(base_error()):
            check(*pair)
        tune(GENERATOR_MAX_SIMILARITY=0.61)
        assert check(*pair) is None

    def test_a_lower_limit_refuses_what_the_default_allows(self, tune):
        pair = pair_with_ratio(0, 59)
        tune(GENERATOR_MAX_SIMILARITY=0.5)
        with pytest.raises(base_error()):
            check(*pair)

    def test_the_stub_pair_is_well_below_the_limit(self):
        import difflib

        left, right = kit.pair()
        ratios = [difflib.SequenceMatcher(None, a["text"], b["text"]).ratio() for a, b in zip(left["messages"], right["messages"])]
        assert max(ratios) < 0.6

    def test_a_near_copy_is_refused_by_build_transcripts(self):
        from seeding import arms

        left, right = pair_with_ratio(1, 90)
        with pytest.raises(arms.BaseError):
            kit.transcripts(kit.range_fact(), left, right)

    def test_the_marker_counts_in_the_comparison(self):
        # Two last messages that differ everywhere except the marker still share the marker's characters.
        left = kit.with_lengths("left", [100, 100, 100, 20])
        right = kit.with_lengths("right", [100, 100, 100, 20])
        left["messages"][3]["text"] = "x" + ". [[CLAIM]]."
        right["messages"][3]["text"] = "q" + ". [[CLAIM]]."
        with pytest.raises(base_error()):
            check(left, right)
