"""build_seeds, build_arms and determinism."""
import pytest
import seeding_kit as kit
from seeding.seeds import Seed, SeedError, build_arms, build_seeds

ORDER = [("left", 1), ("left", 2), ("left", 3), ("right", 1), ("right", 2), ("right", 3)]


def key(seed):
    return (seed.side, seed.level)


class TestStatisticSeeds:
    def test_six_seeds_in_side_then_level_order(self):
        assert [key(s) for s in build_seeds(kit.ready_range())] == ORDER

    def test_seeds_are_seed_objects_with_fact_id(self):
        seeds = build_seeds(kit.ready_range())
        assert [(type(s), s.fact_id) for s in seeds] == [(Seed, "range_fact")] * 6

    def test_directions_when_left_favored(self):
        seeds = build_seeds(kit.ready_range(inflate_favors="left"))
        assert [s.direction for s in seeds] == ["inflate"] * 3 + ["deflate"] * 3

    def test_directions_when_right_favored(self):
        seeds = build_seeds(kit.ready_range(inflate_favors="right"))
        assert [s.direction for s in seeds] == ["deflate"] * 3 + ["inflate"] * 3

    def test_false_values_left_favored(self):
        seeds = build_seeds(kit.ready_range())
        assert [s.false_values for s in seeds] == [
            [550, 616], [750, 840], [1500, 1680], [455, 509], [333, 373], [167, 187]]

    def test_false_claims_are_template_filled_and_formatted(self):
        seeds = build_seeds(kit.ready_range())
        assert [s.false_claim for s in seeds] == [
            "Between 550 and 616 things exist",
            "Between 750 and 840 things exist",
            "Between 1,500 and 1,680 things exist",
            "Between 455 and 509 things exist",
            "Between 333 and 373 things exist",
            "Between 167 and 187 things exist",
        ]

    def test_single_non_integer_claims(self):
        seeds = build_seeds(kit.ready_stat(claim_template="About {v0}% of things"))
        assert [s.false_claim for s in seeds] == [
            "About 110% of things", "About 150% of things", "About 300% of things",
            "About 90.9% of things", "About 66.7% of things", "About 33.3% of things"]

    @pytest.mark.parametrize("favors", ["left", "right"])
    @pytest.mark.parametrize("idx", [0, 1, 2])
    def test_mirror_property_unrounded(self, favors, idx):
        seeds = build_seeds(kit.ready_stat(inflate_favors=favors))
        left, right = seeds[idx], seeds[idx + 3]
        assert left.false_values[0] * right.false_values[0] == pytest.approx(100 ** 2)

    @pytest.mark.parametrize("idx", [0, 1, 2])
    def test_mirror_property_two_values(self, idx):
        f = kit.ready_stat(true_values=[40.0, 90.0], claim_template="{v0} {v1}")
        seeds = build_seeds(f)
        a, b = seeds[idx], seeds[idx + 3]
        assert [x * y for x, y in zip(a.false_values, b.false_values)] == [pytest.approx(1600), pytest.approx(8100)]

    def test_same_level_seeds_use_opposite_directions(self):
        seeds = build_seeds(kit.ready_range())
        assert [(seeds[i].direction, seeds[i + 3].direction) for i in range(3)] == [("inflate", "deflate")] * 3

    def test_overrides_used_for_their_level_and_direction(self):
        f = kit.ready_range(level_overrides={2: {"inflate": [600.0, 650.0]}})
        seeds = build_seeds(f)
        assert [s.false_values for s in seeds] == [
            [550, 616], [600.0, 650.0], [1500, 1680], [455, 509], [333, 373], [167, 187]]
        assert seeds[1].false_claim == "Between 600 and 650 things exist"

    def test_override_with_max_value_like_shipped_fact(self):
        f = kit.ready_stat(
            true_values=[60, 70], claim_template="{v0}% to {v1}% of resolutions", max_value=100, integer=True,
            level_overrides={1: {"inflate": [66, 77], "deflate": [55, 63]},
                             2: {"inflate": [80, 90], "deflate": [40, 47]},
                             3: {"inflate": [95, 100], "deflate": [20, 23]}})
        assert [s.false_claim for s in build_seeds(f)] == [
            "66% to 77% of resolutions", "80% to 90% of resolutions", "95% to 100% of resolutions",
            "55% to 63% of resolutions", "40% to 47% of resolutions", "20% to 23% of resolutions"]

    def test_max_value_breach_propagates_as_seed_error(self):
        with pytest.raises(SeedError):
            build_seeds(kit.ready_stat(true_values=[60], max_value=100))

    def test_no_seed_equals_the_true_claim(self):
        f = kit.ready_stat()
        assert [s.false_claim == f.claim_true for s in build_seeds(f)] == [False] * 6

    def test_deterministic(self):
        f = kit.ready_range()
        assert build_seeds(f) == build_seeds(f)
        assert [s.model_dump() for s in build_seeds(f)] == [s.model_dump() for s in build_seeds(kit.ready_range())]


