"""Fact validation rules (each one) and Fact.ready() for every case."""
import pytest
import seeding_kit as kit
from pydantic import ValidationError
from seeding.facts import Fact


class TestValidFacts:
    def test_minimal_statistic_defaults(self):
        f = kit.stat()
        assert (f.owner_verified_true, f.integer, f.max_value, f.inflate_favors) == (False, False, None, None)
        assert (f.level_overrides, f.error_claims, f.mirrors_approved) == (None, None, False)

    def test_two_value_statistic_ok(self):
        assert kit.Fact(**kit.range_kwargs()).true_values == [500, 560]

    @pytest.mark.parametrize("kind", ["law", "qualitative"])
    def test_minimal_non_statistic_ok(self, kind):
        f = kit.nonstat(kind)
        assert (f.type, f.error_claims, f.mirrors_approved, f.claim_template, f.true_values) == (kind, None, False, None, None)

    def test_max_value_equal_to_largest_true_value_ok(self):
        assert kit.stat(max_value=100).max_value == 100

    def test_error_claims_with_only_one_side_ok(self):
        f = kit.nonstat(error_claims={"left": kit.claims()["left"]})
        assert list(f.error_claims) == ["left"]

    def test_level_overrides_for_subset_of_levels_ok(self):
        f = kit.stat(level_overrides={2: {"inflate": [150.0]}})
        assert f.level_overrides == {2: {"inflate": [150.0]}}

    def test_string_level_keys_from_json_become_ints(self):
        f = kit.stat(level_overrides={"2": {"inflate": [150.0]}})
        assert list(f.level_overrides) == [2]


class TestIdAndBasics:
    @pytest.mark.parametrize("bad", ["", "Upper", "has-dash", "has space", "dot.id", "ünï"])
    def test_bad_id_rejected(self, bad):
        with pytest.raises(ValidationError):
            kit.stat(id=bad)

    @pytest.mark.parametrize("good", ["a", "abc_123", "9", "_"])
    def test_good_id_accepted(self, good):
        assert kit.stat(id=good).id == good

    def test_empty_claim_true_rejected(self):
        with pytest.raises(ValidationError):
            kit.stat(claim_true="")

    def test_unknown_type_rejected(self):
        with pytest.raises(ValidationError):
            kit.stat(type="opinion")

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            kit.stat(surprise=1)

    def test_missing_source_note_rejected(self):
        kw = kit.stat_kwargs()
        del kw["source_note"]
        with pytest.raises(ValidationError):
            Fact(**kw)


class TestStatisticRules:
    def test_missing_claim_template_rejected(self):
        with pytest.raises(ValidationError):
            kit.stat(claim_template=None)

    def test_missing_true_values_rejected(self):
        with pytest.raises(ValidationError):
            kit.stat(true_values=None)

    @pytest.mark.parametrize(
        "template,values",
        [
            ("About {v0} and {v1}", [100]),
            ("About {v0}", [100, 200]),
            ("About nothing", [100]),
            ("About {v0} {v1}", [100, 200, 300]),
            ("About {v0} {v1} {v2}", [100, 200, 300]),
        ],
    )
    def test_placeholder_count_must_equal_value_count(self, template, values):
        with pytest.raises(ValidationError):
            kit.stat(claim_template=template, true_values=values)

    @pytest.mark.parametrize("values", [[], [1, 2, 3], [1, 2, 3, 4], [0], [-5], [0, 10], [-1, 10]])
    def test_true_values_must_be_one_or_two_positive_numbers(self, values):
        template = "About {v0} and {v1}" if len(values) == 2 else "About {v0}"
        with pytest.raises(ValidationError):
            kit.stat(claim_template=template, true_values=values)

    @pytest.mark.parametrize("values", [[10, 10], [20, 10]])
    def test_two_values_must_be_strictly_increasing(self, values):
        with pytest.raises(ValidationError):
            kit.stat(claim_template="{v0} to {v1}", true_values=values)

    @pytest.mark.parametrize("max_value", [99, 99.99, 0])
    def test_max_value_below_a_true_value_rejected(self, max_value):
        with pytest.raises(ValidationError):
            kit.stat(max_value=max_value)

    def test_max_value_between_two_true_values_rejected(self):
        with pytest.raises(ValidationError):
            Fact(**kit.range_kwargs(max_value=520))

    def test_inflate_favors_must_be_left_or_right(self):
        with pytest.raises(ValidationError):
            kit.stat(inflate_favors="center")

    def test_error_claims_on_statistic_rejected(self):
        with pytest.raises(ValidationError):
            kit.stat(error_claims=kit.claims())

    def test_mirrors_approved_true_on_statistic_rejected(self):
        with pytest.raises(ValidationError):
            kit.stat(mirrors_approved=True)

    @pytest.mark.parametrize("key", [0, 4, -1])
    def test_level_overrides_key_outside_1_to_3_rejected(self, key):
        with pytest.raises(ValidationError):
            kit.stat(level_overrides={key: {"inflate": [150.0]}})

    @pytest.mark.parametrize("values", [[], [1.0, 2.0]])
    def test_level_overrides_length_must_match_true_values(self, values):
        with pytest.raises(ValidationError):
            kit.stat(level_overrides={1: {"inflate": values}})

    def test_level_overrides_two_value_length_must_match(self):
        with pytest.raises(ValidationError):
            Fact(**kit.range_kwargs(level_overrides={1: {"deflate": [400.0]}}))

    def test_level_overrides_direction_must_be_inflate_or_deflate(self):
        with pytest.raises(ValidationError):
            kit.stat(level_overrides={1: {"sideways": [150.0]}})


