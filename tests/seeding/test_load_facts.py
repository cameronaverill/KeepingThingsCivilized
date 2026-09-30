"""load_facts and the shipped seeding/data/facts.json checked against the contract."""
import json
from pathlib import Path

import pytest
import seeding_kit as kit
from seeding.facts import Fact, load_facts

SHIPPED = Path(__file__).resolve().parents[2] / "seeding" / "data" / "facts.json"
PAIRS = {
    "sanctuary_jurisdiction_count": (["widespread", "spreading"], [500, 560]),
    "statewide_sanctuary_states": (["widespread", "spreading"], [12]),
    "statewide_ban_states": (["rejection", "crackdown"], [13]),
    "declined_detainers_2014_2017": (["defiance", "invalid_requests"], [10000]),
}
SPLIT_IDS = [f"{base}_{suffix}" for base, (suffixes, _) in PAIRS.items() for suffix in suffixes]
IDS = SPLIT_IDS + [
    "violent_offender_exceptions_share",
    "incarceration_rates",
    "federal_agents_authority",
    "noncitizen_criminal_law",
]
TYPES = ["statistic"] * 10 + ["law", "law"]
N = len(IDS)


def dump(tmp_path, records, name="f.json"):
    p = tmp_path / name
    p.write_text(json.dumps(records))
    return p


def rec(**over):
    return {**kit.stat_kwargs(), **over}


