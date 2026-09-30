"""level_factor, direction_for_side, seeded_values, format_value."""
import pytest
import seeding_kit as kit
from config import tunables
from seeding.seeds import SeedError, direction_for_side, format_value, level_factor, seeded_values


class TestLevelFactor:
    @pytest.mark.parametrize("level,factor", [(1, 1.10), (2, 1.50), (3, 3.00)])
    def test_reads_configured_factors(self, level, factor):
        assert level_factor(level) == factor

    @pytest.mark.parametrize("level", [0, 4, -1, 10])
    def test_unknown_level_raises(self, level):
        with pytest.raises(SeedError):
            level_factor(level)

    def test_seed_error_is_value_error(self):
        assert issubclass(SeedError, ValueError)

    def test_monkeypatched_tunable_is_used(self, monkeypatch):
        monkeypatch.setattr(tunables, "SEED_LEVEL_FACTORS", {1: 2.0, 2: 4.0, 3: 8.0})
        assert [level_factor(1), level_factor(2), level_factor(3)] == [2.0, 4.0, 8.0]

    def test_monkeypatched_tunable_can_add_or_remove_levels(self, monkeypatch):
        monkeypatch.setattr(tunables, "SEED_LEVEL_FACTORS", {1: 2.0, 4: 5.0})
        assert level_factor(4) == 5.0
        with pytest.raises(SeedError):
            level_factor(2)


class TestDirectionForSide:
    @pytest.mark.parametrize("favors,side,expected", [
        ("left", "left", "inflate"), ("left", "right", "deflate"),
        ("right", "right", "inflate"), ("right", "left", "deflate"),
    ])
    def test_direction(self, favors, side, expected):
        assert direction_for_side(kit.ready_stat(inflate_favors=favors), side) == expected

    @pytest.mark.parametrize("bad", ["center", "", None, "Left", 1])
    @pytest.mark.parametrize("favors", ["left", "right"])
    def test_invalid_side_raises_even_when_inflate_favors_set(self, favors, bad):
        with pytest.raises(SeedError):
            direction_for_side(kit.ready_stat(inflate_favors=favors), bad)

    @pytest.mark.parametrize("bad", ["center", ""])
    def test_invalid_side_raises_when_inflate_favors_unset(self, bad):
        with pytest.raises(SeedError):
            direction_for_side(kit.stat(), bad)

    def test_inflate_favors_none_raises(self):
        with pytest.raises(SeedError):
            direction_for_side(kit.stat(), "left")

    @pytest.mark.parametrize("kind", ["law", "qualitative"])
    def test_non_statistic_raises(self, kind):
        with pytest.raises(SeedError):
            direction_for_side(kit.ready_nonstat(kind), "left")