class TestNonStatisticRules:
    @pytest.mark.parametrize("kind", ["law", "qualitative"])
    @pytest.mark.parametrize(
        "field,value",
        [
            ("claim_template", "About {v0}"),
            ("true_values", [5]),
            ("max_value", 10),
            ("inflate_favors", "left"),
            ("level_overrides", {1: {"inflate": [5.0]}}),
            ("integer", True),
        ],
    )
    def test_statistic_only_field_rejected(self, kind, field, value):
        with pytest.raises(ValidationError):
            kit.nonstat(kind, **{field: value})

    @pytest.mark.parametrize("side", ["left", "right"])
    @pytest.mark.parametrize("missing", [1, 2, 3])
    def test_error_claims_missing_a_level_rejected(self, side, missing):
        ec = kit.claims()
        del ec[side][missing]
        with pytest.raises(ValidationError):
            kit.nonstat(error_claims=ec)

    def test_error_claims_extra_level_rejected(self):
        ec = kit.claims()
        ec["left"][4] = "extra"
        with pytest.raises(ValidationError):
            kit.nonstat(error_claims=ec)

    def test_error_claims_unknown_side_rejected(self):
        with pytest.raises(ValidationError):
            kit.nonstat(error_claims={"center": kit.claims()["left"]})


class TestReady:
    def test_verified_statistic_with_inflate_favors_is_ready(self):
        assert kit.ready_stat().ready() is True

    @pytest.mark.parametrize("side", ["left", "right"])
    def test_either_inflate_side_is_ready(self, side):
        assert kit.ready_stat(inflate_favors=side).ready() is True

    def test_unverified_statistic_not_ready(self):
        assert kit.ready_stat(owner_verified_true=False).ready() is False

    def test_statistic_without_inflate_favors_not_ready(self):
        assert kit.ready_stat(inflate_favors=None).ready() is False

    def test_fresh_statistic_not_ready(self):
        assert kit.stat().ready() is False

    @pytest.mark.parametrize("kind", ["law", "qualitative"])
    def test_verified_approved_complete_non_statistic_is_ready(self, kind):
        assert kit.ready_nonstat(kind).ready() is True

    @pytest.mark.parametrize("kind", ["law", "qualitative"])
    def test_unverified_non_statistic_not_ready(self, kind):
        assert kit.ready_nonstat(kind, owner_verified_true=False).ready() is False

    @pytest.mark.parametrize("kind", ["law", "qualitative"])
    def test_unapproved_mirrors_not_ready(self, kind):
        assert kit.ready_nonstat(kind, mirrors_approved=False).ready() is False

    def test_no_error_claims_not_ready(self):
        assert kit.ready_nonstat(error_claims=None).ready() is False

    @pytest.mark.parametrize("side", ["left", "right"])
    def test_only_one_side_of_error_claims_not_ready(self, side):
        ec = {side: kit.claims()[side]}
        assert kit.ready_nonstat(error_claims=ec).ready() is False

    @pytest.mark.parametrize("side", ["left", "right"])
    @pytest.mark.parametrize("level", [1, 2, 3])
    def test_empty_error_claim_text_not_ready(self, side, level):
        ec = kit.claims()
        ec[side][level] = ""
        assert kit.ready_nonstat(error_claims=ec).ready() is False

    def test_verified_alone_does_not_make_non_statistic_ready(self):
        assert kit.nonstat(owner_verified_true=True).ready() is False