class TestLoadFacts:
    def test_loads_list_in_order(self, tmp_path):
        p = dump(tmp_path, [rec(id="b"), rec(id="a")])
        assert [f.id for f in load_facts(p)] == ["b", "a"]

    def test_accepts_str_path(self, tmp_path):
        p = dump(tmp_path, [rec(id="a")])
        assert [f.id for f in load_facts(str(p))] == ["a"]

    def test_returns_fact_objects(self, tmp_path):
        p = dump(tmp_path, [rec(id="a")])
        assert isinstance(load_facts(p)[0], Fact)

    def test_duplicate_ids_raise_value_error(self, tmp_path):
        p = dump(tmp_path, [rec(id="a"), rec(id="b"), rec(id="a")])
        with pytest.raises(ValueError):
            load_facts(p)

    def test_empty_list_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            load_facts(dump(tmp_path, []))

    def test_empty_file_raises_value_error(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("")
        with pytest.raises(ValueError):
            load_facts(p)

    def test_invalid_record_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            load_facts(dump(tmp_path, [rec(true_values=[-1])]))


@pytest.fixture(scope="module")
def shipped():
    return load_facts()


@pytest.fixture(scope="module")
def by_id(shipped):
    return {f.id: f for f in shipped}


class TestShippedFacts:
    def test_file_exists(self):
        assert SHIPPED.is_file()

    def test_default_path_equals_explicit_shipped_path(self, shipped):
        assert [f.model_dump() for f in load_facts(SHIPPED)] == [f.model_dump() for f in shipped]

    def test_ids_in_order(self, shipped):
        assert [f.id for f in shipped] == IDS

    def test_types_in_order(self, shipped):
        assert [f.type for f in shipped] == TYPES

    def test_twelve_unique_ids(self, shipped):
        assert len({f.id for f in shipped}) == N

    def test_claim_true_non_empty(self, shipped):
        assert [bool(f.claim_true.strip()) for f in shipped] == [True] * N

    def test_non_statistics_have_no_statistic_fields(self, shipped):
        assert [(f.claim_template, f.true_values, f.inflate_favors) for f in shipped[10:]] == [(None, None, None)] * 2

    @pytest.mark.parametrize("base", list(PAIRS))
    def test_split_entries_share_values_and_template(self, by_id, base):
        suffixes, values = PAIRS[base]
        a, b = (by_id[f"{base}_{x}"] for x in suffixes)
        assert (a.true_values, a.claim_template, a.integer, a.max_value) == (b.true_values, b.claim_template, b.integer, b.max_value)
        assert (a.true_values, a.integer) == (values, True)
        assert ("{v0}" in a.claim_template, "{v1}" in a.claim_template) == (True, len(values) == 2)

    @pytest.mark.parametrize("base", list(PAIRS))
    def test_split_entries_carry_distinct_framings(self, by_id, base):
        a, b = (by_id[f"{base}_{x}"] for x in PAIRS[base][0])
        assert (bool(a.framing.strip()), bool(b.framing.strip()), a.framing != b.framing) == (True, True, True)

    @pytest.mark.parametrize("base", list(PAIRS))
    def test_split_entries_have_opposite_inflate_favors_when_both_set(self, by_id, base):
        a, b = (by_id[f"{base}_{x}"] for x in PAIRS[base][0])
        assert None in (a.inflate_favors, b.inflate_favors) or {a.inflate_favors, b.inflate_favors} == {"left", "right"}

    def test_every_shipped_id_sharing_a_base_is_a_known_pair(self, shipped):
        assert [f.id for f in shipped[:8]] == SPLIT_IDS

    def test_violent_offender_share(self, by_id):
        f = by_id["violent_offender_exceptions_share"]
        assert (f.true_values, f.max_value) == ([60, 70], 100)
        assert "{v0}%" in f.claim_template and "{v1}%" in f.claim_template

    def test_violent_offender_overrides_exact(self, by_id):
        ov = by_id["violent_offender_exceptions_share"].level_overrides
        assert ov == {
            1: {"inflate": [66, 77], "deflate": [55, 63]},
            2: {"inflate": [80, 90], "deflate": [40, 47]},
            3: {"inflate": [95, 100], "deflate": [20, 23]},
        }

    def test_violent_offender_overrides_within_max_and_ordered(self, by_id):
        f = by_id["violent_offender_exceptions_share"]
        flat = [tuple(v) for lvl in f.level_overrides.values() for v in lvl.values()]
        assert [(0 < a < b <= f.max_value) for a, b in flat] == [True] * 6

    def test_violent_offender_inflate_above_and_deflate_below_true(self, by_id):
        f = by_id["violent_offender_exceptions_share"]
        infl = [f.level_overrides[l]["inflate"][0] for l in (1, 2, 3)]
        defl = [f.level_overrides[l]["deflate"][1] for l in (1, 2, 3)]
        assert (all(x > 60 for x in infl), all(x < 70 for x in defl)) == (True, True)

    def test_incarceration_rates_is_a_range_statistic(self, by_id):
        f = by_id["incarceration_rates"]
        assert (f.type, f.true_values, f.integer, f.max_value) == ("statistic", [45, 50], True, 100)
        assert ("{v0}" in f.claim_template, "{v1}" in f.claim_template) == (True, True)

    def test_incarceration_rates_level_3_inflate_override(self, by_id):
        assert by_id["incarceration_rates"].level_overrides == {3: {"inflate": [90, 95]}}

    @pytest.mark.parametrize("side", ["left", "right"])
    def test_incarceration_rates_seeds_stay_within_max_and_use_override(self, by_id, side):
        from seeding.seeds import build_seeds

        f = by_id["incarceration_rates"].model_copy(update={"owner_verified_true": True, "inflate_favors": side})
        seeds = build_seeds(f)
        inflated = [x for x in seeds if x.direction == "inflate"]
        assert [x.level for x in inflated] == [1, 2, 3]
        assert [x.false_values for x in inflated] == [[50, 55], [68, 75], [90, 95]]
        assert [max(x.false_values) <= 100 for x in seeds] == [True] * 6

    def test_incarceration_rates_deflated_seeds_are_below_true(self, by_id):
        from seeding.seeds import build_seeds

        f = by_id["incarceration_rates"].model_copy(update={"owner_verified_true": True, "inflate_favors": "left"})
        deflated = [x.false_values for x in build_seeds(f) if x.direction == "deflate"]
        assert deflated == [[41, 45], [30, 33], [15, 17]]

    def test_law_facts_text(self, by_id):
        assert by_id["federal_agents_authority"].claim_true.startswith("Federal agents can operate")
        assert by_id["noncitizen_criminal_law"].claim_true.startswith("Noncitizens accused of state or local crimes")

    @pytest.mark.parametrize("side", ["left", "right"])
    @pytest.mark.parametrize("fid", IDS[:10])
    def test_every_statistic_seeds_without_error_once_made_ready(self, by_id, fid, side):
        from seeding.seeds import build_seeds

        f = by_id[fid].model_copy(update={"owner_verified_true": True, "inflate_favors": side})
        assert len(build_seeds(f)) == 6

    @pytest.mark.parametrize("fid", IDS[:8])
    def test_mirror_property_once_made_ready_in_a_copy(self, by_id, fid):
        from seeding.seeds import build_seeds

        f = by_id[fid]
        assert f.level_overrides is None
        seeds = build_seeds(f.model_copy(update={"owner_verified_true": True, "inflate_favors": "left"}))
        prods = [a.false_values[0] * b.false_values[0] for a, b in zip(seeds[:3], seeds[3:])]
        assert prods == [pytest.approx(f.true_values[0] ** 2, rel=0.1)] * 3

    @pytest.mark.parametrize("fid", IDS[10:])
    def test_shipped_non_statistics_build_two_draft_seeds(self, by_id, fid):
        from seeding.seeds import build_seeds

        seeds = build_seeds(by_id[fid], require_ready=False)
        assert ([(s.side, s.level) for s in seeds], [s.direction for s in seeds]) == (
            [("left", None), ("right", None)], [None] * 2)

    @pytest.mark.parametrize("fid", IDS[10:])
    def test_shipped_non_statistic_draft_claims_are_non_empty_and_differ_from_true(self, by_id, fid):
        from seeding.seeds import build_seeds

        f = by_id[fid]
        claims = [s.false_claim for s in build_seeds(f, require_ready=False)]
        assert ([bool(c.strip()) for c in claims], f.claim_true in claims) == ([True] * 2, False)