class TestSeededValues:
    @pytest.mark.parametrize("level,expected", [(1, [550, 616]), (2, [750, 840]), (3, [1500, 1680])])
    def test_inflate_integer_range(self, level, expected):
        assert seeded_values(kit.ready_range(), "inflate", level) == expected

    @pytest.mark.parametrize("level,expected", [(1, [455, 509]), (2, [333, 373]), (3, [167, 187])])
    def test_deflate_integer_range(self, level, expected):
        assert seeded_values(kit.ready_range(), "deflate", level) == expected

    def test_integer_results_are_ints(self):
        vals = seeded_values(kit.ready_range(), "inflate", 1)
        assert [type(v) for v in vals] == [int, int]

    @pytest.mark.parametrize("level,expected", [(1, 110), (2, 150), (3, 300)])
    def test_inflate_non_integer_unrounded(self, level, expected):
        assert seeded_values(kit.ready_stat(), "inflate", level) == [pytest.approx(expected)]

    @pytest.mark.parametrize("level,expected", [(1, 100 / 1.1), (2, 100 / 1.5), (3, 100 / 3.0)])
    def test_deflate_non_integer_unrounded(self, level, expected):
        assert seeded_values(kit.ready_stat(), "deflate", level) == [pytest.approx(expected, rel=1e-12)]

    def test_uses_monkeypatched_factor(self, monkeypatch):
        monkeypatch.setattr(tunables, "SEED_LEVEL_FACTORS", {1: 2.0, 2: 1.5, 3: 3.0})
        assert seeded_values(kit.ready_stat(), "inflate", 1) == [pytest.approx(200)]

    def test_unknown_level_raises(self):
        with pytest.raises(SeedError):
            seeded_values(kit.ready_stat(), "inflate", 4)

    def test_returns_list_same_length_as_true_values(self):
        assert len(seeded_values(kit.ready_stat(), "inflate", 1)) == 1

    # integer rounding: half away from zero (not banker's rounding)
    def test_inflate_half_rounds_up_not_to_even(self, monkeypatch):
        monkeypatch.setattr(tunables, "SEED_LEVEL_FACTORS", {1: 1.5, 2: 1.5, 3: 1.5})
        assert seeded_values(kit.ready_stat(true_values=[3], integer=True), "inflate", 1) == [5]  # 4.5

    def test_deflate_half_rounds_up_not_to_even(self, monkeypatch):
        monkeypatch.setattr(tunables, "SEED_LEVEL_FACTORS", {1: 2.0, 2: 2.0, 3: 2.0})
        assert seeded_values(kit.ready_stat(true_values=[5], integer=True), "deflate", 1) == [3]  # 2.5

    def test_inflate_half_rounds_up_odd_neighbour(self, monkeypatch):
        monkeypatch.setattr(tunables, "SEED_LEVEL_FACTORS", {1: 1.5, 2: 1.5, 3: 1.5})
        assert seeded_values(kit.ready_stat(true_values=[5], integer=True), "inflate", 1) == [8]  # 7.5

    def test_just_below_half_rounds_down(self, monkeypatch):
        monkeypatch.setattr(tunables, "SEED_LEVEL_FACTORS", {1: 1.4, 2: 1.5, 3: 3.0})
        assert seeded_values(kit.ready_stat(true_values=[11], integer=True), "inflate", 1) == [15]  # 15.4

    def test_just_above_half_rounds_up(self, monkeypatch):
        monkeypatch.setattr(tunables, "SEED_LEVEL_FACTORS", {1: 1.6, 2: 1.5, 3: 3.0})
        assert seeded_values(kit.ready_stat(true_values=[11], integer=True), "inflate", 1) == [18]  # 17.6

    # overrides
    def test_override_returned_verbatim(self):
        f = kit.ready_range(level_overrides={2: {"inflate": [600.0, 650.0]}})
        assert seeded_values(f, "inflate", 2) == [600.0, 650.0]

    def test_override_not_rounded_or_scaled_for_integer_fact(self):
        f = kit.ready_range(level_overrides={1: {"deflate": [400.0, 450.0]}})
        assert seeded_values(f, "deflate", 1) == [400.0, 450.0]

    def test_override_for_other_direction_falls_back_to_formula(self):
        f = kit.ready_range(level_overrides={2: {"inflate": [600.0, 650.0]}})
        assert seeded_values(f, "deflate", 2) == [333, 373]

    def test_override_for_other_level_falls_back_to_formula(self):
        f = kit.ready_range(level_overrides={2: {"inflate": [600.0, 650.0]}})
        assert seeded_values(f, "inflate", 3) == [1500, 1680]

    def test_override_allows_value_equal_to_max(self):
        f = kit.ready_stat(true_values=[60, 70], claim_template="{v0} to {v1}", max_value=100,
                           level_overrides={3: {"inflate": [95, 100]}})
        assert seeded_values(f, "inflate", 3) == [95, 100]

    # errors
    def test_result_over_max_value_raises(self):
        f = kit.ready_stat(true_values=[60], max_value=100)
        with pytest.raises(SeedError):
            seeded_values(f, "inflate", 3)  # 180

    def test_result_at_max_value_is_allowed(self, monkeypatch):
        monkeypatch.setattr(tunables, "SEED_LEVEL_FACTORS", {1: 2.0, 2: 1.5, 3: 3.0})
        f = kit.ready_stat(true_values=[50], max_value=100)
        assert seeded_values(f, "inflate", 1) == [pytest.approx(100)]

    def test_result_just_over_max_value_raises(self, monkeypatch):
        monkeypatch.setattr(tunables, "SEED_LEVEL_FACTORS", {1: 2.01, 2: 1.5, 3: 3.0})
        f = kit.ready_stat(true_values=[50], max_value=100)
        with pytest.raises(SeedError):
            seeded_values(f, "inflate", 1)  # 100.5

    def test_max_value_only_breached_by_second_value_raises(self):
        f = kit.ready_stat(true_values=[10, 70], claim_template="{v0} {v1}", max_value=100)
        with pytest.raises(SeedError):
            seeded_values(f, "inflate", 3)  # 30, 210

    def test_deflate_never_hits_max_value(self):
        f = kit.ready_stat(true_values=[60], max_value=100)
        assert seeded_values(f, "deflate", 3) == [pytest.approx(20)]

    def test_unchanged_after_rounding_raises(self):
        f = kit.ready_stat(true_values=[1], integer=True)
        with pytest.raises(SeedError):
            seeded_values(f, "inflate", 1)  # 1.1 -> 1

    def test_unchanged_after_rounding_raises_for_deflate(self):
        f = kit.ready_stat(true_values=[1], integer=True)
        with pytest.raises(SeedError):
            seeded_values(f, "deflate", 1)  # 0.909 -> 1

    def test_two_values_where_only_one_changes_is_fine(self):
        f = kit.ready_stat(true_values=[2, 20], claim_template="{v0} {v1}", integer=True)
        assert seeded_values(f, "inflate", 1) == [2, 22]

    def test_rounding_collapses_range_to_equal_values_raises(self, monkeypatch):
        f = kit.ready_stat(true_values=[1, 2], claim_template="{v0} {v1}", integer=True)
        with pytest.raises(SeedError):
            seeded_values(f, "deflate", 2)  # 0.667 -> 1, 1.333 -> 1

    def test_override_not_increasing_raises(self):
        f = kit.ready_range(level_overrides={1: {"inflate": [650.0, 600.0]}})
        with pytest.raises(SeedError):
            seeded_values(f, "inflate", 1)

    def test_override_equal_values_raises(self):
        f = kit.ready_range(level_overrides={1: {"inflate": [600.0, 600.0]}})
        with pytest.raises(SeedError):
            seeded_values(f, "inflate", 1)


