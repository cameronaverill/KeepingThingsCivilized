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
    def test_either_length_of_conversation_passes(self, count):
        assert check(*kit.pair(count=count)) is None


class TestSameFactAndShape:
    def test_a_different_fact_id_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.base("left", "one_fact"), kit.base("right", "other_fact"))

    def test_a_different_message_count_is_refused(self):
        with pytest.raises(base_error()):
            check(kit.base("left", count=4), kit.base("right", count=6))

    def test_a_different_message_count_is_refused_even_when_the_shared_messages_match_in_length(self):
        with pytest.raises(base_error()):
            check(kit.with_lengths("left", [200] * 4), kit.with_lengths("right", [200] * 6))

    def test_a_different_message_count_is_refused_the_other_way_round(self):
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