class TestNotReady:
    def test_unverified_raises(self):
        with pytest.raises(SeedError):
            build_seeds(kit.ready_stat(owner_verified_true=False))

    def test_statistic_without_inflate_favors_raises(self):
        with pytest.raises(SeedError):
            build_seeds(kit.ready_stat(inflate_favors=None))

    @pytest.mark.parametrize("kind", ["law", "qualitative"])
    def test_non_statistic_without_mirror_approval_raises(self, kind):
        with pytest.raises(SeedError):
            build_seeds(kit.ready_nonstat(kind, mirrors_approved=False))

    def test_non_statistic_without_error_claims_raises(self):
        with pytest.raises(SeedError):
            build_seeds(kit.ready_nonstat(error_claims=None))

    def test_arms_not_ready_raises(self):
        with pytest.raises(SeedError):
            build_arms(kit.stat())


class TestNonStatisticSeeds:
    @pytest.mark.parametrize("kind", ["law", "qualitative"])
    def test_six_seeds_in_order(self, kind):
        assert [key(s) for s in build_seeds(kit.ready_nonstat(kind))] == ORDER

    def test_claims_come_from_error_claims(self):
        seeds = build_seeds(kit.ready_nonstat())
        assert [s.false_claim for s in seeds] == [
            "claim left 1", "claim left 2", "claim left 3", "claim right 1", "claim right 2", "claim right 3"]

    def test_direction_and_values_are_none(self):
        seeds = build_seeds(kit.ready_nonstat())
        assert [(s.direction, s.false_values) for s in seeds] == [(None, None)] * 6

    def test_fact_id_carried(self):
        assert {s.fact_id for s in build_seeds(kit.ready_nonstat("qualitative"))} == {"qualitative_fact"}

    def test_each_side_level_maps_to_its_own_text(self):
        ec = {"left": {1: "L1", 2: "L2", 3: "L3"}, "right": {1: "R1", 2: "R2", 3: "R3"}}
        assert [s.false_claim for s in build_seeds(kit.ready_nonstat(error_claims=ec))] == [
            "L1", "L2", "L3", "R1", "R2", "R3"]

    def test_deterministic(self):
        f = kit.ready_nonstat()
        assert build_seeds(f) == build_seeds(f)


class TestBuildArms:
    def test_keys(self):
        assert set(build_arms(kit.ready_range())) == {"true", "seeds"}

    def test_true_arm_is_claim_true(self):
        assert build_arms(kit.ready_range())["true"] == "Between 500 and 560 things exist"

    def test_seeds_equal_build_seeds(self):
        f = kit.ready_range()
        assert build_arms(f)["seeds"] == build_seeds(f)

    def test_non_statistic_arms(self):
        arms = build_arms(kit.ready_nonstat())
        assert (arms["true"], len(arms["seeds"])) == ("The true claim", 6)