class TestOverrideRules:
    def test_override_above_max_value_raises(self):
        f = kit.ready_stat(true_values=[60], max_value=100, level_overrides={3: {"inflate": [101.0]}})
        with pytest.raises(SeedError):
            seeded_values(f, "inflate", 3)

    def test_override_second_value_above_max_value_raises(self):
        f = kit.ready_stat(true_values=[60, 70], claim_template="{v0} {v1}", max_value=100,
                           level_overrides={3: {"inflate": [95.0, 100.5]}})
        with pytest.raises(SeedError):
            seeded_values(f, "inflate", 3)

    def test_override_equal_to_true_values_raises(self):
        f = kit.ready_stat(level_overrides={1: {"inflate": [100.0]}})
        with pytest.raises(SeedError):
            seeded_values(f, "inflate", 1)

    def test_override_equal_to_true_values_raises_for_range(self):
        f = kit.ready_range(level_overrides={1: {"deflate": [500.0, 560.0]}})
        with pytest.raises(SeedError):
            seeded_values(f, "deflate", 1)

    def test_override_decreasing_range_raises(self):
        f = kit.ready_range(level_overrides={2: {"deflate": [400.0, 300.0]}})
        with pytest.raises(SeedError):
            seeded_values(f, "deflate", 2)

    def test_integer_rounding_applies_to_override_values(self):
        f = kit.ready_range(level_overrides={1: {"inflate": [600.4, 650.6]}})
        assert seeded_values(f, "inflate", 1) == [600, 651]

    def test_integer_override_rounds_half_away(self):
        f = kit.ready_stat(true_values=[100], integer=True, level_overrides={1: {"inflate": [120.5]}})
        assert seeded_values(f, "inflate", 1) == [121]

    def test_integer_override_results_are_ints(self):
        f = kit.ready_range(level_overrides={1: {"inflate": [600.4, 650.6]}})
        assert [type(v) for v in seeded_values(f, "inflate", 1)] == [int, int]

    def test_non_integer_override_not_rounded(self):
        f = kit.ready_stat(level_overrides={1: {"inflate": [120.5]}})
        assert seeded_values(f, "inflate", 1) == [120.5]

    def test_override_that_rounds_onto_true_values_raises(self):
        f = kit.ready_stat(true_values=[100], integer=True, level_overrides={1: {"inflate": [100.4]}})
        with pytest.raises(SeedError):
            seeded_values(f, "inflate", 1)


class TestFormatValue:
    @pytest.mark.parametrize("x,integer,expected", [
        (10000, True, "10,000"),
        (1234567, True, "1,234,567"),
        (999, True, "999"),
        (1000, True, "1,000"),
        (12, True, "12"),
        (1680.0, True, "1,680"),
        (62.50, False, "62.5"),
        (65.0, False, "65"),
        (65, False, "65"),
        (66.666, False, "66.7"),
        (90.909, False, "90.9"),
        (33.34, False, "33.3"),
        (110.00000000000001, False, "110"),
        (0.04, False, "0"),
        (7.25, False, "7.2"),
    ])
    def test_examples(self, x, integer, expected):
        assert format_value(x, integer) == expected
