"""seeding.arms.arm_specs: the arms of a ready fact in their fixed order."""
import gen_kit as kit
import pytest


def specs(fact):
    from seeding import arms

    return arms.arm_specs(fact)


def seeds(fact):
    from seeding.seeds import build_seeds

    return build_seeds(fact)


class TestStatisticArms:
    def test_eight_specs_in_the_fixed_order(self):
        assert [(s["side"], s["arm"]) for s in specs(kit.range_fact())] == kit.ARM_ORDER

    def test_each_spec_has_exactly_these_keys(self):
        assert [sorted(s) for s in specs(kit.range_fact())] == [["arm", "claim", "seed", "side"]] * 8

    def test_the_claims_are_the_true_claim_and_the_hand_worked_false_claims(self):
        assert [s["claim"] for s in specs(kit.range_fact())] == [kit.RANGE_CLAIMS[key] for key in kit.ARM_ORDER]

    def test_true_arms_carry_no_seed(self):
        result = specs(kit.range_fact())
        assert (result[0]["seed"], result[4]["seed"]) == (None, None)

    def test_error_arms_carry_the_seed_of_that_side_and_level(self):
        fact = kit.range_fact()
        expected = seeds(fact)
        result = specs(fact)
        assert [s["seed"] for s in result[1:4] + result[5:8]] == expected

    def test_the_claim_of_a_true_arm_is_the_fact_claim_verbatim(self):
        fact = kit.range_fact(claim_true="Between 500 and 560 things exist.")
        assert specs(fact)[0]["claim"] == "Between 500 and 560 things exist."

    def test_a_deflating_left_side_is_supported(self):
        result = specs(kit.range_fact(inflate_favors="right"))
        assert [s["claim"] for s in result[1:4]] == [
            "Between 455 and 509 things exist", "Between 333 and 373 things exist", "Between 167 and 187 things exist",
        ]

    def test_the_output_is_the_same_twice(self):
        assert specs(kit.range_fact()) == specs(kit.range_fact())


class TestNonStatisticArms:
    def test_four_specs_in_the_fixed_order(self):
        assert [(s["side"], s["arm"]) for s in specs(kit.law_fact())] == [
            ("left", "true"), ("left", "err"), ("right", "true"), ("right", "err"),
        ]

    def test_the_claims_are_the_true_claim_and_the_side_error_claims(self):
        fact = kit.law_fact()
        assert [s["claim"] for s in specs(fact)] == [
            fact.claim_true, fact.error_claims["left"], fact.claim_true, fact.error_claims["right"],
        ]

    def test_the_error_arms_carry_the_matching_seed(self):
        fact = kit.law_fact()
        result = specs(fact)
        assert [result[0]["seed"], result[1]["seed"], result[2]["seed"], result[3]["seed"]] == [None, seeds(fact)[0], None, seeds(fact)[1]]

    def test_qualitative_facts_behave_like_laws(self):
        fact = kit.law_fact(id="qual_fact", type="qualitative")
        assert [(s["side"], s["arm"]) for s in specs(fact)] == [
            ("left", "true"), ("left", "err"), ("right", "true"), ("right", "err"),
        ]


class TestNotReady:
    def test_a_not_ready_fact_is_refused(self):
        with pytest.raises(ValueError):
            specs(kit.unready_fact())
